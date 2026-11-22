# golden-gate-shader-caches

The HD 4000 Metal shader caches of this OS build, so Language Chooser, Setup, the login window and the first desktop
use compiled shaders instead of compiling them on the 2012 CPU. One input, `shader_caches_golden_gate`, made by
`cache/seeds/make` from a harvest of an image of the same build (`cache/seeds/README.md`).

| From `data.tar` | Installed at | Read by |
|---|---|---|
| system accounts' trees | `private/var/folders/zz/.../C/<bundle>/com.apple.metal{,fe}`, owners, modes and ancestor folders as harvested | root (Language Chooser, iconservicesagent), `_windowserver`, `_mbsetupuser` (Setup Assistant, iconservicesagent, Control Center, avatar picker), `_securityagent` |
| the user's trees | `Library/Application Support/PerUserSeed/metal/<bundle>/` | golden-gate-user-seed copies them into each new account |

Post patch; list it after golden-gate-user-seed (apply.sh refuses otherwise). apply.sh refuses a pack whose
`build.json` names another build than the System's.

## When the pack must be remade
- **Another OS build.** A record's key follows the function's bitcode hash, and Apple rebuilds every metallib per
  build: 26A428 -> 26B131 kept none of the 3,437 function hashes in the six libraries cache/metallib repairs.
- **A change in the Metal compiler stack** (the iCBE wrapper of golden-gate-metal-argbuf, the compiler compat
  library, the 31001 downgrader). MetalOld's cache key ignores the compiler, so a record made before the change is
  served as it is. Harvest from an image that has the current stack.
- A repaired library that changed only makes its old records dead (misses, size).

Without a pack, the first boot of 26B131 spends minutes of CPU in MTLCompilerService during Setup.

## Coverage
What the harvested image compiled from Setup through account creation and the first desktop, and the apps opened on
it. Apps not opened there are not covered. To find misses, harvest a boot of an image with this pack and run
`cache/seeds/make compare PACK HARVEST`; each new or changed file is a miss for the next pack.

## Declared files
`patch.json` `files` lists the 118 regular files of `data.tar`, all whole files from row `shader_caches_golden_gate` (no edit
specs): the system accounts' Metal trees under `private/var/folders/zz/` and the per-user trees under
`PerUserSeed/metal/`. `tests/test_edits.py` derives the list from the artifact manifest and runs apply.sh on a fixture;
it is exact for this row's artifact and goes stale when the row changes. Not declared: folders and their owners, and
the per-account copy golden-gate-user-seed makes at run time (its path holds the account's uid and cache-folder hash).

## Verify
Declaration: `python3.14 -I -B -m unittest discover -s patches/golden-gate-shader-caches/tests` (with SHADER_CACHES_ARTIFACT=the artifact folder it also compares the artifact manifest).
Host: `patches/golden-gate-user-seed/tests` (the seed chain). On the image: the system accounts' `com.apple.metal`
trees with their owners (0, 88, 248, 92); MTLCompilerService CPU during Setup's first minutes stays low; then
`make compare` on a new harvest.

## Validation

Laptop 2026-11-22, a MacBookPro9,2: the dev-beta image c3c0a5b (26B131, built from 7459d3e6 with r17; profile dev-beta-v0.0.1: the beta set plus dev-access) was pushed, booted to Setup and used (Maps, System Settings, Weather, Control Center). Its QEMU first boot passed in 490 s with the shared cache rebuilt in place and checked against the release (UUID 31DA2287, 36 files). Known issues, not this recipe's: first boot is slow (load above 400 for several minutes) and Setup's Transfer pane leaves its selection ring on "From a Mac".

## Revisions
- r18 2026-11-22 Validated r17 on hardware (Validation). Nothing it installs changes.
- r17 2026-11-17 The system accounts' Metal cache files (46, under private/var/folders) are declared `"content": "seed"`: Metal appends to them on first GPU use, so `hao verify` checks their presence, mode and owner, not their bytes. The PerUserSeed copies are unchanged sources. Declaration only; nothing installed changes.
- r16 2026-11-17 `requires`: os 26B131, donor 25G342, gpu intel-ivb (the HD 4000), cpu and firmware any; replaces `applies`. status product: it ships in the release profiles (what it installs is unchanged; `validated` stays false until the re-cut's laptop boots prove it).
- r15 2026-11-16 Declares its pack (intel-hd4000): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r14 2026-11-13 Input rows record their content sha256 (folder rows sealed with `lock.py seal`); the same artifacts at the same paths, so what this recipe installs is unchanged.
- r13 2026-11-12 Declare where each installed file is inside its row ("source"; tar members as ARCHIVE!MEMBER) for pipeline/materialize.py; the hook is unchanged.
- r12 2026-11-12 Declare the installed files in patch.json `files` (whole files from the row; test derives them from the artifact manifest); the hook is unchanged.
- r11 2026-11-12 Row `shader_caches_golden_gate` (26B131): the same harvest made by cache/seeds r2, without the user's `com.apple.metalfe` trees. Preview's held a clang module cache recording the harvest account's cache folder, which the Metal front end refused in every new account (SceneKit drew magenta in Preview). Every other file is unchanged.
- r10 2026-11-11 Row `shader_caches_golden_gate` (26B131) now names the pack from the 4d1c48c laptop harvest (84 user trees, was 57: adds Maps/VectorKit, Safari and WebKit records, and the repaired VectorKit/VFX libraries). No recipe change.
- r9 2026-11-11 Optional (group caches): a build without the harvest row or its store artifact skips this patch instead of stopping. apply.sh unchanged.
- r8 2026-11-11 Restored for 26B131 with a pack harvested from 26B131 itself (cache/seeds), ancestors and owners from the harvest instead of src/folders.tsv. Root cause: a54ec244 removed the patch, and the 26A428 pack cannot serve 26B131 (every function hash changed).
- r7 2026-11-09 Preserve r6 and add 166 native records observed in Setup/app consumer trees (26A428).
- r1-r6 2026-11-04 to 11-09 26A428 packs: Tahoe pack regenerated against 27.0 libraries plus native supplements.
