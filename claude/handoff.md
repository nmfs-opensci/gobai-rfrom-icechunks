# Handoff

Rolling index of session state. Keep this lean — a pointer to topic notes in
`claude/notes/`, not a copy of them.

## Repo state

- Repo: `nmfs-opensci/gobai-rfrom-icechunks`, working on `/home/jovyan/gobai-rfrom-icechunks`.
- Branch: `main`, clean. `CLAUDE.md` + `claude/` are committed. **No open PRs and
  no branches other than `main`, local or remote** — every task branch
  (`rfromv-nodd-processing` #4, `rfromv-nodd-batch-script` #6, `local-mac-run` #7,
  `scratch-dir-error` #9, `fix-h5py-dep` #10) is merged and deleted.
- Untracked: `RFROMV/upload_to_nodd.ipynb` (Eli's sandbox — leave it alone).
- `RFROMV/setup_bare_VM.txt` is **Eli's own scratch cheat-sheet**, not pipeline
  code and not generated docs — the shell commands he pastes to stand up a bare
  VM (no JupyterHub, nothing preinstalled). It is deliberately informal and
  overlaps `RFROMV/README.md` "Running off-hub"; do not tidy it, restructure it,
  or treat a divergence from the README as a bug. Leave it to Eli unless he asks.

## Working principles

- Notebooks run interactively on a JupyterHub; no build/test/lint system.
- Eli can't copy from the TUI — write anything he must paste into a file under
  `/home/jovyan/`.
- Commit to `main` only for handoff-only changes; everything else branches + PR.
- Unfixed findings → one GitHub issue per probable root cause.

## In progress / next

- **IMMEDIATE NEXT TASK — more `settings.json` work.** This is Claude Code config,
  not RFROM data work. The portable config lives in the **`~/claude-config`** repo
  (symlinked into `~/.claude` by `bootstrap.sh`); auth/account toggling (personal
  vs Bedrock) is machine-local in `~/.bashrc`/`~/.profile` with the full back-out
  steps in `~/claude-bedrock-toggle-notes.md` (no creds in it). NOTE: on THIS hub
  `~/.claude/settings.json` is a **standalone real file** (not bootstrapped), so
  edit it directly AND mirror portable changes into `~/claude-config/claude/settings.json`.
  Non-destructive-git guard is enforced (deny rules + `deny-destructive-git.py`
  hook); `git reset --hard` / `push --force` must never run. Full context in the
  memory note `no-permission-prompts-for-innocuous-ops.md` (auto-loaded via MEMORY.md).
- **NODD prep** (issue #1, DONE/merged): reference notebook
  `RFROMV/prep-one-netcdf-for-NODD.ipynb`. Detail in `claude/notes/nodd-prep.md`.
- **Off-hub running** (DONE — PR #7 merged). `rfrom_nodd.py` no longer hardcodes
  the two hub paths: `RFROM_SCRATCH_DIR` (scratch, ~35 GB free needed) and
  `RFROM_GCS_TOKEN` (credentials JSON path, or `google_default`) override them,
  hub defaults unchanged. `RFROMV/{requirements.txt,pixi.toml,environment.yml}`
  carry the same dependency set for venv+pip / pixi / conda, and `RFROMV/README.md`
  has a "Running off-hub (bare VM or macOS)" section. Measured from the live
  `temp_stable` run: ~23 GB downloaded and ~7.6 GB written per block, ~31 GB peak
  scratch, ~390 GB down / ~130 GB up per stable stream; wall-clock is
  network-bound, so a bigger instance does not help. Confirmed on Eli's VM:
  `gcloud auth application-default set-quota-project` is NOT needed.
- **h5py missing off-hub** (issue #8, DONE — PR #10 merged, `main` @ `7d67046`).
  `h5netcdf` declares `h5py` as an *optional extra* (`h5netcdf[h5py]`), not a hard
  dependency, so a pip install from `requirements.txt` produced an engine with no
  HDF5 backend: `import h5netcdf` works, xarray lists the engine, and the first
  `open_mfdataset` dies with `No module named 'h5py'` — after the block's ~12 GB
  had downloaded. Fix: `h5py>=3.12` added to all three manifests; new
  `check_netcdf_engine()` preflight in `rfrom_nodd.py` called from `main()` next
  to the credentials check (so a bad env fails in seconds, and `--list` still
  needs no HDF5 stack); README's "h5netcdf brings its own HDF5" claim corrected.
  Downloads already resume — `download()` skips any file whose size matches the
  ERDDAP `Content-Length` — so Eli re-runs after `pip install h5py` and the 12
  files already in his scratch dir are re-used. **Only the hub env has h5py
  preinstalled; that is why this class of bug is invisible here.** Eli still has
  to install h5py in his VM's `.venv` and finish the `temp_realtime` run.
- **RFROM batch script** (issue #5, DONE — PR #6 merged):
  `RFROMV/rfrom_nodd.py`, the notebook generalized to all six streams
  (`--stream`-parameterized, `--blocks`/`--all`, idempotent). Full spec, the
  confirmed six-stream table, CF/standard_name resolutions, and resolved decisions
  are in **`claude/notes/nodd-batch-script.md`**. Salinity is absolute salinity
  (TEOS-10) g/kg (ERDDAP mislabels it PSU — corrected, see the note).

## Follow-ups (not blocking)

- The off-hub manifests are unverified by installation — nothing in this repo
  installs `requirements.txt`/`pixi.toml`/`environment.yml` from scratch, which is
  exactly how issue #8 got through. A clean-venv smoke install would catch the next
  one.
- Nothing in the off-hub path has been run end to end from a bare VM by an agent;
  Eli has done it by hand. If it is ever automated, that is the gap to close.
- `update_nodd.py` — weekly realtime reconcile (re-download the moving realtime
  dataset, replace the affected tail block(s)). Out of scope for the first cut.
- README.md could use a `## Reuse and citation` section (Apache-2.0 → attribution).
