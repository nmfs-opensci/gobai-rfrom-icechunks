# RFROM v2.2 / v2.1 → NODD: recon + decisions

GitHub issue #20. Add older RFROM versions to the same `nodd.py` pipeline that
publishes v2.3.

## The issue's URLs were wrong

The issue listed three v2.2 URLs to process: `argo_rfromv22`,
`argo_rfromv22_realtime`, `argo_rfromv22_error`. Checked against ERDDAP's
search API and each dataset's `.dds`/`.info`, then confirmed by downloading and
opening `RFROMV22_OHC_1993_01.nc`: these three are **not** temperature or
salinity. They are **Ocean Heat Content anomaly**, a derived quantity on a
different, coarser vertical grid:

```
float ocean_heat_content_anomaly(time, mean_depth, latitude, longitude) ;
    ocean_heat_content_anomaly:units = "10^9 J m^-2" ;
float mean_depth_bnds(mean_depth, vertices) ;
mean_depth = 10 ;   // 20 m .. 1975 m -- NOT the 58-level mean_pressure axis
```

`nodd.py`'s `build_dataset()` hardcodes `mean_pressure` throughout (`CHUNKS`,
`COORD_ATTRS`, the bounds fill-value suppression, the dask
`chunks={"mean_pressure": 1}` read strategy) so it cannot process this variable
as-is. Units `"10^9 J m^-2"` also aren't udunits-parseable. Split out to
[issue #21](https://github.com/nmfs-opensci/gobai-rfrom-icechunks/issues/21)
rather than done here, to keep this PR to the low-risk reuse of the existing
pipeline.

## What v2.2 / v2.1 temp/sal actually are

Full ERDDAP dataset list for "rfromv22" (`search/index.json?searchFor=rfromv22`),
cross-checked with direct 404s on the combinations that would mirror v2.3's
six-stream pattern (`argo_rfromv22_temp_realtime`, `_temp_error`,
`_sal_realtime`, `_sal_error` — all 404, none exist):

| dataset_id | variable | vertical dim | months | range |
|---|---|---|---|---|
| `argo_rfromv22_temp` | `ocean_temperature` | `mean_pressure` (58) | 1670 | 1993-01 → 2024-12 |
| `argo_rfromv22_sal`  | `ocean_salinity`    | `mean_pressure` (58) | 1719 | 1993-01 → 2025-12 |
| `argo_rfromv21_temp` | `ocean_temperature` | `mean_pressure` (58) | 1613 | 1993-01 → 2023-12 |

Confirmed by downloading and opening one file per dataset (`RFROMV21_TEMP_1993_01.nc`,
1.2 GB; `RFROMV22_OHC_1993_01.nc`, 207 MB, for the OHC comparison above): the
temp/sal grid — `latitude` (720), `longitude` (1440), `mean_pressure` (58) and
`mean_pressure_bnds(mean_pressure, vertices)` — is **bit-identical** to v2.3's
(`np.allclose` on all four), same float32 dtype, same contiguous/uncompressed
on-disk layout. So the existing pipeline code applies unchanged; only new
`STREAMS` entries were needed.

Neither v2.2 stream has a realtime or error sibling — each is one continuous
series (unlike v2.3's stable/realtime/error split per variable). v2.1 has
temperature only; there is no `argo_rfromv21_sal`.

**temp_v22 and sal_v22 don't end on the same date** (2024-12 vs 2025-12) — real,
confirmed on ERDDAP twice, not a scraping artifact. Left as-is; not investigated
further since it doesn't block processing (each stream's blocks are independent).

Salinity Description confirms the same TEOS-10 mislabel as v2.3: `Description`
says "mapped ocean absolute salinity (TEOS-10)..." while ERDDAP's
`standard_name`/`units` say `sea_water_practical_salinity` / `PSU`. Same
override as v2.3's `sal_stable` applies: `sea_water_absolute_salinity` /
`grams_per_kilogram`, values unchanged.

## Code change: per-stream version override

`PRODUCTS["rfrom"]["default_version"]` is `"v2.3"`. Without a per-stream
override, forgetting `--version v2.2` on a v2.2 stream would silently target
`netcdf/v2.3/temp_v22/` — wrong version folder, easy mistake under multi-VM
production runs. Added an optional `"version"` key to a `STREAMS` entry that
takes precedence over the product default (`args.version` from the CLI still
wins over both, for explicit overrides/testing):

```python
version = args.version or STREAMS[stream].get("version") or product["default_version"]
```

`temp_v22`/`sal_v22` set `"version": "v2.2"`, `temp_v21` sets `"version": "v2.1"`.
v2.3 streams and GOBAI are unaffected (no `"version"` key, same as before).

## Verified end to end

`--list` on all three new streams produces the expected block plan (17, 18, 17
blocks respectively, matching the time-step counts above). A live smoke-test
block (`temp_v21 --blocks 16 --no-upload`) downloaded 3 monthly files, built
the 13-step output with `dims={'time': 13, 'mean_pressure': 58, 'latitude':
720, 'longitude': 1440, 'vertices': 2}` and on-disk chunks `(13, 1, 180, 180)`,
and the written file's `ocean_temperature.standard_name` /
`history` / `Conventions` attrs were exactly as expected.
