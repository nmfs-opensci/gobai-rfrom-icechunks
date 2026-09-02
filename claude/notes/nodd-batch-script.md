# RFROMV NODD batch-processing script (next task)

Goal: turn the tested single-file notebook
`RFROMV/prep-one-netcdf-for-NODD.ipynb` into a script that processes **all**
ERDDAP RFROM v2.3 files into NODD-bound netCDFs and uploads them to
`gs://noaa-oar-rfrom/netcdf/v2.3/<stream>/`.

**Operational requirement from Eli:** do NOT process all streams at once. He runs
one stream at a time, likely on **multiple VMs in parallel (one VM per type)**.
The script must therefore be **parameterized by stream** (and ideally by a block
range within a stream) so each VM runs an independent slice with no coordination.
Make it idempotent — skip work already uploaded — so a re-run or a second VM on
the same stream is safe.

## The six streams (all confirmed against ERDDAP, 2026-09-02)

All datasets are PMEL ERDDAP griddap; files at
`https://data.pmel.noaa.gov/pmel/erddap/files/<dataset_id>/`. Grid is
`(time, mean_pressure, latitude, longitude)`, float32, and `mean_pressure_bnds`
`(mean_pressure, nv)` bounds — same layout across all six.

| stream | dataset_id | data var | units | ERDDAP standard_name | monthly file pattern | # files | # time steps | time extent | blocks* |
|---|---|---|---|---|---|---|---|---|---|
| `temp_stable`    | `argo_rfromv23_temp`          | `ocean_temperature`       | degree_Celsius     | (none)                        | `RFROMV23_TEMP_STABLE_YYYY_MM.nc`          | 384 | 1670 | 1993-01-01 → 2024-12-27 | 17 |
| `temp_realtime`  | `argo_rfromv23_temp_realtime` | `ocean_temperature`       | degree_Celsius     | (none)                        | `RFROMV23_TEMP_STABLE_YYYY_MM_REALTIME.nc` | 12  | 49   | 2025-01-03 → 2025-12-05 | 1  |
| `temp_error`     | `argo_rfromv23_temp_error`    | `ocean_temperature_error` | degree_Celsius     | (none)                        | `RFROMV23_TEMP_ERROR_YYYY_MM.nc`           | 396 | 1719 | 1993-01-01 → 2025-12-05 | 18 |
| `sal_stable`     | `argo_rfromv23_sal`           | `ocean_salinity`          | PSU †              | `sea_water_practical_salinity` †| `RFROMV23_SAL_STABLE_YYYY_MM.nc`           | 384 | 1670 | 1993-01-01 → 2024-12-27 | 17 |
| `sal_realtime`   | `argo_rfromv23_sal_realtime`  | `ocean_salinity`          | PSU †              | `sea_water_practical_salinity` †| `RFROMV23_SAL_STABLE_YYYY_MM_REALTIME.nc`  | 12  | 49   | 2025-01-03 → 2025-12-05 | 1  |
| `sal_error`      | `argo_rfromv23_sal_error`     | `ocean_salinity_error`    | grams_per_kilogram | (none)                        | `RFROMV23_SAL_ERROR_YYYY_MM.nc`            | 396 | 1719 | 1993-01-01 → 2025-12-05 | 18 |

\* blocks at BLOCK_SIZE=100: stable = 17 (16×100 + 70), error = 18 (17×100 + 19),
realtime = 1 (49). Compute per stream from the live time axis — do not hardcode.

† These are the values ERDDAP *reports*, but they are wrong: the data author
confirmed `ocean_salinity` is absolute salinity (TEOS-10) in g/kg. The
PSU/practical label is a known upstream mistake he cannot fix, so the pipeline
overrides `sal_stable`/`sal_realtime` output to `sea_water_absolute_salinity` /
`grams_per_kilogram` (see the CF metadata section).

### Things the table reveals (design-relevant)

1. **Realtime filenames keep the `STABLE` prefix and add a `_REALTIME` suffix.**
   The per-stream monthly-file builder in the notebook (`{MONTHLY_PREFIX}_{y}_{m:02d}.nc`)
   must become stream-aware: error uses an `_ERROR_` infix, realtime appends
   `_REALTIME`. Best to store a filename template per stream, not just a prefix.
2. **Error and realtime run to 2025-12-05; stable stops at 2024-12-27.** The error
   dataset is one continuous series spanning the whole stable+realtime period
   (confirms Eli: "temp_error has dates that include _temp and _temp_realtime").
   Don't assume error aligns block-for-block with stable.
3. **Output-file naming for realtime — RESOLVED (see Decisions below):** mirror
   the ERDDAP norm, `RFROMV23_{TEMP,SAL}_STABLE_<start>_<end>_REALTIME.nc`.

## CF metadata per stream (was flagged as a blocker — mostly resolved)

- **Salinity is absolute salinity (TEOS-10) in g/kg — CORRECTED 2026-09-02.** The
  data author confirmed `ocean_salinity` is absolute salinity (TEOS-10), g/kg. The
  ERDDAP variable metadata (`units=PSU`, `standard_name=sea_water_practical_salinity`)
  is a known upstream mistake he cannot fix ("I don't have direct control over the
  ERDDAP file system"). So we OVERRIDE the main-var attrs to
  `sea_water_absolute_salinity` / `grams_per_kilogram`, matching `ocean_salinity_error`
  — all three salinity streams are now absolute/g/kg and internally consistent. Data
  values are unchanged (metadata-only). NOTE this reverses the earlier decision to
  trust the ERDDAP PSU label; the author's "absolute salinity TEOS-10" description was
  right all along.
- **Temperature standard_name:** ERDDAP carries none. The notebook set
  `sea_water_conservative_temperature` for temp_stable (RFROM is TEOS-10 →
  conservative temperature is the right call). Reuse it for temp_realtime.
  **Still worth a one-line confirmation** that RFROM temperature is conservative,
  not in-situ.
- **Error variables (`*_error`):** no obvious CF standard_name. Options: omit
  standard_name, or use the CF standard-name modifier form
  (`sea_water_..._temperature standard_error`). Leaving standard_name off is
  acceptable and safe. — MINOR OPEN DECISION.
- Everything else (coord standard_names, `positive=down`/`axis=Z` on
  mean_pressure, `Conventions="CF-1.10, ACDD-1.3"`, `clean_utf8` surrogate repair,
  time `seconds since 1970-01-01`, no `_FillValue` on coords) is stream-agnostic —
  copy from the notebook verbatim.

## What to carry over from the notebook UNCHANGED (do not regress)

These are the hard-won correctness/performance fixes — see
`claude/notes/nodd-prep.md` for the why:

- `open_mfdataset(..., engine="h5netcdf", combine="by_coords",
  data_vars="minimal", coords="minimal", compat="override",
  chunks={"mean_pressure": 1})` — preserves `mean_pressure_bnds` shape (does NOT
  alter original data) AND reads contiguous pressure planes (avoids the I/O-bound
  write that looked like OOM).
- On-disk `chunksizes=(100,1,180,180)` capped at dim size for the short final
  block; dask `dask_chunks` = full lat/lon plane per (time-block, pressure),
  an exact multiple of the on-disk chunks.
- Encoding: float32, `_FillValue=NaN`, `zlib=True, complevel=4, shuffle=True`
  (drop to `complevel=1` if write time dominates the full run).
- Download helper skips files already present at the right size.

## Proposed script shape (for the build session to confirm)

A single module + CLI, e.g. `RFROMV/rfrom_nodd.py`:

- A `STREAMS` dict keyed by stream name, each holding: `dataset_id`, `data_var`,
  `units`, `standard_name` (or None), monthly-filename template, output-prefix,
  and the extra variable attrs. This is the ONE place stream differences live.
- Core functions mirroring the notebook stages: `list_blocks(stream)`,
  `download_block(...)`, `build_dataset(...)`, `write_netcdf(...)`,
  `upload(...)`, all stream-parameterized.
- CLI: `python rfrom_nodd.py --stream temp_stable [--blocks 0-4 | --blocks 3 | --all]
  [--version v2.3] [--no-upload] [--keep-scratch]`. Default should require an
  explicit `--stream` (never process everything implicitly).
- **Idempotency / multi-VM friendliness:** before processing a block, check
  whether its target object already exists in the bucket (`fs.exists(dest)`) and
  skip unless `--force`. That makes it safe to run the same stream on two VMs, or
  resume after interruption. Log clearly which blocks were skipped vs written.
- **Weekly realtime reconcile (later):** realtime is a moving draft. A `--mode
  reconcile` (or separate `update_nodd.py`) re-downloads the realtime dataset,
  and re-writes/replaces the affected (tail) block(s), since the last realtime
  block grows as new weeks arrive and old realtime eventually migrates into
  stable on a new version. Keep this out of the first cut unless asked.

Scratch dirs and GCS auth are unchanged from the notebook config cell
(`/home/jovyan/shared-public/rfromv-scratch`, ADC token at
`~/.config/gcloud/application_default_credentials.json`).

## Decisions — RESOLVED 2026-09-02 (issue #5 build session)

Built `RFROMV/rfrom_nodd.py`. Decisions confirmed with Eli + verified on ERDDAP:

1. **Realtime output filenames match the ERDDAP norm.** The realtime *monthly*
   files on ERDDAP are `RFROMV23_TEMP_STABLE_2025_01_REALTIME.nc` — they KEEP the
   `STABLE` token and APPEND `_REALTIME` (Eli's recollection of a `TEMP_REALTIME`
   token was mistaken; verified via the `files/<id>/.csv` listing). Output blocks
   mirror that: `RFROMV23_TEMP_STABLE_<start>_<end>_REALTIME.nc`.
2. **Error `standard_name` uses the CF modifier form** (`<base> standard_error`).
   `temp_error` → `sea_water_conservative_temperature standard_error`.
   `sal_error` → `sea_water_absolute_salinity standard_error` — NOT practical:
   its source Description says "RMS error on … absolute salinity (TEOS-10)" and
   its units are `grams_per_kilogram`, so the absolute-salinity base is the
   units-consistent CF choice (practical salinity is dimensionless/PSU).
3. **Temperature is conservative (TEOS-10).** ERDDAP carries no `standard_name`
   but the variable `Description` = "mapped ocean conservative temperature
   (TEOS-10) …", so `sea_water_conservative_temperature` (a valid CF name) is
   correct for `temp_*`. (For the record `sea_water_temperature`,
   `_potential_temperature`, and `_conservative_temperature` are ALL CF-valid —
   the source physics, not CF-validity, is what picked it.)
4. **Multi-VM granularity: both.** Script is `--stream`-scoped AND supports
   `--blocks 0-8` ranges so one stream can be split across VMs. Requires an
   explicit `--stream` and an explicit `--blocks`/`--all`; idempotent skip via
   `fs.exists(dest)` unless `--force`.

Salinity — CORRECTED 2026-09-02 (reverses an earlier call): ALL salinity streams
are absolute salinity (TEOS-10) in g/kg. The data author confirmed `ocean_salinity`
is absolute salinity g/kg ("It should be 'absolute salinity (TEOS-10)' which has
units of g/kg … I don't have direct control over the ERDDAP file system and I
hadn't realized the mistake"). ERDDAP's `sea_water_practical_salinity` / `PSU`
label on the main var is therefore wrong. We OVERRIDE `sal_stable`/`sal_realtime`
var_attrs to `sea_water_absolute_salinity` / `grams_per_kilogram` (was practical/PSU),
so all three salinity streams are now internally consistent with `sal_error`. Data
values are unchanged — metadata only. This supersedes the earlier "trust the ERDDAP
variable metadata over its Description" reasoning: the Description was right.

### The script — `RFROMV/rfrom_nodd.py`

- `STREAMS` dict is the single place stream differences live (`dataset_id`,
  `data_var`, `var_attrs`, `monthly_template`, `out_template`).
- Functions mirror the notebook stages: `erddap_time_axis`, `make_file_blocks`,
  `download`/`download_block`, `build_dataset` (open+slice+CF+rechunk+encoding),
  `write_netcdf`, `process_block` (adds idempotency skip, upload, scratch clean).
- CLI: `--stream <name>` (required) · `--blocks RANGE | --all` · `--version`
  (default v2.3) · `--list` (print block plan, no download) · `--no-upload` ·
  `--force` · `--keep-scratch`. Default cleans each block's scratch after upload
  (boundary months re-download idempotently) to keep disk bounded.
- Verified without downloading: `--list` for all streams reproduces the spec
  table (block 0 name bit-identical to the notebook; realtime `_REALTIME` names;
  sal_error 18 blocks → 2025-12-05), and HEAD on the generated monthly URLs for
  the changed templates (realtime, error) returns 200.
- NOT yet run end-to-end (a full block is ~30 GB download + bucket write) — that
  is an interactive hub run, same as how the notebook was validated.

### Still not in this cut

Weekly realtime reconcile (`--mode reconcile` / `update_nodd.py`): re-download
the moving realtime dataset and replace the affected tail block(s).
