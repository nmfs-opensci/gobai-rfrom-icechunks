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
- **`GOBAI-O2/`** — two unrelated GOBAI products, do not conflate them:
  - **GOBAI HR-v1.0 weekly** oxygen + nitrate from PMEL ERDDAP, bound for the NODD
    GCP bucket `noaa-oar-gobai` (issue #13). Shares `nodd.py` with RFROMV; see
    `GOBAI-O2/README.md` and `claude/notes/gobai-nodd.md`.
  - **GOBAI-O2 v2.3 monthly** from NCEI. `gobai-o2-monthly-icechunk-sc.ipynb`
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
| `sal_stable`    | `argo_rfromv23_sal`           | `ocean_salinity`          | g/kg (see note) |
| `sal_realtime`  | `argo_rfromv23_sal_realtime`  | `ocean_salinity`          | g/kg (see note) |
| `sal_error`     | `argo_rfromv23_sal_error`     | `ocean_salinity_error`    | grams_per_kilogram |

Stable runs 1993→2024 (1670 steps); error and realtime extend to 2025. Realtime
monthly files keep the `STABLE` prefix but add a `_REALTIME` suffix; error files
use an `_ERROR_` infix. `ocean_salinity` is **absolute salinity (TEOS-10) in
g/kg**, confirmed by the data author. The ERDDAP variable metadata labels it
`sea_water_practical_salinity` / `PSU`, but that is a known upstream mistake the
author cannot fix, so the pipeline overrides `sal_stable`/`sal_realtime` to
`sea_water_absolute_salinity` / `grams_per_kilogram` (values unchanged — metadata
only), matching `ocean_salinity_error`. The error vars use the CF standard-name
modifier form: `ocean_salinity_error` → `sea_water_absolute_salinity
standard_error`, `ocean_temperature_error` → `sea_water_conservative_temperature
standard_error`.

`nodd.py` (repo root) is the batch script form of the notebook (issue #5); it
covers **both** products, since GOBAI HR shares RFROM's grid (issue #13).
`RFROMV/rfrom_nodd.py` is a back-compat shim that forwards to it with every flag
unchanged. Its `STREAMS` dict is the single place stream differences live, and
`PRODUCTS` holds the per-product bucket / default version / scratch default. It
requires an explicit `--stream` plus an explicit `--blocks RANGE` or `--all` —
nothing is processed implicitly. Run one stream at a time (one VM per stream, or split a
stream across VMs with disjoint `--blocks` ranges); it is idempotent (skips blocks
already in the bucket unless `--force`). `--list` prints the block→monthly-file
plan without downloading. See `claude/notes/nodd-batch-script.md` for the resolved
design decisions.

The script also runs off-hub (bare VM, laptop): `NODD_SCRATCH_DIR` and
`NODD_GCS_TOKEN` override the two hub paths, with the hub values as defaults (the
older `RFROM_`-prefixed names are still honoured; the scratch default is
per-product, `rfromv-scratch` vs `gobai-scratch`). `RFROMV/{requirements.txt,pixi.toml,environment.yml}` carry the same
dependency set for venv+pip / pixi / conda, and `RFROMV/README.md` has the full
"Running off-hub" setup.

**`RFROMV/setup_bare_VM.txt` is Eli's personal cheat-sheet**, not pipeline code
and not generated docs — the raw shell commands he pastes to stand up a bare VM,
since he does not always work on a JupyterHub where everything is preinstalled.
It is deliberately informal and overlaps the README on purpose. Do not tidy,
restructure, or "sync" it, and do not treat a difference from the README as a bug
to fix; leave it alone unless Eli asks.

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

## GOBAI HR → NODD pipeline

Issue #13. Same machinery as RFROMV, run through the same `nodd.py`, because the
grid is **identical**: GOBAI HR is built on RFROM, and `latitude` (720),
`longitude` (1440), `mean_pressure` (58) and `mean_pressure_bnds`
`(mean_pressure, vertices)` match RFROM v2.3 value-for-value on the same weekly
time grid. RFROM's 1670-step stable axis is an exact prefix of GOBAI's 1719, so a
combined downstream Icechunk store is geometrically clean — with the caveat that
GOBAI declares `source = "... RFROM v2.2"` while the RFROM NODD product is v2.3.

Two streams, no stable/realtime/error split:

| stream | ERDDAP dataset_id | data variable | destination |
|---|---|---|---|
| `o2`  | `gobai_o2_hr_v10`  | `o2`  | `gs://noaa-oar-gobai/netcdf/v202606/o2/` |
| `no3` | `gobai_no3_hr_v10` | `no3` | `gs://noaa-oar-gobai/netcdf/v202606/no3/` |

1993-01-01 → 2025-12-05 weekly, 1719 steps → **18 blocks** (17 × 100 + 19), 396
monthly source files and ~0.41 TB per stream. Output files are named e.g.
`GOBAI-O2-HR-v202606_1993-01-01_1994-11-25.nc`.

The version prefix is **`v202606`**, the string stamped in the source filenames
and in each file's `title` attribute; ERDDAP's dataset title says `HR-v1.0`
instead and the files win.

CF pass (metadata only, values unchanged; verified against CF standard name table
v94). Neither file carries a `standard_name` for its data variable, and units are
the non-udunits string `"micromole per kilogram"`:

- `o2` → `moles_of_oxygen_per_unit_mass_in_sea_water`, `umol kg-1`
- `no3` → `moles_of_nitrate_per_unit_mass_in_sea_water`, `umol kg-1`
- `mean_pressure` → `sea_water_pressure`, `positive="down"`, `axis="Z"`
- the source's non-standard `Description` attribute is copied into `long_name`
  where none exists (GOBAI streams only — RFROM blocks are already published
  without it, so turning it on there would make that tree inconsistent).

Both CF names are the **per-mass** forms. ERDDAP's own config for
`gobai_no3_hr_v10` advertises `mole_concentration_of_nitrate_in_sea_water`, a
per-**volume** name (`mol m-3`) inconsistent with the per-mass units and absent
from the files — the same class of upstream error as the RFROM salinity mislabel,
pending author confirmation. `comment = "preliminary"` and the "in prep."
`references` are passed through verbatim.

## Chunking reference numbers

From `README.md`, prior chunking choices on related products (useful when picking
new ones):

- RFROM native on-disk: `time:5, mean_pressure:58, latitude:720, longitude:1440`.
- RFROM NODD physical target: `(100, 1, 180, 180)` ≈ 13 MB.
- CEFI uses `100, 10, 200, 200`.
- GOBAI-O2 on NCEI: `pres:58, lat:145, lon:360`.
- GOBAI-O2 Icechunk notebook: `time:14, pres:2, lat:73, lon:120`.
