# Handoff

Rolling index of session state. Keep this lean — a pointer to topic notes in
`claude/notes/`, not a copy of them.

## Repo state

- Repo: `nmfs-opensci/gobai-rfrom-icechunks`, working on `/home/jovyan/gobai-rfrom-icechunks`.
- Branch: `main` (task branch `rfromv-nodd-processing` merged via PR #4 and deleted).
- Open issue #5: batch script to process all RFROM ERDDAP netCDFs → NODD (the
  next major task; spec in `claude/notes/nodd-batch-script.md`).
- Uncommitted/untracked: `CLAUDE.md` (new, from `/init`), `claude/` (new),
  `RFROMV/upload_to_nodd.ipynb` (Eli's — leave it alone).

## Working principles

- Notebooks run interactively on a JupyterHub; no build/test/lint system.
- Eli can't copy from the TUI — write anything he must paste into a file under
  `/home/jovyan/`.
- Commit to `main` only for handoff-only changes; everything else branches + PR.
- Unfixed findings → one GitHub issue per probable root cause.

## In progress / next

- **NODD prep** (issue #1, DONE/merged): reference notebook
  `RFROMV/prep-one-netcdf-for-NODD.ipynb`. Detail in `claude/notes/nodd-prep.md`.
- **NEXT MAJOR TASK — RFROM batch script**: turn the notebook into a
  stream-parameterized script that processes all six streams and uploads to NODD.
  Full spec, the confirmed six-stream table (dataset ids, variables, units, file
  patterns, time extents, block counts), the CF/standard_name resolutions, and
  the open decisions are in **`claude/notes/nodd-batch-script.md`**. Key
  operational constraint: run ONE stream at a time, likely one VM per type — so
  the script must be `--stream`-parameterized and idempotent (skip already-uploaded
  blocks). Streams: `temp_stable temp_realtime temp_error sal_stable sal_realtime
  sal_error`.

## Follow-ups (not blocking)

- README.md could use a `## Reuse and citation` section (Apache-2.0 → attribution).
