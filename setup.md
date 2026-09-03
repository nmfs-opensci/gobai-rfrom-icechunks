# Running `nodd.py` off-hub (bare VM or macOS)

This covers **both** products `nodd.py` handles — RFROM (`gs://noaa-oar-rfrom`)
and GOBAI HR (`gs://noaa-oar-gobai`) — since the environment, dependencies, and
credentials mechanism are identical; only the destination bucket and scratch
default differ per stream. Print this page any time with `python nodd.py
--setup`; the maintained source is `setup.md` at the repo root.

Nothing about the pipeline needs the JupyterHub — it needs Python, ~35 GB of
scratch disk, and credentials that can write to the target bucket. The steps are
the same on a bare Linux VM and on a Mac; where they differ it is called out.

On a truly minimal VM image, install the basics first — the steps below assume
`git`, `curl`, and (for detaching long runs) `tmux` exist:

```sh
sudo apt-get update && sudo apt-get install -y git curl tmux
```

## 1. Python environment

Python 3.11+ (3.12 is what the pipeline was validated on). The dependencies are
`xarray`, `dask`, `h5netcdf`, `h5py`, `gcsfs`, `pandas`, `numpy`, `requests` —
all of them ship prebuilt for Linux x86-64 and Apple Silicon either way you
install, so there is no compiler or system HDF5 to set up; `h5py` is the wheel
that carries HDF5.

`h5py` is listed explicitly on purpose. It is an *optional extra* of `h5netcdf`
(`h5netcdf[h5py]`), not a hard dependency, so installing `h5netcdf` alone gives
you an engine with no HDF5 backend — which fails only on the first file open,
after a block has already been downloaded (issue #8). Install from one of the
manifests below rather than by hand and this is taken care of; `nodd.py`
also checks for it up front and exits before downloading anything.

Three manifests are checked in, one per tool — `RFROMV/requirements.txt` (venv +
pip), `RFROMV/pixi.toml` (pixi), `RFROMV/environment.yml` (conda/mamba). They
declare the same dependency set, so they are interchangeable, and they cover both
products. A bare VM has none of these tools preinstalled, so each option below
starts from nothing.

```sh
git clone https://github.com/nmfs-opensci/gobai-rfrom-icechunks.git
cd gobai-rfrom-icechunks/RFROMV
```

### Option A — venv + pip (lightest; nothing beyond Python itself)

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

Then create and populate the environment. Make sure the venv is activated
(`source .venv/bin/activate`):

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### Option B — pixi (self-contained, no root, no system Python)

pixi is a single binary and brings its own Python, so it needs neither `sudo`
nor a usable system interpreter — handy on a locked-down or minimal VM:

```sh
curl -fsSL https://pixi.sh/install.sh | sh     # or: wget -qO- https://pixi.sh/install.sh | sh
# macOS alternative: brew install pixi
```

That drops the binary in `~/.pixi/bin` and adds it to your shell profile's PATH.
Start a new shell (or `source ~/.bashrc`) so `pixi --version` resolves. Then, in
`RFROMV/`:

```sh
pixi install                             # solves + installs from pixi.toml
pixi shell                               # activated shell; run python directly
# or run without activating, args passed through:
pixi run nodd --stream temp_stable --all
```

Update it later with `pixi self-update` (or `brew upgrade pixi` if installed
that way — don't mix the two).

### Option C — conda / mamba

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

## 2. Scratch space

Point `NODD_SCRATCH_DIR` at any writable path with room to spare. The script
creates the directory tree itself (`erddap/` for monthly downloads, `nodd/` for
assembled output), so there is no `mkdir` to do by hand — but the filesystem must
actually have the space, and on a cloud VM that usually means an attached data
disk rather than the small boot disk. If unset, the default is per-product —
`/home/jovyan/shared-public/rfromv-scratch` for RFROM streams,
`/home/jovyan/shared-public/gobai-scratch` for GOBAI streams — so an explicit
override is required off-hub either way.

```sh
export NODD_SCRATCH_DIR="$HOME/rfromv-scratch"       # VM: e.g. /mnt/data/rfromv-scratch
df -h "$(dirname "$NODD_SCRATCH_DIR")"               # confirm ~35 GB free
```

By default the script deletes each block's downloads and its uploaded output
before moving to the next block, so peak usage stays at roughly one block
(~31 GB) rather than growing across the run. `--keep-scratch` disables that
cleanup — do not combine it with `--all` unless you have hundreds of GB free.

## 3. Credentials for pushing to NODD

Uploads go to the stream's product bucket (`gs://noaa-oar-rfrom` for RFROM
streams, `gs://noaa-oar-gobai` for GOBAI streams) and need an account that has
been granted write access there. Read access is public, so a successful listing
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

export NODD_GCS_TOKEN="$HOME/.config/gcloud/application_default_credentials.json"
```

On a headless VM with no browser, use `gcloud auth application-default login
--no-browser`: it prints a command to run on a machine that *does* have a
browser, and you paste the result back.

**b. Service-account key file (unattended VM runs).** Nothing to install — copy
the key onto the VM and point at it:

```sh
export NODD_GCS_TOKEN=/path/to/service-account-key.json
```

**c. A GCE VM with an attached service account** — no key file needed:

```sh
export NODD_GCS_TOKEN=google_default
```

Verify write access *before* starting a long run, since a permissions failure
would otherwise surface only after the first ~23 GB download (swap in your
stream's bucket/prefix):

```sh
python -c "
import gcsfs, os
fs = gcsfs.GCSFileSystem(token=os.environ['NODD_GCS_TOKEN'])
print(fs.ls('noaa-oar-rfrom/netcdf/v2.3/temp_stable')[:3])   # read
fs.pipe('noaa-oar-rfrom/netcdf/v2.3/_write_check.txt', b'ok'); print('write OK')
fs.rm('noaa-oar-rfrom/netcdf/v2.3/_write_check.txt')
"
```

The script itself fails fast with a clear message if `NODD_GCS_TOKEN` points at
a file that does not exist.

## 4. Run it

```sh
source .venv/bin/activate                             # or: conda activate rfromv-nodd
export NODD_SCRATCH_DIR="$HOME/rfromv-scratch"
export NODD_GCS_TOKEN="$HOME/.config/gcloud/application_default_credentials.json"

python ../nodd.py --stream temp_stable --list          # plan only: no creds, no download
python ../nodd.py --stream temp_stable --blocks 0      # smoke-test one block end to end
python ../nodd.py --stream temp_stable --all           # the production run
```

Under pixi, drop the activation line and prefix each command with `pixi run`,
e.g. `pixi run nodd --stream temp_stable --all`.

The run prints the resolved scratch directory and destination prefix at startup —
check those two lines before walking away.

## 5. Long runs

A full stream is many hours, so do not run it in a foreground shell that a
dropped SSH session or a sleeping laptop can kill:

```sh
tmux new -s rfrom                                     # then run inside; detach with Ctrl-b d
# or, without tmux:
nohup python ../nodd.py --stream temp_stable --all > temp_stable.log 2>&1 &

# macOS: keep the machine awake for the whole run
caffeinate -i python ../nodd.py --stream temp_stable --all
```

Interruptions are cheap. The script skips any block already present in the
bucket, and partial downloads are written to a `.part` file and only renamed on
completion, so a truncated file is never reused. Just rerun the same command and
it picks up where it left off.

## Resource expectations (per stream)

RFROM (`temp_stable`, measured) and GOBAI (same array shapes, same source
layout) both track this table; GOBAI's totals are slightly larger because its
record is 18 blocks instead of 17.

| | RFROM | GOBAI |
|---|---|---|
| monthly source files per block | 23 (~1 GB each) | 23 (~1 GB each); block 17 has 5 |
| output file per block | ~7.6 GB | ~7–8 GB |
| peak scratch disk | ~31 GB (one block, default cleanup) | ~31 GB |
| blocks per stream | 17 | 18 |
| total downloaded per stream | ~390 GB | ~410 GB |
| total uploaded per stream | ~130 GB | ~130 GB |
| RAM | 8 GB minimum, 16 GB comfortable | same |

Wall-clock is dominated by download and upload, so it tracks your network
throughput far more than your CPU — a bigger instance does not speed it up. The
hundreds of GB of ingress are the reason to prefer a well-connected VM over a
laptop on a home or metered connection. Memory stays modest because dask reads
one pressure plane at a time (~415 MB per chunk, a few in flight at once).

## More detail

- `python nodd.py --help` — every flag, with the full stream list and defaults.
- [`RFROMV/README.md`](RFROMV/README.md) / [`GOBAI-O2/README.md`](GOBAI-O2/README.md)
  — per-product quickstart, stream tables, and CF metadata notes.
- [`claude/notes/nodd-prep.md`](claude/notes/nodd-prep.md) and
  [`claude/notes/nodd-batch-script.md`](claude/notes/nodd-batch-script.md) — the
  rationale behind each pipeline stage and design decision.
