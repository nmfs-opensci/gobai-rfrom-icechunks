# RFROMV → NODD prep

Topic notes for the RFROM v2.3 NODD publishing pipeline. Handoff points here;
detail lives here.

## Deliverable

`RFROMV/prep-one-netcdf-for-NODD.ipynb` — the tested single-file pipeline (GitHub
issue #1, merged via PR #4). It is the foundation for a future batch script.

## Pipeline (per output file)

ERDDAP monthly netCDFs → `open_mfdataset` combine → select one 100-timestep block
→ CF metadata fix → rechunk `(100,1,180,180)` ≈ 13 MB + zlib-4/shuffle → upload to
`gs://noaa-oar-rfrom/netcdf/v2.3/<stream>/`.

- 1670 time steps → 17 blocks. Output name pattern
  `RFROMV23_TEMP_STABLE_1993-01-01_1994-11-25.nc`.
- Streams: `temp_stable temp_realtime temp_error sal_stable sal_realtime sal_error`.
  Realtime = draft; reprocessed into stable over time. Downstream Icechunk store
  combines streams with a stable/realtime flag.
- Datasets are versioned (`v2.3` currently). New versions reprocess all data →
  new `netcdf/<version>/` tree.

## Hard-won lessons (do not regress)

1. **Preserve original data.** `open_mfdataset(..., data_vars="minimal",
   coords="minimal", compat="override")`. Default `data_vars="all"` broadcasts
   `mean_pressure_bnds` against `time` → shape `(100,58,2)` instead of `(58,2)`,
   silently corrupting the file. Verified all source files' bounds identical
   before relying on `override`. Keep vertices/bounds dims; keep data values bit-
   identical.
2. **I/O layout, not RAM, was the OOM/slowness.** Source files are contiguous
   `(time,pressure,lat,lon)`. Reading `(180×180)` spatial tiles = strided seeky
   reads → I/O-bound (~8% CPU). Fix: dask `chunks={"mean_pressure":1}` on read
   (sequential full-plane reads), decoupled from on-disk `chunksizes` in
   `encoding`. Result: ~80% CPU, 7.56 GB in ~9 min, 3.2× compression, bit-
   identical. A bigger VM would NOT have helped.
3. **CF compliance:** `standard_name`, `axis`, `positive=down` (mean_pressure Z),
   `Conventions="CF-1.10, ACDD-1.3"`, UTF-8 surrogate repair via `clean_utf8()`,
   no `_FillValue` on coordinate vars, time as `seconds since 1970-01-01`.
4. **Virtualizarr/icechunk friendly:** uniform chunk grid across files; time as a
   single full-length chunk downstream; readers pass explicit `chunks=` not
   `chunks={}`. Reader guidance is in the notebook Step 5 markdown, tied to
   discussion comment
   https://github.com/SAFS-Varanasi-Internship/Summer-2026/discussions/10#discussioncomment-18008706
   (conclusion: no prep-file change needed — 100 steps/file avoids the pathology).

## NODD bucket

`gs://noaa-oar-rfrom`, dir `netcdf/`. Layout `netcdf/<version>/<stream>/`. Bucket
was cleaned of a stray test file. Auth: gcsfs +
`/home/jovyan/.config/gcloud/application_default_credentials.json`.

## Batch script — DELIVERED (issue #5, 2026-09-02)

`RFROMV/rfrom_nodd.py` — the notebook generalized to all six streams. `STREAMS`
dict holds the per-stream differences; CLI is `--stream <name>` (required) +
`--blocks RANGE | --all` (required) + `--version/--list/--no-upload/--force/
--keep-scratch`. Idempotent (skip via `fs.exists`), multi-VM safe. Full decisions
and the resolved standard_name/units details live in `nodd-batch-script.md`.
Verified via `--list` + URL HEAD checks; not yet run end-to-end on the hub.

Resolved open questions:
- Salinity: absolute salinity (TEOS-10) in g/kg for ALL salinity streams —
  confirmed by the data author (2026-09-02). The ERDDAP metadata labels the main
  var `sea_water_practical_salinity`/PSU, but that is a known upstream mistake he
  cannot fix, so we override `sal_stable`/`sal_realtime` to
  `sea_water_absolute_salinity` / `grams_per_kilogram` (values unchanged); `sal_error`
  → `sea_water_absolute_salinity standard_error`, g/kg. (Earlier we had trusted the
  ERDDAP PSU label — now reversed.)
- Error datasets: `ocean_temperature_error` (degree_Celsius) and
  `ocean_salinity_error` (grams_per_kilogram); `_ERROR_` infix filenames; one
  continuous 1993→2025 series (18 blocks), no realtime split.

Still open / next:
- `update_nodd.py` — weekly realtime reconcile (re-download the moving realtime
  dataset, replace the affected tail block(s)).
