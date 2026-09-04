# Handoff

Rolling index of session state. Keep this lean — a pointer to topic notes in
`claude/notes/`, not a copy of them.

## Repo state

- Repo: `nmfs-opensci/gobai-rfrom-icechunks`, working on `/home/jovyan/gobai-rfrom-icechunks`.
- Branch `main`, clean, no open PRs. Every task branch to date is merged and deleted.
- **Open issues: #26** (GOBAI HR virtual Icechunk — *the next task*), **#21**
  (RFROM v2.2 Ocean Heat Content → NODD, not started), **#23** (pandas warning,
  cosmetic).
- `nodd.py` (repo root) is the batch script for every stream of both products:
  RFROM v2.3 (`temp`, `sal`, `temp_error`, `sal_error`), v2.2/v2.1
  (`temp_v22`, `sal_v22`, `temp_v21`), GOBAI HR (`o2`, `no3`).
  `build_icechunk.py` (repo root) builds the virtual Icechunk stores.
  `requirements.txt` covers `nodd.py`; `requirements-icechunk.txt` covers
  `build_icechunk.py`. Off-hub setup is venv+pip only, walkthrough in `setup.md`
  (also `python nodd.py --setup`).
- **Published today:** `gs://noaa-oar-rfrom/` holds `netcdf/v2.1`, `v2.2`,
  `v2.3` (72 files, 527 GB), the virtual store `icechunk/v2.3` (2.4 MB), and
  `index.html`. `gs://noaa-oar-gobai/` holds `netcdf/v202606/{o2,no3}`, 18 files
  each. Both buckets are CORS-enabled.
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

**Issue #26 — GOBAI HR virtual Icechunk.** Blocked on rebuilding two netCDF
blocks; `build_icechunk.py`'s `gobai_hr` config already exists. The issue carries
the full plan, including the follow-on landing-page and README work. Read
`claude/notes/rfromv-icechunk.md` first — GOBAI is the same machinery.

## Notes

| note | covers |
|---|---|
| `rfromv-icechunk.md` | the virtual Icechunk design, measurements, the reader recipe, codec/browser findings. **Read before any store work.** |
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
- Root `README.md` could use a `## Reuse and citation` section (Apache-2.0 → attribution).
- **Project memory on this hub is not backed up** — `eeholmes/claude-config#1`.
  Do not re-run `bootstrap.sh` here until the `gridlook` divergence is merged.
