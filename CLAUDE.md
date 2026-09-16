# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Python scripts that turn PMEL ERDDAP ocean products into cloud-native datasets on
NOAA Open Data Dissemination (NODD) Google Cloud buckets: rechunked, CF-fixed
**netCDFs**, and **virtual Icechunk stores** over them. There is no build, lint
or test system. The pipeline normally runs on a Linux VM or a laptop, not
on a JupyterHub. Start with `README.md` ("Rebuilding from scratch"); setup
is in `setup.md` and, as copy-paste commands, `setup_bare_VM.txt`.

| product | streams (`nodd.py --stream`) | bucket | store (`build_icechunk.py --store`) |
|---|---|---|---|
| RFROM v2.3 temperature/salinity | `temp`, `sal`, `temp_error`, `sal_error` | `noaa-oar-rfrom`, `netcdf/v2.3/` | `rfrom_v23` → `icechunk/v2.3` |
| RFROM v2.2 / v2.1 | `temp_v22`, `sal_v22`, `temp_v21` | `noaa-oar-rfrom`, `netcdf/v2.2/`, `v2.1/` | none |
| GOBAI HR oxygen/nitrate | `o2`, `no3` | `noaa-oar-gobai`, `netcdf/v202606/` | `gobai_hr` → `icechunk/v202606` |

`GOBAI-O2/gobai-o2-monthly-icechunk-sc.ipynb` is an **unrelated** product
(GOBAI-O2 v2.3 monthly from NCEI → materialized Icechunk on Source
Cooperative). Do not mix its chunking or metadata with GOBAI HR's.

## Files

- `nodd.py` — ERDDAP → NODD netCDF batch script for every stream. `STREAMS` is
  the one place stream differences live; `PRODUCTS` holds bucket, default
  version and scratch default. Requires `--stream` plus `--blocks RANGE` or
  `--all`; `--list` plans without downloading. Idempotent: skips blocks already
  in the bucket unless `--force`. `--help` is the flag reference; don't copy
  flag docs into the READMEs.
- `build_icechunk.py` — virtual Icechunk builder, config in `STORES`. Validates
  after committing; `--local-repo DIR` is a full dry run.
- `publish_viewer.py` — builds gridlook and uploads it to `gs://<bucket>/viewer/`.
- `requirements.lock` — pinned install for Python 3.12 (Linux + macOS), generated
  by `uv pip compile --universal` from `requirements.txt`,
  `requirements-icechunk.txt` and `constraints.txt`. Never hand-edit it; see
  "Updating the pinned versions" in `setup.md`. Re-test any new lock in a clean
  venv — an untested install is how the missing `h5py` got through (issue #8).
- `RFROMV/prep-one-netcdf-for-NODD.ipynb` — annotated walkthrough of one block
  (the *why*). Its upload cell refuses to overwrite a published object.
- `RFROMV/icechunk-smoke-test.ipynb` — run before any real store build.
- `RFROMV/index.html`, `GOBAI-O2/index.html` — landing pages at each bucket
  root. Every code block on them was executed before publishing; keep that
  standard, and keep them in step with the product READMEs.

Environment: `NODD_SCRATCH_DIR` (default `~/rfromv-scratch` / `~/gobai-scratch`,
needs ~35 GB) and `NODD_GCS_TOKEN` (default
`~/.config/gcloud/application_default_credentials.json`, or `google_default`).
The old `RFROM_*` names are still honoured. Python 3.12 is recommended.

## The netCDF pipeline

Per output file: ERDDAP monthly netCDFs → `open_mfdataset` → one 100-step block
→ CF metadata fix → rechunk `(100, 1, 180, 180)` ≈ 13 MB, zlib-4 + shuffle →
upload to `gs://<bucket>/netcdf/<version>/<stream>/`. RFROM v2.3 and GOBAI HR
both run 1993-01-01 → 2025-12-05 weekly: 1719 steps, 18 blocks (17 × 100 + 19).
The grids are identical, since GOBAI HR is built on RFROM, which is why one
script serves both. (GOBAI declares `source = "... RFROM v2.2"`.)

**`temp` and `sal` each join two ERDDAP datasets** — the settled record
(`argo_rfromv23_temp`, ends 2024-12-27) and its realtime extension
(`..._temp_realtime`, from 2025-01-03) — into one continuous series. A stream
entry may list several `sources`; `stream_time_axis()` keeps the earlier source
where two overlap. File names carry a mode infix naming what is inside:
`..._STABLE_...`, `..._REALTIME_...`, or `..._STABLE_REALTIME_...` for the seam
block. So **lexical order is not time order** (`build_icechunk.block_start`
sorts on the dates), and a name changes when PMEL settles provisional weeks.
The history of why the streams are joined is in
`claude/notes/pipeline-history.md`.

### Constraints that are easy to break

- **Do not alter the data.** Open with
  `data_vars="minimal", coords="minimal", compat="override"`. The default
  broadcasts `mean_pressure_bnds` against `time` and silently changes the file.
- **Read contiguous pressure planes.** Source files are contiguous
  `(time, pressure, lat, lon)`, so read with dask `chunks={"mean_pressure": 1}`.
  Small spatial read chunks make the write I/O-bound, and a bigger VM does not
  help. Read chunks are decoupled from the on-disk `chunksizes`.
- **The time chunk is always the full 100.** A short final block is written with
  `unlimited_dims=["time"]` so HDF5 pads its edge chunk. A shrunk time chunk
  cannot be virtualized, because Zarr has no variable-length chunks.
- **Don't `--force` a published file casually.** The store references byte
  ranges inside it, so replacing it means rebuilding the store.

### Metadata corrections (metadata only, values unchanged)

- `ocean_salinity` is **absolute salinity (TEOS-10), g/kg**, confirmed by the
  data author. ERDDAP's `sea_water_practical_salinity` / `PSU` is a known
  upstream error, so `sal` and `sal_v22` are overridden to
  `sea_water_absolute_salinity` / `grams_per_kilogram`.
- Temperature is `sea_water_conservative_temperature`. The error variables use
  the modifier form `<name> standard_error`.
- `argo_rfromv23_temp_error` files say `title = "RFROM v2.2"` with a stub
  `references`. That is a stale label on v2.3 data (issue #25), so the stream's
  `global_attrs` restores the v2.3 title and citation and notes it in `history`.
- GOBAI HR: `o2` → `moles_of_oxygen_per_unit_mass_in_sea_water`, `no3` →
  `moles_of_nitrate_per_unit_mass_in_sea_water`, both `umol kg-1`, which are
  per-mass forms (CF table v94). ERDDAP's per-volume nitrate name is wrong,
  pending author confirmation. The GOBAI version prefix is `v202606`, taken
  from the files, not ERDDAP's `HR-v1.0`. `Description` is copied into
  `long_name` for GOBAI only; RFROM was published without it. `comment =
  "preliminary"` and the "in prep." `references` are passed through verbatim.

Details: `claude/notes/nodd-batch-script.md`, `gobai-nodd.md`,
`rfromv-v21-v22-nodd.md`.

## The Icechunk stores

`build_icechunk.py` merges every stream of a product into one **100 % virtual**
store (metadata and byte-range references only). RFROM's store adds a
`data_mode(time)` int8 flag (0 stable, 1 realtime) from
`STORES["rfrom_v23"]["realtime_start"]`; GOBAI's has none. Every file feeding a
variable must share one chunk grid, and only the last may be short.
`concat_virtual` joins manifests directly and enforces both rules with named
errors.

- **`commit()` uses `rebase_tries=0`** on purpose; the default of 1000 retries a
  spurious conflict for hours, and these builds are single-writer. Don't
  restore it.
- **Never diagnose a store anonymously right after building it.** The branch
  pointer is served `max-age=3600`, so an anonymous reader sees the old snapshot
  for up to an hour. Use credentials. This once cost a store.
- The arrays use numcodecs shuffle + zlib, which are outside the Zarr v3 core
  spec. zarr-python reads them (with a warning on every open); other
  implementations may not.
- Reading needs `icechunk`, `zarr` and `xarray`. `virtualizarr` is build-time
  only.

Read `claude/notes/rfromv-icechunk.md` and `gobai-icechunk.md` before any store
work.

## Chunking reference numbers

- RFROM native on-disk: `time:5, mean_pressure:58, latitude:720, longitude:1440`.
- RFROM/GOBAI HR NODD target: `(100, 1, 180, 180)` ≈ 13 MB.
- CEFI uses `100, 10, 200, 200`.
- GOBAI-O2 on NCEI: `pres:58, lat:145, lon:360`.
- GOBAI-O2 Icechunk notebook: `time:14, pres:2, lat:73, lon:120`.
- For a `(T, 1, Y, X)` chunk, a global map read costs `4.15 MB × T` and a point
  time series costs `1719 × Y·X·4` bytes, so no single grid serves both
  (`claude/notes/rfromv-icechunk.md` §7–8).
