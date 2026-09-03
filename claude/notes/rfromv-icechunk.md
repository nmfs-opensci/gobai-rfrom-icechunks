# RFROM v2.3 → Icechunk (issue #17) — design record

Status: **design settled and verified end-to-end; waiting on the netCDF
restructure to be run before the real store can be built.** Produced with the
`virtual-icechunk` skill. Every number below was measured against the published
files on 2026-09-03, not assumed.

## 1. The two configurations

| | Source | Destination |
|---|---|---|
| what | the NODD netCDFs, left in place | the Icechunk repository |
| where | `gs://noaa-oar-rfrom/netcdf/v2.3/` | `gs://noaa-oar-rfrom/icechunk/v2.3` |
| write creds | none (read) | ADC, `NODD_GCS_TOKEN` |
| read creds | `ic.gcs_credentials(anonymous=True)` on the virtual container | `ic.gcs_storage(..., anonymous=True)` |

Same public bucket, still two independent settings. A reader that configures only
the repository gets metadata and no data. The container prefix
`gs://noaa-oar-rfrom/netcdf/v2.3/` must be authorized explicitly (wrapped in
`ic.containers_credentials`, which a bare `GcsCredentials` is rejected without)
and persisted with `repo.save_config()`.

## 2. What the source files are

All 72 v2.3 files were opened and their headers read. One weekly grid, 7-day
spacing, no gaps anywhere; identical `latitude` (720), `longitude` (1440),
`mean_pressure` (58, identical values in all 72), `mean_pressure_bnds` `(58, 2)`;
every data variable float32, gzip-4 + shuffle, `_FillValue` NaN, spatial chunk
`(1, 180, 180)`. `stable` ends 2024-12-27 and `realtime` starts 2025-01-03 —
exactly 7 days, so the two abut without overlap or gap, and
`stable + realtime = 1670 + 49 = 1719 = error`. All four science variables share
one 1719-step axis exactly.

## 3. The blocker

A virtual store cannot rewrite chunks, so every file feeding one array must share
one chunk grid — Zarr has no variable-length chunks. The published tree broke
that in two independent ways:

1. **`nodd.py` shrank the time chunk on short blocks** (`min(CHUNKS[d], size)`),
   so the stable tail had chunk 70, realtime 49, the error tail 19, against 100
   everywhere else.
2. **temperature and salinity were published as two series each** (stable +
   realtime). Merging them onto one axis would need a 70-long chunk in the
   *middle* of the time axis — illegal in Zarr however the netCDFs are written,
   and therefore not fixable downstream at all.

Measured, not assumed. Against stock `open_virtual_mfdataset`:

| final block | result |
|---|---|
| chunk shrunk to fit (what was published) | rejected — inconsistent chunk shapes |
| short block, chunk padded to full 100 | rejected — "partial chunks" |
| 100+19 in one file, chunk 100 | rejected — "partial chunks" |
| time chunk 1 everywhere | accepted |

The last row is why earlier virtual stores worked: with a chunk of 1 every length
is trivially a multiple of the chunk, so the constraint never bites.

## 4. The decision

Restructure the netCDFs (Eli, 2026-09-03) rather than paper over the seam in the
store. The alternative considered and rejected was a hybrid store — bulk virtual
plus ~16.6 GB of the tail materialized into Icechunk — which needed no netCDF
changes but copied data forever and rewrote ~16.6 GB on every realtime refresh.
The restructure costs about the same **once** and yields a 100 % virtual store,
plus a more coherent netCDF product.

Two sub-decisions, both taken after measuring:

- **Tail layout: short tail with a padded chunk.** The final block keeps the full
  100-step time chunk and is written with an unlimited time dimension, so HDF5
  pads the edge chunk. Weekly realtime refreshes then rewrite only the ~1.5 GB
  tail file; the 100-step blocks stay immutable. Measured overhead on the real
  19-step `temp_error` tail: **1.05×** (0.440 → 0.462 GB), values and attributes
  unchanged — the pad is fill value and compresses to nearly nothing. The
  alternative (one 119-step final file) rewrites ~9 GB per weekly update.
- **`data_mode` lives in the Icechunk store, not the files.** An int8 CF flag
  (`flag_values [0, 1]`, `flag_meanings "stable realtime"`) derived from one
  documented `realtime_start` date. Putting it in every netCDF would have meant
  rewriting the 36 untouched temp/sal blocks (~210 GB) to add a per-time flag,
  and promoting realtime weeks to stable becomes a one-line config change.

## 5. The store

```
dims     time 1719 (1993-01-01 → 2025-12-05, weekly)
         mean_pressure 58, latitude 720, longitude 1440
vars     ocean_temperature, ocean_salinity,
         ocean_temperature_error, ocean_salinity_error     float32, all virtual
         chunks (100, 1, 180, 180), gzip-4 + shuffle       [inherited, not chosen]
coords   time, mean_pressure (+ mean_pressure_bnds), latitude, longitude,
         data_mode(time)  int8, 0 = stable, 1 = realtime
```

100 % virtual: 72 files, ~526 GB referenced, nothing copied. The store itself is
a few MB.

`concat_virtual` / `concat_manifest_arrays` in `build_icechunk.py` join the chunk
manifests directly instead of going through `xr.concat`, because VirtualiZarr
requires every input — including the last — to be an exact multiple of the chunk
length on the concat axis. 1719 is not a multiple of 100 and no publishing
schedule will make it one. A trailing partial chunk is valid Zarr (edge chunks
are stored full size and cropped on read), so the helper checks the two
conditions Zarr does impose — one chunk shape everywhere, only the last file
short — and joins the manifests. **Worth an upstream issue**: VirtualiZarr's
`check_no_partial_chunks_on_concat_axis` also rejects the final input, which is
stricter than the spec needs.

## 6. Verified end to end

On real data, before any restructure was run:

- `nodd.py` rebuilt `temp_error` block 17 → shape (19, 58, 720, 1440), chunks
  (100, 1, 180, 180), `maxshape (None, ...)`, 0.462 GB;
- a store combining two **published** 100-step blocks from GCS with that padded
  tail → 219 steps, uniform grid, 3 chunks of 100 with a 19-step partial last one;
- read back through a fresh read-only session: all six sampled time steps match
  the source netCDFs, **including both ends of the padded tail**;
- store on disk 0.12 MB for 53 GB referenced.

`RFROMV/icechunk-smoke-test.ipynb` is that test, and it passes as written.

## 7. Read performance

- **Metadata**: ~134 k chunk references (18 × 58 × 4 × 8 per variable × 4). Small
  for Icechunk; no manifest splitting needed.
- **Data reads hit the same bucket as the store**, so a reader on GCP is close to
  both halves.
- **The chunk layout is inherited and cannot be changed by a virtual store.**
  `(100, 1, 180, 180)` favours time series over a small tile: a 1719-step point
  series touches 18 chunks. The expensive query is a **single global map at one
  time and level** — 32 chunks (4 × 8 tiles), each holding 100 time steps, ≈ 128 MB
  compressed read to deliver a 4 MB field. That is a property of the netCDFs
  (issue #1), not of this store; only a materialized store with different
  chunking would fix it. Worth revisiting at the next version reprocess.
- Writers and readers should both set `zarr.config.set({"async.concurrency": 128})`.
- Header parsing runs ~17 s per file and does not thread well (18 files took 311 s
  with 9 workers), so a full 72-file build is ~20 minutes. Processes instead of
  threads would help if that ever matters.
- gzip + shuffle map to **numcodecs** codecs, which are outside the Zarr v3 core
  spec: the store reads from zarr-python but may not open in other Zarr
  implementations. Document this for users.

## 8. Separate finding: `temp_error` netCDFs are labelled v2.2 (issue #25)

All 18 published `temp_error` files carry `title = "RFROM v2.2"` and
`references = "Lyman, J.M. and G.C. Johnson. 2026. submitted"`, while every other
v2.3 stream says v2.3 and the full "High Resolution Random Field..." reference. It
is a file-level global attribute ERDDAP serves for `argo_rfromv23_temp_error` —
ERDDAP's own dataset title is correct ("ARGO RFROM v2.3 Temperature error data"),
so `nodd.py` passed through a bad upstream attribute, and the newly rebuilt tail
block still has it. Affects the **published netCDFs**, not just this store. Same
class as the salinity mislabel. Tracked as **issue #25**, with the evidence that
it is a label and not a data problem: `title` and `references` are the only
attributes that differ from the other v2.3 streams, and there is no v2.2
temperature-error product on this grid at all (`argo_rfromv22_error` is
dimensioned on `depth` — it is the OHC anomaly product, issue #21). Deliberately
not fixed here, because overriding it on one rebuilt block would make that stream
inconsistent with its own other 17 files.

## 9. Reusability

`build_icechunk.py` is config-driven like `nodd.py`; `STORES` holds the bucket,
prefixes, stream→variable map and `realtime_start` per product.

- **GOBAI HR** is already configured (`gobai_hr`, 2 variables, 36 files, 246.6 GB)
  and fits the same shape — it needs only its two 19-step tails rewritten with a
  padded chunk before the store can be built.
- **RFROM v2.2** does not fit one store: `temp_v22` ends 2024-12 (1670 steps) and
  `sal_v22` ends 2025-12 (1719), and a Zarr group has one length per dimension.
  Two stores, or one store when the axes agree. Decide when we get there.

## 10. Versions

icechunk 2.2.0, virtualizarr 2.2.1, xarray 2025.12.0, zarr 3.1.5, obstore 0.8.2,
gcsfs 2025.12.0, h5py 3.13.0, numpy 2.3.5, Python 3.12.12.

API notes for these versions: `open_virtual_mfdataset` takes an
`ObjectStoreRegistry` (not `object_store=`); `vz.to_icechunk` has no `encoding=`
or `region=` parameter, only `append_dim`; `ic.gcs_anonymous_credentials` does not
exist (use `ic.gcs_credentials(anonymous=True)`); virtual-access maps must be
built with `ic.containers_credentials`; `ic.credentials.LocalFileSystemAccess` is
a sentinel value, not a constructor.
