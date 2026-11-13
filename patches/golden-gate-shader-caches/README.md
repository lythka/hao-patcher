# golden-gate-shader-caches

The HD 4000 Metal shader caches of this OS build, so Language Chooser, Setup, the login window and the first desktop
use compiled shaders instead of compiling them on the 2012 CPU. One input, `shader_caches_golden_gate`, made by
`cache/seeds/make` from a laptop harvest of an image of the same build (`cache/seeds/README.md`).

| From `data.tar` | Installed at | Read by |
|---|---|---|
| system accounts' trees | `private/var/folders/zz/.../C/<bundle>/com.apple.metal{,fe}`, owners, modes and ancestor folders as harvested | root (Language Chooser, iconservicesagent), `_windowserver`, `_mbsetupuser` (Setup Assistant, iconservicesagent, Control Center, avatar picker), `_securityagent` |
| the user's trees | `Library/Application Support/PerUserSeed/metal/<bundle>/` | golden-gate-user-seed copies them into each new account |

Post patch; list it after golden-gate-user-seed (apply.sh refuses otherwise). apply.sh refuses a pack whose
`build.json` names another build than the System's.

## Why a pack per build
FACT 2026-11-11: a Metal function-binary or 31001 record's key follows the function's bitcode hash, and Apple
rebuilds every metallib per OS build. 26A428 -> 26B131 (metalfe 32023.921 -> 32023.922) kept 0 of 3,437 function
hashes in the six libraries cache/metallib repairs (RenderBox, Tungsten, MetalFX, CoreRE, MPSImage,
MPSNeuralNetwork). r7's 26A428 pack (35,173 records) would have shipped only misses.

## Symptom and evidence
bf10de4 (26B131, no pack): MTLCompilerService 4:21 CPU at Setup + 9 min, load 476 at ~6 min, SSH unanswered ~1 min;
benchtest's first login compiled again in its own trees. The harvest (`harvest-26B131-bf10de4-20261111-1135`) shows
the caches persist: Setup's iconservicesagent tree was last written on boot 1 and only Setup Assistant, Control
Center and the video decoder added records on boot 2.

## Coverage
r8 input `shader-caches-26B131-20261111-c5a3fe51f6ee`: what bf10de4 compiled in 3 boots, Setup through account
creation and about 20 min of benchtest's first desktop (46 system-account files, 24 user files, 57 trees, 65 MB; 17 MB of
functions.data/libraries.data in the user's iconservicesagent tree alone). Apps not opened on that laptop
(Safari, Spotlight, Notification Center use, Settings panes) are not covered. Closed loop: harvest a boot of an
image with this pack and run `cache/seeds/make compare PACK HARVEST`; each new or changed file is a miss to fold into
the next pack.

## Declared files
`patch.json` `files` lists the 118 regular files of `data.tar`, all whole files from row `shader_caches_golden_gate` (no edit
specs): the system accounts' Metal trees under `private/var/folders/zz/` and the per-user trees under
`PerUserSeed/metal/`. `tests/test_edits.py` derives the list from the artifact manifest and runs apply.sh on a fixture;
it is exact for this row's artifact and goes stale when the row changes. Not declared: folders and their owners, and
the per-account copy golden-gate-user-seed makes at run time (its path holds the account's uid and cache-folder hash).

## Verify
Declaration: `python3.14 -I -B -m unittest discover -s patches/golden-gate-shader-caches/tests` (with SHADER_CACHES_ARTIFACT=the artifact folder it also compares the artifact manifest).
Host: `patches/golden-gate-user-seed/tests` (the seed chain). On the image: the system accounts' `com.apple.metal`
trees with their owners (0, 88, 248, 92). On the laptop: MTLCompilerService CPU at Setup + 9 min and the load peak
against bf10de4 (4:21, 476); then `make compare` on a new harvest.

## Validation
NEEDS VALIDATION (r11): an image with this pack keeps the first-boot result below, and a new account's Preview
compiles Metal source (SceneKit scenes shaded, no "PCH was compiled with module cache path" error). r10 on the laptop:
Laptop 2026-11-11, image 4d1c48c (26B131): MTLCompilerService was not among the busiest processes during first boot (bf10de4 without the seed: 4:21 CPU at 9 min).
Earlier: r8 is built and chain-tested on the host only; no image with it has booted.
r9 changes only whether the patch is planned (patch.json "optional"); apply.sh and inputs are r8's, so the result above stands.

## Revisions
- r14 2026-11-13 Input rows record their content sha256 (folder rows sealed with `lock.py seal`); the same artifacts at the same paths, so what this recipe installs is unchanged.
- r13 2026-11-12 Declare where each installed file is inside its row ("source"; tar members as ARCHIVE!MEMBER) for pipeline/materialize.py; the hook is unchanged.
- r12 2026-11-12 Declare the installed files in patch.json `files` (whole files from the row; test derives them from the artifact manifest); the hook is unchanged.
- r11 2026-11-12 Row `shader_caches_golden_gate` (26B131): the same harvest made by cache/seeds r2, without the user's `com.apple.metalfe` trees. Preview's held a clang module cache recording the harvest account's cache folder, which the Metal front end refused in every new account (SceneKit drew magenta in Preview). Every other file is unchanged.
- r10 2026-11-11 Row `shader_caches_golden_gate` (26B131) now names the pack from the 4d1c48c laptop harvest (84 user trees, was 57: adds Maps/VectorKit, Safari and WebKit records, and the repaired VectorKit/VFX libraries). No recipe change.
- r9 2026-11-11 Optional (group caches): a build without the harvest row or its store artifact skips this patch instead of stopping. apply.sh unchanged.
- r8 2026-11-11 Restored for 26B131 with a pack harvested from 26B131 itself (cache/seeds), ancestors and owners from the harvest instead of src/folders.tsv. Root cause: a54ec244 removed the patch, and the 26A428 pack cannot serve 26B131 (every function hash changed).
- r7 2026-11-09 Preserve r6 and add 166 native records observed in Setup/app consumer trees (26A428).
- r1-r6 2026-11-04 to 11-09 26A428 packs: Tahoe pack regenerated against 27.0 libraries plus native supplements.
