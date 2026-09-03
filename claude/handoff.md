# Handoff

Rolling index of session state. Keep this lean — a pointer to topic notes in
`claude/notes/`, not a copy of them.

## Repo state

- Repo: `nmfs-opensci/gobai-rfrom-icechunks`, working on `/home/jovyan/gobai-rfrom-icechunks`.
- Branch: `main`. `CLAUDE.md` + `claude/` are committed. Task branches
  `rfromv-nodd-processing` (PR #4), `rfromv-nodd-batch-script` (PR #6) and
  `local-mac-run` (PR #7) are all merged.
- **PR #9 OPEN** (branch `scratch-dir-error`): a one-hunk error-message fix in
  `RFROMV/rfrom_nodd.py` — print the resolved scratch dir *before* `os.makedirs`
  and turn the `OSError` into an argparse error naming `RFROM_SCRATCH_DIR`.
  Tested, awaiting review. https://github.com/nmfs-opensci/gobai-rfrom-icechunks/pull/9
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
- **RFROM batch script** (issue #5, DONE — PR #6 merged):
  `RFROMV/rfrom_nodd.py`, the notebook generalized to all six streams
  (`--stream`-parameterized, `--blocks`/`--all`, idempotent). Full spec, the
  confirmed six-stream table, CF/standard_name resolutions, and resolved decisions
  are in **`claude/notes/nodd-batch-script.md`**. Salinity is absolute salinity
  (TEOS-10) g/kg (ERDDAP mislabels it PSU — corrected, see the note).

## Follow-ups (not blocking)

- After PR #9 merges, delete the `scratch-dir-error` branch (and the already-merged
  `rfromv-nodd-batch-script` / `local-mac-run` branches if still present).
- Nothing in the off-hub path has been run end to end from a bare VM by an agent;
  Eli has done it by hand. If it is ever automated, that is the gap to close.
- `update_nodd.py` — weekly realtime reconcile (re-download the moving realtime
  dataset, replace the affected tail block(s)). Out of scope for the first cut.
- README.md could use a `## Reuse and citation` section (Apache-2.0 → attribution).
