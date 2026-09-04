# GOBAI — GOBAI HR → NODD pipeline

Prepares the **GOBAI HR** gridded oxygen and nitrate fields for the NOAA Open
Data Dissemination (NODD) GCP bucket `gs://noaa-oar-gobai`. Source files come
from PMEL ERDDAP; outputs are CF-compliant, rechunked, compressed netCDFs laid
out per stream under `netcdf/<version>/<stream>/`, ready to be virtualized into a
downstream Icechunk / VirtualiZarr store.

Despite the directory name, this covers **both** GOBAI streams — oxygen (`o2`)
and nitrate (`no3`).

Per output file the pipeline is:

```
ERDDAP monthly netCDFs
  → open_mfdataset combine
  → select one 100-time-step block
  → fix CF metadata
  → rechunk (100, 1, 180, 180) + zlib-4/shuffle
  → upload to gs://noaa-oar-gobai/netcdf/<version>/<stream>/
```

The 1719-step record splits into **18 blocks** (17 × 100 plus a final 19); output
files are named e.g. `GOBAI-O2-HR-v202606_1993-01-01_1994-11-25.nc`.

> **Not the same product as `gobai-o2-monthly-icechunk-sc.ipynb` in this
> directory.** That notebook builds GOBAI-O2 **v2.3 monthly** from NCEI and
> publishes it to Source Cooperative. This pipeline handles GOBAI **HR-v1.0,
> weekly** from PMEL ERDDAP, bound for NODD. Different version, different
> cadence, different source, different destination — do not mix their chunking
> or metadata decisions.

## The two product streams

| stream | ERDDAP dataset | data variable | units (source) | NODD prefix |
|---|---|---|---|---|
| `o2`  | [`gobai_o2_hr_v10`](https://data.pmel.noaa.gov/pmel/erddap/griddap/gobai_o2_hr_v10.html)   | `o2`  | micromole per kilogram | `netcdf/v202606/o2/` |
| `no3` | [`gobai_no3_hr_v10`](https://data.pmel.noaa.gov/pmel/erddap/griddap/gobai_no3_hr_v10.html) | `no3` | micromole per kilogram | `netcdf/v202606/no3/` |

Both run 1993-01-01 → 2025-12-05, weekly, 1719 steps, 396 monthly source files,
~0.41 TB per stream. There is **no stable/realtime/error split** — one stream per
variable, unlike RFROM's six.

### Version string

The prefix uses **`v202606`**, the version stamped inside the files themselves:
it appears both in every source filename (`GOBAI-O2-HR-v202606-1993-01.nc`) and
in each file's `title` global attribute (`"GOBAI-O2-HR-v202606"`). ERDDAP's
*dataset title* says `HR-v1.0` instead; the two disagree and the files win.
`--version` overrides the prefix if that is ever revisited.

### CF metadata overrides

The source files declare `Conventions = "CF-1.8"` but neither carries a
`standard_name` for its data variable, and both use the non-udunits units string
`"micromole per kilogram"`. The pipeline sets these (verified against **CF
standard name table v94**; **metadata only, values unchanged**):

| variable | source | published |
|---|---|---|
| `o2` | no `standard_name`, `micromole per kilogram` | `moles_of_oxygen_per_unit_mass_in_sea_water`, `umol kg-1` |
| `no3` | no `standard_name`, `micromole per kilogram` | `moles_of_nitrate_per_unit_mass_in_sea_water`, `umol kg-1` |
| `mean_pressure` | no `standard_name` | `sea_water_pressure`, `positive = "down"`, `axis = "Z"` |
| `time` / `latitude` / `longitude` | `standard_name` present | `axis` T / Y / X added |

Both CF names are the **per-mass** forms (canonical units `mol kg-1`), matching
the data. ⚠️ ERDDAP's own dataset config for `gobai_no3_hr_v10` advertises
`mole_concentration_of_nitrate_in_sea_water`, which is a **per-volume** quantity
(canonical `mol m-3`) and therefore inconsistent with the per-mass units; that
name is not in the netCDF files and is not used here. This is the same class of
upstream error as the RFROM salinity mislabel — **pending confirmation from the
data author**, and cheap to change since it is metadata only.

The source annotates variables with a non-standard `Description` attribute and no
`long_name`. The pipeline copies `Description` into `long_name` where none exists
(keeping `Description` as provenance) rather than inventing its own wording.

`comment = "preliminary"` and `references = "Sharp, et al. GOBAI High Resolution
Data Products, in prep."` are passed through **verbatim** — the published files
stay honest about the product's pre-publication status.

## The Icechunk store

[`../build_icechunk.py`](../build_icechunk.py) merges both streams into one
virtual Icechunk store at `gs://noaa-oar-gobai/icechunk/v202606` — one dataset,
one 1719-step weekly time axis, `o2` and `no3` side by side. It is **100 %
virtual**: the netCDFs stay exactly where they are, nothing is copied, and the
store itself is a few MB.

```sh
python build_icechunk.py --store gobai_hr --list                 # what gets referenced
python build_icechunk.py --store gobai_hr --local-repo /tmp/x    # full dry run, no upload
python build_icechunk.py --store gobai_hr                        # build and validate
python build_icechunk.py --store gobai_hr --validate             # re-check a built store
```

This is the same machinery as RFROM v2.3 (GitHub issue #17), because GOBAI HR
shares RFROM's grid and block layout; the `gobai_hr` entry in `STORES` differs
only in bucket, prefixes and variables. The design decisions, the measurements
behind them and the reader recipe are in
[`../claude/notes/rfromv-icechunk.md`](../claude/notes/rfromv-icechunk.md) —
read that rather than rediscovering them. Two differences from RFROM:

- **No `data_mode` coordinate.** GOBAI HR has no stable/realtime split, so
  `realtime_start` is `None` and no mode flag is written. RFROM's store has one
  because half its record is provisional.
- **Nothing to migrate.** The tree was published as one continuous series per
  variable from the start, so there is no equivalent of RFROM's `migrate_v23.py`
  and no old prefixes to retire.

Two constraints the netCDFs must satisfy, both enforced with named errors: every
file feeding one variable shares one chunk grid, and only the **last** file may
be short (written with an unlimited time dimension so HDF5 pads its edge chunk).
Zarr has no variable-length chunks, so a virtual store cannot paper over either.
Both GOBAI tails were originally published with the time chunk shrunk to 19 and
had to be rebuilt with `--force` before the store could be built at all (issue
#26); the padding cost 1.05× on disk, 1.30 → 1.37 GB.

### Reading the store

```python
import icechunk as ic, xarray as xr

SRC = "gs://noaa-oar-gobai/netcdf/v202606/"
repo = ic.Repository.open(
    ic.gcs_storage(bucket="noaa-oar-gobai", prefix="icechunk/v202606", anonymous=True),
    authorize_virtual_chunk_access=ic.containers_credentials({SRC: ic.gcs_credentials(anonymous=True)}),
)
ds = xr.open_zarr(repo.readonly_session("main").store, consolidated=False, chunks={})
```

Byte-for-byte the snippet on the landing page. If reads feel slow, raise Zarr's
default concurrency of 10 — for readers and writers alike:
`import zarr; zarr.config.set({"async.concurrency": 128})`.

The store and the netCDFs it references are **two independent credential
settings**, even though both live in the same public bucket. A reader that
configures only the repository gets metadata and no data — the
`authorize_virtual_chunk_access` argument above is what makes the byte ranges
readable.

**Compatibility caveat**, inherited from the netCDFs: the arrays carry
`numcodecs.shuffle` + `numcodecs.zlib`, which are *extension* codecs in the Zarr
v3 registry rather than core spec codecs. **The store reads from zarr-python;
other Zarr implementations may refuse it.** zarr-python warns to this effect on
every build and open — expected, and nothing to fix in the store. Background and
the fallback plan are in §8 of
[`../claude/notes/rfromv-icechunk.md`](../claude/notes/rfromv-icechunk.md).

**Reading needs `icechunk`, `zarr` and `xarray` — not `virtualizarr`.**
VirtualiZarr is a *build*-time dependency: it parses the HDF5 headers into chunk
manifests. Nothing at read time goes near it, so do not put it in a consumer's
install line.

```sh
pip install icechunk zarr xarray          # to read the store
```

To *build* a store you need the full set, which is **not** in
[`../requirements.txt`](../requirements.txt) — that file covers `nodd.py` only:

```sh
pip install -r ../requirements.txt -r ../requirements-icechunk.txt
```

## Reading the published data

Everything below is anonymous — the bucket is open data, so no account, no
credentials, no quota. This mirrors the public landing page,
[`index.html`](index.html), served at
<https://storage.googleapis.com/noaa-oar-gobai/index.html>. **Keep the two in
step**: if you change the code here, change it there and re-upload.

There are two routes in, returning identical values because the store holds no
copy of the data:

| | Where | For |
|---|---|---|
| Icechunk store | `gs://noaa-oar-gobai/icechunk/v202606` | the whole 1719-week record, both variables, as one dataset — Python only |
| netCDF files | `gs://noaa-oar-gobai/netcdf/v202606/` | ordinary netCDF-4, 18 files per variable, any language |

For the store, see ["Reading the store"](#reading-the-store) above.

### Reading the netCDFs directly

```sh
pip install xarray gcsfs h5netcdf
```

```python
import xarray as xr

url = "gs://noaa-oar-gobai/netcdf/v202606/o2/GOBAI-O2-HR-v202606_1993-01-01_1994-11-25.nc"
ds = xr.open_dataset(url, engine="h5netcdf", storage_options={"token": "anon"}, chunks={})
```

To browse the bucket by eye, use the Google Cloud console — the bucket is
public, so no project or permissions are needed, but the console does require a
Google sign-in:
<https://console.cloud.google.com/storage/browser/noaa-oar-gobai/netcdf/v202606/>

With no account at all, the XML listing works anonymously
(`https://storage.googleapis.com/noaa-oar-gobai?prefix=netcdf/v202606/&delimiter=/`),
and so does this:

```python
import gcsfs
gcsfs.GCSFileSystem(token="anon").ls("noaa-oar-gobai/netcdf/v202606/o2")
```

Note that `https://storage.googleapis.com/noaa-oar-gobai/netcdf/v202606/o2/` —
the plain prefix path — returns 404. That is not a permissions problem; that
endpoint just does not do directory listings.

Unlike RFROM's, these file names **do** sort chronologically: there is no
stable/realtime infix, so the date in the name is the first thing that varies.

### Reading from R

R can read these files **without downloading them**. Appending `#mode=bytes` to
the HTTPS URL makes netCDF fetch only the byte ranges it needs — measured on the
hub against a 7.5 GB RFROM file on the identical grid: `nc_open` in 4.3 s, a 4×4
slice in 0.9 s.

```r
install.packages("ncdf4")
library(ncdf4)

u <- paste0("https://storage.googleapis.com/noaa-oar-gobai/netcdf/v202606/o2/",
            "GOBAI-O2-HR-v202606_1993-01-01_1994-11-25.nc#mode=bytes")
nc <- nc_open(u)
x  <- ncvar_get(nc, "o2", start = c(600, 300, 1, 1), count = c(4, 4, 1, 1))
nc_close(nc)
```

Two things that bite:

- **Dimension order is reversed** relative to Python:
  `(longitude, latitude, mean_pressure, time)`. Getting this wrong returns data
  rather than an error, so it fails silently.
- **Byte-range is a netCDF-C build option** (`--enable-byterange`). Most builds
  have it; if yours does not, `nc_open` fails with an unknown-file-format or
  inaccessible-DAP message and the file must be downloaded first:

```r
u <- paste0("https://storage.googleapis.com/noaa-oar-gobai/netcdf/v202606/o2/",
            "GOBAI-O2-HR-v202606_1993-01-01_1994-11-25.nc")
download.file(u, "gobai.nc", mode = "wb")   # ~7.1 GB
nc <- nc_open("gobai.nc")
```

  Files run 1.4–7.3 GB each, so avoid this where you can.

`RNetCDF::open.nc()` streams the same way. There is no Icechunk reader for R, so
the store is Python-only.

## Files in this directory

### Deliverables

- **`../nodd.py`** — the batch script, shared with RFROMV. Handles all eight
  streams (RFROM's six plus GOBAI's two); `--stream o2` / `--stream no3` select
  these. See "Running the batch script" below.
- **`README.md`** — this file.

- **`index.html`** — the public landing page, uploaded to
  `gs://noaa-oar-gobai/index.html` and served at
  <https://storage.googleapis.com/noaa-oar-gobai/index.html>. It documents what
  is *in the bucket*, i.e. GOBAI HR-v1.0; the monthly GOBAI-O2 product appears
  only as a pointer to its NCEI accession. Keep its code blocks in step with
  ["Reading the published data"](#reading-the-published-data) above, and note
  that it is CDN-cached for an hour — verify an upload with `?cb=$RANDOM` or a
  stale copy reads as a failed upload.

### Unrelated to this pipeline

- **`gobai-o2-monthly-icechunk-sc.ipynb`** — the GOBAI-O2 v2.3 monthly → Source
  Cooperative Icechunk build. A different product; see the note above.

## Why one script for two products

GOBAI HR is built on RFROM, and the coordinates are **identical**: opening a
GOBAI file next to `RFROMV23_TEMP_STABLE_1993_01.nc` shows `latitude` (720),
`longitude` (1440), `mean_pressure` (58) and `mean_pressure_bnds`
`(mean_pressure, vertices)` matching value-for-value, same float32 dtype, on the
same weekly time grid — RFROM's 1670-step stable axis is an exact prefix of
GOBAI's 1719. The array shapes, the contiguous on-disk layout, and therefore the
chunking, compression and I/O strategy are all the same, so the two products
share `nodd.py` rather than forking it.

One caveat if these are ever combined into a single Icechunk store: GOBAI HR
declares `source = "Argo float data, GLODAP ship data, RFROM v2.2"` — it is built
on RFROM **v2.2**, while the RFROM NODD product is **v2.3**. The grids match; the
underlying field versions do not.

## Environment

Identical to RFROMV's — same dependencies, same credentials mechanism, same
scratch layout. The full walkthrough (venv install, gcloud auth, tmux for long
runs) is in [`../setup.md`](../setup.md), or run `python ../nodd.py --setup` to
print it; everything there applies unchanged except the bucket you need write
access to, which is `gs://noaa-oar-gobai`. The dependency manifest,
`requirements.txt`, lives at the repo root next to `nodd.py` and covers both
products.

Two environment variables override the JupyterHub defaults so the script runs on
a bare VM or a laptop:

| variable | default | meaning |
|---|---|---|
| `NODD_SCRATCH_DIR` | `/home/jovyan/shared-public/gobai-scratch` (GOBAI streams) | download + output scratch; needs ~35 GB free |
| `NODD_GCS_TOKEN` | `~/.config/gcloud/application_default_credentials.json` (hub path) | credentials JSON path, **or** the keyword `google_default` to resolve ADC the usual way |

The older `RFROM_SCRATCH_DIR` / `RFROM_GCS_TOKEN` names are still honoured. Note
the scratch **default** is product-specific (`gobai-scratch` vs
`rfromv-scratch`), so GOBAI and RFROM runs on the same hub do not collide; an
explicit `NODD_SCRATCH_DIR` overrides both.

## Running the batch script

`nodd.py` requires an explicit `--stream` **and** an explicit `--blocks RANGE` or
`--all` — nothing is processed implicitly. Run one stream at a time (one VM per
stream, or split a stream across VMs with disjoint `--blocks` ranges). It is
idempotent: before writing a block it checks whether the target object already
exists in the bucket and skips it unless `--force`, so a resume-after-interrupt
or a second VM on the same stream is safe. Downloads resume the same way — a
monthly file is streamed to a `.part` file, checked that it opens as netCDF, and
only then renamed into place, with 4 retries on flaky ERDDAP reads.

```sh
# Plan only: print the block → monthly-file cross-walk, download nothing.
python nodd.py --stream o2 --list

# Smoke-test one block end to end. Block 17 is the cheapest (19 steps, 5 files).
python nodd.py --stream o2 --blocks 17 --no-upload --keep-scratch

# Process a single block and upload it.
python nodd.py --stream o2 --blocks 0

# Process EVERY block in the stream and upload them (a typical production run,
# one stream per VM). Idempotent: already-uploaded blocks are skipped.
python nodd.py --stream o2 --all
python nodd.py --stream no3 --all

# Split a stream across two VMs (disjoint block ranges).
python nodd.py --stream no3 --blocks 0-8      # VM A
python nodd.py --stream no3 --blocks 9-17     # VM B
```

The run prints the resolved scratch directory and destination prefix at startup —
check those two lines before walking away. Run `python ../nodd.py --help` for
the full flag reference; `--version` defaults to `v202606` for these streams.

### Resource expectations (per stream)

The arrays are exactly the same size as RFROM's, so these track the measured
RFROM `temp_stable` run:

| | |
|---|---|
| monthly source files per block | 23 (~1 GB each, ~23 GB); block 17 has 5 |
| output file per block | ~7–8 GB |
| peak scratch disk | ~31 GB (one block, default cleanup) |
| blocks per stream | 18 |
| total downloaded per stream | ~410 GB |
| total uploaded per stream | ~130 GB |
| RAM | 8 GB minimum, 16 GB comfortable |

Wall-clock is dominated by download and upload, so it tracks network throughput
far more than CPU — a bigger instance does not speed it up.

## Notes

Reconnaissance, the measured comparison against RFROM, and the resolved design
decisions are written up in
[`../claude/notes/gobai-nodd.md`](../claude/notes/gobai-nodd.md). The rationale
for each pipeline stage — why `data_vars="minimal"`, why
`chunks={"mean_pressure": 1}` — is in
[`../claude/notes/nodd-prep.md`](../claude/notes/nodd-prep.md) and
[`../claude/notes/nodd-batch-script.md`](../claude/notes/nodd-batch-script.md).
