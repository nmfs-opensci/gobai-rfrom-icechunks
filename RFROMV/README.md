# RFROMV — RFROM v2.3 → NODD pipeline

Prepares RFROM v2.3 gridded Argo temperature/salinity fields for the NOAA Open
Data Dissemination (NODD) GCP bucket `gs://noaa-oar-rfrom`. Source files come
from PMEL ERDDAP; outputs are CF-compliant, rechunked, compressed netCDFs laid
out per stream under `netcdf/<version>/<stream>/`, ready to be virtualized into a
downstream Icechunk / VirtualiZarr store.

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

## Files in this directory

### Deliverables

- **`rfrom_nodd.py`** — the batch script (GitHub issue #5). Processes any of the
  six streams into NODD netCDFs and uploads them. This is what you run in
  production. See "Running the batch script" below.
- **`prep-one-netcdf-for-NODD.ipynb`** — the tested single-file reference pipeline
  (GitHub issue #1, merged via PR #4). Run interactively cell-by-cell; it prepares
  and uploads **one** block so the workflow can be validated before scaling up.
  `rfrom_nodd.py` is this notebook generalized to all six streams — the notebook
  remains the readable, step-annotated explanation of *why* each stage is the way
  it is.
- **`index.html`** — landing page for the published product.

### Sandbox (exploratory scratch — not part of the pipeline)

- **`prep-for-NODD-rfromv23.ipynb`** — earlier exploratory notebook.
- **`upload_to_nodd.ipynb`** — a short scratch uploader.

Both are kept for reference only; don't treat them as the source of truth.

## Environment

These run on the JupyterHub where the data volumes and cloud credentials are
already mounted. Dependencies (`xarray`, `dask`, `netCDF4`/`h5netcdf`, `zarr`,
`gcsfs`, `requests`, `pandas`, `numpy`) are installed into the kernel by the
`%pip install -qU ...` cell at the top of the notebook. NODD upload uses `gcsfs`
with application-default credentials at
`~/.config/gcloud/application_default_credentials.json`.

Scratch space (download + local output) lives under
`/home/jovyan/shared-public/rfromv-scratch`.

## Running the batch script

`rfrom_nodd.py` requires an explicit `--stream` **and** an explicit
`--blocks RANGE` or `--all` — nothing is processed implicitly. Run one stream at a
time (one VM per stream, or split a stream across VMs with disjoint `--blocks`
ranges). It is idempotent: before writing a block it checks whether the target
object already exists in the bucket and skips it unless `--force`, so a
resume-after-interrupt or a second VM on the same stream is safe.

```sh
# Plan only: print the block → monthly-file cross-walk, download nothing.
python rfrom_nodd.py --stream temp_stable --list

# Process a single block and upload it.
python rfrom_nodd.py --stream temp_stable --blocks 0

# Process EVERY block in the stream and upload them (a typical production run,
# one stream per VM). Idempotent: already-uploaded blocks are skipped.
python rfrom_nodd.py --stream temp_stable --all

# Split a stream across two VMs (disjoint block ranges).
python rfrom_nodd.py --stream sal_stable --blocks 0-8      # VM A
python rfrom_nodd.py --stream sal_stable --blocks 9-16     # VM B

# Whole stream, local test — build but don't upload, keep scratch files.
python rfrom_nodd.py --stream temp_realtime --all --no-upload --keep-scratch
```

### Flags

| flag | meaning |
|---|---|
| `--stream <name>` | **required** — one of the six stream keys above |
| `--blocks RANGE` | blocks to process: `3`, `0-4`, or `0,2,5` (mutually exclusive with `--all`) |
| `--all` | process every block in the stream |
| `--version` | product version prefix (default `v2.3`) |
| `--list` | print the block → monthly-file plan and exit (no download) |
| `--no-upload` | build files locally but do not upload |
| `--force` | reprocess/overwrite even if the target object already exists |
| `--keep-scratch` | do not delete downloaded monthly files / local outputs |

A typical production run for one stream is `--all` on its own VM; start with
`--list` to preview the plan and `--blocks 0` to smoke-test one block end to end.

### Where things live in the code

The `STREAMS` dict near the top of `rfrom_nodd.py` is the single place per-stream
differences live (`dataset_id`, `data_var`, `var_attrs`, filename templates). The
core functions mirror the notebook stages. Two correctness/performance choices are
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
