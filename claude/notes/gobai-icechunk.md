# GOBAI HR → virtual Icechunk (issue #26) — design record

Companion to [`rfromv-icechunk.md`](rfromv-icechunk.md), which is the primary
design record for the virtual-store machinery. **Read that one first** — the
chunk-grid rules, the padded-tail pattern, the codec analysis and the reader
recipe all live there and are not repeated here. This note carries only what is
specific to GOBAI HR, plus two `build_icechunk.py` failures that GOBAI hit first
but which apply to every store.

## 1. The blocker, and what it cost

Both GOBAI streams were published with the final 19-step block's time chunk
shrunk to fit (`chunks=(19, 1, 180, 180)`), the same defect that blocked RFROM's
`temp_error`. A virtual store cannot rewrite chunks, so `concat_virtual` rejects
this by name.

Fixed by rebuilding block 17 of each stream against the current `nodd.py`, whose
`unlimited_dims` path keeps the full 100-step time chunk and lets HDF5 pad the
edge chunk:

```sh
python nodd.py --stream o2  --blocks 17 --force
python nodd.py --stream no3 --blocks 17 --force
```

Measured, 2026-09-04 — far cheaper than the issue's estimate of ~25 min each:

| stream | downloaded | wrote | before → after | padding cost | wall clock |
|---|---|---|---|---|---|
| `o2`  | 4.6 GB (5 monthly files) | 1.37 GB | 1.30 → 1.37 GB | 1.05× | ~7 min |
| `no3` | 4.6 GB (5 monthly files) | 1.41 GB | 1.35 → 1.41 GB | 1.05× | ~7 min |

Confirmed on the published objects afterwards: `shape (19, 58, 720, 1440)`,
`chunks (100, 1, 180, 180)`, `maxshape (None, 58, 720, 1440)`. The 1.05× matches
the RFROM measurement on its 19-step `temp_error` tail exactly — the pad is fill
value and compresses to almost nothing.

## 2. The store

```
dims     time 1719 (1993-01-01 → 2025-12-05, weekly)
         mean_pressure 58, latitude 720, longitude 1440
vars     o2, no3                                        float32, both virtual
         chunks (100, 1, 180, 180), gzip-4 + shuffle    [inherited, not chosen]
coords   time, mean_pressure (+ mean_pressure_bnds), latitude, longitude
```

100 % virtual: 36 files, 246.6 GB referenced (o2 121.4, no3 125.2), nothing
copied.

**No `data_mode` coordinate.** `realtime_start` is `None` for `gobai_hr` — GOBAI
HR has no stable/realtime split and no error streams. That path had never been
exercised (RFROM always passes a date); it works, because both uses in
`build_virtual_dataset` are guarded by `cfg.get("realtime_start")`, so neither
the coordinate nor the `realtime_start` global attribute is written. No code
change was needed.

**No migration.** Unlike RFROM v2.3, the tree was published as one continuous
series per variable from the start: no `migrate_v23.py` equivalent, no old
prefixes to retire, and file names sort chronologically because there is no
`STABLE`/`REALTIME` infix to break lexical order.

Header parsing ran 344–378 s per 18-file stream, in line with RFROM's ~17 s per
file.

## 3. `commit()` can spin forever — fixed

**The failure.** The first real build wrote its snapshot and then hung for an
hour inside `session.commit()`. Diagnosed live with `py-spy dump`:

```
Thread (idle): "MainThread"
    commit (icechunk/session.py:452)
    write_store (build_icechunk.py:410)
```

The process had burned 23 s of CPU in 61 minutes — blocked, not working. What the
bucket showed:

- snapshot `Q6ZDV7A3XF6D6KBKGV0G` and its 206 KB transaction log **written at
  21:58:32**, along with every manifest and chunk object;
- then **82 rewrites of the ref object** at exponentially growing intervals
  (0.4 s → 55 s), still going when it was killed.

At the time this was also read as "the branch never moved off the initial empty
snapshot". **That part of the diagnosis was wrong, or at least unfounded**: it
came from anonymous reads, and §4 below shows those can return a stale branch
pointer for up to an hour after a write. The store had already been deleted by
the time that was understood, so whether its commit had actually landed can no
longer be established. What is solid is the pathology itself — an unbounded
retry loop that had not returned after 60 minutes on 23 s of CPU, writing a new
ref generation every ~55 s — and that is what the fix addresses.

**The cause.** `Session.commit()` defaults to `rebase_tries=1000`. On a conflict
it rebases and retries — correct for concurrent writers, wrong here. A transient
GCS conditional-PUT failure on the branch-ref update cannot be told apart from a
real conflict, because GCS lost-response recovery needs write-id stamping that is
off by default; icechunk says so in a warning at every startup:

> conditional PUT is enabled but `unsafe_use_metadata` is disabled — lost-response
> recovery for conditional writes requires user metadata to stamp write-ids;
> without it, transient PUT failures may surface as spurious conflicts even when
> the write actually landed.

Rebasing cannot resolve a conflict that does not exist, so it loops. RFROM's
build printed the same warning and simply got lucky.

**Recovery was not possible either.** Reading the snapshot directly gave
`SnapshotNotFoundError: snapshot id not found Q6ZDV7A3XF6D6KBKGV0G` from
`resolve_ref_version_v2` — though that too was an anonymous read, so treat it
with the same suspicion as the branch-pointer claim above. If this recurs, do the
diagnosis **with credentials** before concluding anything (§4).

**The fix**, in `write_store`: `commit(message, rebase_tries=0)`. These builds are
single-writer by construction — one script, one branch, one commit — so a
conflict is never contention and always a bug. Failing in seconds with a real
error beats spinning for hours.

**Second fix**, in `build`: assert that `main` points at the snapshot just
committed, before validating through it. Without that check the symptom surfaces
as a bare `GroupNotFoundError` from `open_zarr`, which names nothing. This is
distinct from the §6.2 bug in `rfromv-icechunk.md` (validating against the
*writer's* repo object): that one was a false alarm on a good store, this one is a
real failure on a bad one, and they present identically.

**The published RFROM store was checked and is healthy** —
`main → ERWNPYN2CDCJ5SGHHBBG`. No regression there. (That store was committed
hours earlier, so it is well past the caching window of §4.)

## 4. An anonymous reader can see a stale branch pointer for an hour

**This is the single most misleading thing in this whole exercise, and it will
mislead the next person too.** After the successful build, the same store gave
two different answers depending on how it was opened:

```
authenticated   main -> MD92HF22BRCTRF47BR60     (correct, every time)
anonymous       main -> 1CECHNKREP0F1RSTCMT0     (the initial empty snapshot)
```

and the anonymous answer *flapped*: sampled 15 times over 30 s, it returned the
correct snapshot once and the stale one 14 times. Opening it anonymously then
fails as a bare `GroupNotFoundError` naming `snapshot_id:
1CECHNKREP0F1RSTCMT0` — which reads exactly like a store whose commit never
landed.

The cause is ordinary public-object caching. Icechunk keeps the branch pointer in
the `repo` object at the store prefix root, and on a public bucket GCS serves it
with:

```
cache-control: public, max-age=3600
```

So every anonymous reader can hold a pre-commit view of the ref for up to an
hour, and different edges disagree in the meantime. The bucket is not
misconfigured — this is the same hour-long caching already known to affect
`index.html` (`?cb=$RANDOM` to check that one). Note that cache-busting the URL
does **not** help here: the query string is inside icechunk, not something a
caller can add.

Consequences, in order of how much they matter:

1. **Never diagnose a store anonymously right after building it.** Use
   credentials. `build_icechunk.py --validate` does, which is why the build's own
   post-commit check passed while a hand-rolled anonymous check failed.
2. **Do not publish a store's address and immediately tell people to read it.**
   Give it an hour, then verify anonymously.
3. **Every future update has this window**, including the planned weekly realtime
   reconcile for RFROM. Readers who touched the store in the preceding hour may
   briefly see the old snapshot. Since a virtual store's whole point is that
   updates are cheap and frequent, this is worth thinking about before automating
   updates — an unresolved follow-up, not something this issue settles.

## 5. A repository can vanish right after a bulk delete — fixed

After deleting the 100 objects of the failed store, the next build failed at
`open_repo`:

```
icechunk.RepositoryNotFoundError: the repository doesn't exist
  ... icechunk::asset_manager::fetch_repo_info_from_path
  ... icechunk::repository::save_config
```

`Repository.open_or_create` returned an object, then `save_config()` found no
repository, and the prefix was left completely empty. Best reading: a stale GCS
listing shortly after the bulk delete made `open_or_create` take the *open* path
rather than *create*. Retried a few minutes later on the same empty prefix, it
created the repository normally. Transient, not a code defect.

It was expensive anyway, because it surfaced **after** 12 minutes of header
parsing. So `build()` now opens the repository **before** the concat loop rather
than just before the write: creating it is cheap and idempotent, and a
credentials or repository problem should cost seconds, not a full parse.

If a bulk delete is ever needed again, expect this and give the prefix a minute.

## 6. Landing page and README

`GOBAI-O2/index.html` is the source of `gs://noaa-oar-gobai/index.html`. It had
described **GOBAI-O2 v2.3 monthly** (NCEI, Source Cooperative) — a product that is
not in this bucket at all — while the bucket holds only GOBAI HR. Rebuilt to the
shape RFROM's page got in issue #27 / PR #28, per Eli (2026-09-04): keep the
About and the citations, make everything else like RFROM, leave the Source
Cooperative material out entirely, and keep the monthly product visible in
Download only as a pointer to its NCEI accession. Getting the monthly product
onto NODD is a later task, after which it becomes a version block like RFROM's
v2.2.

Removed in the rewrite: a `new Freezeframe({selector: '.ani-maps'})` script that
referenced a class the page does not contain and a library the page never loads —
it threw a `ReferenceError` on every view.

Code blocks executed before publishing, per the standing rule:

| block | result |
|---|---|
| Python, single netCDF via `xr.open_dataset` | opens; `o2` carries `umol kg-1` + `moles_of_oxygen_per_unit_mass_in_sea_water`; file is 7.06 GB, so the page's "~7.1 GB" is right |
| R, `ncdf4` + `#mode=bytes` | `nc_open` 4.0 s, 4×4 slice 1.1 s, values ~142 µmol kg⁻¹; dim order `(longitude, latitude, mean_pressure, time)` as documented. The `&lt;-` escaping survives extraction |

R on this hub is 4.5.1 with `ncdf4` present.

## 7. Still open

- **One store or two?** GOBAI and RFROM share a grid value-for-value and both run
  1719 weekly steps, so merging them is geometrically clean. Against it: different
  products, different versioning cadences, different buckets, and GOBAI declares
  `source = "... RFROM v2.2"` against RFROM's v2.3 product. Defaulted to two
  stores. Revisit only if someone asks.
- **The chunk shape is inherited and bad for map reads.** `(100, 1, 180, 180)`
  means one global map at one time and level touches 32 chunks holding 100 time
  steps each. Same tradeoff, same answer as RFROM: not fixable in a virtual store;
  a browser viewer wants a separate materialized, map-chunked store. See §7–§8 of
  `rfromv-icechunk.md`.
- **`unsafe_use_metadata`** would let icechunk distinguish a lost response from a
  real conflict on GCS. Not enabled — `rebase_tries=0` makes the failure loud and
  cheap instead, which is enough for a single-writer build. Worth revisiting if
  commits ever fail for real.
