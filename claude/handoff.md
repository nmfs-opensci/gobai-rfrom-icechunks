# Handoff

Rolling index of session state. Keep this lean — a pointer to topic notes in
`claude/notes/`, not a copy of them.

## Repo state

- Repo: `nmfs-opensci/gobai-rfrom-icechunks`, working on `/home/jovyan/gobai-rfrom-icechunks`.
- Branch: `main` (task branch `rfromv-nodd-processing` merged via PR #4 and deleted).
- No open GitHub issues.
- Uncommitted/untracked: `CLAUDE.md` (new, from `/init`), `claude/` (new),
  `RFROMV/upload_to_nodd.ipynb` (Eli's — leave it alone).

## Working principles

- Notebooks run interactively on a JupyterHub; no build/test/lint system.
- Eli can't copy from the TUI — write anything he must paste into a file under
  `/home/jovyan/`.
- Commit to `main` only for handoff-only changes; everything else branches + PR.
- Unfixed findings → one GitHub issue per probable root cause.

## In progress / next

- **NODD prep** (issue #1, DONE/merged): see `claude/notes/nodd-prep.md`.
- **Next major task**: RFROM batch `.py` scripts. Blockers to resolve first are
  listed at the bottom of `claude/notes/nodd-prep.md` (salinity standard_name
  conflict; error-dataset variable/structure).

## Follow-ups (not blocking)

- README.md could use a `## Reuse and citation` section (Apache-2.0 → attribution).
