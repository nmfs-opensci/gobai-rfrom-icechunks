# gobai-rfrom-icechunks

Scripts that turn two NOAA/PMEL ocean products into cloud-native datasets on the
NOAA Open Data Dissemination (NODD) program's Google Cloud buckets:

| product | source | published to |
|---|---|---|
| **RFROM v2.3** (plus v2.2, v2.1) — gridded Argo temperature and salinity | PMEL ERDDAP | `gs://noaa-oar-rfrom` — [landing page](https://storage.googleapis.com/noaa-oar-rfrom/index.html) |
| **GOBAI HR** (`v202606`) — gridded oxygen and nitrate | PMEL ERDDAP | `gs://noaa-oar-gobai` — [landing page](https://storage.googleapis.com/noaa-oar-gobai/index.html) |

For each product there are two outputs:

1. **netCDFs** under `netcdf/<version>/<stream>/`. `nodd.py` builds them from the
   ERDDAP monthly files: 100-week blocks, CF metadata fixed, rechunked to
   `(100, 1, 180, 180)` and compressed.
2. **A virtual Icechunk store** under `icechunk/<version>`. `build_icechunk.py`
   builds it. The store holds only Zarr metadata and byte-range references into
   the netCDFs above, so nothing is copied and the store is a few MB.

The two products share one grid, so they share both scripts. Each product's
README has its stream table, metadata decisions and read examples:
[`RFROMV/README.md`](RFROMV/README.md) and [`GOBAI-O2/README.md`](GOBAI-O2/README.md).

## What is where

| path | what it is |
|---|---|
| `nodd.py` | ERDDAP → NODD netCDF batch script, every stream of both products |
| `build_icechunk.py` | builds and validates the virtual Icechunk stores |
| `publish_viewer.py` | builds the [gridlook](https://github.com/eeholmes/gridlook) browser viewer and uploads it to `gs://<bucket>/viewer/` |
| `requirements.lock` | pinned install (exact versions, Python 3.12) for both scripts |
| `requirements.txt`, `requirements-icechunk.txt` | minimum versions, for other Python versions; `constraints.txt` sets the lock's pins |
| `setup.md` | full environment setup (also `python nodd.py --setup`) |
| `setup_bare_VM.txt` | the same setup as copy-paste commands for a fresh VM, through to the production runs |
| `RFROMV/index.html`, `GOBAI-O2/index.html` | the public landing pages, uploaded to each bucket root |
| `RFROMV/prep-one-netcdf-for-NODD.ipynb` | step-by-step walkthrough of one block — explains *why*, not needed to rebuild |
| `RFROMV/icechunk-smoke-test.ipynb` | builds a small local store and checks it; run before a real store build |
| `GOBAI-O2/gobai-o2-monthly-icechunk-sc.ipynb` | a **different** product (GOBAI-O2 v2.3 monthly → Source Cooperative); not part of the pipeline below |
| `claude/notes/` | design records: why each choice was made, with measurements |

## Rebuilding from scratch

All of this runs anywhere with Python 3.12 (recommended; 3.11 works), ~35 GB
of scratch disk and a fast network. Only uploads need credentials: reading
ERDDAP and the public buckets is anonymous.

### 1. Environment

```sh
git clone https://github.com/nmfs-opensci/gobai-rfrom-icechunks.git
cd gobai-rfrom-icechunks
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip
pip install -r requirements.lock      # pinned versions; needs Python 3.12

export NODD_SCRATCH_DIR="$HOME/nodd-scratch"      # needs ~35 GB free
gcloud auth application-default login             # an account with write access to the bucket
export NODD_GCS_TOKEN="$HOME/.config/gcloud/application_default_credentials.json"
```

[`setup.md`](setup.md) covers each step in detail: installing Python and
gcloud on a bare VM, service-account alternatives, checking write access, and
keeping long runs alive with `tmux`.

### 2. The netCDFs

Check the plan first. `--list` downloads nothing and needs no credentials:

```sh
python nodd.py --stream temp --list
```

Then build and upload every block of every stream. Each stream takes hours and
downloads ~410 GB. Streams are independent, so run one per VM in parallel if you
can:

```sh
# RFROM v2.3 -> gs://noaa-oar-rfrom/netcdf/v2.3/
python nodd.py --stream temp --all
python nodd.py --stream sal --all
python nodd.py --stream temp_error --all
python nodd.py --stream sal_error --all

# RFROM v2.2 and v2.1 (older versions; no Icechunk store) -> netcdf/v2.2/, netcdf/v2.1/
python nodd.py --stream temp_v22 --all
python nodd.py --stream sal_v22 --all
python nodd.py --stream temp_v21 --all

# GOBAI HR -> gs://noaa-oar-gobai/netcdf/v202606/
python nodd.py --stream o2 --all
python nodd.py --stream no3 --all
```

Runs can be resumed. A block already in the bucket is skipped, so after an
interruption you just re-run the same command. `--force` rebuilds a block that
already exists. **Don't** use it on a published file unless you plan to rebuild
the store afterwards (step 3), because the store points at byte offsets inside
these files.

### 3. The Icechunk stores

Build a store only after all of its streams from step 2 are complete.

```sh
# Once, and after any change to build_icechunk.py:
#   run RFROMV/icechunk-smoke-test.ipynb (local, writes nothing to the bucket)

python build_icechunk.py --store rfrom_v23 --list                     # the files it will reference
python build_icechunk.py --store rfrom_v23 --local-repo /tmp/rfrom    # full dry run, no upload
python build_icechunk.py --store rfrom_v23                            # build, commit, validate

python build_icechunk.py --store gobai_hr --local-repo /tmp/gobai
python build_icechunk.py --store gobai_hr
```

The build validates the store against the source netCDFs before it returns.
`--validate` re-checks an existing store.

⚠️ **For about an hour after a build, anonymous reads can return the previous
snapshot**, because the bucket's CDN caches the store's metadata. Check the new
store with credentials, and don't delete anything because of a stale anonymous
read. Details are in §3–4 of
[`claude/notes/gobai-icechunk.md`](claude/notes/gobai-icechunk.md).

### 4. Landing pages and viewer

```sh
python -c "
import gcsfs, os
fs = gcsfs.GCSFileSystem(token=os.environ['NODD_GCS_TOKEN'])
fs.put('RFROMV/index.html', 'noaa-oar-rfrom/index.html')
fs.put('GOBAI-O2/index.html', 'noaa-oar-gobai/index.html')
"

# gridlook viewer (needs Node >= 24.16 and a gridlook checkout; see the script's docstring)
python publish_viewer.py --product gobai --build ~/gridlook
python publish_viewer.py --product rfrom --dist /tmp/gridlook-dist
```

The landing pages are cached for an hour. Check an upload with
`curl -s "https://storage.googleapis.com/noaa-oar-rfrom/index.html?cb=$RANDOM"`.

### What will differ on a rebuild

- **The source data changes.** PMEL's realtime datasets gain weeks, and
  provisional weeks are later moved into the settled record. A rebuild picks up
  whatever ERDDAP serves that day. The RFROM store's settled/realtime boundary
  is set by hand in `STORES["rfrom_v23"]["realtime_start"]` in
  `build_icechunk.py`. See "Updating the record" in
  [`RFROMV/README.md`](RFROMV/README.md#updating-the-record).
- **Dependencies are pinned for Python 3.12 only.** `requirements.lock` holds
  the versions the published RFROM v2.3 store was built with. Installing the
  minimums from `requirements*.txt` instead picks up newer releases. See
  "Updating the pinned versions" in [`setup.md`](setup.md).

## Reuse and citation

This work is released under the [Apache License 2.0](LICENSE). You are free to
use, copy, modify, and redistribute it, including commercially. If you use it in
published work, in a presentation, or in another repository, please give
attribution:

> Holmes, E.E. (2026). *gobai-rfrom-icechunks*. nmfs-opensci/gobai-rfrom-icechunks. https://github.com/nmfs-opensci/gobai-rfrom-icechunks

The datasets themselves have their own citations, given on each landing page
and in each file's `references` attribute.
