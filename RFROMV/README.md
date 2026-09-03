# RFROMV — RFROM → NODD pipeline

Prepares RFROM gridded Argo temperature/salinity fields (v2.3, v2.2, v2.1) for
the NOAA Open Data Dissemination (NODD) GCP bucket `gs://noaa-oar-rfrom`.
Source files come from PMEL ERDDAP; outputs are CF-compliant, rechunked,
compressed netCDFs laid out per stream under `netcdf/<version>/<stream>/`,
ready to be virtualized into a downstream Icechunk / VirtualiZarr store.

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

## The six product streams (RFROM v2.3)

These are the current **v2.3** products, published under `netcdf/v2.3/` in the
bucket. New versions reprocess all data into a new `netcdf/<version>/` tree; the
script's `--version` flag (default `v2.3`) sets the prefix. Each ERDDAP
dataset_id below links to its griddap data-access page.

| stream | version | ERDDAP dataset | data variable | units |
|---|---|---|---|---|
| `temp_stable`   | v2.3 | [`argo_rfromv23_temp`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_temp.html)                   | `ocean_temperature`       | degree_Celsius |
| `temp_realtime` | v2.3 | [`argo_rfromv23_temp_realtime`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_temp_realtime.html) | `ocean_temperature`       | degree_Celsius |
| `temp_error`    | v2.3 | [`argo_rfromv23_temp_error`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_temp_error.html)       | `ocean_temperature_error` | degree_Celsius |
| `sal_stable`    | v2.3 | [`argo_rfromv23_sal`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_sal.html)                     | `ocean_salinity`          | g/kg † |
| `sal_realtime`  | v2.3 | [`argo_rfromv23_sal_realtime`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_sal_realtime.html)   | `ocean_salinity`          | g/kg † |
| `sal_error`     | v2.3 | [`argo_rfromv23_sal_error`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_sal_error.html)         | `ocean_salinity_error`    | grams_per_kilogram |

Stable runs 1993→2024 (1670 steps); error and realtime extend to 2025. Realtime
is a moving draft that is eventually reprocessed into stable on a new version.

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
- **`index.html`** — landing page for the published product.
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
