# Handoff

Rolling index of session state. Keep this lean — a pointer to topic notes in
`claude/notes/`, not a copy of them.

## Repo state

- Repo: `nmfs-opensci/gobai-rfrom-icechunks`, working on `/home/jovyan/gobai-rfrom-icechunks`.
- Branch: `main`. **No open PRs.** PR #18 (`issue-16-cleanup-help-readme`, issue
  #16) merged 2026-09-03; branch deleted, issue #16 auto-closed. PR #14
  (`gobai-nodd-script`, issue #13) reviewed by Eli 2026-09-03 and merged earlier
  the same day; branch deleted, issue #13 auto-closed. Every earlier task branch
  (`rfromv-nodd-processing` #4, `rfromv-nodd-batch-script` #6, `local-mac-run`
  #7, `scratch-dir-error` #9, `fix-h5py-dep` #10, `fix-erddap-download` #12) is
  also merged and deleted.
- **The batch script lives at the repo root as `nodd.py`** (PR #14), covering
  RFROM's six streams and GOBAI's two. `RFROMV/rfrom_nodd.py`, the back-compat
  shim from that promotion, is **removed** (PR #18/issue #16) — every VM run
  still using it had finished.
- **Off-hub setup is now venv+pip only** (PR #18/issue #16): the single
  `requirements.txt` moved from `RFROMV/` to the repo root next to `nodd.py`;
  `pixi.toml`/`environment.yml` are deleted. The full walkthrough moved out of
  `RFROMV/README.md` into a new root-level **`setup.md`**, also printed by
  `python nodd.py --setup`; `--help` gained an epilog with examples. The three
  READMEs (root, `RFROMV/`, `GOBAI-O2/`) are now quickstart + tables only,
  pointing at `--help`/`--setup` instead of duplicating them.
- Untracked: `RFROMV/upload_to_nodd.ipynb` (Eli's sandbox — leave it alone).
- `RFROMV/setup_bare_VM.txt` is **Eli's own scratch cheat-sheet**, not pipeline
  code and not generated docs — the shell commands he pastes to stand up a bare
  VM (no JupyterHub, nothing preinstalled). It is deliberately informal and
  overlaps `setup.md`; do not tidy it, restructure it, or treat a divergence
  from `setup.md` as a bug. Leave it to Eli unless he asks — it still has
  commented-out `rfrom_nodd.py` example lines, now stale, left for him to
  update himself.

## Working principles

- Notebooks run interactively on a JupyterHub; no build/test/lint system.
- Eli can't copy from the TUI — write anything he must paste into a file under
  `/home/jovyan/`.
- Commit to `main` only for handoff-only changes; everything else branches + PR.
- Unfixed findings → one GitHub issue per probable root cause.

## In progress / next

- **Clean up help and READMEs** (issue #16, DONE — PR #18 merged 2026-09-03,
  branch `issue-16-cleanup-help-readme` deleted). `nodd.py --help` now carries
  an epilog with usage examples; new `python nodd.py --setup` prints a
  root-level `setup.md` extracted from `RFROMV/README.md`'s old "Running
  off-hub" section (Python env, scratch disk, GCS credentials, tmux, resource
  expectations) so there's one copy of it, shared by both products. Follow-up
  from Eli's issue comment, same PR: `RFROMV/rfrom_nodd.py` back-compat shim
  deleted (no VM runs still used it), `requirements.txt` moved from `RFROMV/`
  to the repo root, `pixi.toml`/`environment.yml` removed (venv+pip only). A
  reproducibility pass (imports vs. `requirements.txt`, stale references,
  fresh-clone path correctness, hardcoded paths, committed lockfiles/version
  pins) found no gaps. `RFROMV/setup_bare_VM.txt` untouched, per standing
  guidance below.

- **GOBAI HR → NODD** (issue #13, DONE — PR #14 reviewed and merged 2026-09-03,
  branch `gobai-nodd-script` deleted). Full recon, resolved decisions and the
  validation log are in **`claude/notes/gobai-nodd.md`** — read that first.
  Headlines: GOBAI HR's grid is *identical* to RFROM v2.3 (coords and
  `mean_pressure_bnds` match value-for-value; RFROM's 1670-step axis is an exact
  prefix of GOBAI's 1719), so `rfrom_nodd.py` was promoted to **`nodd.py` at the
  repo root** covering all eight streams, with `RFROMV/rfrom_nodd.py` left as a
  back-compat shim (later removed in issue #16, once VM runs no longer needed
  it). Two streams `o2`/`no3` → `gs://noaa-oar-gobai/netcdf/v202606/`,
  18 blocks each, ~0.41 TB per stream. Validated end to end on both `o2` and `no3`
  block 17: data bit-identical to source, `cfchecker` 0 errors / 0 warnings.
  `cf_refinements` decided: on for GOBAI, off for RFROM (metadata-only, not worth
  reprocessing RFROM's published blocks for — revisit at RFROM's next version
  bump). Env vars are `NODD_SCRATCH_DIR`/`NODD_GCS_TOKEN` (old `RFROM_` names
  still honoured), scratch defaults per-product.
  **Still open:** Eli emailed Sharp 2026-09-03 about the `gobai_no3_hr_v10`
  per-volume standard_name vs. per-mass units mismatch (RFROM-salinity redux) —
  **awaiting Sharp's reply**; do not change the `no3` CF mapping in `nodd.py`
  until that lands.
  **Next: production runs, one VM per stream** —
  `python nodd.py --stream o2 --all` and `python nodd.py --stream no3 --all`.
  `git pull` first on each VM to pick up `nodd.py` from `main`. Idempotent
  (safe to interrupt/re-run, uploaded blocks are skipped). Nothing uploaded to
  `gs://noaa-oar-gobai` yet as of this handoff — still only its `index.html`.

- **ERDDAP download timeout + re-download** (issue #11, DONE — PR #12 merged,
  `main` @ `a28b921`, branch deleted). Both symptoms had one cause: the `requests.head()`
  at the top of `download()`. ERDDAP's `/files/` endpoint serves these netCDFs
  `Content-Encoding: gzip` + `Transfer-Encoding: chunked`, so there is **no
  `Content-Length`** — the skip test `... and remote_size and ...` was dead code
  (`remote_size` always 0) and every re-run re-downloaded ~12 GB per block. The same
  HEAD is the crash site: measured from the hub it takes **36–45 s** against the old
  `timeout=60`, and there was no retry anywhere in the download path. That is the
  hub-vs-VM split Eli saw — steady on the hub all day, dead on all 3 VMs: everyone
  is near the cliff, only the VMs cross it. The forgotten `git pull` on the VM was a
  red herring; `download()` was untouched since #5. Fix: HEAD removed outright (it
  was useless *and* the crash site); completeness is now stream-to-`.part` →
  verify it opens as HDF5/netCDF → atomic rename, so an existing file is known
  complete (Range is unsupported — 416 — so a retry restarts that one file);
  4 attempts with 15/30/60 s backoff, 4xx fails fast, `.part` cleaned up on every
  failure path. Verified live: 46 s download, 0.00 s skip on re-run, truncation
  rejected, 404 in 1.1 s without retry, injected mid-stream error recovers on
  attempt 3. Diagnostic for Eli at `/home/jovyan/erddap-head-check.sh` (curl-only,
  run on a VM to compare HEAD latency with the hub) — optional, since the fix
  deletes the cliff rather than moving it.
- **`settings.json` work (still deferred; #11 is now closed).** This is Claude Code config,
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
  preinstalled; that is why this class of bug is invisible here.** Eli has since
  installed h5py on the VM and moved on to `sal_realtime`, which hit issue #11.
- **RFROM batch script** (issue #5, DONE — PR #6 merged):
  `RFROMV/rfrom_nodd.py`, the notebook generalized to all six streams
  (`--stream`-parameterized, `--blocks`/`--all`, idempotent). Full spec, the
  confirmed six-stream table, CF/standard_name resolutions, and resolved decisions
  are in **`claude/notes/nodd-batch-script.md`**. Salinity is absolute salinity
  (TEOS-10) g/kg (ERDDAP mislabels it PSU — corrected, see the note).

## Follow-ups (not blocking)

- A bad `--blocks` value raises a raw `ValueError` traceback rather than a clean
  argparse error. Pre-existing, untouched by #13.
- `~7.5 GB` of GOBAI scratch is on the hub at
  `/home/jovyan/shared-public/gobai-scratch/` (two sample monthly files, block
  17's five sources, and its output). Delete when prototyping is done.

- `requirements.txt` (root) is unverified by installation — nothing in this repo
  installs it into a clean venv from scratch, which is exactly how issue #8 got
  through. A clean-venv smoke install would catch the next one. (Narrower than
  before #16: it's now the only manifest, no more pixi/conda copies to keep in
  sync.)
- Nothing in the off-hub path has been run end to end from a bare VM by an agent;
  Eli has done it by hand. If it is ever automated, that is the gap to close.
- `update_nodd.py` — weekly realtime reconcile (re-download the moving realtime
  dataset, replace the affected tail block(s)). Out of scope for the first cut.
- README.md could use a `## Reuse and citation` section (Apache-2.0 → attribution).
