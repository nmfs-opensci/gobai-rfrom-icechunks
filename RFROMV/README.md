# RFROMV — RFROM v2.3 → NODD pipeline

Prepares RFROM v2.3 gridded Argo temperature/salinity fields for the NOAA Open
Data Dissemination (NODD) GCP bucket `gs://noaa-oar-rfrom`. Source files come
from PMEL ERDDAP; outputs are CF-compliant, rechunked, compressed netCDFs laid
out per stream under `netcdf/<version>/<stream>/`, ready to be virtualized into a
downstream Icechunk / VirtualiZarr store.

Per output file the pipeline is:

```
ERDDAP monthly netCDFs
  → open_mfdataset combine
  → select one 100-time-step block
  → fix CF metadata
  → rechunk (100, 1, 180, 180) + zlib-4/shuffle
  → upload to gs://noaa-oar-rfrom/netcdf/<version>/<stream>/
```

The 1670-step stable record splits into 17 blocks; output files are named e.g.
`RFROMV23_TEMP_STABLE_1993-01-01_1994-11-25.nc`.

## The six product streams (RFROM v2.3)

These are the current **v2.3** products, published under `netcdf/v2.3/` in the
bucket. New versions reprocess all data into a new `netcdf/<version>/` tree; the
script's `--version` flag (default `v2.3`) sets the prefix. Each ERDDAP
dataset_id below links to its griddap data-access page.

| stream | version | ERDDAP dataset | data variable | units |
|---|---|---|---|---|
| `temp_stable`   | v2.3 | [`argo_rfromv23_temp`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_temp.html)                   | `ocean_temperature`       | degree_Celsius |
| `temp_realtime` | v2.3 | [`argo_rfromv23_temp_realtime`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_temp_realtime.html) | `ocean_temperature`       | degree_Celsius |
| `temp_error`    | v2.3 | [`argo_rfromv23_temp_error`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_temp_error.html)       | `ocean_temperature_error` | degree_Celsius |
| `sal_stable`    | v2.3 | [`argo_rfromv23_sal`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_sal.html)                     | `ocean_salinity`          | g/kg † |
| `sal_realtime`  | v2.3 | [`argo_rfromv23_sal_realtime`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_sal_realtime.html)   | `ocean_salinity`          | g/kg † |
| `sal_error`     | v2.3 | [`argo_rfromv23_sal_error`](https://data.pmel.noaa.gov/pmel/erddap/griddap/argo_rfromv23_sal_error.html)         | `ocean_salinity_error`    | grams_per_kilogram |

Stable runs 1993→2024 (1670 steps); error and realtime extend to 2025. Realtime
is a moving draft that is eventually reprocessed into stable on a new version.

† `ocean_salinity` is **absolute salinity (TEOS-10) in g/kg**, confirmed by the
data author. ERDDAP labels the variable `sea_water_practical_salinity` / `PSU`,
but that is a known upstream mistake the author cannot fix, so the pipeline
overrides `sal_stable` / `sal_realtime` to `sea_water_absolute_salinity` /
`grams_per_kilogram` — **metadata only, values unchanged**.

## Files in this directory

### Deliverables

- **`rfrom_nodd.py`** — the batch script (GitHub issue #5). Processes any of the
  six streams into NODD netCDFs and uploads them. This is what you run in
  production. See "Running the batch script" below.
- **`prep-one-netcdf-for-NODD.ipynb`** — the tested single-file reference pipeline
  (GitHub issue #1, merged via PR #4). Run interactively cell-by-cell; it prepares
  and uploads **one** block so the workflow can be validated before scaling up.
  `rfrom_nodd.py` is this notebook generalized to all six streams — the notebook
  remains the readable, step-annotated explanation of *why* each stage is the way
  it is.
- **`index.html`** — landing page for the published product.
- **`requirements.txt`** / **`environment.yml`** / **`pixi.toml`** — the same
  dependency set for pip, conda/mamba, and pixi respectively. Only needed off-hub;
  see "Running off-hub" below.

### Sandbox (exploratory scratch — not part of the pipeline)

- **`prep-for-NODD-rfromv23.ipynb`** — earlier exploratory notebook.
- **`upload_to_nodd.ipynb`** — a short scratch uploader.
- **`setup_bare_VM.txt`** — Eli's personal cheat-sheet of shell commands for
  standing up a bare VM. Informal by design and overlapping "Running off-hub"
  below, which is the maintained version.

Both are kept for reference only; don't treat them as the source of truth.

## Environment

The notebooks run on the JupyterHub, where the data volumes and cloud
credentials are already mounted; dependencies are installed into the kernel by
the `%pip install -qU ...` cell at the top of each notebook. NODD upload uses
`gcsfs` with application-default credentials at
`~/.config/gcloud/application_default_credentials.json`, and scratch space
(downloads + local output) defaults to `/home/jovyan/shared-public/rfromv-scratch`.

`rfrom_nodd.py` also runs anywhere else — a bare VM, a laptop — via two
environment variables that override those two hub paths:

| variable | default | meaning |
|---|---|---|
| `RFROM_SCRATCH_DIR` | `/home/jovyan/shared-public/rfromv-scratch` | download + output scratch; needs ~35 GB free |
| `RFROM_GCS_TOKEN` | `~/.config/gcloud/application_default_credentials.json` (hub path) | credentials JSON path, **or** the keyword `google_default` to resolve ADC the usual way |

See below for the full off-hub setup.

## Running off-hub (bare VM or macOS)

Nothing about the pipeline needs the hub — it needs Python, ~35 GB of scratch
disk, and credentials that can write to `gs://noaa-oar-rfrom`. The steps are the
same on a bare Linux VM and on a Mac; where they differ it is called out.

On a truly minimal VM image, install the basics first — the steps below assume
`git`, `curl`, and (for detaching long runs) `tmux` exist:

```sh
sudo apt-get update && sudo apt-get install -y git curl tmux
```

### 1. Python environment

Python 3.11+ (3.12 is what the pipeline was validated on). The dependencies are
`xarray`, `dask`, `h5netcdf`, `h5py`, `gcsfs`, `pandas`, `numpy`, `requests` —
all of them ship prebuilt for Linux x86-64 and Apple Silicon either way you
install, so there is no compiler or system HDF5 to set up; `h5py` is the wheel
that carries HDF5.

`h5py` is listed explicitly on purpose. It is an *optional extra* of `h5netcdf`
(`h5netcdf[h5py]`), not a hard dependency, so installing `h5netcdf` alone gives
you an engine with no HDF5 backend — which fails only on the first file open,
after a block has already been downloaded (issue #8). Install from one of the
manifests below rather than by hand and this is taken care of; `rfrom_nodd.py`
also checks for it up front and exits before downloading anything.

Three manifests are checked in, one per tool — `requirements.txt` (venv + pip),
`pixi.toml` (pixi), `environment.yml` (conda/mamba). They declare the same
dependency set, so they are interchangeable. A bare VM has none of these tools
preinstalled, so each option below starts from nothing.

```sh
git clone https://github.com/nmfs-opensci/gobai-rfrom-icechunks.git
cd gobai-rfrom-icechunks/RFROMV
```

#### Option A — venv + pip (lightest; nothing beyond Python itself)

A bare Debian/Ubuntu image ships `python3` but usually splits out the `venv` and
`pip` modules, so install those first — that is the whole prerequisite:

```sh
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip
python3 --version                        # must be 3.11+

# RHEL / Amazon Linux instead: sudo dnf install -y python3.12 python3.12-pip
#   (then use python3.12 in place of python3 below)
# macOS: python.org installer or `brew install python@3.12`;
#   don't build on the Xcode-provided python3.
```

Then create and populate the environment. Make sure the .ven is activated `source .venv/bin/activate`

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

#### Option B — pixi (self-contained, no root, no system Python)

pixi is a single binary and brings its own Python, so it needs neither `sudo`
nor a usable system interpreter — handy on a locked-down or minimal VM:

```sh
curl -fsSL https://pixi.sh/install.sh | sh     # or: wget -qO- https://pixi.sh/install.sh | sh
# macOS alternative: brew install pixi
```

That drops the binary in `~/.pixi/bin` and adds it to your shell profile's PATH.
Start a new shell (or `source ~/.bashrc`) so `pixi --version` resolves. Then, in
this directory:

```sh
pixi install                             # solves + installs from pixi.toml
pixi shell                               # activated shell; run python directly
# or run without activating, args passed through:
pixi run nodd --stream temp_stable --all
```

Update it later with `pixi self-update` (or `brew upgrade pixi` if installed
that way — don't mix the two).

#### Option C — conda / mamba

Only worth it if you already have conda on the box; a fresh install is a heavy
prerequisite for seven pure-Python-plus-wheels dependencies. If you want it
anyway, Miniforge is the lean, conda-forge-default choice:

```sh
curl -fsSLO "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname)-$(uname -m).sh"
bash "Miniforge3-$(uname)-$(uname -m).sh"

conda env create -f environment.yml       # or: mamba env create -f environment.yml
conda activate rfromv-nodd
```

With venv or conda, re-activate in every new shell (and every new `tmux` pane) —
the script must run inside the environment. `pixi run` needs no activation, but
still needs the environment variables from steps 2–3 exported in that shell.

### 2. Scratch space

Point `RFROM_SCRATCH_DIR` at any writable path with room to spare. The script
creates the directory tree itself (`erddap/` for monthly downloads, `nodd/` for
assembled output), so there is no `mkdir` to do by hand — but the filesystem must
actually have the space, and on a cloud VM that usually means an attached data
disk rather than the small boot disk.

```sh
export RFROM_SCRATCH_DIR="$HOME/rfromv-scratch"      # VM: e.g. /mnt/data/rfromv-scratch
df -h "$(dirname "$RFROM_SCRATCH_DIR")"              # confirm ~35 GB free
```

By default the script deletes each block's downloads and its uploaded output
before moving to the next block, so peak usage stays at roughly one block
(~31 GB) rather than growing across the run. `--keep-scratch` disables that
cleanup — do not combine it with `--all` unless you have hundreds of GB free.

### 3. Credentials for pushing to NODD

Uploads go to `gs://noaa-oar-rfrom` and need an account that has been granted
write access on the NODD bucket. Read access is public, so a successful listing
does **not** prove you can write.

Pick whichever fits the machine:

**a. User credentials (Mac, or any VM you can log in from) — the default.**

This needs the gcloud CLI, which a bare VM will not have. It installs into your
home directory without root:

```sh
curl -fsSL https://sdk.cloud.google.com | bash     # then restart the shell:
exec -l $SHELL
# macOS alternative: brew install --cask google-cloud-sdk
# apt/dnf repository instructions: https://cloud.google.com/sdk/docs/install
```

Then authenticate:

```sh
gcloud auth application-default login          # opens a browser
#gcloud auth application-default set-quota-project <your-gcp-project> # not really needed

export RFROM_GCS_TOKEN="$HOME/.config/gcloud/application_default_credentials.json"
```

On a headless VM with no browser, use `gcloud auth application-default login
--no-browser`: it prints a command to run on a machine that *does* have a
browser, and you paste the result back.

**b. Service-account key file (unattended VM runs).** Nothing to install — copy
the key onto the VM and point at it:

```sh
export RFROM_GCS_TOKEN=/path/to/service-account-key.json
```

**c. A GCE VM with an attached service account** — no key file needed:

```sh
export RFROM_GCS_TOKEN=google_default
```

Verify write access *before* starting a long run, since a permissions failure
would otherwise surface only after the first ~23 GB download:

```sh
python -c "
import gcsfs, os
fs = gcsfs.GCSFileSystem(token=os.environ['RFROM_GCS_TOKEN'])
print(fs.ls('noaa-oar-rfrom/netcdf/v2.3/temp_stable')[:3])   # read
fs.pipe('noaa-oar-rfrom/netcdf/v2.3/_write_check.txt', b'ok'); print('write OK')
fs.rm('noaa-oar-rfrom/netcdf/v2.3/_write_check.txt')
"
```

The script itself now fails fast with a clear message if `RFROM_GCS_TOKEN`
points at a file that does not exist.

### 4. Run it

```sh
source .venv/bin/activate                             # or: conda activate rfromv-nodd
export RFROM_SCRATCH_DIR="$HOME/rfromv-scratch"
export RFROM_GCS_TOKEN="$HOME/.config/gcloud/application_default_credentials.json"

python rfrom_nodd.py --stream temp_stable --list      # plan only: no creds, no download
python rfrom_nodd.py --stream temp_stable --blocks 0  # smoke-test one block end to end
python rfrom_nodd.py --stream temp_stable --all       # the production run
```

Under pixi, drop the activation line and prefix each command with `pixi run`,
e.g. `pixi run nodd --stream temp_stable --all`.

The run prints the resolved scratch directory and destination prefix at startup —
check those two lines before walking away.

### 5. Long runs

A full stream is many hours, so do not run it in a foreground shell that a
dropped SSH session or a sleeping laptop can kill:

```sh
tmux new -s rfrom                                     # then run inside; detach with Ctrl-b d
# or, without tmux:
nohup python rfrom_nodd.py --stream temp_stable --all > temp_stable.log 2>&1 &

# macOS: keep the machine awake for the whole run
caffeinate -i python rfrom_nodd.py --stream temp_stable --all
```

Interruptions are cheap. The script skips any block already present in the
bucket, and partial downloads are written to a `.part` file and only renamed on
completion, so a truncated file is never reused. Just rerun the same command and
it picks up where it left off.

### Resource expectations (per stream, from the `temp_stable` run)

| | |
|---|---|
| monthly source files per block | 23 (~1 GB each, ~23 GB) |
| output file per block | ~7.6 GB |
| peak scratch disk | ~31 GB (one block, default cleanup) |
| blocks per stable stream | 17 |
| total downloaded per stream | ~390 GB |
| total uploaded per stream | ~130 GB |
| RAM | 8 GB minimum, 16 GB comfortable |

Wall-clock is dominated by that download and upload, so it tracks your network
throughput far more than your CPU — a bigger instance does not speed it up. The
~390 GB of ingress is the reason to prefer a well-connected VM over a laptop on
a home or metered connection. Memory stays modest because dask reads one
pressure plane at a time (~415 MB per chunk, a few in flight at once).

## Running the batch script

`rfrom_nodd.py` requires an explicit `--stream` **and** an explicit
`--blocks RANGE` or `--all` — nothing is processed implicitly. Run one stream at a
time (one VM per stream, or split a stream across VMs with disjoint `--blocks`
ranges). It is idempotent: before writing a block it checks whether the target
object already exists in the bucket and skips it unless `--force`, so a
resume-after-interrupt or a second VM on the same stream is safe.

Downloads resume too. A monthly file is streamed to a `.part` file, checked that
it opens as netCDF, and only then renamed into place, so anything sitting in
`<scratch>/erddap/` is known-complete and is re-used instead of re-fetched — a
re-run after a crash does not pay for the ~12 GB again. Flaky ERDDAP reads are
retried with exponential backoff (4 attempts, 15/30/60 s), which is what makes a
multi-hour `--all` run survive the transient timeouts the endpoint throws
(issue #11). ERDDAP serves these files gzip-encoded and chunked with no
`Content-Length` and no Range support, so a retry restarts that one file; the
files already finished are untouched.

```sh
# Plan only: print the block → monthly-file cross-walk, download nothing.
python rfrom_nodd.py --stream temp_stable --list

# Process a single block and upload it.
python rfrom_nodd.py --stream temp_stable --blocks 0

# Process EVERY block in the stream and upload them (a typical production run,
# one stream per VM). Idempotent: already-uploaded blocks are skipped.
python rfrom_nodd.py --stream temp_stable --all

# Split a stream across two VMs (disjoint block ranges).
python rfrom_nodd.py --stream sal_stable --blocks 0-8      # VM A
python rfrom_nodd.py --stream sal_stable --blocks 9-16     # VM B

# Whole stream, local test — build but don't upload, keep scratch files.
python rfrom_nodd.py --stream temp_realtime --all --no-upload --keep-scratch
```

### Flags

| flag | meaning |
|---|---|
| `--stream <name>` | **required** — one of the six stream keys above |
| `--blocks RANGE` | blocks to process: `3`, `0-4`, or `0,2,5` (mutually exclusive with `--all`) |
| `--all` | process every block in the stream |
| `--version` | product version prefix (default `v2.3`) |
| `--list` | print the block → monthly-file plan and exit (no download) |
| `--no-upload` | build files locally but do not upload |
| `--force` | reprocess/overwrite even if the target object already exists |
| `--keep-scratch` | do not delete downloaded monthly files / local outputs |

A typical production run for one stream is `--all` on its own VM; start with
`--list` to preview the plan and `--blocks 0` to smoke-test one block end to end.

### Where things live in the code

The `STREAMS` dict near the top of `rfrom_nodd.py` is the single place per-stream
differences live (`dataset_id`, `data_var`, `var_attrs`, filename templates). The
core functions mirror the notebook stages. Two correctness/performance choices are
load-bearing and must not regress:

- Open with `data_vars="minimal", coords="minimal", compat="override"` so
  `mean_pressure_bnds` keeps its `(mean_pressure, nv)` shape — the default would
  broadcast it against `time` and silently alter the file.
- Read with dask `chunks={"mean_pressure": 1}` (contiguous lat/lon planes). The
  source files are contiguous `(time, pressure, lat, lon)`; small spatial chunks
  force strided, seeky reads and the write goes I/O-bound (a bigger VM does not
  help). This is decoupled from the on-disk `chunksizes` in `encoding`.

The rationale for every stage is written up in
[`../claude/notes/nodd-prep.md`](../claude/notes/nodd-prep.md) and
[`../claude/notes/nodd-batch-script.md`](../claude/notes/nodd-batch-script.md).

## Not yet built

`update_nodd.py` — a weekly realtime reconcile that re-downloads the moving
realtime dataset and replaces the affected tail block(s). Out of scope for the
first cut.
