# golden-gate-font-registry

fontd's font registries of this OS build, so Setup and each new account do not wait while fontd scans every font
from scratch. One input, `font_registry_golden_gate`, made by `cache/seeds/make` from a laptop harvest of an image of
the same build (`cache/seeds/README.md`; the recipe of the earlier Tahoe image project's font-registry recipe r2).

| From the artifact | Installed at | Read by |
|---|---|---|
| `setup.tar`: the Setup account's `C/com.apple.FontRegistry/` (WAL folded in) | `private/var/folders/zz/zyxvpxvq6csfxvn_n00000z000007r/...`, owner 248 | Setup's fontd |
| `user/fontregistry.user` (user-font-folder row reset to `/var/empty/Library/Fonts`, no bookmark) | `Library/Application Support/PerUserSeed/fonts/` | golden-gate-user-seed writes the new account's `~/Library/Fonts` into it and places it |

Post patch; list it after golden-gate-user-seed (apply.sh refuses otherwise) and refuses an artifact for another build.
Split out of golden-gate-user-seed r2/r3 (26A428 registry), so the font seed can be dropped alone.

## Why per build
A registry's version (26B131: 104/88) and its font rows follow the build's fonts; fontd compares each font's
stored bookmark with a fresh one, so a registry from another build is at best rescanned.
26B131 artifact `font-registry-26B131-20261111-81a2b271420f`: 3,921 fonts in both registries; the user seed names no
`/Users/benchtest` path.

## Declared files
`patch.json` `files` lists the two files apply.sh installs on the Data volume, both whole files from row
`font_registry_golden_gate` (no edit specs): the Setup account's `C/com.apple.FontRegistry/fontregistry.user` and
`PerUserSeed/fonts/fontregistry.user`. `tests/test_edits.py` derives them from the artifact manifest and runs apply.sh
on a fixture. Not declared: the per-account registry golden-gate-user-seed writes at run time (its path holds the account's
uid and cache-folder hash, and its content has that account's `~/Library/Fonts` in it).

## Verify
Declaration: `python3.14 -I -B -m unittest discover -s patches/golden-gate-font-registry/tests` (with FONT_REGISTRY_ARTIFACT=the artifact folder it also compares the artifact manifest).
Host: `patches/golden-gate-user-seed/tests` (the seed chain). On the laptop: `peruser-seed.log` reports "font
registry placed" for the new account; fontd's scan count at Setup and first login.

## Validation
Laptop 2026-11-11, image 4d1c48c (26B131): the user seed placed fontd's registry in the new account before its first login; text renders in Setup and apps.
Earlier: built and chain-tested on the host only; no image with it has booted. OPEN (carried from
the earlier Tahoe image project): that fontd adopts a seeded registry without a full rescan.
r2 changes only whether the patch is planned (patch.json "optional"); apply.sh and inputs are r1's, so the result above stands.

## Revisions
- r5 2026-11-12 Declare where each installed file is inside its row ("source"; tar members as ARCHIVE!MEMBER) for pipeline/materialize.py; the hook is unchanged.
- r4 2026-11-12 Declare the installed files in patch.json `files` (whole files from the row; test derives them from the artifact manifest); the hook is unchanged.
- r3 2026-11-11 Row `font_registry_golden_gate` (26B131) now names the pack from the 4d1c48c laptop harvest. No recipe change.
- r2 2026-11-11 Optional (group caches): a build without the harvest row or its store artifact skips this patch instead of stopping. apply.sh unchanged.
- r1 2026-11-11 Setup and per-user font registries harvested from 26B131 (bf10de4). Root cause: a54ec244 removed the 26A428 registries with golden-gate-user-seed, so Setup's and each new account's fontd built its registry from scratch.
