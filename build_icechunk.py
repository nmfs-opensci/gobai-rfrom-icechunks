#!/usr/bin/env python
"""Build a virtual Icechunk store over the NODD netCDFs (GitHub issue #17).

The netCDFs published by ``nodd.py`` stay exactly where they are; this script
writes only Zarr metadata and byte-range references, so the store costs a few MB
and duplicates no science data. One store merges every stream of a product into a
single dataset on one time axis -- for RFROM v2.3, temperature, salinity and
their two error fields, 1993-01-01 to 2025-12-05 weekly.

    python build_icechunk.py --store rfrom_v23 --list
    python build_icechunk.py --store rfrom_v23 --local-repo /tmp/rfrom-test
    python build_icechunk.py --store rfrom_v23           # writes to the bucket
    python build_icechunk.py --store rfrom_v23 --validate

WHY THE SOURCE FILES MUST BE CHUNKED THE WAY THEY ARE
-----------------------------------------------------
A virtual store cannot rewrite chunks, so every file feeding one array must share
one chunk grid. Two consequences drove the netCDF layout (see
claude/notes/rfromv-icechunk.md):

  * Each variable must be ONE continuous series of files. RFROM publishes
    temperature as a stable record plus a realtime extension; if those stayed
    separate, the merged time axis would need a 70-long chunk in the middle,
    which Zarr cannot express. ``nodd.py``'s ``temp``/``sal`` streams join them.
  * The final short block must keep the full time chunk, written with an
    unlimited time dimension so HDF5 pads the edge chunk. A block whose chunk was
    shrunk to fit cannot be concatenated at all; this script says so by name if
    it meets one.

Reads the same NODD_GCS_TOKEN / NODD_SCRATCH_DIR environment as nodd.py.
"""

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np
import pandas as pd
import xarray as xr
import zarr

# --------------------------------------------------------------------------- #
# Configuration                                                                #
# --------------------------------------------------------------------------- #

# Zarr's default of 10 concurrent requests is the usual reason a store "feels
# slow"; it applies to writers and readers alike.
ZARR_CONCURRENCY = 128

# One entry per published store. ``variables`` maps a netCDF stream directory
# (the <stream> in netcdf/<version>/<stream>/) to the data variable inside it.
# Every stream listed here must resolve to the SAME time axis -- they become one
# xarray dataset, and a Zarr group has one length per dimension. Streams that end
# on different dates belong in different stores (RFROM v2.2's temp and sal do:
# 2024-12 vs 2025-12).
STORES = {
    "rfrom_v23": {
        "bucket": "noaa-oar-rfrom",
        "netcdf_prefix": "netcdf/v2.3",
        "store_prefix": "icechunk/v2.3",
        "variables": {
            "temp": "ocean_temperature",
            "sal": "ocean_salinity",
            "temp_error": "ocean_temperature_error",
            "sal_error": "ocean_salinity_error",
        },
        # First week that is provisional rather than settled, i.e. where PMEL's
        # realtime record takes over from the stable one. Drives data_mode; move
        # it when realtime weeks are promoted to stable and rebuild.
        "realtime_start": "2025-01-03",
        "attrs": {
            "title": "RFROM v2.3 gridded Argo temperature and salinity",
            "summary": (
                "High Resolution Random Field Ocean Model (RFROM) v2.3 weekly "
                "gridded Argo conservative temperature and absolute salinity "
                "(TEOS-10) with their RMS error fields, on a 0.25 degree global "
                "grid over 58 mean-pressure levels. A virtual Icechunk store over "
                "the netCDFs published at gs://noaa-oar-rfrom/netcdf/v2.3/."
            ),
            "institution": "NOAA PMEL, CIMAR",
            "Conventions": "CF-1.10, ACDD-1.3",
        },
    },
    # GOBAI HR shares RFROM's grid and block layout (issue #13), so the same
    # machinery applies once its tails are rewritten with a padded time chunk.
    "gobai_hr": {
        "bucket": "noaa-oar-gobai",
        "netcdf_prefix": "netcdf/v202606",
        "store_prefix": "icechunk/v202606",
        "variables": {"o2": "o2", "no3": "no3"},
        "realtime_start": None,          # no stable/realtime split in GOBAI HR
        "attrs": {
            "title": "GOBAI HR-v1.0 gridded oxygen and nitrate",
            "summary": (
                "GOBAI HR-v1.0 weekly gridded dissolved oxygen and nitrate on the "
                "RFROM grid. A virtual Icechunk store over the netCDFs published "
                "at gs://noaa-oar-gobai/netcdf/v202606/."
            ),
            "institution": "NOAA PMEL, CIMAR",
            "Conventions": "CF-1.10, ACDD-1.3",
        },
    },
}

# Small variables materialized into the store rather than referenced. Everything
# else -- i.e. the science arrays -- stays virtual.
LOADABLE = ["time", "latitude", "longitude", "mean_pressure", "mean_pressure_bnds"]

DATA_MODE_ATTRS = {
    "long_name": "stable or realtime data mode",
    "flag_values": np.array([0, 1], dtype="int8"),
    "flag_meanings": "stable realtime",
    "comment": (
        "Applies to the temperature and salinity fields: 0 marks weeks from the "
        "settled (stable) record, 1 marks provisional realtime weeks that will be "
        "reprocessed. The error fields are a single record over the whole period."
    ),
}

GCS_TOKEN = (
    os.environ.get("NODD_GCS_TOKEN")
    or os.environ.get("RFROM_GCS_TOKEN")
    or "/home/jovyan/.config/gcloud/application_default_credentials.json"
)
if os.sep in GCS_TOKEN or GCS_TOKEN.startswith("~"):
    GCS_TOKEN = os.path.expanduser(GCS_TOKEN)


# --------------------------------------------------------------------------- #
# Source discovery                                                             #
# --------------------------------------------------------------------------- #

# Block file names end in _<start>_<end>.nc, e.g.
# RFROMV23_TEMP_STABLE_REALTIME_2023-09-01_2025-07-25.nc.
_BLOCK_DATES = re.compile(r"_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.nc$")


def block_start(path):
    """Sort key: the block's start date, taken from the file name.

    Lexical order is NOT time order for these names -- the combined temp/sal
    streams carry a STABLE / STABLE_REALTIME / REALTIME infix, and "REALTIME"
    sorts before "STABLE", which would put the newest block first. Ordering the
    store by name would then be wrong, so order by the dates the name carries.
    (concat_virtual re-sorts on the files' real time values regardless; this
    keeps listings and validation sampling honest.)
    """
    match = _BLOCK_DATES.search(path)
    return (0, match.group(1)) if match else (1, path)


def list_stream_files(cfg, stream, fs=None):
    """The stream's netCDFs as gs:// URLs, in time order."""
    import gcsfs
    fs = fs or gcsfs.GCSFileSystem(token=GCS_TOKEN)
    prefix = f"{cfg['bucket']}/{cfg['netcdf_prefix']}/{stream}"
    try:
        paths = sorted((p for p in fs.ls(prefix) if p.endswith(".nc")), key=block_start)
    except FileNotFoundError:
        paths = []
    if not paths:
        raise FileNotFoundError(
            f"no netCDFs under gs://{prefix}\n"
            f"  Stream {stream!r} has not been published yet. Build it with:\n"
            f"      python nodd.py --stream {stream} --all"
        )
    return [f"gs://{p}" for p in paths]


def source_registry(bucket):
    """ObjectStoreRegistry for reading the source netCDFs (public, anonymous)."""
    from obstore.store import GCSStore
    from virtualizarr.registry import ObjectStoreRegistry
    return ObjectStoreRegistry({f"gs://{bucket}": GCSStore(bucket, skip_signature=True)})


def open_virtual_files(urls, registry, max_workers=8):
    """Open each file as a virtual dataset (header reads only), in parallel."""
    from virtualizarr import open_virtual_dataset
    from virtualizarr.parsers import HDFParser
    parser = HDFParser()

    def one(url):
        return open_virtual_dataset(url, registry=registry, parser=parser,
                                    loadable_variables=LOADABLE)

    with ThreadPoolExecutor(max_workers) as ex:
        return list(ex.map(one, urls))


# --------------------------------------------------------------------------- #
# Concatenation                                                                #
# --------------------------------------------------------------------------- #

def concat_manifest_arrays(arrays, axis=0):
    """Join ManifestArrays along `axis`, allowing a partial chunk on the LAST one.

    ``xr.concat`` / ``open_virtual_mfdataset`` reject this. VirtualiZarr requires
    every input to be an exact multiple of the chunk length along the concat axis
    -- including the final one -- so any series whose length is not a multiple of
    the time chunk cannot be virtualized through the stock path. RFROM is 1719
    steps with a 100-step chunk, and no publishing schedule makes that divide.

    A trailing partial chunk is nonetheless valid Zarr: edge chunks are stored
    full size and cropped on read, which is exactly what HDF5 does for the padded
    tail block ``nodd.py`` writes. So the manifests are joined directly, after
    checking the two conditions Zarr actually imposes:

      * every input shares one chunk shape, dtype and codec pipeline;
      * every input except the last is an exact multiple of the chunk length on
        `axis` -- only the last may own the array's edge chunk.
    """
    from virtualizarr.manifests import ChunkManifest, ManifestArray
    from virtualizarr.manifests.utils import check_combinable_zarr_arrays

    if len(arrays) == 1:
        return arrays[0]

    check_combinable_zarr_arrays(arrays)          # dtype, codecs, chunk shape
    chunk = arrays[0].chunks[axis]
    for i, arr in enumerate(arrays[:-1]):
        if arr.shape[axis] % chunk:
            raise ValueError(
                f"input {i} of {len(arrays)} has length {arr.shape[axis]} along the "
                f"concat axis, which is not a multiple of the chunk length {chunk}. "
                "Only the final file may be short. A short block in the middle of a "
                "series cannot be virtualized -- the series has to be republished so "
                "that every file except the last holds whole chunks."
            )
    off_axis = [s[:axis] + s[axis + 1:] for s in (a.shape for a in arrays)]
    if len(set(off_axis)) != 1:
        raise ValueError(f"inputs disagree off the concat axis: {sorted(set(off_axis))}")

    manifest = ChunkManifest.from_arrays(
        paths=np.concatenate([a.manifest._paths for a in arrays], axis=axis),
        offsets=np.concatenate([a.manifest._offsets for a in arrays], axis=axis),
        lengths=np.concatenate([a.manifest._lengths for a in arrays], axis=axis),
    )
    shape = list(arrays[0].shape)
    shape[axis] = sum(a.shape[axis] for a in arrays)
    # ArrayV3Metadata is a frozen dataclass; only the shape changes.
    return ManifestArray(chunkmanifest=manifest,
                         metadata=replace(arrays[0].metadata, shape=tuple(shape)))


def concat_virtual(vds_list, data_var):
    """Concatenate one stream's per-file virtual datasets along time.

    Returns (ManifestArray, time DatetimeIndex, variable attrs).
    """
    order = np.argsort([v["time"].values[0] for v in vds_list])
    vds_list = [vds_list[i] for i in order]

    arrays = []
    for v in vds_list:
        arr = v[data_var].data
        if not hasattr(arr, "manifest"):
            raise TypeError(f"{data_var} was loaded, not virtualized -- check LOADABLE")
        arrays.append(arr)

    chunks = {a.chunks[0] for a in arrays}
    if len(chunks) > 1:
        shapes = [(a.shape[0], a.chunks[0]) for a in arrays]
        raise ValueError(
            f"{data_var}: the files do not share one time chunk: {sorted(chunks)}.\n"
            f"  (time length, time chunk) per file: {shapes}\n"
            "A block whose time chunk was shrunk to fit a short block cannot be "
            "concatenated -- Zarr has no variable-length chunk grid. Rewrite that "
            "block with nodd.py (it now writes short blocks with an unlimited time "
            "dimension, so the edge chunk keeps the full time chunk and HDF5 pads it)."
        )

    times = pd.DatetimeIndex(np.concatenate([v["time"].values for v in vds_list]))
    if not times.is_monotonic_increasing or times.has_duplicates:
        raise ValueError(f"{data_var}: time axis is not strictly increasing")
    return concat_manifest_arrays(arrays), times, dict(vds_list[0][data_var].attrs)


# --------------------------------------------------------------------------- #
# Dataset assembly                                                             #
# --------------------------------------------------------------------------- #

def build_virtual_dataset(cfg, per_stream, coords_from):
    """Merge every stream's concatenated array into one xarray Dataset."""
    axes = {s: t for s, (_, t, _) in per_stream.items()}
    reference = next(iter(axes))
    for stream, times in axes.items():
        if not times.equals(axes[reference]):
            raise ValueError(
                f"stream {stream!r} has a different time axis from {reference!r} "
                f"({len(times)} vs {len(axes[reference])} steps, ending "
                f"{times[-1].date()} vs {axes[reference][-1].date()}).\n"
                "Streams sharing a store must share a time axis -- a Zarr group has "
                "one length per dimension. Publish them as separate stores instead."
            )
    times = axes[reference]

    step = np.unique(np.diff(times))
    if len(step) != 1:
        raise ValueError(f"time axis is not evenly spaced: steps {step}")

    data_vars = {}
    for stream, (arr, _, attrs) in per_stream.items():
        var = cfg["variables"][stream]
        data_vars[var] = xr.Variable(
            ("time", "mean_pressure", "latitude", "longitude"), arr, attrs=attrs
        )

    ds = xr.Dataset(
        data_vars,
        coords={
            "time": ("time", times.values, dict(coords_from["time"].attrs)),
            **{c: coords_from[c] for c in ("mean_pressure", "latitude", "longitude")},
        },
    )
    if "mean_pressure_bnds" in coords_from:
        ds = ds.assign_coords(mean_pressure_bnds=coords_from["mean_pressure_bnds"])

    if cfg.get("realtime_start"):
        boundary = pd.Timestamp(cfg["realtime_start"])
        if boundary not in times:
            raise ValueError(
                f"realtime_start {boundary.date()} is not a step on the time axis"
            )
        mode = (times >= boundary).astype("int8")
        ds = ds.assign_coords(data_mode=("time", mode, dict(DATA_MODE_ATTRS)))

    stamp = pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    source = f"gs://{cfg['bucket']}/{cfg['netcdf_prefix']}/"
    ds.attrs = {
        **cfg["attrs"],
        "source": source,
        "time_coverage_start": f"{times[0]:%Y-%m-%d}",
        "time_coverage_end": f"{times[-1]:%Y-%m-%d}",
        "time_coverage_resolution": f"P{step[0].astype('timedelta64[D]').astype(int)}D",
        "history": f"{stamp}: virtual Icechunk store built from {source} "
                   f"by build_icechunk.py (github.com/nmfs-opensci/gobai-rfrom-icechunks)",
    }
    if cfg.get("realtime_start"):
        ds.attrs["realtime_start"] = cfg["realtime_start"]
    return ds


# --------------------------------------------------------------------------- #
# Destination                                                                  #
# --------------------------------------------------------------------------- #

def virtual_prefix(cfg):
    """URL prefix the virtual references live under. Must end in '/'."""
    return f"gs://{cfg['bucket']}/{cfg['netcdf_prefix']}/"


def open_repo(cfg, local_repo=None, create=True, local_source_dir=None):
    """Create or open the destination repository, with virtual access authorized.

    Source and destination are configured independently even though both sit in
    the same bucket: the store is written with the caller's credentials, while the
    references are read anonymously, the way a consumer will read them.

    ``local_source_dir`` authorizes a second container over a local directory, so
    a block that has been built but not yet uploaded can be referenced alongside
    the published ones. That is how the smoke test rehearses the real layout --
    and how you can check a rebuilt tail block before it goes to the bucket. A
    store written this way is for testing only: its file:// references mean
    nothing to anyone else.
    """
    import icechunk as ic

    prefix = virtual_prefix(cfg)
    container = ic.VirtualChunkContainer(prefix, ic.gcs_store({"bucket": cfg["bucket"]}))
    creds = {prefix: ic.gcs_credentials(anonymous=True)}
    extra = []
    if local_source_dir:
        local_prefix = "file://" + os.path.abspath(local_source_dir).rstrip("/") + "/"
        extra.append(ic.VirtualChunkContainer(
            local_prefix, ic.local_filesystem_store(os.path.abspath(local_source_dir))))
        creds[local_prefix] = ic.credentials.LocalFileSystemAccess

    if local_repo:
        storage = ic.local_filesystem_storage(local_repo)
    else:
        storage = ic.gcs_storage(bucket=cfg["bucket"], prefix=cfg["store_prefix"],
                                 application_credentials=GCS_TOKEN)

    config = ic.RepositoryConfig.default()
    config.set_virtual_chunk_container(container)
    for c in extra:
        config.set_virtual_chunk_container(c)
    # containers_credentials wraps each entry in the per-backend Credentials type
    # icechunk expects; a bare GcsCredentials is rejected.
    auth = ic.containers_credentials(creds)
    if create:
        repo = ic.Repository.open_or_create(storage, config=config,
                                            authorize_virtual_chunk_access=auth)
    else:
        repo = ic.Repository.open(storage, config=config,
                                  authorize_virtual_chunk_access=auth)
    repo.save_config()
    return repo


def write_store(repo, ds, message):
    """Write the virtual dataset and commit. Returns the snapshot id."""
    session = repo.writable_session("main")
    ds.vz.to_icechunk(session.store)
    snapshot = session.commit(message)
    return snapshot


# --------------------------------------------------------------------------- #
# Validation                                                                   #
# --------------------------------------------------------------------------- #

def _sample(urls, n):
    """Pick n files to check: always the first and the last, then evenly spaced.

    The last file matters most -- it owns the padded edge chunk, the part stock
    tooling refuses to build and therefore the part most likely to be wrong.
    """
    if n >= len(urls):
        return list(urls)
    if n <= 1:
        return [urls[-1]]
    middle = [urls[round(i * (len(urls) - 1) / (n - 1))] for i in range(1, n - 1)]
    return [urls[0]] + middle + [urls[-1]]


def validate(repo, cfg, sample_files=2, verbose=True):
    """Read the store back the way a consumer will, and check it against the source.

    Three checks, in increasing cost: the time axis is evenly spaced; the store
    spans exactly the published files (endpoints, not just length); and the values
    at both ends of a sample of files match the netCDFs they came from.
    """
    import gcsfs

    ds = xr.open_zarr(repo.readonly_session("main").store, consolidated=False, chunks={})
    if verbose:
        print(f"  store: {dict(ds.sizes)}")
        print(f"  variables: {', '.join(ds.data_vars)}")
        print(f"  time: {str(ds.time.values[0])[:10]} -> {str(ds.time.values[-1])[:10]}")

    problems = []
    store_times = pd.DatetimeIndex(ds.time.values)
    step = np.unique(np.diff(store_times))
    if len(step) != 1:
        problems.append(f"time axis is not evenly spaced: {len(step)} distinct steps")

    fs = gcsfs.GCSFileSystem(token=GCS_TOKEN)
    for stream, var in cfg["variables"].items():
        urls = list_stream_files(cfg, stream, fs)
        chosen = _sample(urls, sample_files)
        if verbose:
            print(f"  {var}: {len(urls)} source files, checking {len(chosen)}")
        for url in chosen:
            with fs.open(url.replace("gs://", ""), "rb", block_size=8 * 1024 * 1024) as fh:
                src = xr.open_dataset(fh, engine="h5netcdf")
                name = url.split("/")[-1]
                for t in (src.time.values[0], src.time.values[-1]):
                    when = np.datetime_as_string(t, "D")
                    # Coverage first: a store that simply stops early would other-
                    # wise fail as an unreadable KeyError from .sel().
                    if t not in store_times:
                        problems.append(f"{var}: the store does not cover {when} "
                                        f"(published in {name})")
                        continue
                    a = src[var].sel(time=t).isel(mean_pressure=0).values
                    b = ds[var].sel(time=t).isel(mean_pressure=0).values
                    if not np.array_equal(a, b, equal_nan=True):
                        problems.append(f"{var}: values at {when} differ from {name}")
                    elif verbose:
                        print(f"    {when} matches ({name})")

    if problems:
        for problem in problems:
            print(f"  FAIL: {problem}")
        return False
    if verbose:
        print("  time axis regular, endpoints covered, sampled values match the source")
    return True


# --------------------------------------------------------------------------- #
# Driver                                                                       #
# --------------------------------------------------------------------------- #

def plan(cfg):
    """Print what would be virtualized, without opening any file."""
    import gcsfs
    fs = gcsfs.GCSFileSystem(token=GCS_TOKEN)
    total_files = total_bytes = 0
    missing = []
    for stream, var in cfg["variables"].items():
        prefix = f"{cfg['bucket']}/{cfg['netcdf_prefix']}/{stream}"
        try:
            entries = [e for e in fs.ls(prefix, detail=True) if e["name"].endswith(".nc")]
        except FileNotFoundError:
            entries = []
        if not entries:
            missing.append(stream)
            print(f"  {var:<26} {stream:<12}   -  NOT PUBLISHED YET")
            continue
        size = sum(e["size"] for e in entries)
        total_files += len(entries)
        total_bytes += size
        print(f"  {var:<26} {stream:<12} {len(entries):>3} files  {size / 1e9:8.1f} GB")
    print(f"  {'':<26} {'total':<12} {total_files:>3} files  {total_bytes / 1e9:8.1f} GB "
          f"referenced, nothing copied")
    if missing:
        print(f"\n  Missing streams: {', '.join(missing)}")
        print("  Publish them first, e.g.:")
        for stream in missing:
            print(f"      python nodd.py --stream {stream} --all")


def build(store_name, local_repo=None, validate_after=True, workers=8):
    cfg = STORES[store_name]
    zarr.config.set({"async.concurrency": ZARR_CONCURRENCY})
    registry = source_registry(cfg["bucket"])

    per_stream, coords_from = {}, None
    for stream, var in cfg["variables"].items():
        urls = list_stream_files(cfg, stream)
        t0 = time.time()
        vds_list = open_virtual_files(urls, registry, max_workers=workers)
        arr, times, attrs = concat_virtual(vds_list, var)
        per_stream[stream] = (arr, times, attrs)
        if coords_from is None:
            coords_from = vds_list[0]
        print(f"  {var:<26} {len(urls):>3} files -> {arr.shape} "
              f"chunks {arr.chunks}  ({time.time() - t0:.0f}s)")

    ds = build_virtual_dataset(cfg, per_stream, coords_from)
    print(f"\nDataset: {dict(ds.sizes)}")
    print(f"  data_vars: {', '.join(ds.data_vars)}")
    print(f"  coords:    {', '.join(ds.coords)}")

    where = local_repo or f"gs://{cfg['bucket']}/{cfg['store_prefix']}"
    print(f"\nWriting {where}")
    repo = open_repo(cfg, local_repo=local_repo)
    snapshot = write_store(repo, ds, f"{store_name}: virtual store over "
                                     f"{virtual_prefix(cfg)}")
    print(f"  committed {snapshot}")

    if validate_after:
        print("\nValidating from the consumer read path")
        if not validate(repo, cfg):
            return 1
    print(f"\nDone. {where}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="See claude/notes/rfromv-icechunk.md for the design decisions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--store", choices=sorted(STORES), required=True)
    p.add_argument("--list", action="store_true",
                   help="Print the files that would be virtualized and exit.")
    p.add_argument("--local-repo", metavar="DIR",
                   help="Write to a local Icechunk repository instead of the bucket. "
                        "Use this to rehearse a build; the references still point at "
                        "the real netCDFs in GCS.")
    p.add_argument("--validate", action="store_true",
                   help="Validate the existing store instead of building.")
    p.add_argument("--no-validate", action="store_true",
                   help="Skip the post-build validation.")
    p.add_argument("--workers", type=int, default=8,
                   help="Parallel header reads (default 8).")
    args = p.parse_args(argv)
    cfg = STORES[args.store]

    print(f"Store: {args.store}")
    print(f"Source: {virtual_prefix(cfg)}")
    print(f"Destination: {args.local_repo or f'gs://{cfg["bucket"]}/{cfg["store_prefix"]}'}\n")

    if args.list:
        plan(cfg)
        return 0
    if args.validate:
        zarr.config.set({"async.concurrency": ZARR_CONCURRENCY})
        repo = open_repo(cfg, local_repo=args.local_repo, create=False)
        return 0 if validate(repo, cfg) else 1
    return build(args.store, local_repo=args.local_repo,
                 validate_after=not args.no_validate, workers=args.workers)


if __name__ == "__main__":
    sys.exit(main())
