#!/usr/bin/env python3
"""Batch-process RFROM v2.3 ERDDAP netCDFs into NODD-bound files and upload them.

This is the script form of the tested single-file notebook
``RFROMV/prep-one-netcdf-for-NODD.ipynb`` (GitHub issue #1), generalized to all
six RFROM v2.3 product streams (GitHub issue #5). Per output file the pipeline is:

    ERDDAP monthly netCDFs -> open_mfdataset combine -> select one BLOCK_SIZE-step
    block -> CF metadata fix -> rechunk (100,1,180,180) + zlib-4/shuffle
    -> upload to gs://noaa-oar-rfrom/netcdf/<version>/<stream>/

Operational model (see claude/notes/nodd-batch-script.md): streams are run ONE AT
A TIME, likely on several VMs in parallel (one VM per stream, or one VM per block
range). The script therefore:

  * requires an explicit ``--stream`` (never processes everything implicitly);
  * requires an explicit ``--blocks RANGE`` or ``--all`` (never all blocks implicitly);
  * is idempotent / multi-VM safe: before writing a block it checks whether the
    target object already exists in the bucket and skips it unless ``--force``,
    so a resume-after-interrupt or a second VM on the same stream is safe.

Examples
--------
    # Plan only: print the block -> monthly-file cross-walk, download nothing.
    python rfrom_nodd.py --stream temp_stable --list

    # Process a single block and upload it.
    python rfrom_nodd.py --stream temp_stable --blocks 0

    # Split a stream across two VMs.
    python rfrom_nodd.py --stream sal_stable --blocks 0-8     # VM A
    python rfrom_nodd.py --stream sal_stable --blocks 9-16    # VM B

    # Whole stream, no upload (local test), keep the scratch files.
    python rfrom_nodd.py --stream temp_realtime --all --no-upload --keep-scratch

Environment
-----------
The defaults assume the JupyterHub. Two paths are overridable so the script also
runs on a bare VM or a laptop: ``RFROM_SCRATCH_DIR`` (download + output scratch,
needs ~35 GB free) and ``RFROM_GCS_TOKEN`` (a credentials JSON path, or the
keyword "google_default" to resolve ADC the usual way). See RFROMV/README.md,
"Running off-hub", for the venv + credentials setup.

The hard-won correctness / performance choices from the notebook are preserved
verbatim; see claude/notes/nodd-prep.md for the why. In short: open with
``data_vars="minimal", coords="minimal", compat="override"`` so ``mean_pressure_bnds``
is not broadcast against time (do NOT alter the source data), and read with dask
``chunks={"mean_pressure": 1}`` (contiguous lat/lon planes) so the write is not
I/O-bound on the unchunked source files.
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
import requests
import xarray as xr
import gcsfs

# --------------------------------------------------------------------------- #
# Fixed configuration (stream-agnostic).                                       #
# --------------------------------------------------------------------------- #

BLOCK_SIZE = 100  # time steps per output file (the 1670-step stable record -> 17 blocks)

# Physical (on-disk) chunk sizes, in variable dim order (time, mean_pressure,
# latitude, longitude): 100 * 1 * 180 * 180 * 4 bytes ~= 12.96 MB per chunk.
CHUNKS = {"time": 100, "mean_pressure": 1, "latitude": 180, "longitude": 180}

# Scratch / local paths. erddap/ holds monthly source downloads; nodd/ holds the
# assembled output files before upload. The default is the JupyterHub shared
# volume; off-hub (bare VM, laptop) set RFROM_SCRATCH_DIR to any writable path
# with room for ~35 GB (one block's downloads plus its output).
SCRATCH_DIR = os.path.expanduser(
    os.environ.get("RFROM_SCRATCH_DIR", "/home/jovyan/shared-public/rfromv-scratch")
)
DOWNLOAD_DIR = os.path.join(SCRATCH_DIR, "erddap")
OUTPUT_DIR = os.path.join(SCRATCH_DIR, "nodd")

# NODD (GCP) destination: netcdf/<version>/<stream>/<file>.nc
# GCS_TOKEN is passed straight to gcsfs: either a path to a credentials JSON
# (the default is where `gcloud auth application-default login` writes on the
# hub) or a gcsfs token keyword -- "google_default" resolves ADC the normal way,
# including GOOGLE_APPLICATION_CREDENTIALS. Override with RFROM_GCS_TOKEN.
GCS_TOKEN = os.environ.get(
    "RFROM_GCS_TOKEN",
    "/home/jovyan/.config/gcloud/application_default_credentials.json",
)
if os.sep in GCS_TOKEN or GCS_TOKEN.startswith("~"):
    GCS_TOKEN = os.path.expanduser(GCS_TOKEN)
NODD_BUCKET = "noaa-oar-rfrom"
NODD_NETCDF_DIR = "netcdf"
DEFAULT_VERSION = "v2.3"

ERDDAP_FILES = "https://data.pmel.noaa.gov/pmel/erddap/files"
ERDDAP_GRIDDAP = "https://data.pmel.noaa.gov/pmel/erddap/griddap"

# --------------------------------------------------------------------------- #
# The six streams. This dict is the ONE place stream differences live.         #
#                                                                              #
# All confirmed against ERDDAP 2026-09-02. Grid is (time, mean_pressure,       #
# latitude, longitude) float32 with (mean_pressure, nv) mean_pressure_bnds for #
# every stream. ``monthly_template`` matches the exact file names ERDDAP        #
# serves (realtime files keep the STABLE prefix and append _REALTIME; error    #
# files use an _ERROR_ infix and are a single continuous 1993->2025 series      #
# with no realtime split). ``out_template`` mirrors that naming for the block   #
# output files, with the block's first/last date substituted for {start}/{end}. #
#                                                                              #
# standard_name notes:                                                          #
#   * Temperature: source Description says "conservative temperature (TEOS-10)" #
#     -> sea_water_conservative_temperature (a valid CF standard name).         #
#   * Salinity: the data are absolute salinity (TEOS-10) in g/kg -- confirmed   #
#     by the data author. The ERDDAP variable metadata (standard_name=          #
#     sea_water_practical_salinity, units=PSU) is a known upstream mistake      #
#     the author cannot fix, so we override to sea_water_absolute_salinity /    #
#     grams_per_kilogram (matching ocean_salinity_error). Values unchanged.     #
#   * Error vars use the CF modifier form <name> standard_error.                #
#     ocean_salinity_error is absolute salinity in g/kg, so its modifier base   #
#     is sea_water_absolute_salinity (units-consistent).                        #
# --------------------------------------------------------------------------- #

STREAMS = {
    "temp_stable": {
        "dataset_id": "argo_rfromv23_temp",
        "data_var": "ocean_temperature",
        "var_attrs": {
            "standard_name": "sea_water_conservative_temperature",
            "units": "degree_Celsius",
        },
        "monthly_template": "RFROMV23_TEMP_STABLE_{year}_{month:02d}.nc",
        "out_template": "RFROMV23_TEMP_STABLE_{start}_{end}.nc",
    },
    "temp_realtime": {
        "dataset_id": "argo_rfromv23_temp_realtime",
        "data_var": "ocean_temperature",
        "var_attrs": {
            "standard_name": "sea_water_conservative_temperature",
            "units": "degree_Celsius",
        },
        "monthly_template": "RFROMV23_TEMP_STABLE_{year}_{month:02d}_REALTIME.nc",
        "out_template": "RFROMV23_TEMP_STABLE_{start}_{end}_REALTIME.nc",
    },
    "temp_error": {
        "dataset_id": "argo_rfromv23_temp_error",
        "data_var": "ocean_temperature_error",
        "var_attrs": {
            "standard_name": "sea_water_conservative_temperature standard_error",
            "units": "degree_Celsius",
        },
        "monthly_template": "RFROMV23_TEMP_ERROR_{year}_{month:02d}.nc",
        "out_template": "RFROMV23_TEMP_ERROR_{start}_{end}.nc",
    },
    "sal_stable": {
        "dataset_id": "argo_rfromv23_sal",
        "data_var": "ocean_salinity",
        "var_attrs": {
            "standard_name": "sea_water_absolute_salinity",
            "units": "grams_per_kilogram",
        },
        "monthly_template": "RFROMV23_SAL_STABLE_{year}_{month:02d}.nc",
        "out_template": "RFROMV23_SAL_STABLE_{start}_{end}.nc",
    },
    "sal_realtime": {
        "dataset_id": "argo_rfromv23_sal_realtime",
        "data_var": "ocean_salinity",
        "var_attrs": {
            "standard_name": "sea_water_absolute_salinity",
            "units": "grams_per_kilogram",
        },
        "monthly_template": "RFROMV23_SAL_STABLE_{year}_{month:02d}_REALTIME.nc",
        "out_template": "RFROMV23_SAL_STABLE_{start}_{end}_REALTIME.nc",
    },
    "sal_error": {
        "dataset_id": "argo_rfromv23_sal_error",
        "data_var": "ocean_salinity_error",
        "var_attrs": {
            "standard_name": "sea_water_absolute_salinity standard_error",
            "units": "grams_per_kilogram",
        },
        "monthly_template": "RFROMV23_SAL_ERROR_{year}_{month:02d}.nc",
        "out_template": "RFROMV23_SAL_ERROR_{start}_{end}.nc",
    },
}

# Coordinate / global metadata that is identical for every stream.
COORD_ATTRS = {
    "mean_pressure": {
        "standard_name": "sea_water_pressure",
        "units": "decibar",
        "positive": "down",
        "axis": "Z",
    },
    "latitude": {"standard_name": "latitude", "units": "degrees_north", "axis": "Y"},
    "longitude": {"standard_name": "longitude", "units": "degrees_east", "axis": "X"},
    "time": {"standard_name": "time", "axis": "T", "long_name": "Time"},
}


# --------------------------------------------------------------------------- #
# Small helpers                                                                #
# --------------------------------------------------------------------------- #

def clean_utf8(v):
    """Repair source string attributes that were read as lone surrogates.

    Some source attrs (e.g. ``references``, which contains an en-dash) hold valid
    UTF-8 *bytes* that xarray decoded with surrogateescape, producing lone
    surrogates that h5netcdf then refuses to write. Re-encode to the original
    bytes and decode as proper UTF-8.
    """
    if isinstance(v, str):
        return v.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    return v


def erddap_time_axis(dataset_id):
    """Read the full time axis for a griddap dataset as a tz-naive DatetimeIndex.

    Row 0 is the header and row 1 is the units row (skipped). ERDDAP returns
    tz-aware UTC times (trailing "Z") but the netCDF files decode to tz-naive
    datetimes, so strip the tz to make the two comparable when slicing.
    """
    url = f"{ERDDAP_GRIDDAP}/{dataset_id}.csv?time"
    times = pd.read_csv(url, skiprows=[1])
    times["time"] = pd.to_datetime(times["time"], utc=True).dt.tz_localize(None)
    return pd.DatetimeIndex(times["time"])


def make_file_blocks(stream, times, block_size=BLOCK_SIZE):
    """Split the time axis into blocks and map each to its monthly source files.

    Returns a list of dicts with keys: block, start, end, n_times, filename, urls.
    """
    cfg = STREAMS[stream]
    files_url = f"{ERDDAP_FILES}/{cfg['dataset_id']}"
    times = pd.DatetimeIndex(times)
    blocks = []
    for i in range(0, len(times), block_size):
        block_times = times[i:i + block_size]
        months = block_times.to_period("M").unique()
        urls = [
            f"{files_url}/" + cfg["monthly_template"].format(year=m.year, month=m.month)
            for m in months
        ]
        start, end = block_times[0], block_times[-1]
        filename = cfg["out_template"].format(
            start=f"{start:%Y-%m-%d}", end=f"{end:%Y-%m-%d}"
        )
        blocks.append({
            "block": i // block_size,
            "start": start,
            "end": end,
            "n_times": len(block_times),
            "filename": filename,
            "urls": urls,
        })
    return blocks


def download(url, dest, chunk=16 * 1024 * 1024):
    """Download url -> dest, skipping if a complete copy already exists."""
    head = requests.head(url, timeout=60)
    head.raise_for_status()
    remote_size = int(head.headers.get("Content-Length", 0))
    if os.path.exists(dest) and remote_size and os.path.getsize(dest) == remote_size:
        print(f"    skip (have) {os.path.basename(dest)}")
        return dest
    tmp = dest + ".part"
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for block_bytes in r.iter_content(chunk_size=chunk):
                f.write(block_bytes)
    os.replace(tmp, dest)
    print(f"    got  {os.path.basename(dest)}  ({os.path.getsize(dest) / 1e9:.2f} GB)")
    return dest


def download_block(block):
    """Download all monthly source files for one block; return local paths."""
    local_files = []
    for url in block["urls"]:
        dest = os.path.join(DOWNLOAD_DIR, url.split("/")[-1])
        local_files.append(download(url, dest))
    total_gb = sum(os.path.getsize(f) for f in local_files) / 1e9
    print(f"    downloaded {len(local_files)} files, {total_gb:.1f} GB")
    return local_files


def build_dataset(stream, local_files, block, version):
    """Open + combine the block's monthly files, slice to the block, fix CF metadata.

    Returns (ds, encoding) ready for ``to_netcdf``.
    """
    cfg = STREAMS[stream]
    data_var = cfg["data_var"]

    # data_vars="minimal" keeps mean_pressure_bnds as (mean_pressure, nv) instead of
    # broadcasting it along time (i.e. does NOT alter the source data). compat="override"
    # takes coords/bounds from the first file (identical across files, verified).
    # chunks={"mean_pressure": 1} makes each dask read a whole contiguous lat/lon plane
    # at one pressure level -- the file's natural layout -- which is what keeps the write
    # from thrashing on strided I/O.
    ds = xr.open_mfdataset(
        local_files,
        engine="h5netcdf",
        combine="by_coords",
        data_vars="minimal",
        coords="minimal",
        compat="override",
        chunks={"mean_pressure": 1},
    )

    # Trim to exactly this block's time steps (boundary months carry a few extra).
    ds = ds.sel(time=slice(block["start"], block["end"]))
    assert ds.sizes["time"] == block["n_times"], (
        f"expected {block['n_times']} times, got {ds.sizes['time']}"
    )

    # --- CF-compliant metadata ------------------------------------------------
    stamp = pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    ds[data_var].attrs.update(cfg["var_attrs"])
    for coord, attrs in COORD_ATTRS.items():
        ds[coord].attrs.update(attrs)
    # The source carries a stale, contradictory time "Description" ("days since 1950");
    # the real encoding is seconds since 1970 (set below), so drop it.
    ds["time"].attrs.pop("Description", None)

    ds.attrs["Conventions"] = "CF-1.10, ACDD-1.3"
    note = (
        f"{stamp}: repackaged for NODD ({version}) from ERDDAP {cfg['dataset_id']} "
        f"monthly files; rechunked to "
        f"{tuple(CHUNKS[d] for d in ('time', 'mean_pressure', 'latitude', 'longitude'))} "
        f"(time, mean_pressure, latitude, longitude)."
    )
    ds.attrs["history"] = note + "\n" + ds.attrs.get("history", "")

    # Repair any mojibake so h5netcdf can write the attributes (global + per-variable).
    ds.attrs = {k: clean_utf8(v) for k, v in ds.attrs.items()}
    for var in ds.variables:
        ds[var].attrs = {k: clean_utf8(v) for k, v in ds[var].attrs.items()}

    # --- Rechunk + on-disk encoding ------------------------------------------
    var_dims = ds[data_var].dims
    # On-disk chunk sizes, capped at each dim so the short final block stays valid.
    chunksizes = tuple(min(CHUNKS[d], ds.sizes[d]) for d in var_dims)
    # Dask chunks are kept LARGER than the on-disk chunks -- a full lat/lon plane per
    # (time-block, pressure) -- so each dask task reads a contiguous slab from the source
    # rather than many strided sub-tiles. They are exact multiples of chunksizes, so each
    # on-disk chunk is written exactly once (no partial-chunk read-modify-write).
    dask_chunks = {
        "time": min(CHUNKS["time"], ds.sizes["time"]),
        "mean_pressure": 1,
        "latitude": ds.sizes["latitude"],
        "longitude": ds.sizes["longitude"],
    }
    ds = ds.chunk(dask_chunks)

    encoding = {
        data_var: {
            "chunksizes": chunksizes,
            "dtype": "float32",
            "_FillValue": np.float32(np.nan),
            "zlib": True,
            "complevel": 4,
            "shuffle": True,
        },
        "time": {
            "units": "seconds since 1970-01-01T00:00:00Z",
            "dtype": "float64",
            "_FillValue": None,
        },
    }
    # CF: coordinate variables should not carry a _FillValue (xarray adds one to float
    # coords by default), so suppress it explicitly on each coordinate.
    for coord in ("mean_pressure", "latitude", "longitude"):
        encoding[coord] = {"_FillValue": None}

    print(f"    dims={dict(ds.sizes)}  on-disk chunks={chunksizes}")
    return ds, encoding


def write_netcdf(ds, encoding, out_path):
    """Write ds to out_path with the given encoding."""
    print(f"    writing {out_path} ...")
    ds.to_netcdf(out_path, engine="h5netcdf", encoding=encoding, mode="w")
    print(f"    wrote {os.path.getsize(out_path) / 1e9:.2f} GB")


# --------------------------------------------------------------------------- #
# Per-block driver                                                             #
# --------------------------------------------------------------------------- #

def process_block(stream, block, version, fs, nodd_dest, do_upload, force, keep_scratch):
    """Process one block end to end. Returns "written", "skipped", or "uploaded"."""
    filename = block["filename"]
    dest = f"{nodd_dest}/{filename}"
    print(f"[{stream} block {block['block']:2d}] {filename} "
          f"({block['n_times']} steps, {len(block['urls'])} monthly files)")

    # Idempotency / multi-VM safety: skip a block already in the bucket unless --force.
    if do_upload and not force and fs.exists(dest):
        print(f"    skip: already in bucket ({dest})")
        return "skipped"

    local_files = download_block(block)
    ds, encoding = build_dataset(stream, local_files, block, version)
    out_path = os.path.join(OUTPUT_DIR, filename)
    write_netcdf(ds, encoding, out_path)
    ds.close()

    result = "written"
    if do_upload:
        print(f"    uploading -> {dest}")
        fs.put(out_path, dest)
        info = fs.info(dest)
        print(f"    uploaded {filename} ({info['size'] / 1e9:.2f} GB)")
        result = "uploaded"

    if not keep_scratch:
        # Remove this block's monthly downloads and the local output. Boundary months
        # shared with the next block are re-downloaded (download() is idempotent); this
        # keeps scratch bounded when processing many blocks.
        for f in local_files:
            try:
                os.remove(f)
            except OSError:
                pass
        if do_upload:
            try:
                os.remove(out_path)
            except OSError:
                pass
        print("    cleaned scratch for this block")

    return result


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def parse_blocks(spec, n_blocks):
    """Parse a --blocks spec ("3", "0-4", "0,2,5", "0-2,7") into a sorted index list."""
    indices = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo, hi = token.split("-", 1)
            lo, hi = int(lo), int(hi)
            if lo > hi:
                raise ValueError(f"bad block range '{token}': start > end")
            indices.update(range(lo, hi + 1))
        else:
            indices.add(int(token))
    out_of_range = sorted(i for i in indices if i < 0 or i >= n_blocks)
    if out_of_range:
        raise ValueError(
            f"block index/indices {out_of_range} out of range (0-{n_blocks - 1})"
        )
    return sorted(indices)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Process RFROM v2.3 ERDDAP netCDFs into NODD-bound files and upload.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--stream", required=True, choices=sorted(STREAMS),
                   help="Product stream to process (required; one at a time).")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--blocks", metavar="RANGE",
                     help="Blocks to process, e.g. '3', '0-4', '0,2,5'. "
                          "Split a stream across VMs with disjoint ranges.")
    grp.add_argument("--all", action="store_true",
                     help="Process every block in the stream.")
    p.add_argument("--version", default=DEFAULT_VERSION,
                   help=f"RFROM product version prefix (default {DEFAULT_VERSION}).")
    p.add_argument("--list", action="store_true",
                   help="Print the block -> monthly-file plan and exit (no download).")
    p.add_argument("--no-upload", action="store_true",
                   help="Build files locally but do not upload (implies keeping output).")
    p.add_argument("--force", action="store_true",
                   help="Reprocess/overwrite even if the target object already exists.")
    p.add_argument("--keep-scratch", action="store_true",
                   help="Do not delete downloaded monthly files / local outputs.")
    args = p.parse_args(argv)

    stream = args.stream
    print(f"Stream: {stream}  ({STREAMS[stream]['dataset_id']})")

    times = erddap_time_axis(STREAMS[stream]["dataset_id"])
    blocks = make_file_blocks(stream, times)
    print(f"{len(times)} time steps -> {len(blocks)} blocks\n")

    if args.list:
        for b in blocks:
            print(f"[{b['block']:2d}] {b['filename']}  "
                  f"({b['n_times']} steps, {len(b['urls'])} monthly files)  "
                  f"{b['start'].date()} -> {b['end'].date()}")
        return 0

    if not args.all and not args.blocks:
        p.error("specify --blocks RANGE or --all (nothing processed by default)")
    selected = list(range(len(blocks))) if args.all else parse_blocks(args.blocks, len(blocks))
    print(f"Selected blocks: {selected}\n")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"Scratch: {SCRATCH_DIR}")

    do_upload = not args.no_upload
    nodd_dest = f"gs://{NODD_BUCKET}/{NODD_NETCDF_DIR}/{args.version}/{stream}"
    # Fail before downloading ~23 GB if the credentials are not where we expect.
    if do_upload and os.sep in GCS_TOKEN and not os.path.exists(GCS_TOKEN):
        p.error(
            f"credentials file not found: {GCS_TOKEN}\n"
            "Run `gcloud auth application-default login`, or point RFROM_GCS_TOKEN at a "
            "credentials JSON (or set it to 'google_default' to use ADC / "
            "GOOGLE_APPLICATION_CREDENTIALS)."
        )
    fs = gcsfs.GCSFileSystem(token=GCS_TOKEN) if do_upload else None
    if do_upload:
        print(f"Destination: {nodd_dest}")

    tally = {"uploaded": 0, "written": 0, "skipped": 0}
    for i in selected:
        result = process_block(
            stream, blocks[i], args.version, fs, nodd_dest,
            do_upload=do_upload, force=args.force, keep_scratch=args.keep_scratch,
        )
        tally[result] += 1
        print()

    print(f"Done. uploaded={tally['uploaded']} written={tally['written']} "
          f"skipped={tally['skipped']} (of {len(selected)} selected).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
