# RFROM v2.3 → Icechunk (issue #17) — research & plan

Status: **plan, awaiting review**. No build code written yet. Produced with the
`virtual-icechunk` skill (Create mode, step 1). Everything below was measured
against the published files on 2026-09-03, not assumed.

## 1. The two configurations

| | Source | Destination |
|---|---|---|
| what | the 72 NODD netCDFs, left in place | the Icechunk repository |
| where | `gs://noaa-oar-rfrom/netcdf/v2.3/<stream>/` | `gs://noaa-oar-rfrom/icechunk/v2.3` |
| write creds | none (read) | ADC at `~/.config/gcloud/application_default_credentials.json` |
| read creds | `ic.gcs_anonymous_credentials()` on the virtual container | `ic.gcs_storage(..., anonymous=True)` |

Source and destination are the same public bucket, so a reader needs one kind of
credential in two places — they are still declared separately, and the virtual
chunk container prefix `gs://noaa-oar-rfrom/netcdf/v2.3/` must be authorized
explicitly and persisted with `repo.save_config()`.

## 2. What the source files actually are

All 72 files were opened and their headers read (survey script in the session
scratchpad). The result is unusually clean:

- one weekly time grid, **7-day spacing with no gaps anywhere**, in every stream;
- identical `latitude` (720), `longitude` (1440), `mean_pressure` (58, identical
  values in all 72 files), `mean_pressure_bnds` `(58, 2)` contiguous;
- every data variable `float32`, gzip level 4 + shuffle, `_FillValue` NaN,
  spatial chunk `(1, 180, 180)`;
- `stable` ends 2024-12-27 and `realtime` starts 2025-01-03 — exactly 7 days,
  i.e. the two streams **abut without overlap or gap**.

Time-step counts:

| stream | files | steps | block layout | covers |
|---|---|---|---|---|
| `temp_stable`, `sal_stable` | 17 | 1670 | 16 × 100 + **70** | 1993-01-01 → 2024-12-27 |
| `temp_realtime`, `sal_realtime` | 1 | 49 | **49** | 2025-01-03 → 2025-12-05 |
| `temp_error`, `sal_error` | 18 | 1719 | 17 × 100 + **19** | 1993-01-01 → 2025-12-05 |

`stable + realtime = 1670 + 49 = 1719 = error`. The four science variables
therefore share one 1719-step axis exactly. That is the product this store
should expose.

## 3. The blocker: short blocks carry short chunks

`nodd.py` sets `chunksizes = min(CHUNKS[d], ds.sizes[d])` (line 583), so a short
final block gets a short time chunk. Measured: time chunk is 100 in full blocks,
but **70** in the stable tail, **49** in realtime, **19** in the error tail.

VirtualiZarr cannot concatenate arrays whose chunk shapes differ — Zarr has no
variable-length chunk grid. Verified on a synthetic replica of this exact layout:

```
ValueError: Cannot concatenate arrays with inconsistent chunk shapes:
(7, 1, 4, 4) vs (10, 1, 4, 4). Requires ZEP003 (Variable-length Chunks).
```

This is not fixable by any build-time setting, and not fixable by republishing
either: for `ocean_temperature` the 70-step chunk sits **in the middle** of the
merged axis (stable tail, then realtime), and a mid-array partial chunk is
illegal in Zarr no matter how the netCDF is written.

So a purely virtual store cannot span the seam. Three honest options — see §7.

## 4. Recommended design: 96 % virtual, materialized seam

One repository, one root group, one time axis:

```
dims     time 1719 (1993-01-01 → 2025-12-05, weekly)
         mean_pressure 58, latitude 720, longitude 1440
vars     ocean_temperature, ocean_salinity,
         ocean_temperature_error, ocean_salinity_error     float32
         chunks (100, 1, 180, 180), gzip-4 + shuffle       [inherited, not chosen]
coords   time, mean_pressure (+ mean_pressure_bnds), latitude, longitude
         data_mode(time)  int8 flag, 0 = stable, 1 = realtime
```

`data_mode` is the stable/realtime flag CLAUDE.md anticipated; it describes
`ocean_temperature` / `ocean_salinity` (the error variables are a single stream
over the whole period).

| region | how | files | bytes |
|---|---|---|---|
| `time[0:1600]`, all 4 vars | **virtual** | 64 | ~493 GB referenced |
| `time[1600:1700]`, error vars | **virtual** (error block 16) | 2 | ~18 GB referenced |
| `time[1600:1719]`, temp + sal | **materialized** (stable tail + realtime) | 4 | ~15.7 GB written |
| `time[1700:1719]`, error vars | **materialized** (error tail) | 2 | ~0.9 GB written |

≈ **16.6 GB written, ~511 GB referenced — 3.2 % of the product is copied**, and
that copy is confined to the last 119 weeks. Verified end-to-end on the
synthetic replica: virtual chunks and native chunks coexist in one Icechunk
array on a uniform chunk grid, and a slice straddling the seam returns values
identical to the source netCDFs.

Materialized chunks are written with the array's existing codecs, so the seam is
invisible to readers — no dtype, codec, or chunk-grid discontinuity.

## 5. Loadable vs virtual

Loadable (materialized, tiny): `time`, `latitude`, `longitude`, `mean_pressure`,
`mean_pressure_bnds`, plus the new `data_mode`. Virtual: the four science
variables only. `time` is written as a single chunk.

## 6. Predicted read performance

- **Metadata**: ~134 k chunk references (18 × 58 × 4 × 8 per variable × 4). Small
  for Icechunk; no manifest splitting needed.
- **Data reads hit the same bucket as the store**, so a reader on GCP is close to
  both halves. Off-cloud readers pay egress on the netCDFs, not on the manifest.
- **The chunk layout is inherited and cannot be changed.** `(100, 1, 180, 180)`
  is ~13 MB uncompressed / ~4 MB compressed, and it favours time series over a
  small tile: a 1719-step point series touches 18 chunks. A **single global map
  at one time and one level is the expensive query** — 32 chunks (4 × 8 tiles),
  each holding 100 time steps, ≈ 128 MB compressed read to deliver a 4 MB field.
  That is a property of the published netCDFs (issue #1), not of this store; the
  only fix would be a fully materialized store with a different chunking.
- Writers and readers should both set `zarr.config.set({"async.concurrency": 128})`.

## 7. The decision that needs a human

| | data covered | bytes copied | consumer experience |
|---|---|---|---|
| **A. hybrid (recommended)** | full 1719 steps | ~16.6 GB (3.2 %) | one dataset, four variables, one time axis |
| **B. pure virtual, truncated** | 1600 steps, ends **2023-08-25** | 0 | one dataset, but the last 2.3 years — including all of realtime — are missing |
| **C. pure virtual, multi-group** | full 1719 steps | 0 | 5 groups (bulk / stable-tail / realtime / error-bulk / error-tail); readers must `xr.concat` three pieces to get one series |

B is not a publishable product and C pushes the seam onto every consumer, so
the plan above assumes A.

## 8. Separate finding: `temp_error` netCDFs are labelled v2.2

All 18 published `temp_error` files carry `title = "RFROM v2.2"` and
`references = "Lyman, J.M. and G.C. Johnson. 2026. submitted"`, while every other
v2.3 stream says `title = "RFROM v2.3"` and the full "High Resolution Random
Field..." reference. It comes from the file-level global attributes ERDDAP serves
for `argo_rfromv23_temp_error` — ERDDAP's own dataset title is correct
("ARGO RFROM v2.3 Temperature error data"), so `nodd.py` passed through a bad
upstream attribute. Affects the **published netCDFs**, not just this store.
Same class as the salinity mislabel. Needs its own issue.

## 9. Versions used for this survey

icechunk 2.2.0, virtualizarr 2.2.1, xarray 2025.12.0, zarr 3.1.5, obstore 0.8.2,
gcsfs 2025.12.0, h5py 3.13.0, numpy 2.3.5, Python 3.12.12.

Note for the build code: `open_virtual_mfdataset` in 2.2.x takes an
`ObjectStoreRegistry` (not `object_store=`), and `vz.to_icechunk` has no
`encoding=` / `region=` parameter — only `append_dim`. Icechunk 2.2 wants
`ic.credentials.LocalFileSystemAccess` / `gcs_anonymous_credentials()` sentinels
rather than `None`.

## 10. Reusability (GOBAI-O2, RFROM v2.2)

The build script should be config-driven like `nodd.py`: a `STORES` dict naming,
per product, the source prefix, the streams that merge into each variable, and
the destination prefix. GOBAI HR fits the same shape (2 variables, 17 × 100 + 19,
so ~0.9 GB materialized). RFROM v2.2 does **not** fit cleanly: `temp_v22` ends
2024-12 (1670) while `sal_v22` ends 2025-12 (1719), so those two cannot share one
time axis without NaN-padding temperature — decide when we get there (issue #20
follow-up), don't pre-build for it.
