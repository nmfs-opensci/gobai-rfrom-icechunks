# Reproducibility review — 2026-09-16

**Status.** Fixed in the `docs/reproducibility-review` PR:
- item 1 (the Python 3.11 syntax error);
- items 2 and 4 (old stream names and counts, and the four retired stream
  entries in `nodd.py`, which are now deleted);
- item 3 (a "Rebuilding from scratch" section in the root README, and the
  migration material removed);
- item 6 (root README).

`RFROMV/migrate_v23.py` was deleted. The migration is complete, and the bucket
holds only `temp`, `sal`, `temp_error` and `sal_error` under `netcdf/v2.3/`.
The prep notebook now targets `temp` and refuses to overwrite a published
object.

Item 9 is fixed too. `requirements.lock` pins everything, built by `uv pip compile
--universal` for Python 3.12 around `constraints.txt`. That file holds the
versions recorded in `rfromv-icechunk.md` §11; dask, h5netcdf and pandas were
never recorded, so they are pinned to their validated lower bounds. The lock was
tested on 2026-09-16 with `pip install -r requirements.lock` into a clean
Python 3.12.12 venv on the hub, and `pip check` was clean:
- a full `build_icechunk.py --store gobai_hr --local-repo` build validated (about 10 min);
- `nodd.py --stream o2 --blocks 17 --no-upload` rebuilt that block. The result
  matches the published file: identical values (3 levels checked), coords and
  chunk/compression encoding, with only `history` differing.
The lock has not been tested on macOS or with a full 100-step block (the hub
container is capped at ~1.9 GB of memory).

Still open: items 5 (`CLAUDE.md`), 8, 10 and 11, and the GOBAI monthly notebook.
One more gap turned up while writing the rebuild recipe:
`build_icechunk.open_repo` passes `GCS_TOKEN` to
`ic.gcs_storage(application_credentials=...)`, which expects a file path. So
setup.md's option c (`NODD_GCS_TOKEN=google_default`) works for `nodd.py` but
probably not for the store build. This is untested.

Question asked: could someone new to this repo work out exactly how to rebuild
the NODD netCDFs and the virtual Icechunk stores? Short answer: **mostly, but
not from any one page, and some of the paths they would find first are wrong.**
All the needed information exists. It is spread across the root README, `setup.md`,
two product READMEs, `CLAUDE.md` and script docstrings, and several of those
still describe the superseded six-stream RFROM layout.

Checked by reading every doc and script, running `nodd.py --list` and
`build_icechunk.py --list`, and byte-compiling every script with the hub's
current Python (3.11.14).

## Blocking

1. **`build_icechunk.py` does not parse on Python 3.11.** Line 617 nests
   same-type quotes in an f-string (`f'gs://{cfg["bucket"]}…'`), which is only
   legal from 3.12 (PEP 701). But `setup.md` says "Python 3.11+", and the hub's
   `notebook` env is now 3.11.14 **with no icechunk installed**. The handoff
   records the store being built on 3.12.12, so the hub image has changed since.
   As things stand, a newcomer can't run the store build on the hub, and can't
   run it off-hub on the documented minimum Python. Fix: rewrite that line
   without the nested quotes, or raise the documented minimum to 3.12.

2. **The first examples a newcomer sees build the retired layout.** The
   `setup.md` "Run it" section (which `nodd.py --setup` also prints), the
   `nodd.py` module docstring, and the RFROMV README's "Running the batch script"
   all use `temp_stable`, `sal_stable` or `temp_realtime`. They also split a
   stream as `--blocks 0-8` / `9-16`, which is the old 17-block plan. Anyone who
   follows them gets the six-stream tree, which cannot be virtualized, and the
   Icechunk build then fails. The current streams are `temp`, `sal`,
   `temp_error` and `sal_error`, with 18 blocks each (0–17).

## Confusing or stale

3. **No end-to-end "rebuild from nothing" recipe.** The real order is:
   `nodd.py --all` for each stream → `build_icechunk.py --store …` (which
   validates) → upload `index.html` → `publish_viewer.py`. The RFROM README
   instead spends a large section on `migrate_v23.py` and on retiring old
   prefixes. That was a one-off move of an existing tree. A fresh rebuild
   doesn't need it, and the README never says so.
4. **Stale counts.** RFROMV README line 21 says "1670-step stable record, 17
   blocks", `setup.md`'s resource table says RFROM has 17 blocks, and the
   `nodd.py` docstring says "v2.3 (six streams)". The current RFROM v2.3 record
   is 1719 steps in 18 blocks.
5. **`CLAUDE.md` opening is stale.** It says there is "no build system" and that
   the deliverables are notebooks. The deliverables are now the scripts.
6. **Root README.** The title says `gobai-icechunks` rather than the repo name.
   The `## Chunking` section is unfinished scratch notes. There is no
   `## Reuse and citation` section, although `LICENSE` is Apache-2.0.
7. **`RFROMV/setup_bare_VM.txt`** refers to `rfrom_nodd.py`, the `RFROM_*`
   environment variables, `cd RFROMV` and a `requirements.txt` in that
   directory, and contains Eli's own git identity. It is a personal
   cheat-sheet by design, and the handoff says not to tidy it. The RFROM README
   already labels it as informal, but a newcomer browsing the files still finds
   it first. Moving it out of the repo, or renaming it, is Eli's call.

## Reproducibility limits (inherent, worth stating in the docs)

8. **The source moves.** The realtime ERDDAP datasets change weekly, and
   PMEL promotes provisional weeks into the settled record. Re-running
   `nodd.py` later yields different tail blocks, and possibly different
   filenames. `realtime_start` is hard-coded in `build_icechunk.py`. Nothing
   records *when* the sources were pulled, beyond each file's `history`
   timestamp.
9. **Loose dependency pins.** Both requirements files set lower bounds only,
   and there is no lock file. The icechunk and virtualizarr APIs have moved
   recently, as `requirements-icechunk.txt` itself says. A pinned
   `requirements-icechunk.lock` (or the exact versions the published store was
   built with) would make the store build repeatable.
10. **No commit hash in output provenance.** The file `history` and store
    `history` attributes name the script and repo but not the commit.
11. **Credentials are needed even to list files.** `build_icechunk.py --list`
    lists the bucket with `GCS_TOKEN`, which defaults to the hub credentials
    path. The bucket is public, so `--list` and `--local-repo` could default to
    anonymous access.

## GOBAI-O2 v2.3 monthly → Source Cooperative notebook

This product can't be reproduced by a newcomer. It reads
`~/shared-public/GOBAI-O2-v2.3.nc`, a hub-local file, and nothing says where it
came from (presumably NCEI accession 0259304). It needs the `source-coop` CLI at
a hard-coded `~/.cargo` path, with no install instructions. Its last cell opens
an unrelated globcolour store, which looks like it was left over from another
notebook. No README section covers how to run it.
