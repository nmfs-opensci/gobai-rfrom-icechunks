# GOBAI-O2 → NODD: ERDDAP reconnaissance

Starting point for the next task: process the GOBAI files on PMEL ERDDAP for
NODD, the same way `RFROMV/` does for RFROM v2.3. Different product, different
author. **Nothing below is a decision** — it is what the server reports, plus the
questions that need Eli or the author to settle them.

Gathered 2026-09-03 by querying PMEL ERDDAP directly.

## What is actually on ERDDAP

`search/index.json?searchFor=gobai` returns exactly two datasets:

| dataset_id | title | data var | units |
|---|---|---|---|
| `gobai_o2_hr_v10`  | GOBAI-O2 HR-v1.0  | `o2`  | micromole per kilogram |
| `gobai_no3_hr_v10` | GOBAI-NO3 HR-v1.0 | `no3` (assumed — confirm) | (confirm) |

**These are NOT the product in `GOBAI-O2/gobai-o2-monthly-icechunk-sc.ipynb`.**
That notebook builds GOBAI-O2 **v2.3 monthly** pulled from **NCEI**
(`doi:10.25921/z72m-yz67`) and publishes to Source Cooperative. ERDDAP carries
**HR-v1.0, weekly**. Different version, different cadence, different source.
Do not conflate them or reuse that notebook's chunking as if it applied.

## Grid — identical to RFROM v2.3

`info/gobai_o2_hr_v10/index.json`:

- dims `(time, mean_pressure, latitude, longitude)` = `(1719, 58, 720, 1440)`, `o2` float32
- `time`: evenly spaced **7 days**, 1993-01-01 → 2025-12-05, units `seconds since 1970-01-01T00:00:00Z`
- `mean_pressure`: 58 levels, unevenly spaced, `decibar`
- `latitude` 720 @ 0.25°, `longitude` 1440 @ 0.25°

This is the RFROM grid (GOBAI is built on RFROM), so the `RFROMV/rfrom_nodd.py`
machinery — 100-step blocks, `chunks={"mean_pressure": 1}` on read, `(100,1,180,180)`
on disk, the `.part`+verify+rename download — should transfer nearly as-is.

1719 steps / 100 ⇒ **18 blocks**: 17 full plus a final block of 19.

## Source files

`files/<dataset_id>/.json` (use this JSON listing — the HTML listing entity-encodes
every hyphen and dot, so scraping it for `*.nc` silently finds nothing):

- **396 monthly files** per dataset, ~1.04 GB average, **~0.41 TB per dataset**
- naming: `GOBAI-O2-HR-v202606-1993-01.nc` … `GOBAI-O2-HR-v202606-2025-12.nc`
- NO3 mirrors it exactly: `GOBAI-NO3-HR-v202606-YYYY-MM.nc`, same count and sizes

Note the filename version stamp is **`v202606`** while the dataset title says
**HR-v1.0** — these disagree, and the NODD prefix needs one of them.

## CF metadata gap

The ERDDAP info table carries `standard_name` for `time`, `latitude`, and
`longitude` — and **none for `o2` or `mean_pressure`**. `o2.long_name` is just
`"O2"` and units are the non-CF string `"micromole per kilogram"`. So this needs
the same CF pass RFROM got. Likely target (to be confirmed with the author, not
assumed):

- `o2`: `standard_name = moles_of_oxygen_per_unit_mass_in_sea_water`, `units = umol kg-1`
- `mean_pressure`: `standard_name = sea_water_pressure`, `positive = "down"`, `axis = "Z"`

The RFROM salinity episode is the cautionary tale here: ERDDAP variable metadata
can be wrong in ways only the author can adjudicate. Ask; do not infer from units.

## Open questions for Eli / the author

1. **Is NO3 in scope**, or O2 only? It is the same size and shape, so it is a
   second stream rather than a second project.
2. **Target bucket and prefix.** RFROM goes to `gs://noaa-oar-rfrom/netcdf/v2.3/<stream>/`.
   Is there a GOBAI NODD bucket, and is it already provisioned?
3. **Version string** for that prefix: `v1.0` (title) or `v202606` (filenames)?
4. **Stable/realtime split?** RFROM has six streams because of it. GOBAI appears
   to be a single stream per variable — confirm there is no realtime sibling.
5. **CF names and units** for `o2` / `no3` / `mean_pressure` — author confirmation.
6. Does GOBAI carry `mean_pressure_bnds` like RFROM? If so the same
   `data_vars="minimal"` do-not-broadcast rule applies. Not visible in the info
   table; check an actual file.

## Method note

Everything above came from three cheap ERDDAP endpoints, no downloads:
`search/index.json`, `info/<ds>/index.json`, `files/<ds>/.json`. Next step is to
pull **one** monthly file and inspect it (bounds vars, `_FillValue`, attrs,
on-disk chunking) before designing anything.
