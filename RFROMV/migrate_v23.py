#!/usr/bin/env python
"""Move the v2.3 tree from six stream directories to four (GitHub issue #17).

Blocks 0-15 of temp/sal are the same weeks from the same monthly sources as the
published *_stable blocks, and a pure-stable block keeps the name it already has,
so they are copied server-side inside the bucket rather than re-downloaded from
ERDDAP (~200 GB of traffic saved). Only the seam block and the tail are new work,
and those are built by nodd.py.

    python RFROMV/migrate_v23.py --plan     # what would be copied or built
    python RFROMV/migrate_v23.py --copy     # server-side copy, idempotent
    python RFROMV/migrate_v23.py --check    # verify before deleting the old prefixes

Uses gcsfs, not `gcloud storage`: gcsfs authenticates with the application-default
credentials this repo already uses (NODD_GCS_TOKEN), whereas the gcloud CLI needs
its own `gcloud auth login`. gcsfs issues a GCS rewrite and loops on the rewrite
token until the server reports done, so multi-GB objects copy correctly and
without being downloaded.

One-off: once the migration is done and the old prefixes are gone, this script has
no further purpose and can be deleted.
"""

import argparse
import os
import sys
import time

import gcsfs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import nodd  # noqa: E402  -- for the authoritative block plan

BASE = "noaa-oar-rfrom/netcdf/v2.3"
# new stream -> the old stream whose unchanged blocks are copied into it
COMBINED = [("temp", "temp_stable"), ("sal", "sal_stable")]
UNCHANGED = ["temp_error", "sal_error"]
OLD_PREFIXES = ["temp_stable", "temp_realtime", "sal_stable", "sal_realtime"]

GCS_TOKEN = (
    os.environ.get("NODD_GCS_TOKEN")
    or os.environ.get("RFROM_GCS_TOKEN")
    or "/home/jovyan/.config/gcloud/application_default_credentials.json"
)


def objects(fs, prefix):
    """{filename: metadata} for one stream directory, empty if it does not exist."""
    try:
        return {e["name"].split("/")[-1]: e
                for e in fs.ls(f"{BASE}/{prefix}", detail=True)
                if e["name"].endswith(".nc")}
    except FileNotFoundError:
        return {}


def plan(stream):
    """The stream's target block file names, in block order, straight from nodd.py."""
    times, origin = nodd.stream_time_axis(stream)
    return [b["filename"] for b in nodd.make_file_blocks(stream, times, origin)]


def do_plan(fs, copy=False):
    """Report (and optionally perform) the copy of every block that already exists."""
    copied = built = failed = 0
    for stream, old in COMBINED:
        names = plan(stream)
        source, dest = objects(fs, old), objects(fs, stream)
        print(f"\n{stream}/  ({len(names)} blocks)")
        for i, name in enumerate(names):
            if name in dest and (name not in source
                                 or dest[name]["crc32c"] == source[name]["crc32c"]):
                print(f"  [{i:2d}] present            {name}")
                continue
            if name not in source:
                print(f"  [{i:2d}] BUILD with nodd.py  {name}")
                built += 1
                continue
            size = source[name]["size"] / 1e9
            if not copy:
                print(f"  [{i:2d}] copy {size:5.1f} GB       {name}")
                copied += 1
                continue
            start = time.time()
            fs.copy(f"{BASE}/{old}/{name}", f"{BASE}/{stream}/{name}")
            check = objects(fs, stream).get(name)
            ok = check and check["crc32c"] == source[name]["crc32c"]
            print(f"  [{i:2d}] copied {size:5.1f} GB in {time.time() - start:5.1f}s  "
                  f"{'crc ok' if ok else 'CRC MISMATCH'}  {name}")
            copied += 1
            failed += not ok
    verb = "copied" if copy else "to copy"
    print(f"\n{copied} {verb}, {built} to build with nodd.py"
          + (f", {failed} FAILED" if failed else ""))
    return 1 if failed else 0


def do_check(fs):
    """Verify the new tree is complete and every copied object matches its source."""
    ok = True
    for stream in [s for s, _ in COMBINED] + UNCHANGED:
        old = dict(COMBINED).get(stream)
        current = objects(fs, stream)
        previous = objects(fs, old) if old else {}
        expected = plan(stream)
        size = sum(e["size"] for e in current.values()) / 1e9
        print(f"{stream:<11} {len(current):>2}/{len(expected)} files  {size:7.1f} GB", end="")

        missing = [n for n in expected if n not in current]
        extra = [n for n in current if n not in expected]
        if missing or extra:
            print()
            for n in missing:
                print(f"   MISSING  {n}")
            for n in extra:
                print(f"   UNEXPECTED  {n}")
            ok = False
            continue

        carried = [f for f in current if f in previous]
        bad = [f for f in carried if current[f]["crc32c"] != previous[f]["crc32c"]]
        print(f"   {len(carried)} copied, {len(current) - len(carried)} rebuilt", end="")
        print(f"   CRC MISMATCH: {', '.join(sorted(bad))}" if bad
              else ("   copies verified" if carried else ""))
        ok &= not bad

    if not ok:
        print("\nDO NOT DELETE — fix the above first.")
        return 1
    stale = sum(sum(e["size"] for e in objects(fs, p).values()) for p in OLD_PREFIXES)
    print(f"\nNew tree complete and verified. The old prefixes hold {stale / 1e9:.1f} GB.")
    print("Confirm the store builds before deleting them:")
    print("    python build_icechunk.py --store rfrom_v23 --local-repo /tmp/rehearsal")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="Show what would be copied or built.")
    g.add_argument("--copy", action="store_true", help="Perform the server-side copy.")
    g.add_argument("--check", action="store_true", help="Verify before deleting the old prefixes.")
    args = p.parse_args(argv)

    fs = gcsfs.GCSFileSystem(token=GCS_TOKEN)
    if args.check:
        return do_check(fs)
    return do_plan(fs, copy=args.copy)


if __name__ == "__main__":
    sys.exit(main())
