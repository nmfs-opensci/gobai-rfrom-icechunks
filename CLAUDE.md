# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Data-engineering notebooks that turn ocean-data source files into cloud-native
published products: NODD-bound netCDFs and materialized Icechunk / VirtualiZarr
Zarr stores. There is **no build, lint, or test system** — the deliverables are
Jupyter notebooks (`.ipynb`) and the HTML landing pages they generate. Notebooks
are run interactively cell-by-cell on a JupyterHub where the data volumes
(`/home/jovyan/shared-public/`) and cloud credentials are already mounted.

Two product areas:

- **`RFROMV/`** — RFROM v2.3 gridded Argo temperature/salinity. Pipeline
  notebooks prepare netCDFs for the NOAA Open Data Dissemination (NODD) GCP
  bucket `noaa-oar-rfrom`.
- **`GOBAI-O2/`** — GOBAI-O2 v2.3 gridded oxygen. `gobai-o2-monthly-icechunk-sc.ipynb`
  builds a materialized Zarr v3 Icechunk store published to Source Cooperative
  (`data.source.coop/fish-pace/gobai-o2/monthly`).

## Common operations

There are no test/build commands. Typical work:

- **Run a notebook**: open in JupyterLab on the hub and execute cells top to
  bottom. `%pip install -qU ...` cells at the top install dependencies into the
  running kernel (icechunk, xarray, dask, netCDF4, zarr, virtualizarr, gcsfs,
  h5netcdf).
- **Inspect notebook structure without a kernel**: use `nbformat` in a plain
  `python` process to read cell sources.
- **NODD upload** uses `gcsfs` with application-default credentials at
  `/home/jovyan/.config/gcloud/application_default_credentials.json`.
- **Source Cooperative upload** uses the `source-coop` CLI at
  `/home/jovyan/.cargo/bin/source-coop`.

## RFROMV NODD pipeline architecture

`RFROMV/prep-one-netcdf-for-NODD.ipynb` is the reference single-file pipeline
(GitHub issue #1), the tested foundation for a future batch script. Stages:

1. **Source**: monthly netCDFs pulled from PMEL ERDDAP griddap
   (`argo_rfromv23_temp` and siblings).
2. **Combine + block**: `open_mfdataset` over the monthly files, then select a
   fixed 100-timestep block. The 1670-step record splits into 17 blocks; output
   files are named e.g. `RFROMV23_TEMP_STABLE_1993-01-01_1994-11-25.nc`.
3. **CF metadata**: set `standard_name`/`axis`/`positive`, `Conventions`, repair
   UTF-8 surrogate attrs; drop discouraged `_FillValue` on coordinate vars.
4. **Rechunk + compress**: on-disk chunks `(100,1,180,180)` ≈ 13 MB, dtype
   float32, zlib level 4 + shuffle.
5. **Upload**: to a versioned per-stream prefix `netcdf/v2.3/<stream>/` in the
   bucket. Realtime is a draft that gets reprocessed into stable over time; the
   downstream Icechunk store combines streams with a stable/realtime flag.

The six streams map to six ERDDAP datasets (all `(time, mean_pressure, latitude,
longitude)` float32):

| stream | ERDDAP dataset_id | data variable | units |
|---|---|---|---|
| `temp_stable`   | `argo_rfromv23_temp`          | `ocean_temperature`       | degree_Celsius |
| `temp_realtime` | `argo_rfromv23_temp_realtime` | `ocean_temperature`       | degree_Celsius |
| `temp_error`    | `argo_rfromv23_temp_error`    | `ocean_temperature_error` | degree_Celsius |
| `sal_stable`    | `argo_rfromv23_sal`           | `ocean_salinity`          | PSU |
| `sal_realtime`  | `argo_rfromv23_sal_realtime`  | `ocean_salinity`          | PSU |
| `sal_error`     | `argo_rfromv23_sal_error`     | `ocean_salinity_error`    | grams_per_kilogram |

Stable runs 1993→2024 (1670 steps); error and realtime extend to 2025. Realtime
monthly files keep the `STABLE` prefix but add a `_REALTIME` suffix; error files
use an `_ERROR_` infix. `ocean_salinity` is practical salinity in PSU
(`standard_name=sea_water_practical_salinity`) — do not mistake it for TEOS-10
absolute salinity, though `ocean_salinity_error` is confusingly reported in g/kg
(its CF `standard_name` uses the `sea_water_absolute_salinity standard_error`
modifier form, matching its g/kg units rather than the practical-salinity base of
the main variable; `ocean_temperature_error` uses
`sea_water_conservative_temperature standard_error`).

`RFROMV/rfrom_nodd.py` is the batch script form of the notebook (issue #5). Its
`STREAMS` dict is the single place stream differences live. It requires an
explicit `--stream` plus an explicit `--blocks RANGE` or `--all` — nothing is
processed implicitly. Run one stream at a time (one VM per stream, or split a
stream across VMs with disjoint `--blocks` ranges); it is idempotent (skips blocks
already in the bucket unless `--force`). `--list` prints the block→monthly-file
plan without downloading. See `claude/notes/nodd-batch-script.md` for the resolved
design decisions.

### Two constraints that are easy to break

- **Do not alter the original data.** Open with
  `data_vars="minimal", coords="minimal", compat="override"` so `mean_pressure_bnds`
  keeps its `(mean_pressure, nv)` shape — the default `data_vars="all"` broadcasts
  it against `time` and silently changes the file. Preserve the vertices/bounds
  dims and the data values exactly.
- **Read contiguous pressure planes, not spatial tiles.** The source files are
  laid out `(time, pressure, lat, lon)` and are contiguous, so a small
  `(180×180)` spatial dask chunk forces strided, seeky reads and the write goes
  I/O-bound (this looked like OOM/slowness but a bigger VM does not help). Use
  dask `chunks={"mean_pressure": 1}` on read (whole lat/lon planes, sequential),
  and decouple that from the on-disk `chunksizes` set in `encoding`.

### Downstream: VirtualiZarr / Icechunk

The netCDFs are produced to be virtualized into an Icechunk store later, so the
chunk grid is uniform across files for concatenation. Notes for the reader side
live in the notebook's Step 5 markdown (single-chunk `time`, `loadable_variables`,
`zarr.config async.concurrency`, and passing an explicit `chunks=` rather than
`chunks={}` when opening).

## Chunking reference numbers

From `README.md`, prior chunking choices on related products (useful when picking
new ones):

- RFROM native on-disk: `time:5, mean_pressure:58, latitude:720, longitude:1440`.
- RFROM NODD physical target: `(100, 1, 180, 180)` ≈ 13 MB.
- CEFI uses `100, 10, 200, 200`.
- GOBAI-O2 on NCEI: `pres:58, lat:145, lon:360`.
- GOBAI-O2 Icechunk notebook: `time:14, pres:2, lat:73, lon:120`.
