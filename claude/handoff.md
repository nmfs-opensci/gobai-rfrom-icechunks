# Handoff

Rolling index of session state. Keep this lean — a pointer to topic notes in
`claude/notes/`, not a copy of them.

## Repo state (2026-09-17)

- Repo: `nmfs-opensci/gobai-rfrom-icechunks`, at `/home/jovyan/gobai-rfrom-icechunks`.
- Branch `main`, clean. **No open PRs.** PR #35 (moved the GOBAI-O2 monthly notebook
  out — see below) was squash-merged on 2026-09-17 and its branch deleted; PR #32
  (reproducibility review) on 2026-09-16.
- PRs #29–#31 were **squash-merged**. A branch stacked on an unmerged PR has to be
  rebased onto `main` afterwards (`git rebase --onto origin/main <old tip>`).
- **Open issues:** #34 (**next task** — see below), #33 (reproducibility
  cleanup checklist from the #32 review), #21 (RFROM v2.2 Ocean Heat Content → NODD, not started), #23 (pandas warning,
  cosmetic).
- **Published:** `gs://noaa-oar-rfrom/` holds `netcdf/v2.1`, `v2.2` and `v2.3`,
  the virtual store `icechunk/v2.3`, `index.html` and `viewer/`.
  `gs://noaa-oar-gobai/` holds `netcdf/v202606/{o2,no3}`, `icechunk/v202606`
  (snapshot `MD92HF22BRCTRF47BR60`), `index.html` and `viewer/`. Both products
  are fully published. The RFROM viewer has not been opened in a browser yet.

## What PR #32 changed (merged)

The goal was that a newcomer can rebuild the netCDFs and stores from the docs.
Summary; the detail is in `claude/notes/reproducibility-review.md` and
`pipeline-history.md`:

- **Root README:** a "Rebuilding from scratch" recipe, a file map, and a reuse
  statement (Apache-2.0, attribution).
- **Retired RFROM streams removed:** `temp_stable`/`temp_realtime`/`sal_stable`/`sal_realtime`
  are gone from code and docs, and `migrate_v23.py` is deleted. The
  migration finished 2026-09-04.
- **No hub assumptions.** Defaults are `~/rfromv-scratch` / `~/gobai-scratch`
  and `~/.config/gcloud/...`. **On the hub, set `NODD_SCRATCH_DIR` to use
  `shared-public`.**
- **Pinned install:** `requirements.lock` plus `constraints.txt`, for Python 3.12,
  with the versions the published store was built with. Tested in a clean venv
  with a full local GOBAI store build, and an o2 block 17 rebuild that is
  identical to the published file.
- **Fixes:** a 3.12-only f-string in `build_icechunk.py`, and
  `NODD_GCS_TOKEN=google_default` failing in the store build (now uses
  Icechunk's `from_env=True`).
- **`setup_bare_VM.txt`** moved to the repo root and is now **maintained**,
  updated to the current commands. The old "don't tidy it" rule is withdrawn.
- **`CLAUDE.md`** was rewritten for the current layout; the old-stream history
  moved to `pipeline-history.md`.

## GOBAI-O2 v2.3 monthly left this repo (2026-09-17)

`GOBAI-O2/gobai-o2-monthly-icechunk-sc.ipynb` was never part of this pipeline — it
builds the **monthly** NCEI product as a materialized Icechunk store on Source
Cooperative, which is why CLAUDE.md had to carry a standing "do not mix these"
warning. It now lives in [`fish-pace/icechunks`](https://github.com/fish-pace/icechunks)
under `gobai-o2-monthly/`, with a README, a `requirements.txt` and a notebook that runs
end to end without credentials. Nothing in this repo's pipeline changes; only the
pointers to it did. Store: `https://data.source.coop/fish-pace/gobai-o2/monthly`.

## Working principles

- Scripts run on a Linux VM or laptop, not normally on the hub. Don't write
  hub paths into defaults or docs.
- Eli can't copy from the TUI — write anything he must paste into a file under
  `/home/jovyan/`.
- **Every new task branches and ends in a PR**, including tasks given in chat.
  Only `CLAUDE.md` and `claude/` commit straight to `main`. The exception is a
  `CLAUDE.md` change that describes unmerged work: that goes in the PR.
- Resolved decisions get edited into the PR body, not just here.
- Unfixed findings → a GitHub issue (a checklist issue like #33 is fine for small ones).

## Next task

**Issue #34, when Eli says to start** (don't start it unprompted):

1. Record when each netCDF's sources were pulled from ERDDAP (download date,
   dataset ids, ERDDAP last-update time, last time step), and carry that
   through to the store.
2. An update script (`update_nodd.py` or `nodd.py --update`) that adds new
   ERDDAP weeks to the short tail block. For example, a tail with weeks 1–20
   gains week 21; at 100 steps it becomes a full block and a new tail starts.
   It then rebuilds the store in the same run, because overwriting a netCDF
   breaks the store's byte-range references. Also: tail names change with the
   end date, RFROM promotion renames blocks and moves `realtime_start`, and
   there is the hour-long stale anonymous read.

The issue lists the known traps. Read `rfromv-icechunk.md` and
`gobai-icechunk.md` before designing. #33, #21 and #23 are not queued.

## Notes

| note | covers |
|---|---|
| `rfromv-icechunk.md` | the virtual Icechunk design, measurements, reader recipe, codec/browser findings, recorded versions (§11). **Read before any store work.** |
| `gobai-icechunk.md` | the GOBAI HR store, and three `build_icechunk.py` traps that apply to every store. **Read with `rfromv-icechunk.md` before any store work.** |
| `reproducibility-review.md` | the #32 review, what was fixed, and the lock-file test record |
| `pipeline-history.md` | resolved work: ERDDAP timeout, h5py, the six → four stream restructure, the hub-free defaults |
| `nodd-batch-script.md` | `nodd.py` design decisions and CF resolutions (historical six-stream layout) |
| `nodd-prep.md` | the reference single-file pipeline (historical) |
| `gobai-nodd.md` | GOBAI HR → NODD recon and validation |
| `rfromv-v21-v22-nodd.md` | v2.2/v2.1 streams |

## Facts worth not re-deriving

- **The hub is not a test bed for full runs.** The container is capped at
  ~1.9 GB, shared by every session. The `notebook` env is now Python 3.11
  **without icechunk**. For store work on the hub, make a venv with
  `/srv/conda/bin/python3.12 -m venv …` and `pip install -r requirements.lock`.
  A 19-step block (block 17) fits in memory; a full 100-step block (~8 GB)
  does not.
- **Chunking is a zero-sum trade.** For chunk `(T, 1, Y, X)`, a global map read
  costs `4.15 MB × T` and a point time series costs `1719 × Y·X·4` bytes, so no
  grid serves both. Browser visualisation wants a separate map-chunked store.
- **A freshly built store looks broken to anonymous readers for about an hour**
  (the branch pointer is cached for 3600 s). Diagnose with credentials, and delete
  nothing because of an anonymous read. See §3–4 of `gobai-icechunk.md`.
- **`commit()` uses `rebase_tries=0` on purpose.** Don't restore the default.
- **Don't `--force` a published netCDF casually.** The store references byte
  ranges inside it.
- **`index.html` is CDN-cached for an hour.** Verify uploads with `?cb=$RANDOM`.
- **`npm run build` of gridlook is OOM-killed on the hub.** `publish_viewer.py`
  uses `vite build --sourcemap false` with a 500 MB heap. To republish, run
  `python publish_viewer.py --product gobai --build ~/gridlook`, then
  `--product rfrom --dist /tmp/gridlook-dist`.
- **R streams netCDF over HTTPS** with `#mode=bytes`, and its dimension order is
  reversed. Reading an Icechunk store does not need `virtualizarr`.
- **`gobai.css` out-specifies naive selectors**, and there is no browser on the
  hub, so reason about the cascade.
- Every code block on the landing pages and in the product READMEs was executed
  before publishing. Keep that standard.

## Follow-ups (not blocking)

- Everything small is in #33.
- `unsafe_use_metadata` on the Icechunk storage settings is not enabled;
  `rebase_tries=0` makes failures loud instead. Revisit if commits ever fail
  for real.
- **This project's memory is now symlinked** into `~/claude-config`
  (`claude/memory/-home-jovyan-gobai-rfrom-icechunks/`), as of 2026-09-17, so edits land
  in the repo and only need committing — no hand mirroring. `icechunks`, `agent-skills`,
  `xpublish-erddap` and `ohw24-…` are linked too.
  **`gridlook` is still a real directory whose contents have diverged from the repo copy
  (`eeholmes/claude-config#1`), so do not run `bootstrap.sh` here** — it would move the
  hub's gridlook memories aside and link the repo's different set in their place.
