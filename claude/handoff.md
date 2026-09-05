# Handoff

Rolling index of session state. Keep this lean — a pointer to topic notes in
`claude/notes/`, not a copy of them.

## Repo state

- Repo: `nmfs-opensci/gobai-rfrom-icechunks`, working on `/home/jovyan/gobai-rfrom-icechunks`.
- Branch `main`, clean. **One open PR: #29** (issue #26, GOBAI HR Icechunk) on
  branch `issue-26-gobai-icechunk` — written and verified, waiting on Eli to
  merge. Every earlier task branch is merged and deleted.
- **Open issues: #26** (GOBAI HR virtual Icechunk — done, closes with PR #29),
  **#21** (RFROM v2.2 Ocean Heat Content → NODD, not started), **#23** (pandas
  warning, cosmetic).
- `nodd.py` (repo root) is the batch script for every stream of both products:
  RFROM v2.3 (`temp`, `sal`, `temp_error`, `sal_error`), v2.2/v2.1
  (`temp_v22`, `sal_v22`, `temp_v21`), GOBAI HR (`o2`, `no3`).
  `build_icechunk.py` (repo root) builds the virtual Icechunk stores.
  `requirements.txt` covers `nodd.py`; `requirements-icechunk.txt` covers
  `build_icechunk.py`. Off-hub setup is venv+pip only, walkthrough in `setup.md`
  (also `python nodd.py --setup`).
- **Published:** `gs://noaa-oar-rfrom/` holds `netcdf/v2.1`, `v2.2`, `v2.3`
  (72 files, 527 GB), the virtual store `icechunk/v2.3` (2.4 MB), and
  `index.html`. `gs://noaa-oar-gobai/` holds `netcdf/v202606/{o2,no3}` (36 files,
  247 GB), the virtual store `icechunk/v202606` (1.17 MB, snapshot
  `MD92HF22BRCTRF47BR60`), and `index.html`. Both buckets are CORS-enabled.
  **Both products are now fully published — netCDFs, store and landing page.**
- `RFROMV/setup_bare_VM.txt` is **Eli's own scratch cheat-sheet** — informal by
  design, overlaps `setup.md` on purpose. Do not tidy or sync it.

## Working principles

- Notebooks run interactively on a JupyterHub; no build/test/lint system.
- Eli can't copy from the TUI — write anything he must paste into a file under
  `/home/jovyan/`.
- **Every new task branches and ends in a PR**, including tasks given in chat.
  Only `CLAUDE.md` and `claude/` commit straight to `main`.
- Resolved decisions get edited into the PR body, not just here.
- Unfixed findings → one GitHub issue per probable root cause.

## Next task

**Nothing is assigned.** Issue #26 is finished and sitting in PR #29; the only
thing outstanding on it is Eli merging and closing. The remaining open issues
(#21 OHC, #23 pandas warning) have not been started and are not queued — ask.

Do not start #21 from this handoff.

## Notes

| note | covers |
|---|---|
| `rfromv-icechunk.md` | the virtual Icechunk design, measurements, the reader recipe, codec/browser findings. **Read before any store work.** |
| `gobai-icechunk.md` | the GOBAI HR store, and three `build_icechunk.py` traps that apply to every store — `commit()` spinning, a repo looking absent after a bulk delete, and cached anonymous reads. **Read with `rfromv-icechunk.md` before any store work.** |
| `nodd-batch-script.md` | `nodd.py` design decisions, stream table, CF resolutions |
| `nodd-prep.md` | the reference single-file pipeline (issue #1) |
| `gobai-nodd.md` | GOBAI HR → NODD recon and validation |
| `rfromv-v21-v22-nodd.md` | v2.2/v2.1 streams |
| `pipeline-history.md` | resolved work: ERDDAP timeout, h5py, off-hub measurements, hub config |

## Facts worth not re-deriving

- **Chunking is a zero-sum trade.** For chunk `(T, 1, Y, X)`: a global map read
  costs `4.15 MB × T` and depends only on the *time* chunk; a point time series
  costs `1719 × Y·X·4` and depends only on the *spatial* tile. Their product is
  fixed by chunk size, so no single grid serves both. The published
  `(100, 1, 180, 180)` is mediocre at both. Browser visualisation wants a
  separate materialized, map-chunked store — not a re-tuned virtual one.
- **R streams netCDF over HTTPS.** Append `#mode=bytes`; measured 4.3 s to open
  and 0.9 s for a slice against a 7.5 GB remote file. `RNetCDF` too. Byte-range
  is a netCDF-C build option, so a download fallback is documented. R's dimension
  order is reversed: `(lon, lat, pressure, time)`.
- **Reading an Icechunk store does not need `virtualizarr`** — build-time only.
- **The bucket browses** via `console.cloud.google.com/storage/browser/...`
  (needs a Google sign-in, but no project or permissions).
  `storage.googleapis.com/<prefix>/` 404s — that is the endpoint, not
  permissions. `?prefix=...&delimiter=/` lists with no account.
- **`index.html` is CDN-cached for an hour.** Verify uploads with `?cb=$RANDOM`
  or a stale copy reads as a failed upload. Viewers see the old page that long.
- **A freshly built Icechunk store looks broken to an anonymous reader for about
  an hour, and this will fool you.** Icechunk's `repo` object holds the branch
  pointer *and* the snapshot index, and on a public bucket it is served
  `max-age=3600`. So an anonymous read returns the pre-commit snapshot, and even
  an explicit snapshot id raises `SnapshotNotFoundError` — on a store that is
  perfectly fine. The same calls with credentials are correct immediately.
  Measured convergence: 1/15 correct at ~6 min, 5/10 at ~28, 12/12 at ~64.
  Cache-busting cannot help — the URL is inside icechunk. **Diagnose with
  credentials, and delete nothing on the strength of an anonymous read.** This
  cost a store: see §3–§4 of `gobai-icechunk.md`.
- **`build_icechunk.py` commits with `rebase_tries=0` on purpose.** Icechunk
  defaults it to 1000 and will retry a spurious conflict for hours. These builds
  are single-writer, so a conflict is always a bug. Do not restore the default.
- **`gobai.css` out-specifies naive selectors** — `nav a:link` is (0,1,2). And
  its `margin: 0 auto` centring dies if you set the `margin` shorthand. There is
  no browser on the hub, so the cascade must be reasoned about or simulated.
- **Only the hub has the scientific stack preinstalled**, which is how both the
  h5py bug (#8) and the missing icechunk (#17) got through. Assume nothing is
  installed off-hub.
- Every code block in `RFROMV/index.html` and `RFROMV/README.md` was executed
  before publishing. Keep that standard.

## Follow-ups (not blocking)

- A bad `--blocks` value raises a raw `ValueError` rather than a clean argparse error.
- `requirements.txt` is unverified by a clean-venv install — exactly how #8 got through.
- Nothing in the off-hub path has been run end to end from a bare VM by an agent.
- `update_nodd.py` — weekly realtime reconcile, replacing the affected tail
  block(s). Note that block 16 is named `..._STABLE_REALTIME_...`; promoting
  those weeks renames the file and strands the store's reference to it, so the
  first promotion needs a store rebuild, not just a `realtime_start` change.
  Also: **every store rebuild has the hour-long stale-read window above**, so
  automating frequent updates needs that settled first.
- `unsafe_use_metadata` on the icechunk storage settings would let it tell a lost
  response from a real conflict on GCS. Not enabled — `rebase_tries=0` makes the
  failure loud and cheap instead. Revisit if commits ever fail for real.
- Root `README.md` could use a `## Reuse and citation` section (Apache-2.0 → attribution).
- **Project memory on this hub is not backed up** — `eeholmes/claude-config#1`.
  Do not re-run `bootstrap.sh` here until the `gridlook` divergence is merged.
