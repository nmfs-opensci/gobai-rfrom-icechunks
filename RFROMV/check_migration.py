#!/usr/bin/env python
"""Verify the v2.3 stream restructure before deleting the old prefixes (issue #17).

The restructure replaces six stream directories with four. GCS has no rename, so
the move is a copy followed by a delete — and the delete is destructive and
NODD-visible, ~225 GB. This checks, from object metadata only (no downloads),
that the new tree is complete and that every object carried over from the old
tree is byte-identical to its source, comparing GCS's own CRC32C.

    python RFROMV/check_migration.py        # exit 0 only if it is safe to delete

Deliberately narrow and one-off: once the migration is done and the old prefixes
are gone, this script has no further purpose and can be deleted.
"""

import os
import sys

import gcsfs

BASE = "noaa-oar-rfrom/netcdf/v2.3"
EXPECTED_BLOCKS = 18
# new stream -> the old stream its unchanged blocks were copied from
STREAMS = [("temp", "temp_stable"), ("sal", "sal_stable"),
           ("temp_error", None), ("sal_error", None)]
OLD_PREFIXES = ["temp_stable", "temp_realtime", "sal_stable", "sal_realtime"]

GCS_TOKEN = (
    os.environ.get("NODD_GCS_TOKEN")
    or os.environ.get("RFROM_GCS_TOKEN")
    or "/home/jovyan/.config/gcloud/application_default_credentials.json"
)


def objects(fs, prefix):
    """{filename: metadata} for one stream directory, empty if it does not exist."""
    if prefix is None:
        return {}
    try:
        return {e["name"].split("/")[-1]: e
                for e in fs.ls(f"{BASE}/{prefix}", detail=True)
                if e["name"].endswith(".nc")}
    except FileNotFoundError:
        return {}


def main():
    fs = gcsfs.GCSFileSystem(token=GCS_TOKEN)
    ok = True

    for new, old in STREAMS:
        current, previous = objects(fs, new), objects(fs, old)
        size = sum(e["size"] for e in current.values()) / 1e9
        print(f"{new:<11} {len(current):>2} files  {size:7.1f} GB", end="")

        if len(current) != EXPECTED_BLOCKS:
            print(f"   EXPECTED {EXPECTED_BLOCKS}, GOT {len(current)}")
            ok = False
            continue

        # A pure-stable block keeps the name it was published under, so anything
        # present in both trees is a copy and must match bit for bit. The rest are
        # the rebuilt seam and tail blocks, which are new by design.
        carried = [f for f in current if f in previous]
        mismatched = [f for f in carried if current[f]["crc32c"] != previous[f]["crc32c"]]
        print(f"   {len(carried)} copied, {len(current) - len(carried)} rebuilt", end="")
        if mismatched:
            print(f"   CRC MISMATCH: {', '.join(sorted(mismatched))}")
            ok = False
        else:
            print("   copies verified" if carried else "")

    if not ok:
        print("\nDO NOT DELETE — fix the above first.")
        return 1

    stale = sum(sum(e["size"] for e in objects(fs, p).values()) for p in OLD_PREFIXES)
    print(f"\nNew tree complete and verified. The old prefixes hold {stale / 1e9:.1f} GB.")
    print("Check the Icechunk store builds before deleting:")
    print("    python build_icechunk.py --store rfrom_v23 --local-repo /tmp/rehearsal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
