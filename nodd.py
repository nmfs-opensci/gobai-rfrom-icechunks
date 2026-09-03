#!/usr/bin/env python3
"""Batch-process PMEL ERDDAP netCDFs into NODD-bound files and upload them.

Covers two products that share a grid and therefore share this pipeline:

  * **RFROM** gridded Argo temperature/salinity -- v2.3 (six streams), v2.2
    (temp_v22, sal_v22) and v2.1 (temp_v21), to
    ``gs://noaa-oar-rfrom/netcdf/<version>/<stream>/`` (GitHub issues #1, #5, #20).
    v2.2/v2.1 are each a single continuous series -- no realtime/error split
    (that split exists only for a *different* v2.2 product, Ocean Heat Content
    anomaly, which is out of scope here; see GitHub issue #21).
  * **GOBAI HR** gridded oxygen and nitrate -- two streams, to
    ``gs://noaa-oar-gobai/netcdf/<version>/<stream>/`` (GitHub issue #13).

GOBAI HR is built on RFROM, and its latitude / longitude / mean_pressure /
mean_pressure_bnds coordinates are *identical* to RFROM v2.3's, on the same
weekly time grid -- so one code path serves both. (Note this is GOBAI **HR-v1.0
weekly**, not the v2.3 monthly product from NCEI that
``GOBAI-O2/gobai-o2-monthly-icechunk-sc.ipynb`` publishes to Source Cooperative.)

This is the script form of the tested single-file notebook
``RFROMV/prep-one-netcdf-for-NODD.ipynb``. Per output file the pipeline is:

    ERDDAP monthly netCDFs -> open_mfdataset combine -> select one BLOCK_SIZE-step
    block -> CF metadata fix -> rechunk (100,1,180,180) + zlib-4/shuffle
    -> upload to gs://<bucket>/netcdf/<version>/<stream>/

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
    python nodd.py --stream temp_stable --list

    # Process a single block and upload it.
    python nodd.py --stream temp_stable --blocks 0

    # Split a stream across two VMs.
    python nodd.py --stream sal_stable --blocks 0-8     # VM A
    python nodd.py --stream sal_stable --blocks 9-16    # VM B

    # GOBAI oxygen, whole stream.
    python nodd.py --stream o2 --all

    # Whole stream, no upload (local test), keep the scratch files.
    python nodd.py --stream temp_realtime --all --no-upload --keep-scratch

Environment
-----------
The defaults assume the JupyterHub. Two paths are overridable so the script also
runs on a bare VM or a laptop: ``NODD_SCRATCH_DIR`` (download + output scratch,
needs ~35 GB free) and ``NODD_GCS_TOKEN`` (a credentials JSON path, or the
keyword "google_default" to resolve ADC the usual way). The older ``RFROM_``-
prefixed names are still honoured. Run ``--setup`` (or see setup.md) for the
venv + credentials walkthrough.

The hard-won correctness / performance choices from the notebook are preserved
verbatim; see claude/notes/nodd-prep.md for the why. In short: open with
``data_vars="minimal", coords="minimal", compat="override"`` so ``mean_pressure_bnds``
is not broadcast against time (do NOT alter the source data), and read with dask
``chunks={"mean_pressure": 1}`` (contiguous lat/lon planes) so the write is not
I/O-bound on the unchunked source files.
"""

import argparse
import os
import pydoc
import sys
import time
from pathlib import Path

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

# NODD (GCP) destination: gs://<bucket>/netcdf/<version>/<stream>/<file>.nc
NODD_NETCDF_DIR = "netcdf"

# The two products. A product fixes the destination bucket, the default version
# prefix, and the default scratch location; STREAMS entries name their product.
#
# ``cf_refinements`` turns on two CF fixes that postdate the first RFROM upload:
#   * copy the source's non-standard ``Description`` into a CF ``long_name``
#     where none exists (both authors write ``Description`` and no ``long_name``);
#   * suppress the ``_FillValue`` xarray adds to ``mean_pressure_bnds`` -- CF
#     ch. 7.1 says a boundary variable should not have one, and it has no
#     missing values anyway.
# ON for GOBAI, OFF for RFROM: RFROM blocks are already in the bucket without
# them, and adding them mid-stream would make that published tree inconsistent
# with itself. Turn it on for RFROM at the next version reprocess.
PRODUCTS = {
    "rfrom": {
        "bucket": "noaa-oar-rfrom",
        "default_version": "v2.3",
        "scratch_default": "/home/jovyan/shared-public/rfromv-scratch",
        "cf_refinements": False,
    },
    "gobai": {
        "bucket": "noaa-oar-gobai",
        # The version stamped inside the files (both the filenames and the
        # ``title`` global attribute). ERDDAP's dataset title says "HR-v1.0"
        # instead; the files are the authority here.
        "default_version": "v202606",
        "scratch_default": "/home/jovyan/shared-public/gobai-scratch",
        "cf_refinements": True,
    },
}

# Scratch / local paths. erddap/ holds monthly source downloads; nodd/ holds the
# assembled output files before upload. The default is the JupyterHub shared
# volume and depends on the product, so these are resolved per run by
# ``configure_paths()``; off-hub (bare VM, laptop) set NODD_SCRATCH_DIR to any
# writable path with room for ~35 GB (one block's downloads plus its output).
SCRATCH_DIR = None
DOWNLOAD_DIR = None
OUTPUT_DIR = None

# GCS_TOKEN is passed straight to gcsfs: either a path to a credentials JSON
# (the default is where `gcloud auth application-default login` writes on the
# hub) or a gcsfs token keyword -- "google_default" resolves ADC the normal way,
# including GOOGLE_APPLICATION_CREDENTIALS. Override with NODD_GCS_TOKEN.
GCS_TOKEN = (
    os.environ.get("NODD_GCS_TOKEN")
    or os.environ.get("RFROM_GCS_TOKEN")  # pre-#13 name, still honoured
    or "/home/jovyan/.config/gcloud/application_default_credentials.json"
)
if os.sep in GCS_TOKEN or GCS_TOKEN.startswith("~"):
    GCS_TOKEN = os.path.expanduser(GCS_TOKEN)


def configure_paths(stream):
    """Resolve the scratch paths for a stream's product. Call once from main()."""
    global SCRATCH_DIR, DOWNLOAD_DIR, OUTPUT_DIR
    default = PRODUCTS[STREAMS[stream]["product"]]["scratch_default"]
    SCRATCH_DIR = os.path.expanduser(
        os.environ.get("NODD_SCRATCH_DIR")
        or os.environ.get("RFROM_SCRATCH_DIR")  # pre-#13 name, still honoured
        or default
    )
    DOWNLOAD_DIR = os.path.join(SCRATCH_DIR, "erddap")
    OUTPUT_DIR = os.path.join(SCRATCH_DIR, "nodd")

# Download resilience. ERDDAP reads go flaky on long runs (GitHub issue #11), and a
# single failed read used to kill the whole run. DOWNLOAD_TIMEOUT is a per-read
# socket timeout, not a whole-file deadline; the files stream in a few minutes each.
DOWNLOAD_ATTEMPTS = 4          # 1 initial try + 3 retries
DOWNLOAD_TIMEOUT = 120         # seconds, per socket read
RETRY_BACKOFF = 15             # seconds; doubles each retry (15, 30, 60)

ERDDAP_FILES = "https://data.pmel.noaa.gov/pmel/erddap/files"
ERDDAP_GRIDDAP = "https://data.pmel.noaa.gov/pmel/erddap/griddap"

# --------------------------------------------------------------------------- #
# The streams. This dict is the ONE place stream differences live.            #
#                                                                              #
# --- RFROM v2.3 (six streams) ------------------------------------------------#
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
        "product": "rfrom",
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
        "product": "rfrom",
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
        "product": "rfrom",
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
        "product": "rfrom",
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
        "product": "rfrom",
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
        "product": "rfrom",
        "dataset_id": "argo_rfromv23_sal_error",
        "data_var": "ocean_salinity_error",
        "var_attrs": {
            "standard_name": "sea_water_absolute_salinity standard_error",
            "units": "grams_per_kilogram",
        },
        "monthly_template": "RFROMV23_SAL_ERROR_{year}_{month:02d}.nc",
        "out_template": "RFROMV23_SAL_ERROR_{start}_{end}.nc",
    },

    # --- RFROM v2.3 combined temp / sal (issue #17) ---------------------------- #
    #
    # The same weeks as temp_stable + temp_realtime, published as ONE continuous
    # series instead of two, because the downstream Icechunk store cannot join
    # them otherwise. PMEL splits the record because the 2025 weeks are still
    # provisional; that distinction is preserved in the Icechunk store as a
    # data_mode(time) flag derived from ``realtime_start`` (see icechunk.py),
    # rather than by splitting the files.
    #
    # Why one series and not two: virtualizing a store means concatenating each
    # file's chunk grid, and Zarr has no variable-length chunks. temp_stable ends
    # mid-block (1670 steps = 16x100 + 70), so a merged temp array would need a
    # 70-long chunk in the MIDDLE of its time axis -- illegal in Zarr no matter
    # how the netCDFs are written. Merging the two records at the netCDF layer is
    # what makes the store possible at all. See claude/notes/rfromv-icechunk.md.
    #
    # Blocks 0-15 (time steps 0-1599) are byte-for-byte the same data as
    # temp_stable's blocks 0-15: same weeks, same monthly sources, same CF pass.
    # Copy them across server-side rather than re-downloading ~200 GB from
    # ERDDAP -- see "Restructuring an existing tree" in RFROMV/README.md.
    "temp": {
        "product": "rfrom",
        "data_var": "ocean_temperature",
        "var_attrs": {
            "standard_name": "sea_water_conservative_temperature",
            "units": "degree_Celsius",
        },
        "sources": [
            {"dataset_id": "argo_rfromv23_temp",
             "monthly_template": "RFROMV23_TEMP_STABLE_{year}_{month:02d}.nc"},
            {"dataset_id": "argo_rfromv23_temp_realtime",
             "monthly_template": "RFROMV23_TEMP_STABLE_{year}_{month:02d}_REALTIME.nc"},
        ],
        "out_template": "RFROMV23_TEMP_{start}_{end}.nc",
    },
    "sal": {
        "product": "rfrom",
        "data_var": "ocean_salinity",
        "var_attrs": {
            "standard_name": "sea_water_absolute_salinity",
            "units": "grams_per_kilogram",
        },
        "sources": [
            {"dataset_id": "argo_rfromv23_sal",
             "monthly_template": "RFROMV23_SAL_STABLE_{year}_{month:02d}.nc"},
            {"dataset_id": "argo_rfromv23_sal_realtime",
             "monthly_template": "RFROMV23_SAL_STABLE_{year}_{month:02d}_REALTIME.nc"},
        ],
        "out_template": "RFROMV23_SAL_{start}_{end}.nc",
    },

    # --- RFROM v2.2 / v2.1 (three streams) ------------------------------------ #
    #
    # GitHub issue #20. Confirmed against ERDDAP and one downloaded monthly file
    # of each dataset, 2026-09-03: grid (mean_pressure 58, latitude 720,
    # longitude 1440) and mean_pressure_bnds are bit-identical to v2.3.
    #
    # Gotcha the issue's original URLs got wrong: argo_rfromv22 / _realtime /
    # _error are NOT temp/sal -- they are a different product, Ocean Heat
    # Content anomaly, on a different vertical grid (mean_depth, 10 levels).
    # See issue #21. The real v2.2 temp/sal analogs are argo_rfromv22_temp and
    # argo_rfromv22_sal, and unlike v2.3 there is no realtime/error sibling for
    # either -- each is one continuous series (confirmed: no
    # argo_rfromv22_{temp,sal}_{realtime,error} dataset exists on ERDDAP).
    # v2.1 has temperature only, no salinity dataset at all.
    #
    # Each entry sets "version" to override PRODUCTS["rfrom"]["default_version"]
    # (v2.3) -- these are older versions, not the current default, and getting
    # this wrong would upload v2.2/v2.1 files into the v2.3 tree.
    #
    # temp_v22 and sal_v22 don't even end on the same date (2024-12 vs
    # 2025-12) -- real, confirmed on ERDDAP, not a bug in this script.
    "temp_v22": {
        "product": "rfrom",
        "version": "v2.2",
        "dataset_id": "argo_rfromv22_temp",
        "data_var": "ocean_temperature",
        "var_attrs": {
            "standard_name": "sea_water_conservative_temperature",
            "units": "degree_Celsius",
        },
        "monthly_template": "RFROMV22_TEMP_STABLE_{year}_{month:02d}.nc",
        "out_template": "RFROMV22_TEMP_STABLE_{start}_{end}.nc",
    },
    "sal_v22": {
        "product": "rfrom",
        "version": "v2.2",
        "dataset_id": "argo_rfromv22_sal",
        "data_var": "ocean_salinity",
        # Same TEOS-10 mislabel as v2.3's sal streams (Description says
        # "absolute salinity (TEOS-10)"; ERDDAP's standard_name/units say
        # sea_water_practical_salinity / PSU) -- same override, values unchanged.
        "var_attrs": {
            "standard_name": "sea_water_absolute_salinity",
            "units": "grams_per_kilogram",
        },
        "monthly_template": "RFROMV22_SAL_STABLE_{year}_{month:02d}.nc",
        "out_template": "RFROMV22_SAL_STABLE_{start}_{end}.nc",
    },
    "temp_v21": {
        "product": "rfrom",
        "version": "v2.1",
        "dataset_id": "argo_rfromv21_temp",
        "data_var": "ocean_temperature",
        "var_attrs": {
            "standard_name": "sea_water_conservative_temperature",
            "units": "degree_Celsius",
        },
        # v2.1 filenames have no STABLE infix (unlike v2.3/v2.2).
        "monthly_template": "RFROMV21_TEMP_{year}_{month:02d}.nc",
        "out_template": "RFROMV21_TEMP_{start}_{end}.nc",
    },

    # --- GOBAI HR (two streams) ---------------------------------------------- #
    #
    # Confirmed against ERDDAP and against one downloaded monthly file of each
    # dataset, 2026-09-03 (claude/notes/gobai-nodd.md). Same grid, dims and
    # mean_pressure_bnds as RFROM above; 1719 weekly steps -> 18 blocks.
    #
    # standard_name notes: neither netCDF file carries a standard_name for its
    # data variable, and units are the non-udunits string "micromole per
    # kilogram". These are per-MASS quantities, so the CF names below (verified
    # against standard name table v94, canonical units mol kg-1) are the right
    # ones. Beware ERDDAP's own dataset config for gobai_no3_hr_v10, which
    # declares mole_concentration_of_nitrate_in_sea_water -- a per-VOLUME name
    # (mol m-3) inconsistent with the data's per-mass units, and absent from the
    # file itself. Pending confirmation from the data author.
    #
    # The v202606 in the templates is the version stamped in the source
    # filenames and in each file's ``title``; it is deliberately part of the
    # file name, independent of the --version prefix.
    "o2": {
        "product": "gobai",
        "dataset_id": "gobai_o2_hr_v10",
        "data_var": "o2",
        "var_attrs": {
            "standard_name": "moles_of_oxygen_per_unit_mass_in_sea_water",
            "units": "umol kg-1",
        },
        "monthly_template": "GOBAI-O2-HR-v202606-{year}-{month:02d}.nc",
        "out_template": "GOBAI-O2-HR-v202606_{start}_{end}.nc",
    },
    "no3": {
        "product": "gobai",
        "dataset_id": "gobai_no3_hr_v10",
        "data_var": "no3",
        "var_attrs": {
            "standard_name": "moles_of_nitrate_per_unit_mass_in_sea_water",
            "units": "umol kg-1",
        },
        "monthly_template": "GOBAI-NO3-HR-v202606-{year}-{month:02d}.nc",
        "out_template": "GOBAI-NO3-HR-v202606_{start}_{end}.nc",
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


def stream_sources(stream):
    """The ERDDAP datasets a stream draws from, in time order.

    Most streams have exactly one. A *combined* stream lists several: RFROM v2.3
    temperature and salinity are each published by PMEL as a stable record plus a
    realtime extension that continues it, and the NODD product joins the two into
    one continuous series (see the ``temp`` entry, and issue #17 for why).
    """
    cfg = STREAMS[stream]
    if "sources" in cfg:
        return cfg["sources"]
    return [{"dataset_id": cfg["dataset_id"],
             "monthly_template": cfg["monthly_template"]}]


def stream_time_axis(stream):
    """Full time axis for a stream, and the source index each step comes from.

    Segments are concatenated in order and each later one is clipped to steps
    strictly after the previous segment's end, so where two sources overlap the
    earlier (more authoritative) one wins. Today RFROM's stable and realtime
    records abut exactly -- stable ends 2024-12-27, realtime starts 2025-01-03,
    one 7-day step -- but a later realtime refresh could overlap.
    """
    times, origin = [], []
    for i, src in enumerate(stream_sources(stream)):
        seg = erddap_time_axis(src["dataset_id"])
        if times:
            seg = seg[seg > times[-1][-1]]
        if len(seg) == 0:
            continue
        times.append(seg)
        origin.append(np.full(len(seg), i))
    return pd.DatetimeIndex(np.concatenate(times)), np.concatenate(origin)


def make_file_blocks(stream, times, origin=None, block_size=BLOCK_SIZE):
    """Split the time axis into blocks and map each to its monthly source files.

    ``origin`` is the per-step source index from ``stream_time_axis``; it lets a
    block that straddles the stable/realtime seam pull its months from both
    datasets, in order.

    Returns a list of dicts with keys: block, start, end, n_times, filename, urls.
    """
    cfg = STREAMS[stream]
    sources = stream_sources(stream)
    times = pd.DatetimeIndex(times)
    origin = np.zeros(len(times), dtype=int) if origin is None else np.asarray(origin)

    # A month must come from exactly one source, or open_mfdataset would be handed
    # two files covering the same weeks. Cannot happen while the seam falls on a
    # month boundary (it does today); fail loudly rather than silently if a future
    # realtime refresh starts mid-month.
    by_month = {}
    for t, o in zip(times, origin):
        by_month.setdefault((t.year, t.month), set()).add(int(o))
    split = {m: srcs for m, srcs in by_month.items() if len(srcs) > 1}
    if split:
        month = sorted(split)[0]
        raise ValueError(
            f"stream {stream!r}: {month[0]}-{month[1]:02d} draws on more than one "
            f"ERDDAP dataset ({[sources[i]['dataset_id'] for i in sorted(split[month])]}). "
            "The stable/realtime seam must fall on a month boundary; it does not, "
            "so the monthly files for that month would overlap in time."
        )

    blocks = []
    for i in range(0, len(times), block_size):
        block_times = times[i:i + block_size]
        block_origin = origin[i:i + block_size]
        urls, seen = [], set()
        for t, o in zip(block_times, block_origin):
            key = (int(o), t.year, t.month)
            if key in seen:
                continue
            seen.add(key)
            src = sources[int(o)]
            urls.append(
                f"{ERDDAP_FILES}/{src['dataset_id']}/"
                + src["monthly_template"].format(year=t.year, month=t.month)
            )
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


def _remove_quietly(path):
    """Delete path if it exists, ignoring errors."""
    try:
        os.remove(path)
    except OSError:
        pass


def is_readable_netcdf(path):
    """True if path opens as HDF5/netCDF-4 -- used to tell a complete file from a stub.

    ERDDAP's /files/ endpoint sends these gzip-encoded with chunked transfer, so a
    response carries no Content-Length and supports no Range requests (GitHub issue
    #11): there is no byte count to compare a local file against. Opening the file
    is the available completeness test -- a truncated download has no valid HDF5
    superblock/root group and fails here.
    """
    try:
        import h5py
        with h5py.File(path, "r"):
            return True
    except Exception:
        return False


def download(url, dest, chunk=16 * 1024 * 1024, attempts=DOWNLOAD_ATTEMPTS):
    """Download url -> dest, skipping if a complete copy already exists.

    Downloads land in a ".part" file that is validated and then atomically renamed,
    so the presence of ``dest`` means a verified-complete file: re-running the script
    re-uses what is already in scratch instead of re-fetching ~12 GB per block.

    Transient network failures are retried with exponential backoff. ERDDAP does not
    honour Range requests (it answers 416), so a retry restarts the file rather than
    resuming it.
    """
    name = os.path.basename(dest)
    if os.path.exists(dest) and is_readable_netcdf(dest):
        print(f"    skip (have) {name}")
        return dest

    tmp = dest + ".part"
    for attempt in range(1, attempts + 1):
        try:
            # No HEAD first: ERDDAP's HEAD on these files takes ~45 s (it appears to
            # build the response body to answer), routinely blowing past a read
            # timeout and killing the run, and it returns no Content-Length anyway.
            with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for block_bytes in r.iter_content(chunk_size=chunk):
                        f.write(block_bytes)
            if not is_readable_netcdf(tmp):
                raise OSError(f"downloaded file is not readable netCDF: {name}")
        except (requests.RequestException, OSError) as exc:
            _remove_quietly(tmp)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            fatal = status is not None and 400 <= status < 500
            if fatal or attempt == attempts:
                raise
            wait = RETRY_BACKOFF * 2 ** (attempt - 1)
            print(f"    retry {attempt}/{attempts - 1} for {name} in {wait}s "
                  f"({type(exc).__name__}: {exc})")
            time.sleep(wait)
            continue
        os.replace(tmp, dest)
        print(f"    got  {name}  ({os.path.getsize(dest) / 1e9:.2f} GB)")
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

    # Both authors annotate variables with a non-standard "Description" and no
    # CF "long_name". Where the product opts in, copy it across (keeping
    # Description as provenance) rather than inventing wording of our own. The
    # trailing spaces are in the source strings.
    cf_refinements = PRODUCTS[cfg["product"]]["cf_refinements"]
    if cf_refinements:
        for var in ds.variables:
            attrs = ds[var].attrs
            if "long_name" not in attrs and isinstance(attrs.get("Description"), str):
                attrs["long_name"] = attrs["Description"].strip()

    ds[data_var].attrs.update(cfg["var_attrs"])
    for coord, attrs in COORD_ATTRS.items():
        ds[coord].attrs.update(attrs)
    # The source carries a stale, contradictory time "Description" ("days since 1950");
    # the real encoding is seconds since 1970 (set below), so drop it.
    ds["time"].attrs.pop("Description", None)

    ds.attrs["Conventions"] = "CF-1.10, ACDD-1.3"

    var_dims = ds[data_var].dims
    # On-disk chunk sizes. The TIME chunk is always the full CHUNKS["time"], even
    # when the block is shorter -- a short final block with a shrunken time chunk
    # cannot be virtualized into the downstream Icechunk store, because Zarr has
    # no variable-length chunk grid and VirtualiZarr refuses to concatenate
    # arrays whose chunk shapes differ (issue #17). HDF5 only allows a chunk
    # longer than its dimension when that dimension is unlimited, so a short
    # block is written with time unlimited and HDF5 pads the edge chunk; the pad
    # is fill value and compresses to almost nothing (measured: 1.05x on the
    # 19-step temp_error tail). The spatial dims are still capped -- they are
    # never short in practice, but a cap there would be harmless.
    chunksizes = tuple(
        CHUNKS[d] if d == "time" else min(CHUNKS[d], ds.sizes[d]) for d in var_dims
    )
    short_block = ds.sizes["time"] < CHUNKS["time"]
    unlimited_dims = ["time"] if short_block else None

    sources = ", ".join(s["dataset_id"] for s in stream_sources(stream))
    note = (
        f"{stamp}: repackaged for NODD ({version}) from ERDDAP {sources} "
        f"monthly files; rechunked to {chunksizes} ({', '.join(var_dims)})"
        + (" with time unlimited (padded edge chunk)." if short_block else ".")
    )
    ds.attrs["history"] = note + "\n" + ds.attrs.get("history", "")

    # Repair any mojibake so h5netcdf can write the attributes (global + per-variable).
    ds.attrs = {k: clean_utf8(v) for k, v in ds.attrs.items()}
    for var in ds.variables:
        ds[var].attrs = {k: clean_utf8(v) for k, v in ds[var].attrs.items()}

    # --- Rechunk + on-disk encoding ------------------------------------------
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
    # Same for the bounds variable (CF ch. 7.1), which xarray also decorates.
    if cf_refinements and "mean_pressure_bnds" in ds.variables:
        encoding["mean_pressure_bnds"] = {"_FillValue": None}

    print(f"    dims={dict(ds.sizes)}  on-disk chunks={chunksizes}"
          + ("  (time unlimited, padded edge chunk)" if short_block else ""))
    return ds, encoding, unlimited_dims


def write_netcdf(ds, encoding, out_path, unlimited_dims=None):
    """Write ds to out_path with the given encoding."""
    print(f"    writing {out_path} ...")
    ds.to_netcdf(out_path, engine="h5netcdf", encoding=encoding, mode="w",
                 unlimited_dims=unlimited_dims)
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
    ds, encoding, unlimited_dims = build_dataset(stream, local_files, block, version)
    out_path = os.path.join(OUTPUT_DIR, filename)
    write_netcdf(ds, encoding, out_path, unlimited_dims=unlimited_dims)
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
            _remove_quietly(f)
        if do_upload:
            _remove_quietly(out_path)
        print("    cleaned scratch for this block")

    return result


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def check_netcdf_engine(p):
    """Fail before downloading ~12 GB if the h5netcdf engine cannot actually open a file.

    h5netcdf's HDF5 backend (h5py) is an optional extra, not a hard dependency:
    `pip install h5netcdf` succeeds, `import h5netcdf` succeeds, xarray lists the
    engine as available -- and then the first open_mfdataset dies with
    "No module named 'h5py'", after the whole block has been downloaded
    (GitHub issue #8). Check the import up front instead.
    """
    try:
        import h5py  # noqa: F401
    except ImportError:
        p.error(
            "the h5netcdf engine has no HDF5 backend: h5py is not installed.\n"
            "h5py is an optional extra of h5netcdf, so it is easy to miss. Install it:\n"
            "    pip install h5py          (or: pip install -r requirements.txt)"
        )


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


SETUP_MD = Path(__file__).resolve().parent / "setup.md"

EPILOG = """\
Examples
--------
  python nodd.py --stream temp_stable --list           plan only, no download
  python nodd.py --stream temp_stable --blocks 0       one block, smoke-test
  python nodd.py --stream temp_stable --all            production run, one VM
  python nodd.py --stream sal_stable --blocks 0-8      VM A of a split stream
  python nodd.py --stream sal_stable --blocks 9-16     VM B, disjoint range
  python nodd.py --stream temp_v22 --all               v2.2, version implied
  python nodd.py --stream o2 --all --no-upload --keep-scratch   local test run

Setup (venv, scratch disk, GCS credentials, tmux for long runs):
  python nodd.py --setup

This help covers every flag; RFROMV/README.md and GOBAI-O2/README.md have the
per-product quickstart and stream tables.
"""


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Process PMEL ERDDAP netCDFs (RFROM v2.3 or GOBAI HR) into "
                    "NODD-bound files and upload.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--stream", choices=sorted(STREAMS), default=None,
                   help="Product stream to process (one at a time). "
                        "RFROM v2.3: temp, sal (each one continuous "
                        "stable+realtime series), temp_error, sal_error; "
                        "temp_stable, temp_realtime, sal_stable, sal_realtime "
                        "are the superseded split form. RFROM v2.2/v2.1: "
                        "temp_v22, sal_v22, temp_v21. GOBAI: o2, no3. Required "
                        "unless --setup is given.")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--blocks", metavar="RANGE",
                     help="Blocks to process, e.g. '3', '0-4', '0,2,5'. "
                          "Split a stream across VMs with disjoint ranges.")
    grp.add_argument("--all", action="store_true",
                     help="Process every block in the stream.")
    p.add_argument("--version", default=None,
                   help="Product version prefix. Defaults to the stream's own "
                        "version if it sets one (e.g. temp_v22 -> v2.2), else the "
                        "stream's product default: "
                        + ", ".join(f"{k} {v['default_version']}"
                                    for k, v in PRODUCTS.items()) + ".")
    p.add_argument("--list", action="store_true",
                   help="Print the block -> monthly-file plan and exit (no download).")
    p.add_argument("--no-upload", action="store_true",
                   help="Build files locally but do not upload (implies keeping output).")
    p.add_argument("--force", action="store_true",
                   help="Reprocess/overwrite even if the target object already exists.")
    p.add_argument("--keep-scratch", action="store_true",
                   help="Do not delete downloaded monthly files / local outputs.")
    p.add_argument("--setup", action="store_true",
                   help="Print the full off-hub setup walkthrough (setup.md) and exit: "
                        "Python env, scratch disk, GCS credentials, long-run tips.")
    args = p.parse_args(argv)

    if args.setup:
        pydoc.pager(SETUP_MD.read_text())
        return 0

    if not args.stream:
        p.error("--stream is required (or use --setup for setup instructions)")

    stream = args.stream
    product = PRODUCTS[STREAMS[stream]["product"]]
    version = args.version or STREAMS[stream].get("version") or product["default_version"]
    configure_paths(stream)
    sources = stream_sources(stream)
    print(f"Stream: {stream}  ({', '.join(s['dataset_id'] for s in sources)})")

    times, origin = stream_time_axis(stream)
    blocks = make_file_blocks(stream, times, origin)
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

    # Preflight the environment before anything expensive: a missing h5py only
    # surfaces on the first file open, i.e. after a block has been downloaded.
    check_netcdf_engine(p)

    # Report the resolved scratch dir BEFORE creating it: if NODD_SCRATCH_DIR is
    # unset off-hub this falls back to the hub path, and the failure should name it.
    print(f"Scratch: {SCRATCH_DIR}")
    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
    except OSError as exc:
        p.error(
            f"cannot create scratch directory {SCRATCH_DIR}: {exc}\n"
            "Set NODD_SCRATCH_DIR to a writable path with ~35 GB free "
            "(the default is the JupyterHub location)."
        )

    do_upload = not args.no_upload
    nodd_dest = f"gs://{product['bucket']}/{NODD_NETCDF_DIR}/{version}/{stream}"
    # Fail before downloading ~23 GB if the credentials are not where we expect.
    if do_upload and os.sep in GCS_TOKEN and not os.path.exists(GCS_TOKEN):
        p.error(
            f"credentials file not found: {GCS_TOKEN}\n"
            "Run `gcloud auth application-default login`, or point NODD_GCS_TOKEN at a "
            "credentials JSON (or set it to 'google_default' to use ADC / "
            "GOOGLE_APPLICATION_CREDENTIALS)."
        )
    fs = gcsfs.GCSFileSystem(token=GCS_TOKEN) if do_upload else None
    if do_upload:
        print(f"Destination: {nodd_dest}")

    tally = {"uploaded": 0, "written": 0, "skipped": 0}
    for i in selected:
        result = process_block(
            stream, blocks[i], version, fs, nodd_dest,
            do_upload=do_upload, force=args.force, keep_scratch=args.keep_scratch,
        )
        tally[result] += 1
        print()

    print(f"Done. uploaded={tally['uploaded']} written={tally['written']} "
          f"skipped={tally['skipped']} (of {len(selected)} selected).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
