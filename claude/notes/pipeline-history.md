# Pipeline history — resolved work worth not re-deriving

Detail moved out of `claude/handoff.md` when it got long. Everything here is
**done and merged**; the handoff keeps one line per item and points here. Notes
with their own file (`nodd-prep.md`, `nodd-batch-script.md`, `gobai-nodd.md`,
`rfromv-v21-v22-nodd.md`, `rfromv-icechunk.md`) are not duplicated.

## ERDDAP download timeout and endless re-download (issue #11, PR #12)

Two symptoms, one cause: the `requests.head()` at the top of `download()`.

ERDDAP's `/files/` endpoint serves these netCDFs `Content-Encoding: gzip` +
`Transfer-Encoding: chunked`, so there is **no `Content-Length`**. The skip test
`... and remote_size and ...` was therefore dead code (`remote_size` always 0)
and every re-run re-downloaded ~12 GB per block.

The same HEAD was the crash site: measured from the hub it takes **36–45 s**
against the old `timeout=60`, with no retry anywhere in the download path. That
explains the hub-vs-VM split — steady on the hub all day, dead on all three VMs.
Everyone sits near the cliff; only the VMs cross it. The forgotten `git pull` on
the VM was a red herring; `download()` had been untouched since #5.

Fix: the HEAD was removed outright, being useless *and* the crash site.
Completeness is now stream-to-`.part` → verify it opens as HDF5/netCDF → atomic
rename, so an existing file is known complete. Range is unsupported (416), so a
retry restarts that one file. Four attempts, 15/30/60 s backoff, 4xx fails fast,
`.part` cleaned up on every failure path.

Verified live: 46 s download, 0.00 s skip on re-run, truncation rejected, 404 in
1.1 s without retry, injected mid-stream error recovers on attempt 3.

## h5py missing off-hub (issue #8, PR #10)

`h5netcdf` declares `h5py` as an *optional extra* (`h5netcdf[h5py]`), not a hard
dependency, so a pip install produced an engine with no HDF5 backend: `import
h5netcdf` works, xarray lists the engine, and the first `open_mfdataset` dies
with `No module named 'h5py'` — **after** the block's ~12 GB had downloaded.

Fix: `h5py>=3.12` pinned explicitly, plus a `check_netcdf_engine()` preflight
called from `main()` beside the credentials check, so a bad environment fails in
seconds while `--list` still needs no HDF5 stack.

**Only the hub environment has h5py preinstalled, which is why this class of bug
is invisible here.** The same blindness produced the icechunk gap in issue #17 —
see `requirements-icechunk.txt`.

## Running off-hub (PR #7)

`NODD_SCRATCH_DIR` and `NODD_GCS_TOKEN` (formerly `RFROM_`-prefixed, still
honoured) override the two hub paths; hub defaults unchanged.

Measured on the live `temp_stable` run: ~23 GB downloaded and ~7.6 GB written per
block, ~31 GB peak scratch, ~390 GB down / ~130 GB up per stable stream.
**Wall-clock is network-bound, so a bigger instance does not help.** Confirmed on
a bare VM: `gcloud auth application-default set-quota-project` is *not* needed.

## Claude Code configuration on this hub

Not RFROM work, but it bites if assumed. The portable config lives in the
`~/claude-config` repo, symlinked into `~/.claude` by `bootstrap.sh`. Auth and
account toggling (personal vs Bedrock) is machine-local in `~/.bashrc` /
`~/.profile`, with back-out steps in `~/claude-bedrock-toggle-notes.md` (no
credentials in it).

On **this** hub `~/.claude/settings.json` is a standalone real file, not
bootstrapped: edit it directly *and* mirror portable changes into
`~/claude-config/claude/settings.json`.

A non-destructive-git guard is enforced by deny rules plus a
`deny-destructive-git.py` hook — hard resets and force pushes are blocked.

**Project memory on this hub is not symlinked into `~/claude-config`**, so it is
unbacked, and `gridlook`'s two copies have diverged into disjoint sets. Tracked
as `eeholmes/claude-config#1`, which also warns against re-running
`bootstrap.sh` here until that is merged.

## Cleanups (issues #15, #16)

- **#15** folded the two RFROMV sandbox notebooks into
  `prep-one-netcdf-for-NODD.ipynb` and deleted them.
- **#16** gave `nodd.py` an `--help` epilog and a `--setup` that prints the
  root-level `setup.md`; removed the `rfrom_nodd.py` shim; moved
  `requirements.txt` to the repo root; dropped pixi/conda for venv+pip only.
