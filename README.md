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
python nodd.py --stream temp_stable --list    # preview the block plan, no download
python nodd.py --stream temp_stable --blocks 0   # smoke-test one block
python nodd.py --stream temp_stable --all        # production run, one VM per stream
```

- `python nodd.py --help` — every flag, defaults, and more examples.
- `python nodd.py --setup` — full off-hub setup walkthrough (Python env,
  scratch disk, GCS credentials, long-run tips); same content as
  [`setup.md`](setup.md).
- [`RFROMV/README.md`](RFROMV/README.md) / [`GOBAI-O2/README.md`](GOBAI-O2/README.md)
  — per-product stream tables and CF metadata notes.

## Chunking

CEFI uses 100, 10, 200, 200 (time, depth, lat, lon). coords: time: 390, lat: 815, lon: 341, z_l: 52

GOBAI-O2 on NCEI pres: 58, lat: 145, lon: 360

RFROM

The results are near-global ¼-degree x ¼-degree x 7-day resolution maps of OHCA for ten different depth layers.

time: 5, mean_pressure: 58, latitude: 720, longitude: 1440

physical = (100, 1, 180, 180) ≈ 13 MB

