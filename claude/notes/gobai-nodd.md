# GOBAI → NODD: reconnaissance + processing plan

GitHub issue #13. Process the GOBAI HR files on PMEL ERDDAP for NODD, the same
way `RFROMV/` does for RFROM v2.3. Different product, different author.

Recon 2026-09-03: ERDDAP metadata endpoints, plus **one real monthly file of each
dataset downloaded and inspected** (`/home/jovyan/shared-public/gobai-scratch/erddap/`,
`GOBAI-{O2,NO3}-HR-v202606-2020-06.nc`, ~0.96 GB each). Everything below is
measured fact; the decisions Eli settled on review are at the end.

## What is on ERDDAP

Exactly two datasets:

| dataset_id | title | data var | file stem |
|---|---|---|---|
| `gobai_o2_hr_v10`  | GOBAI-O2 HR-v1.0  | `o2`  | `GOBAI-O2-HR-v202606-YYYY-MM.nc` |
| `gobai_no3_hr_v10` | GOBAI-NO3 HR-v1.0 | `no3` | `GOBAI-NO3-HR-v202606-YYYY-MM.nc` |

**Not** the product in `GOBAI-O2/gobai-o2-monthly-icechunk-sc.ipynb` — that is
GOBAI-O2 **v2.3 monthly** from NCEI, published to Source Cooperative. These are
**HR-v1.0, weekly**. Do not conflate them or reuse that notebook's chunking.

396 monthly files per dataset, **0.413 TB per dataset**, 1993-01 → 2025-12.
Use the JSON listing `files/<dataset_id>/.json`; the HTML listing entity-encodes
every hyphen and dot, so scraping it for `*.nc` silently finds nothing.
No realtime or error sibling exists — **one stream per variable** (unlike RFROM's six).

## Grid: bit-identical to RFROM v2.3

Verified by opening a GOBAI file and `shared-public/RFROMV23_TEMP_STABLE_1993_01.nc`
side by side:

- dims `(time, mean_pressure, latitude, longitude)` = `(1719, 58, 720, 1440)`, float32
- `latitude` (720), `longitude` (1440), `mean_pressure` (58) — **`np.allclose` identical**, same float32 dtype
- `mean_pressure_bnds(mean_pressure, vertices)` present in **both**, values identical
- time: weekly, 1993-01-01 → 2025-12-05. The RFROM stable axis (1670 steps,
  → 2024-12-27) is an **exact prefix** of the GOBAI axis (1719 steps).

So `RFROMV/rfrom_nodd.py` transfers essentially as-is, and 100-step block
boundaries land on the *same dates* as RFROM's.

**18 blocks**: 17 × 100 + a final 19. Block 0 = `1993-01-01 … 1994-11-25`,
block 16 = `2023-09-01 … 2025-07-25`, block 17 = `2025-08-01 … 2025-12-05`.
Each block spans 23 monthly files (block 11 spans 24, block 17 spans 5).

### Answer to "can GOBAI and RFROM share one Icechunk store?"

On coordinates, **yes** — lat/lon/pressure/bounds are identical and the time axes
are on the same weekly grid with GOBAI a superset. The caveat is provenance, not
geometry: GOBAI HR-v1.0 declares `source = "Argo float data, GLODAP ship data,
RFROM v2.2"`, i.e. it is built on RFROM **v2.2**, while the NODD RFROM product is
**v2.3**. A combined store would mix underlying field versions.

## What the actual files contain

```
float o2(time, mean_pressure, latitude, longitude) ;
    o2:units = "micromole per kilogram" ;
    o2:Description = "Mapped Dissolved Oxygen Amount Content averaged from ..." ;
    o2:_Storage = "contiguous" ; o2:_NoFill = "true" ;
float mean_pressure_bnds(mean_pressure, vertices) ;
:Conventions = "CF-1.8" ;
:title = "GOBAI-O2-HR-v202606" ;
:source = "Argo float data, GLODAP ship data, RFROM v2.2" ;
:references = "Sharp, et al. GOBAI High Resolution Data Products, in prep." ;
:history = "Created: 15-Jul-2026" ;    (NO3: "Created: 31-Jul-2026")
:comment = "preliminary" ;
```

- Source files are **uncompressed and contiguous** (962 MB = 4 × 58 × 720 × 1440 × 4 B
  exactly). Same read-contiguous-pressure-planes rule as RFROM applies.
- 4–5 time steps per monthly file (weekly cadence).
- **No `_FillValue`** anywhere; `_NoFill = "true"`. Land/no-data is NaN
  (52.4% NaN on the surface level of the sampled file).
- Variables carry **`Description`** (capital D, non-standard) and **no `long_name`**.
- Time in file is `float32` `days since 1950-1-1 0:0:0` — same as RFROM, and the
  same reason to re-encode to float64 `seconds since 1970` on output.

## CF gaps

Checked against CF standard name table **v94**:

| variable | in the file | proposed | notes |
|---|---|---|---|
| `o2`  | units `micromole per kilogram`, **no standard_name** | `moles_of_oxygen_per_unit_mass_in_sea_water`, units `umol kg-1` | canonical units `mol kg-1` ✓ |
| `no3` | units `micromole per kilogram`, **no standard_name** | `moles_of_nitrate_per_unit_mass_in_sea_water`, units `umol kg-1` | canonical units `mol kg-1` ✓ |
| `mean_pressure` | units `decibar`, no standard_name | `sea_water_pressure`, `positive = "down"`, `axis = "Z"` | same as RFROM |
| `time` / `latitude` / `longitude` | standard_name present | add `axis` T/Y/X | |

`"micromole per kilogram"` is not udunits-parseable, so it must be rewritten
regardless.

**Upstream metadata error, RFROM-salinity style.** The ERDDAP *dataset config* for
`gobai_no3_hr_v10` declares `no3:standard_name = mole_concentration_of_nitrate_in_sea_water`.
That name is a **per-volume** quantity (canonical `mol m-3`) while the data are
**per-mass** (`micromole per kilogram`) — the two are inconsistent. The name is
**not in the netCDF file**; ERDDAP adds it. `gobai_o2_hr_v10` adds no standard_name
at all. Worth reporting to the author, as with RFROM salinity.

## Target bucket

`gs://noaa-oar-gobai` **exists and is public** (RFROM's is `gs://noaa-oar-rfrom`).
It currently contains **only `index.html`** — no `netcdf/` tree yet, so this
pipeline lays down the first data objects.

## Resource expectations

Per block, extrapolating from the measured RFROM `temp_stable` run (the array
sizes are identical): ~23–24 GB downloaded (23 monthly files), ~7–8 GB written,
~31–35 GB peak scratch. Per dataset: ~0.41 TB down, ~0.13 TB up, 18 blocks.
Both datasets: ~0.83 TB down. Wall-clock is network-bound; a bigger VM does not help.

## Plan — reviewed and implemented (PR for issue #13)

Eli reviewed the plan 2026-09-03 and took the recommended option on all four
forks. Resolved:

1. **Code layout** — one shared script, `nodd.py` at the repo root, covering all
   eight streams (RFROM's six + GOBAI's two). `RFROMV/rfrom_nodd.py` is now a
   back-compat shim forwarding to it with every flag unchanged, so in-flight VM
   commands and the `pixi run` tasks keep working. A `PRODUCTS` dict holds the
   per-product bucket, default version and scratch default; `STREAMS` entries
   name their product.
2. **Bucket prefix** — `gs://noaa-oar-gobai/netcdf/v202606/{o2,no3}/`,
   version-segmented like RFROM's `netcdf/v2.3/<stream>/`.
3. **Version string** — `v202606`, the version stamped in the source filenames
   *and* in each file's `title` global attribute. ERDDAP's dataset title says
   `HR-v1.0`; the files win. `--version` overrides.
4. **Output filenames** — `GOBAI-O2-HR-v202606_1993-01-01_1994-11-25.nc`: source
   stem verbatim, underscore before the date range.
5. **"preliminary"** — `comment` and `references` pass through verbatim.
6. **Env vars** — `NODD_SCRATCH_DIR` / `NODD_GCS_TOKEN`, with the old `RFROM_`
   names still honoured. Scratch defaults are per-product (`rfromv-scratch` vs
   `gobai-scratch`) so concurrent runs do not collide.
7. **Directory** — GOBAI work stays in `GOBAI-O2/` per the issue, with
   `GOBAI-O2/README.md` noting that `no3` lives there too.

Everything else is unchanged from RFROM: 100-step blocks, on-disk
`(100, 1, 180, 180)` float32 + zlib-4/shuffle, `_FillValue = NaN` on the data var
and suppressed on coordinates, dask read `chunks={"mean_pressure": 1}`, and
`data_vars="minimal", coords="minimal", compat="override"` so
`mean_pressure_bnds` is not broadcast against time.

### The `cf_refinements` product flag

Two CF fixes were found while validating GOBAI that apply equally to RFROM:
promoting the source's non-standard `Description` into a CF `long_name`, and
suppressing the `_FillValue` xarray adds to `mean_pressure_bnds` (CF ch. 7.1 says
a boundary variable should not have one). They are **ON for GOBAI, OFF for
RFROM** — RFROM blocks are already in the bucket without them, and switching
mid-stream would leave that published tree inconsistent with itself. Flip the
flag for RFROM at the next version reprocess.

**Decided (Eli, 2026-09-03): keep as coded.** Both fixes are metadata-only —
they don't touch data values, and RFROM's already-published blocks pass
`cfchecker` clean without them — so there's no correctness reason to force a
reprocess. Re-running and re-uploading every published RFROM block (6 streams
× up to 18 blocks, hundreds of GB) just for two attribute tweaks isn't worth
it now. Revisit when RFROM gets a real reprocess (e.g. a v2.4 bump), not on
its own.

## Validation (block 17, run end to end 2026-09-03)

`python nodd.py --stream o2 --blocks 17 --no-upload --keep-scratch` — the 19-step
tail block, the cheapest real test at 5 monthly files.

- 5 files / 4.6 GB downloaded, output 1.34 GB, on-disk chunks `(19, 1, 180, 180)`
  (time capped at the short block, as intended).
- **Data bit-identical to source**: `np.array_equal(..., equal_nan=True)` on the
  overlapping time steps; `latitude` / `longitude` / `mean_pressure` /
  `mean_pressure_bnds` all `array_equal` to the source, and the bounds variable
  kept its `(mean_pressure, vertices)` shape — not broadcast against time.
- **`cfchecker` 4.1.0: 0 errors, 0 warnings** against CF-1.8 with standard name
  table v94. Note the checker rejects the `Conventions = "CF-1.10, ACDD-1.3"`
  string we write ("CF-1.10 is not a valid CF version") — that is a checker
  limitation, not a file defect; re-running with `CF-1.8` in that attribute gives
  a clean pass. The same applies to the RFROM output.
- Re-running the same command skipped all five downloads in 0 s (idempotency),
  and `--blocks 18` / `--stream bogus` are rejected.

Not yet run: any upload, and any `no3` block. Nothing has been written to
`gs://noaa-oar-gobai`.

## Method note

ERDDAP metadata came from `search/index.json`, `info/<ds>/index.json`,
`files/<ds>/.json`, `griddap/<ds>.csv?time` — no downloads needed for any of it.
Sample and test files are in `/home/jovyan/shared-public/gobai-scratch/`
(~7.5 GB: two 2020-06 samples, block 17's five monthly sources, and its output);
delete when done prototyping.
