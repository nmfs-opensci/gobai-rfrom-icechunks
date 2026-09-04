# RFROMV — RFROM → NODD pipeline

Prepares RFROM gridded Argo temperature/salinity fields (v2.3, v2.2, v2.1) for
the NOAA Open Data Dissemination (NODD) GCP bucket `gs://noaa-oar-rfrom`.
Source files come from PMEL ERDDAP; outputs are CF-compliant, rechunked,
compressed netCDFs laid out per stream under `netcdf/<version>/<stream>/`, which
are then virtualized into an Icechunk store under `icechunk/<version>` by
[`../build_icechunk.py`](../build_icechunk.py) — see "The Icechunk store" below.

Per output file the pipeline is:

```
ERDDAP monthly netCDFs
  → open_mfdataset combine
  → select one 100-time-step block
  → fix CF metadata
  → rechunk (100, 1, 180, 180) + zlib-4/shuffle
  → upload to gs://noaa-oar-rfrom/netcdf/<version>/<stream>/
```

The 1670-step stable record splits into 17 blocks; output files are named e.g.
`RFROMV23_TEMP_STABLE_1993-01-01_1994-11-25.nc`.

## The product streams (RFROM v2.3)

These are the current **v2.3** products, published under `netcdf/v2.3/` in the
bucket. `temp` and `sal` each publish **one continuous series** spanning both of
PMEL's ERDDAP datasets — the settled record plus the realtime extension that
continues it. They replace the four `*_stable` / `*_realtime` streams, which
published the same weeks as two separate series; that split made a virtual
Icechunk store impossible (issue #17, and "Restructuring the v2.3 tree" below).
Which weeks are provisional is recorded in the Icechunk store's `data_mode` flag
rather than in the file layout. New versions reprocess all data into a new `netcdf/<version>/` tree; the
script's `--version` flag (default `v2.3`) sets the prefix. Each ERDDAP
dataset_id below links to its griddap data-access page.

| stream | ERDDAP dataset(s) | data variable | units | steps |
|---|---|---|---|---|
| `temp`       | [`argo_rfromv23_temp`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_temp.html) + [`..._temp_realtime`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_temp_realtime.html) | `ocean_temperature`       | degree_Celsius | 1719 |
| `sal`        | [`argo_rfromv23_sal`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_sal.html) + [`..._sal_realtime`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_sal_realtime.html) | `ocean_salinity`          | g/kg † | 1719 |
| `temp_error` | [`argo_rfromv23_temp_error`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_temp_error.html) | `ocean_temperature_error` | degree_Celsius | 1719 |
| `sal_error`  | [`argo_rfromv23_sal_error`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_sal_error.html)   | `ocean_salinity_error`    | grams_per_kilogram | 1719 |

All four run 1993-01-01 → 2025-12-05 weekly, 18 blocks (17 × 100 + 19). The 2025
weeks are provisional and will be reprocessed into the settled record; that is
what `data_mode` marks in the Icechunk store.

The superseded streams `temp_stable`, `temp_realtime`, `sal_stable` and
`sal_realtime` are still defined in `nodd.py` — they built the tree that is
published today, and the entries are what the migration copies from.

† `ocean_salinity` is **absolute salinity (TEOS-10) in g/kg**, confirmed by the
data author. ERDDAP labels the variable `sea_water_practical_salinity` / `PSU`,
but that is a known upstream mistake the author cannot fix, so the pipeline
overrides `sal_stable` / `sal_realtime` to `sea_water_absolute_salinity` /
`grams_per_kilogram` — **metadata only, values unchanged**.

## v2.2 and v2.1 (GitHub issue #20)

Older RFROM versions, published under `netcdf/v2.2/` and `netcdf/v2.1/`. Each
is a **single continuous series per variable** — unlike v2.3, there is no
realtime/error split, and the script's per-stream `version` overrides the
`--version` default so you don't have to pass `--version` yourself:

| stream | version | ERDDAP dataset | data variable | ends |
|---|---|---|---|---|
| `temp_v22` | v2.2 | [`argo_rfromv22_temp`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv22_temp.html) | `ocean_temperature` | 2024-12 |
| `sal_v22`  | v2.2 | [`argo_rfromv22_sal`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv22_sal.html)   | `ocean_salinity` (same TEOS-10 fix as v2.3 †) | 2025-12 |
| `temp_v21` | v2.1 | [`argo_rfromv21_temp`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv21_temp.html) | `ocean_temperature` | 2023-12 |

v2.1 has no salinity dataset at all. `temp_v22` and `sal_v22` don't end on the
same date — confirmed on ERDDAP, not a bug.

**Not included**: `argo_rfromv22` / `argo_rfromv22_realtime` / `argo_rfromv22_error`.
Despite the naming, these are a *different* product — Ocean Heat Content
anomaly, on a different, coarser vertical grid (`mean_depth`, 10 levels, vs.
temp/sal's `mean_pressure`, 58 levels) — not variants of temperature/salinity.
Tracked separately as [issue #21](https://github.com/nmfs-opensci/gobai-rfrom-icechunks/issues/21).

## Restructuring the v2.3 tree (issue #17)

The published tree has to move from six streams to four before the Icechunk store
can be built. **Blocks 0–15 do not need rebuilding** — they are the same weeks
from the same monthly sources, so they are copied server-side inside the bucket
rather than re-downloaded (~200 GB of ERDDAP traffic saved). Only the seam and the
tails are new work: about 16 GB downloaded, 19 GB written.

```sh
# 1. Copy blocks 0-15 of temp and sal to the new prefixes. Server-side rewrite:
#    no bytes cross the network, and each object is CRC-verified as it lands.
#    Idempotent -- an object already present with a matching CRC is skipped.
#    (--plan first if you want to see what it will do.)
python RFROMV/migrate_v23.py --copy

# 2. Build the two new blocks per variable: block 16 spans the stable/realtime
#    seam, block 17 is the padded 19-step tail.
python nodd.py --stream temp --blocks 16,17
python nodd.py --stream sal  --blocks 16,17

# 3. Rewrite the error tails so their time chunk is 100 rather than 19.
python nodd.py --stream temp_error --blocks 17 --force
python nodd.py --stream sal_error  --blocks 17 --force

# 4. Verify before deleting anything: every block the plan expects is present,
#    and every object carried over is byte-identical to its source (CRC32C,
#    metadata only -- nothing is downloaded). Exits non-zero if not.
python RFROMV/migrate_v23.py --check
```

Step 1 uses `gcsfs`, not `gcloud storage`: gcsfs authenticates with the
application-default credentials this repo already uses, while the gcloud CLI
wants its own `gcloud auth login` — on the hub `gcloud auth list` reports no
credentialed accounts. gcsfs issues a GCS rewrite and loops on the rewrite token
until the server says done, so multi-GB objects copy correctly without being
downloaded. Measured: **32 objects, 219 GB, ~15 seconds.**

### File names

The four directories are `temp/`, `sal/`, `temp_error/` and `sal_error/`. Inside
`temp/` and `sal/` the file name says what the file actually holds:

| block | contents | name |
|---|---|---|
| 0–15 | all settled | `RFROMV23_TEMP_STABLE_1993-01-01_1994-11-25.nc` … |
| 16 | 70 settled + 30 provisional | `RFROMV23_TEMP_STABLE_REALTIME_2023-09-01_2025-07-25.nc` |
| 17 | all provisional | `RFROMV23_TEMP_REALTIME_2025-08-01_2025-12-05.nc` |

The seam block carries **both** labels, in the order it meets them. Pure-stable
blocks keep the name they already have, which is why step 1 above is a plain copy.
The old `_REALTIME` *suffix* (on files whose name also said `STABLE`) is gone —
the label is now an infix and means what it says.

Two consequences worth knowing:

- **Lexical order is no longer time order** — `REALTIME` sorts before `STABLE`.
  `build_icechunk.py` sorts on the dates in the name (`block_start`), and the
  store's own concatenation orders by the files' real time values, so neither is
  fooled; a hand-written `ls | sort` would be.
- **Names churn when provisional weeks are settled.** When PMEL promotes 2025
  into the settled record, block 16 becomes all-stable and is rebuilt as
  `..._STABLE_2023-09-01_2025-07-25.nc`; the `STABLE_REALTIME` object is then
  stale and should be deleted. That is the cost of putting the mode in the name;
  the machine-readable version of the same fact is `data_mode` in the Icechunk
  store, which never churns.

### Retiring the old prefixes

GCS has no rename, so the four old directories (`temp_stable`, `temp_realtime`,
`sal_stable`, `sal_realtime` — 225.5 GB) survive the copy and have to be deleted
deliberately. Two things make that safe here:

- **The public landing page does not link to them.** Its stream links point at the
  ERDDAP source datasets (`data.pmel.noaa.gov/.../argo_rfromv23_temp_realtime/`),
  which are unaffected; the page never references a `netcdf/` path.
- **They were only published on 2026-09-02/03**, so nothing has had time to link
  to them. That argument weakens the longer they stay up — this is the cheapest
  moment to retire them.

Nothing is lost. Three objects are *not* copied — the 70-step stable block 16 and
the two realtime files — because the rebuilt blocks 16 and 17 contain exactly
those weeks, re-blocked.

```sh
python RFROMV/migrate_v23.py --check   # must exit 0
python build_icechunk.py --store rfrom_v23 --local-repo /tmp/rehearsal   # must validate

for d in temp_stable temp_realtime sal_stable sal_realtime; do
  gcloud storage rm --recursive "gs://noaa-oar-rfrom/netcdf/v2.3/$d"
done
```

Do not run the delete until both checks above pass. If something is wrong
afterwards, everything is rebuildable from ERDDAP — but that is a multi-hour
re-run, which is what the checks exist to avoid.

**Weekly realtime updates** rewrite only the tail block (`--blocks 17 --force`,
~1.5 GB) until it reaches 100 steps, at which point it becomes a full block and a
new tail starts. When PMEL promotes the 2025 weeks into the settled record,
rebuild blocks 16–17 and move `realtime_start` in `build_icechunk.py`.

## The Icechunk store

[`../build_icechunk.py`](../build_icechunk.py) merges all four streams into one
virtual Icechunk store at `gs://noaa-oar-rfrom/icechunk/v2.3` — one dataset, one
1719-step time axis, four science variables plus a `data_mode(time)` flag. It is
**100 % virtual**: the netCDFs stay where they are, nothing is copied, and the
store itself is a few MB.

```sh
python build_icechunk.py --store rfrom_v23 --list                 # what gets referenced
python build_icechunk.py --store rfrom_v23 --local-repo /tmp/x    # full dry run, no upload
python build_icechunk.py --store rfrom_v23                        # build and validate
python build_icechunk.py --store rfrom_v23 --validate             # re-check a built store
```

Run [`icechunk-smoke-test.ipynb`](icechunk-smoke-test.ipynb) first — it builds a
small store into a local throwaway repository and checks the values against the
source netCDFs. The design decisions, the measurements behind them, and the
reader recipe are in
[`../claude/notes/rfromv-icechunk.md`](../claude/notes/rfromv-icechunk.md).

Two constraints the netCDFs must satisfy, both enforced with named errors:
every file feeding one variable shares one chunk grid, and only the **last** file
may be short (written with an unlimited time dimension so HDF5 pads its edge
chunk). Zarr has no variable-length chunks, so a store cannot paper over either.

### Reading the store

```python
import icechunk as ic, xarray as xr, zarr

zarr.config.set({"async.concurrency": 128})   # the default of 10 is why a store "feels slow"

storage = ic.gcs_storage(bucket="noaa-oar-rfrom", prefix="icechunk/v2.3", anonymous=True)
prefix = "gs://noaa-oar-rfrom/netcdf/v2.3/"
repo = ic.Repository.open(
    storage,
    authorize_virtual_chunk_access=ic.containers_credentials(
        {prefix: ic.gcs_credentials(anonymous=True)}
    ),
)
ds = xr.open_zarr(repo.readonly_session("main").store, consolidated=False, chunks={})
```

The store and the netCDFs it references are **two independent credential
settings**, even though both live in the same public bucket. A reader that
configures only the repository gets metadata and no data — the
`authorize_virtual_chunk_access` argument above is what makes the byte ranges
readable.

**Compatibility caveat.** The arrays carry `numcodecs.shuffle` +
`numcodecs.zlib`. Those are *extension* codecs in the Zarr v3 registry, not core
spec codecs, because the netCDFs are compressed with HDF5's deflate filter (which
emits zlib framing, RFC 1950) plus shuffle (which has no core v3 equivalent),
and a virtual store must describe the bytes exactly as they are rather than
re-encode them. **The store reads from zarr-python; other Zarr implementations
may refuse it.** zarr-python emits a warning to this effect on every build and
open — it is expected, and there is nothing to fix in the store. Background and
the fallback plan are in §8 of
[`../claude/notes/rfromv-icechunk.md`](../claude/notes/rfromv-icechunk.md).

Reading and building need `icechunk` and `virtualizarr`, which are **not** in
[`../requirements.txt`](../requirements.txt) — that file covers `nodd.py` only.
They live in [`../requirements-icechunk.txt`](../requirements-icechunk.txt):

```sh
pip install -r ../requirements.txt -r ../requirements-icechunk.txt
```

## Files in this directory

### Deliverables

- **`../nodd.py`** — the batch script (GitHub issue #5). Processes any of the six
  streams into NODD netCDFs and uploads them. This is what you run in production.
  It lives at the repository root because GOBAI HR shares RFROM's grid and
  therefore this pipeline (issue #13); see [`../GOBAI-O2/README.md`](../GOBAI-O2/README.md).
  See "Running the batch script" below.
- **`prep-one-netcdf-for-NODD.ipynb`** — the tested single-file reference pipeline
  (GitHub issue #1, merged via PR #4). Run interactively cell-by-cell; it prepares
  and uploads **one** block so the workflow can be validated before scaling up.
  `nodd.py` is this notebook generalized to all six streams — the notebook
  remains the readable, step-annotated explanation of *why* each stage is the way
  it is.
- **`../build_icechunk.py`** — builds the virtual Icechunk store from the
  published netCDFs (GitHub issue #17). Config-driven like `nodd.py`; also
  configured for GOBAI HR. See "The Icechunk store" above.
- **`icechunk-smoke-test.ipynb`** — run this before building the real store, and
  after any change to `build_icechunk.py`. Builds a small store into a local
  temporary repository and validates it against the source netCDFs.
- **`migrate_v23.py`** — one-off tool for the issue #17 restructure. `--plan`
  shows which blocks are copied and which must be built; `--copy` does the
  server-side copy; `--check` confirms the new tree is complete and every copied
  object matches its source by CRC32C, before the old prefixes are deleted. Takes
  its block plan straight from `nodd.py`, so the two cannot drift. Can be deleted
  once the migration is done.
- **`index.html`** — landing page for the published product, and the source of
  `gs://noaa-oar-rfrom/index.html`. Carries the cloud-access instructions:
  `pip install` lines, opening the Icechunk store, opening a single netCDF with
  xarray, and reading the netCDFs from R. Upload with
  `gcsfs.GCSFileSystem(token=...).put("RFROMV/index.html", "noaa-oar-rfrom/index.html")`.
  Every code block on the page was executed verbatim before publishing — keep it
  that way.
- **`../requirements.txt`** — pip dependencies for running `nodd.py` off-hub.
  Only needed off-hub; see "Running off-hub" below.

### Sandbox (exploratory scratch — not part of the pipeline)

- **`setup_bare_VM.txt`** — Eli's personal cheat-sheet of shell commands for
  standing up a bare VM. Informal by design and overlapping
  [`../setup.md`](../setup.md), which is the maintained version; don't treat it
  as the source of truth.

(The earlier exploratory notebooks, `prep-for-NODD-rfromv23.ipynb` and
`upload_to_nodd.ipynb`, are gone — issue #15. Anything worth keeping from them
was merged into `prep-one-netcdf-for-NODD.ipynb`.)

## Environment

The notebooks run on the JupyterHub, where the data volumes and cloud
credentials are already mounted; dependencies are installed into the kernel by
the `%pip install -qU ...` cell at the top of each notebook. NODD upload uses
`gcsfs` with application-default credentials at
`~/.config/gcloud/application_default_credentials.json`, and scratch space
(downloads + local output) defaults to `/home/jovyan/shared-public/rfromv-scratch`.

`nodd.py` also runs anywhere else — a bare VM, a laptop — via two environment
variables that override those two hub paths:

| variable | default | meaning |
|---|---|---|
| `NODD_SCRATCH_DIR` | `/home/jovyan/shared-public/rfromv-scratch` (RFROM streams) | download + output scratch; needs ~35 GB free |
| `NODD_GCS_TOKEN` | `~/.config/gcloud/application_default_credentials.json` (hub path) | credentials JSON path, **or** the keyword `google_default` to resolve ADC the usual way |

The pre-issue-#13 names `RFROM_SCRATCH_DIR` / `RFROM_GCS_TOKEN` are still
honoured. The scratch **default** is product-specific — RFROM streams default to
`rfromv-scratch`, GOBAI streams to `gobai-scratch` — so the two do not collide on
a shared machine; an explicit `NODD_SCRATCH_DIR` overrides both.

## Running off-hub (bare VM or macOS)

Nothing about the pipeline needs the hub — it needs Python, ~35 GB of scratch
disk, and credentials that can write to `gs://noaa-oar-rfrom`. The full
walkthrough (venv install, scratch space, GCS credentials, tmux for long runs,
measured resource expectations) is in [`../setup.md`](../setup.md), or run
`python ../nodd.py --setup` to print it. The dependency manifest it references,
`requirements.txt`, lives at the repo root next to `nodd.py`.

## Running the batch script

`nodd.py` requires an explicit `--stream` **and** an explicit
`--blocks RANGE` or `--all` — nothing is processed implicitly. Run one stream at a
time (one VM per stream, or split a stream across VMs with disjoint `--blocks`
ranges). It is idempotent: before writing a block it checks whether the target
object already exists in the bucket and skips it unless `--force`, so a
resume-after-interrupt or a second VM on the same stream is safe.

Downloads resume too. A monthly file is streamed to a `.part` file, checked that
it opens as netCDF, and only then renamed into place, so anything sitting in
`<scratch>/erddap/` is known-complete and is re-used instead of re-fetched — a
re-run after a crash does not pay for the ~12 GB again. Flaky ERDDAP reads are
retried with exponential backoff (4 attempts, 15/30/60 s), which is what makes a
multi-hour `--all` run survive the transient timeouts the endpoint throws
(issue #11). ERDDAP serves these files gzip-encoded and chunked with no
`Content-Length` and no Range support, so a retry restarts that one file; the
files already finished are untouched.

```sh
# Plan only: print the block → monthly-file cross-walk, download nothing.
python nodd.py --stream temp_stable --list

# Process a single block and upload it.
python nodd.py --stream temp_stable --blocks 0

# Process EVERY block in the stream and upload them (a typical production run,
# one stream per VM). Idempotent: already-uploaded blocks are skipped.
python nodd.py --stream temp_stable --all

# Split a stream across two VMs (disjoint block ranges).
python nodd.py --stream sal_stable --blocks 0-8      # VM A
python nodd.py --stream sal_stable --blocks 9-16     # VM B

# Whole stream, local test — build but don't upload, keep scratch files.
python nodd.py --stream temp_realtime --all --no-upload --keep-scratch
```

### Flags

Run `python ../nodd.py --help` for the full flag reference and more examples.
A typical production run for one stream is `--all` on its own VM; start with
`--list` to preview the plan and `--blocks 0` to smoke-test one block end to end.

### Where things live in the code

The `STREAMS` dict near the top of `nodd.py` is the single place per-stream
differences live (`dataset_id`, `data_var`, `var_attrs`, filename templates, and
which `PRODUCTS` entry — RFROM or GOBAI — supplies the bucket, default version
and scratch default). The core functions mirror the notebook stages. Two correctness/performance choices are
load-bearing and must not regress:

- Open with `data_vars="minimal", coords="minimal", compat="override"` so
  `mean_pressure_bnds` keeps its `(mean_pressure, nv)` shape — the default would
  broadcast it against `time` and silently alter the file.
- Read with dask `chunks={"mean_pressure": 1}` (contiguous lat/lon planes). The
  source files are contiguous `(time, pressure, lat, lon)`; small spatial chunks
  force strided, seeky reads and the write goes I/O-bound (a bigger VM does not
  help). This is decoupled from the on-disk `chunksizes` in `encoding`.

The rationale for every stage is written up in
[`../claude/notes/nodd-prep.md`](../claude/notes/nodd-prep.md) and
[`../claude/notes/nodd-batch-script.md`](../claude/notes/nodd-batch-script.md).

## Not yet built

`update_nodd.py` — a weekly realtime reconcile that re-downloads the moving
realtime dataset and replaces the affected tail block(s). Out of scope for the
first cut.
