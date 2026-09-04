# RFROM v2.3 → Icechunk (issue #17) — design record

Status: **done — the store is published at `gs://noaa-oar-rfrom/icechunk/v2.3`
and verified anonymously** (§6.2). The netCDF restructure is complete; the
rehearsal failure recorded in §6.1 is resolved. Produced with the
`virtual-icechunk` skill. Every number below was measured against the published
files on 2026-09-03/04, not assumed.

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

### 6.1 First full rehearsal build, 2026-09-04 — fails on `temp_error` block 17

`python build_icechunk.py --store rfrom_v23 --local-repo <dir>` against the
restructured tree:

```
ocean_temperature   18 files -> (1719, 58, 720, 1440) chunks (100, 1, 180, 180)  (257s)
ocean_salinity      18 files -> (1719, 58, 720, 1440) chunks (100, 1, 180, 180)  (341s)
ocean_temperature_error: ValueError -- the files do not share one time chunk: [19, 100]
```

`concat_virtual`'s guard fired exactly as designed and named the block. Surveying
all 36 error-stream files by header:

| stream | chunk grid | issue #25 title |
|---|---|---|
| `temp` | uniform, tail padded to 100 | n/a |
| `sal` | uniform, tail padded to 100 | n/a |
| `sal_error` | uniform, tail padded to 100 | n/a |
| `temp_error` | **block 17 still chunk 19** | **blocks 3-17 still say v2.2** |

Reconstructed from bucket object timestamps: the temp/sal migration copy ran
2026-09-03 23:21, their seam and tail blocks were rebuilt 23:48-00:35, and
`sal_error`'s tail at 00:42 — all correct. A `temp_error --force` re-run then
started 2026-09-04 01:14 to apply the issue #25 title correction, completed
blocks 0, 1 and 2 at ~26 min each, and **died at 02:13 partway through block 3**,
leaving 12 GB of scratch at `/home/jovyan/shared-public/rfromv-scratch/erddap/`
ending in `RFROMV23_TEMP_ERROR_1999_09.nc.part`. No process was running 13 hours
later; the run is dead, not slow. Cause of death unknown — nothing was captured.

So `temp_error` is the only stream left, and it needs the re-run finished:

    python nodd.py --stream temp_error --blocks 3-17 --force

Two separate reasons, and they want different scopes — worth deciding
deliberately rather than by accident:

- **Block 17 alone blocks the store.** Its time chunk is 19, not 100. Nothing
  downstream can work around it.
- **Blocks 3-16 are only a metadata inconsistency.** They still carry the issue
  #25 v2.2 `title`/`references` while blocks 0-2 now say v2.3. Rebuilding just
  block 17 unblocks the Icechunk build but leaves the published stream
  self-inconsistent: 0-2 and 17 saying v2.3, 3-16 saying v2.2. That is worse
  than the uniform-but-wrong state it started in, so finishing the whole
  3-17 range is the coherent choice. ~15 blocks x ~26 min = **~6.5 hours**.

Downloads are resumable (complete monthly files are skipped, the `.part` file
restarts), so the re-run does not repeat the 12 GB already fetched for block 3.

### 6.2 Real build published, 2026-09-04

`python build_icechunk.py --store rfrom_v23` → **`gs://noaa-oar-rfrom/icechunk/v2.3`**,
snapshot `ERWNPYN2CDCJ5SGHHBBG`. All four streams concatenated cleanly once
`temp_error` was rebuilt (259 / 279 / 298 / 271 s of header parsing). The store is
**20 objects, 2.38 MB**, referencing 526.6 GB of netCDFs — nothing copied.

```
chunks 3 · manifests 10 (1.95 MB) · overwritten 2 · repo 1 · snapshots 2 · transactions 2 (0.41 MB)
```

Verified **anonymously**, with the exact recipe in `RFROMV/README.md` and no
credentials: opens, reports 1719 × 58 × 720 × 1440, `data_mode` 1670 stable /
49 realtime, and real data reads back through the virtual byte-range references
(first-step and last-step samples). `--validate` from a fresh process checks both
endpoints of the first and last source file of all four variables: 16/16 match.

**Bug found and fixed by this build.** The in-build validation failed with
`GroupNotFoundError` even though the commit had succeeded, because `build()`
passed the *writer's* repository object to `validate()`. On object storage that
object's branch pointer still resolved to the initial empty snapshot
(`1CECHNKREP0F1RSTCMT0`, `branch: None`) immediately after the commit; on a local
filesystem it did not, which is why every rehearsal passed. `build()` now reopens
the repository with `create=False` before validating — which is what "validate
from the consumer read path" was always supposed to mean. The published store was
never affected: it was correct all along, and re-validating from a fresh process
proved it.


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
  implementations. Full analysis and the fallback plan in §8.

## 8. Codec compatibility: the store does not open in every Zarr implementation

The virtual arrays carry `numcodecs.shuffle` + `numcodecs.zlib`, and zarr-python
warns on every build:

> Numcodecs codecs are not in the Zarr version 3 specification and may not be
> supported by other zarr implementations.

**This is forced, not chosen.** The NODD netCDFs are written with HDF5's deflate
filter at level 4 plus the shuffle filter. HDF5's deflate emits a *zlib*-framed
stream (RFC 1950 — 2-byte header, Adler-32 trailer); Zarr v3's core `gzip` codec
is defined on *gzip* framing (RFC 1952 — magic bytes, CRC32 trailer). Same
DEFLATE underneath, incompatible containers, so a decoder pointed at one with the
other fails. VirtualiZarr maps the filter accordingly — `parsers/hdf/filters.py`
carries the literal `"gzip": "zlib"` entry and `codecs.py` prefixes the
`numcodecs.` namespace. Shuffle has no standalone codec in the v3 core at all
(blosc's internal shuffle is a different byte layout), so it lands on
`numcodecs.shuffle` for the same reason.

A virtual store holds byte-range references into HDF5 files it does not own and
cannot rewrite, so the codec chain must describe those bytes exactly as they are.
**Any codec chain that does not raise this warning is a chain that cannot decode
this data.** Nothing in `build_icechunk.py` can act on it, and it is left
unsilenced deliberately — it fires ~3 times per build (Python dedups it), which
is not enough noise to be worth a filter.

`numcodecs.*` is a registered extension namespace in the Zarr v3 extension
registry, so the store is spec-conformant: it uses extension codecs rather than
core ones. zarr-python decodes it, which covers the intended consumers
(xarray + icechunk from Python). Support in other implementations varies by
codec and by version.

### If this becomes a real problem

The fix is on the **netCDF** side, not in the store. Eli's call, 2026-09-04:
if the codecs turn out to block real consumers, **publish the NODD netCDFs
uncompressed**. An uncompressed netCDF virtualizes to the core `bytes` codec
alone and the store opens anywhere.

Cost, computed from the array shape rather than estimated: 1719 x 58 x 720 x 1440
float32 is **413.5 GB per variable, 1.65 TB for the four**, against 526.6 GB
published today — **3.1x the storage**, plus a full reprocess of all 72 blocks.
Per stream the ratio runs 2.7x (`temp_error`) to 4.3x (`sal`).

Options considered, and why the others lose:

| option | outcome |
|---|---|
| drop shuffle, keep deflate | still `numcodecs.zlib` — the warning does not go away |
| rewrite with blosc via `hdf5plugin` | core `blosc` codec, no warning, **but** the netCDFs then need a filter plugin to open at all, which guts the primary NODD deliverable |
| **uncompressed netCDFs** | core `bytes` only, store opens anywhere, 3.1x storage |
| materialize the store with core codecs | copies 526 GB and abandons the virtual design entirely |

Same category as the chunk shape in §7: a property inherited from the netCDFs,
revisitable only at a reprocess, and constrained there by netCDF readability
rather than by Zarr. If a reprocess ever happens for chunking reasons, settle the
codec question in the same pass.

### Checked against gridlook, 2026-09-04 — the codecs are not the problem

[gridlook](https://github.com/d70-t/gridlook) is the concrete test case: a
browser WebGL viewer for cloud-hosted Zarr. Desk investigation only — nothing was
rendered, because the store does not exist yet.

**Verdict: the codec chain is fine, and two other things block it.**

The exact chain the store writes, dumped from a real virtual dataset rather than
assumed:

```
{"name": "bytes",             "configuration": {"endian": "little"}}
{"name": "numcodecs.shuffle", "configuration": {"elementsize": 4}}
{"name": "numcodecs.zlib",    "configuration": {"level": 4}}
```

gridlook pins `zarrita ^0.7.4`, and that version's codec registry
(`packages/zarrita/src/codecs.ts` at tag `zarrita@0.7.4`) registers **all three**,
`numcodecs.zlib` and `numcodecs.shuffle` included, with its own
`./codecs/zlib.js` and `./codecs/shuffle.js` implementations. gridlook also
already depends on `icechunk-js ^0.6.0`, a read-only Icechunk reader for the
browser that supports **virtual** chunk payloads and rewrites `gs://bucket/key`
to `https://storage.googleapis.com/bucket/key`. Icechunk support landed in
gridlook 1.1.0 (2026-06-01), icechunk-group datasets in 1.3.0.

So the "may not be supported by other zarr implementations" warning does **not**
describe this consumer. Note that this does not generalize: zarrita having the
codecs says nothing about zarrs (Rust), zarr-java, or TensorStore.

**Blocker 1 — CORS. RESOLVED 2026-09-04 by Eli, who is a bucket admin.**

Originally neither NODD bucket had any CORS policy: anonymous ranged GETs
returned HTTP 206 and honoured `content-range`, but no `Access-Control-Allow-Origin`
came back, the `OPTIONS` preflight carried no `access-control-*` headers at all,
and the bucket metadata `cors` field was unset. A browser refuses both halves in
that state — the Icechunk metadata *and* the netCDF byte ranges.

Both `noaa-oar-rfrom` and `noaa-oar-gobai` now carry:

```json
[{"origin": ["*"],
  "method": ["HEAD", "GET"],
  "responseHeader": ["Range", "Content-Type", "Content-Length", "Content-Range"],
  "maxAgeSeconds": 3600}]
```

Verified on both: preflight returns 200 with
`access-control-allow-headers: Range,Content-Type,Content-Length,Content-Range`,
and a ranged GET returns 206 with `access-control-allow-origin: *` and an
`access-control-expose-headers` covering `Content-Range`/`Content-Length`/`Range`.
One policy covers store and netCDFs because they share a bucket.

Two things that matter if this is ever redone:

- **`Range` in `responseHeader` is mandatory.** Every chunk read is a byte-range
  request; `Range` is not CORS-safelisted, so the browser preflights it and GCS
  only allows it if `Range` is listed. Omit it and nothing loads, with a generic
  CORS error that does not mention ranges.
- **Do not put `OPTIONS` in `method`.** GCS answers preflights itself and echoes
  back exactly the methods configured. (An older NMFS bucket,
  `nmfs_odp_nwfsc`, does list it; harmless but unnecessary.)

**Still verified only with curl, not a browser.** The server now returns correct
CORS headers. Whether gridlook renders the store is untested.

**Blocker 2 — the chunk shape, which is much worse in a browser.** A single
global map at one time and one level touches 32 chunks (4 x 8 tiles), each
holding 100 time steps: **~131 MB compressed to download and ~415 MB
decompressed in JS memory, to draw one 4 MB field.** Scrubbing time inside one
100-step block is then cached, but crossing a block boundary re-fetches the lot.
This is §7's read-performance finding, and it is the thing that would actually
make gridlook feel broken. It is a property of the netCDFs, not of the store.

**Not yet checked at all**, and cheap to settle once a store exists: whether
gridlook handles a 4D dataset with a 58-level vertical dimension and offers a
level selector; whether it needs a per-dataset catalog/config JSON; whether it
handles rectilinear lat/lon as well as the healpix grids it is built around; CF
time decoding in-browser; and whether `icechunk-js` 0.6.0 reads the on-disk
format `icechunk` 2.2.0 (Python) writes.

**Consequence for the uncompressed-netCDF fallback in §8.** Publishing
uncompressed would remove a non-problem and make blocker 2 strictly worse
(~415 MB per map frame with no compression on the wire). If browser
visualisation becomes a real requirement, the answer is not uncompressed
netCDFs — it is a **separate materialized, map-chunked store** built for that
purpose, alongside the virtual one. Do not spend 3.1x storage on the codec
question for gridlook's sake.

## 9. Separate finding: `temp_error` netCDFs are labelled v2.2 (issue #25)

All 18 published `temp_error` files carried `title = "RFROM v2.2"` and
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
dimensioned on `depth` — it is the OHC anomaly product, issue #21).

**Resolved in `nodd.py`** (commit 83a4d04): a stream entry may carry a
`global_attrs` dict overriding file globals, and `temp_error` uses it to restore
the v2.3 title and the full citation. Metadata only, values untouched, and the
correction is recorded in each file's `history`. Applying it means **rewriting
every `temp_error` block**, which is why it rode along with the issue #17
restructure rather than being done on its own.

That rewrite is **incomplete**: blocks 0-2 carry the corrected v2.3 title, blocks
3-17 still say v2.2, because the re-run died. See §6.1 — this is now the same
piece of work as unblocking the store.

## 10. Reusability

`build_icechunk.py` is config-driven like `nodd.py`; `STORES` holds the bucket,
prefixes, stream→variable map and `realtime_start` per product.

- **GOBAI HR** is already configured (`gobai_hr`, 2 variables, 36 files, 246.6 GB)
  and fits the same shape — it needs only its two 19-step tails rewritten with a
  padded chunk before the store can be built.
- **RFROM v2.2** does not fit one store: `temp_v22` ends 2024-12 (1670 steps) and
  `sal_v22` ends 2025-12 (1719), and a Zarr group has one length per dimension.
  Two stores, or one store when the axes agree. Decide when we get there.

## 11. Versions

icechunk 2.2.0, virtualizarr 2.2.1, xarray 2025.12.0, zarr 3.1.5, obstore 0.8.2,
gcsfs 2025.12.0, h5py 3.13.0, numpy 2.3.5, Python 3.12.12.

API notes for these versions: `open_virtual_mfdataset` takes an
`ObjectStoreRegistry` (not `object_store=`); `vz.to_icechunk` has no `encoding=`
or `region=` parameter, only `append_dim`; `ic.gcs_anonymous_credentials` does not
exist (use `ic.gcs_credentials(anonymous=True)`); virtual-access maps must be
built with `ic.containers_credentials`; `ic.credentials.LocalFileSystemAccess` is
a sentinel value, not a constructor.
