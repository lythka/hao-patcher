# golden-gate-icon-caches

IconServices' caches of this OS build, so Language Chooser, Setup and the first desktop draw icons from the cache
instead of rendering them in the hao-translated iconservicesagent. One input, `icon_caches_golden_gate`, made by
`cache/seeds/make` from a laptop harvest of an image of the same build (`cache/seeds/README.md`).

| From the artifact | Installed at | Read by |
|---|---|---|
| `icon-caches.tar`: `Library/Caches/com.apple.iconservices.store/` | as harvested (`_iconservices` 240:80, 0711) | every account's iconservicesagent |
| `icon-caches.tar`: system accounts' `C/com.apple.iconservices/{store.index,__cache_info__}` with their ancestors | `private/var/folders/zz/...`, owners and modes as harvested | root (Language Chooser), `_mbsetupuser` (248) |
| `user/store.index`, `user/__cache_info__` | `Library/Application Support/PerUserSeed/is/` | golden-gate-user-seed copies them into each new account |

Post patch; list it after golden-gate-user-seed (apply.sh refuses otherwise) and refuses an artifact for another build.

## Format (FACT 2026-11-11, 26B123 host and the 26B131 harvest)
Store `store-info.plist` version 4 and `<UUID>.isdata` units as in Tahoe. Index `store.index` version 11, 4000
buckets for a new account, **172-byte records** (Tahoe 25G332: 152); harvest: root 168, Setup 598, user 801 records,
540 store units (937 MB). The index's `__cache_info__` must name the running build or the agent resets the index.
Shipped as harvested; the earlier Tahoe image project's icon generator (universe, render, append) needs the 27 record layout before
it can add records for icons no harvest drew.

## Limits
A bundle icon's digest includes the bundle folder's mtime; records serve the next image only for bundles whose folder
mtimes the build reproduces (OPEN: compare the next image's iconservicesagent log, "Rejected cached image" and
"Generated image", with a run of bf10de4). Icons drawn on bf10de4 only: Language Chooser, Setup, ~20 min of a first
desktop.

## Declared files
`patch.json` `files` lists every file apply.sh installs on the Data volume: the 673 regular files of `icon-caches.tar` (store
units, `store-info.plist`, the system accounts' indexes) and `PerUserSeed/is/{store.index,__cache_info__}`, each a whole
file from row `icon_caches_golden_gate`, so there are no edit specs. The list is derived from the artifact's manifest in
`tests/test_edits.py`, which also runs apply.sh on a fixture; it is exact for this row's artifact and goes stale when the
row changes. Not declared: folders and their owners, and the per-account copy golden-gate-user-seed makes at run time
(its path holds the account's uid and cache-folder hash).

## Verify
Declaration: `python3.14 -I -B -m unittest discover -s patches/golden-gate-icon-caches/tests` (with ICON_CACHES_ARTIFACT=the artifact folder it also compares the artifact manifest).
Host: `patches/golden-gate-user-seed/tests` (the seed chain). On the laptop: iconservicesagent CPU at Setup + 9 min
against bf10de4 (1:24 Setup, 2:13 benchtest at 08:22); `log show --predicate 'subsystem == "com.apple.iconservices"'`
with private data shows records served, not generated.

## Validation
Laptop 2026-11-11, image 4d1c48c (26B131): iconservicesagent was not among the busiest processes during first boot (bf10de4 without the seed: 1:24 CPU at 9 min).
Earlier: built and chain-tested on the host only; no image with it has booted.
r2 changes only whether the patch is planned (patch.json "optional"); apply.sh and inputs are r1's, so the result above stands.

## Revisions
- r5 2026-11-12 Declare where each installed file is inside its row ("source"; tar members as ARCHIVE!MEMBER) for pipeline/materialize.py; the hook is unchanged.
- r4 2026-11-12 Declare the installed files in patch.json `files` (whole files from the row; test derives them from the artifact manifest); the hook is unchanged.
- r3 2026-11-11 Row `icon_caches_golden_gate` (26B131) now names the pack from the 4d1c48c laptop harvest (668 store units, was 540). No recipe change.
- r2 2026-11-11 Optional (group caches): a build without the harvest row or its store artifact skips this patch instead of stopping. apply.sh unchanged.
- r1 2026-11-11 IconServices store and indexes harvested from 26B131 (bf10de4), with the 27 index checks. Root cause: a54ec244 removed Tahoe's icon-caches and no 27 artifact existed, so every account rendered every icon in the translated agent.
