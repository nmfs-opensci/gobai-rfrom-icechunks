# gobai-icechunks

Data-engineering notebooks and scripts that turn ocean-data source files into
cloud-native published products: NODD-bound netCDFs and materialized Icechunk /
VirtualiZarr Zarr stores. Two areas, unrelated products:

- **`RFROMV/`** — RFROM v2.3 gridded Argo temperature/salinity → NODD.
- **`GOBAI-O2/`** — GOBAI HR-v1.0 weekly oxygen/nitrate → NODD, and GOBAI-O2
  v2.3 monthly → Source Cooperative.

## Running `nodd.py`

`nodd.py` (repo root) is the NODD batch script for both RFROM and GOBAI HR
streams — pulls monthly ERDDAP netCDFs, blocks/rechunks/compresses them, and
uploads to the product's NODD bucket. Run one stream at a time:

```sh
python nodd.py --stream temp --list      # preview the block plan, no download
python nodd.py --stream temp --blocks 0  # smoke-test one block
python nodd.py --stream temp --all       # production run, one VM per stream
```

- `python nodd.py --help` — every flag, defaults, and more examples.
- `python nodd.py --setup` — full off-hub setup walkthrough (Python env,
  scratch disk, GCS credentials, long-run tips); same content as
  [`setup.md`](setup.md).
- [`RFROMV/README.md`](RFROMV/README.md) / [`GOBAI-O2/README.md`](GOBAI-O2/README.md)
  — per-product stream tables and CF metadata notes.

## Building the Icechunk stores

`build_icechunk.py` (repo root) turns a published netCDF tree into a **virtual**
Icechunk store: Zarr metadata and byte-range references only, so the netCDFs stay
where they are and nothing is copied. One store merges every stream of a product
into a single dataset on one time axis.

```sh
python build_icechunk.py --store rfrom_v23 --list               # what gets referenced
python build_icechunk.py --store rfrom_v23 --local-repo /tmp/x  # dry run, no upload
python build_icechunk.py --store rfrom_v23                      # build and validate
```

Run `RFROMV/icechunk-smoke-test.ipynb` before the first real build. The design
record — including why the netCDF chunking had to change first — is in
[`claude/notes/rfromv-icechunk.md`](claude/notes/rfromv-icechunk.md).

## Chunking

CEFI uses 100, 10, 200, 200 (time, depth, lat, lon). coords: time: 390, lat: 815, lon: 341, z_l: 52

GOBAI-O2 on NCEI pres: 58, lat: 145, lon: 360

RFROM

The results are near-global ¼-degree x ¼-degree x 7-day resolution maps of OHCA for ten different depth layers.

time: 5, mean_pressure: 58, latitude: 720, longitude: 1440

physical = (100, 1, 180, 180) ≈ 13 MB

