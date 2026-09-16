#!/usr/bin/env python3
"""Build the gridlook browser viewer and publish it into a NODD bucket.

gridlook (https://github.com/eeholmes/gridlook, a fork of
https://github.com/d70-t/gridlook) is a WebGL viewer for cloud-hosted Zarr and
Icechunk stores. Its production build is a folder of plain static files with
relative paths, so it runs from any bucket prefix exactly the way the landing
page ``index.html`` does -- no server, no redirects. The dataset to open goes in
the URL *fragment* (after ``#``), which the bucket never sees:

    https://storage.googleapis.com/noaa-oar-gobai/viewer/index.html#icechunk+https://storage.googleapis.com/noaa-oar-gobai/icechunk/v202606::varname=o2

Viewer and store sharing one bucket means the browser needs nothing beyond the
bucket's existing CORS policy (GET/HEAD from any origin, with ``Range`` in
``responseHeader`` -- see claude/notes/rfromv-icechunk.md §8).

Steps
-----
1. Get gridlook and its dependencies (Node >= 24.16)::

       git clone https://github.com/eeholmes/gridlook ~/gridlook
       cd ~/gridlook && npm ci

2. Build and upload. ``--build`` runs the Vite build for you; without it the
   script uploads an existing ``--dist`` folder::

       python publish_viewer.py --product gobai --build ~/gridlook --dry-run
       python publish_viewer.py --product gobai --build ~/gridlook   # build + upload
       python publish_viewer.py --product rfrom --dist /tmp/gridlook-dist

   ``--product`` picks the bucket and store (see ``PRODUCTS``); it is required,
   like ``nodd.py --stream``, so nothing is uploaded to a bucket by default. One
   build serves every product -- the dataset lives in the link, not the build --
   so build once and pass ``--dist`` for the second bucket.

3. Open the viewer URL the script prints. ``--prune`` also deletes objects under
   the viewer prefix that the new build no longer contains -- every build has new
   content-hashed asset names, so old ones pile up otherwise. Pruning never
   touches anything outside ``gs://<bucket>/<prefix>/``.

Why the build is not ``npm run build``: that runs ``vue-tsc`` and writes
source maps, and on a small machine (tested with a ~1.9 GB memory cap) it is
OOM-killed. ``vite build --sourcemap false`` with a capped Node heap fits,
builds in under a minute, and halves the upload (≈23 MB). Type checking belongs
to gridlook's own CI, not to publishing.

Caching: ``index.html`` is uploaded with ``Cache-Control: no-cache`` so a new
build shows up immediately (the landing page's default one-hour CDN cache would
otherwise serve the old page, which references asset files that ``--prune`` may
have deleted). Files under ``assets/`` carry a content hash in their names and are
cached for a year. Everything else gets a five-minute cache.

Credentials: same mechanism as ``nodd.py`` -- ``NODD_GCS_TOKEN`` (a credentials
JSON path, or ``google_default``), falling back to the
application-default credentials file that ``gcloud auth application-default
login`` writes. The bucket must already be publicly
readable; this script sets no ACLs.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Same buckets and store prefixes as build_icechunk.py's STORES.
PRODUCTS = {
    "gobai": {
        "bucket": "noaa-oar-gobai",
        "store_prefix": "icechunk/v202606",
        "variables": ("o2", "no3"),
    },
    "rfrom": {
        "bucket": "noaa-oar-rfrom",
        "store_prefix": "icechunk/v2.3",
        "variables": ("ocean_temperature", "ocean_salinity",
                      "ocean_temperature_error", "ocean_salinity_error"),
    },
}
PREFIX = "viewer"
DEFAULT_DIST = Path("/tmp/gridlook-dist")
PUBLIC = "https://storage.googleapis.com"

GCS_TOKEN = (
    os.environ.get("NODD_GCS_TOKEN")
    or "~/.config/gcloud/application_default_credentials.json"
)
if os.sep in GCS_TOKEN or GCS_TOKEN.startswith("~"):
    GCS_TOKEN = os.path.expanduser(GCS_TOKEN)

# Browsers refuse ES modules served with the wrong type, and refuse to stream-
# compile wasm unless it is application/wasm. Do not rely on the platform table.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".geojson": "application/geo+json",
    ".wasm": "application/wasm",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}


def viewer_urls(product: dict, prefix: str) -> dict[str, str]:
    bucket = product["bucket"]
    store = f"icechunk+{PUBLIC}/{bucket}/{product['store_prefix']}"
    base = f"{PUBLIC}/{bucket}/{prefix}/index.html"
    return {var: f"{base}#{store}::varname={var}" for var in product["variables"]}


def build(gridlook: Path, dist: Path) -> None:
    gridlook = gridlook.expanduser().resolve()
    if not (gridlook / "package.json").exists():
        sys.exit(f"{gridlook} is not a gridlook checkout (no package.json)")
    if not (gridlook / "node_modules").exists():
        subprocess.run(["npm", "ci"], cwd=gridlook, check=True)
    env = dict(os.environ, NODE_OPTIONS="--max-old-space-size=500")
    cmd = ["npx", "vite", "build", "--outDir", str(dist), "--emptyOutDir",
           "--sourcemap", "false"]
    print("$", " ".join(cmd), f"  (in {gridlook})")
    subprocess.run(cmd, cwd=gridlook, env=env, check=True)
    write_build_info(gridlook, dist)


def write_build_info(gridlook: Path, dist: Path) -> None:
    """Record which gridlook commit is live, so the bucket copy is traceable."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=gridlook, capture_output=True,
                              text=True).stdout.strip()
    info = {
        "gridlook_remote": git("remote", "get-url", "origin"),
        "gridlook_commit": git("rev-parse", "HEAD"),
        "gridlook_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (dist / "build-info.json").write_text(json.dumps(info, indent=2) + "\n")


def plan(dist: Path) -> list[tuple[Path, str, str, str]]:
    """(local file, relative key, content type, cache control) for every file."""
    if not (dist / "index.html").exists():
        sys.exit(f"{dist} has no index.html -- build first (--build)")
    rows = []
    for path in sorted(p for p in dist.rglob("*") if p.is_file()):
        rel = path.relative_to(dist).as_posix()
        ctype = (CONTENT_TYPES.get(path.suffix.lower())
                 or mimetypes.guess_type(path.name)[0]
                 or "application/octet-stream")
        if rel == "index.html":
            cache = "no-cache"
        elif rel.startswith("assets/"):
            cache = "public, max-age=31536000, immutable"
        else:
            cache = "public, max-age=300"
        rows.append((path, rel, ctype, cache))
    return rows


def upload(rows, bucket: str, prefix: str, prune: bool) -> None:
    import gcsfs

    fs = gcsfs.GCSFileSystem(token=GCS_TOKEN)
    root = f"{bucket}/{prefix}"
    # Assets first, index.html last: a visitor never gets a page whose assets
    # are not up yet.
    for path, rel, ctype, cache in sorted(rows, key=lambda r: r[1] == "index.html"):
        fs.put_file(str(path), f"{root}/{rel}", content_type=ctype,
                    fixed_key_metadata={"cache_control": cache})
        print(f"  put {rel}")
    if prune:
        keep = {f"{root}/{rel}" for _, rel, _, _ in rows}
        for obj in fs.find(root):
            if obj not in keep:
                fs.rm_file(obj)
                print(f"  removed stale {obj[len(root) + 1:]}")


def verify(bucket: str, prefix: str) -> None:
    """Anonymous check that the page is public and served with the right type."""
    url = f"{PUBLIC}/{bucket}/{prefix}/index.html?cb={os.getpid()}"
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD")) as r:
        print(f"  {r.status} {r.headers['Content-Type']}  "
              f"cache-control: {r.headers['Cache-Control']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--product", required=True, choices=sorted(PRODUCTS),
                    help="which bucket and store to publish the viewer for")
    ap.add_argument("--build", type=Path, metavar="GRIDLOOK_DIR",
                    help="gridlook checkout to build before uploading")
    ap.add_argument("--dist", type=Path, default=DEFAULT_DIST,
                    help=f"build output folder (default {DEFAULT_DIST})")
    ap.add_argument("--prefix", default=PREFIX,
                    help=f"folder in the bucket for the viewer (default {PREFIX!r})")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be uploaded; upload nothing")
    ap.add_argument("--prune", action="store_true",
                    help="delete objects under the prefix that this build lacks")
    args = ap.parse_args()
    prefix = args.prefix.strip("/")
    product = PRODUCTS[args.product]
    bucket = product["bucket"]

    if args.build:
        build(args.build, args.dist)
    rows = plan(args.dist)
    size = sum(p.stat().st_size for p, *_ in rows)
    print(f"{len(rows)} files, {size / 2**20:.1f} MB -> gs://{bucket}/{prefix}/")

    if args.dry_run:
        for _, rel, ctype, cache in rows:
            print(f"  {rel:55s} {ctype:28s} {cache}")
    else:
        upload(rows, bucket, prefix, args.prune)
        verify(bucket, prefix)

    print("\nViewer links:")
    for var, url in viewer_urls(product, prefix).items():
        print(f"  {var}: {url}")


if __name__ == "__main__":
    main()
