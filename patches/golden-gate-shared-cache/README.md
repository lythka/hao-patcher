# golden-gate-shared-cache

Makes the B image of the shared-cache fold-in (owner, 2026-11-14: "The fold-in is the main effort"). The OS
cryptex's x86_64 shared cache becomes a rebuilt family that holds the libraries the image otherwise has on disk at
cache install paths, and those files leave System. dyld then loads nothing from disk that the cache also has,
`_dyld_shared_cache_some_image_overridden()` is false, and libswiftCore keeps its prespecialized metadata on with no
cache edit. This is the 26B131 port of Tahoe's `shared-cache` recipe (19 providers, installed acceptance on
Tahoe images). Only `pipeline/sets/dev-bs-test-fold.txt` has it; `golden-gate-shared-cache-catalog` runs the same
code on the B+catalog family (set `dev-bs-test-fold-catalog`). The A image (golden-gate-userspace r75: the switch
reads false, 113 descriptor slots nulled) stays as it is.

## Input

`rebuilt_cache` (`pipeline/inputs/25G342/`): the shared-cache generator's artifact
(`rebuilt-cache-fold-in-26B131-20261118-5aacad92`, cache UUID 31C0215A), made by tools/shared-cache r86 from the System
of a dev-lite image of release/v0.0.1 aae0ab3 and its OS cryptex's x86_64 cache with the two later edit groups of step 3
taken back out; no prebuilt loaders.
The recipe reads:
- `dyld_shared_cache_x86_64*`: the family (main, subcaches, `.map`, `.atlas`);
- `retired-roots.txt`: the providers' install paths, one per line;
- `provider-provenance.json` (schema 1): `provider_sources`, per provider its `install_path`, `origin` (`system`: read
  from the artifact's System view, under `build.json` `system`; `file`: a recipe's own built file) and `sha256`;
- `build.json` `inputs`: each input cache file (`.../System/Library/dyld/dyld_shared_cache_x86_64*`) and its sha256;
  `revision`, `git_commit` and `git_dirty` go into the record.

## How it works

`src/kc-hook.sh` runs `src/fold.py install` in set order, after every recipe that changes a provider and before
golden-gate-native-identities. `make.sh` stages golden-gate-userspace's `src` as `build/userspace`, so the image
build, seal, 4 KiB re-sign and Swift reference check are that recipe's own code.

1. **Roots.** The artifact's roots must include every `R` line of `src/providers.txt` (the 10 images
   golden-gate-userspace's gate counts as overridden) and may include only its `C` lines (companions the providers
   load: the Metal and Wi-Fi wrappers' libraries, and golden-gate-hello's libhelloblur with golden-gate-live-text-shared's
   six LiveText dylibs, which the Metal wrapper loads; the LiveText data files stay on disk). MTLCompiler 3802 stays on
   disk (metal's review). Any other root stops the build until its lane has reviewed it.
2. **Provider bytes.** Each root on this System must have the sha256 the artifact folded in. A provider changed
   since the artifact was made (a new OCLP, a Metal or Wi-Fi recipe revision) stops the build: rebuild the artifact.
   This is what refuses a new revision of golden-gate-hello (it edits the Metal wrapper and libhelloblur) or of
   golden-gate-live-text-shared (it rewrites libhelloblur and installs the LiveText dylibs) until the artifact is
   made again from an image with that revision. A revision that changes only their data files changes no root and
   needs no new artifact.
3. **Input cache.** The artifact must have been made from this image's cache. Its recorded input files must equal
   the x86_64 files of the OS cryptex golden-gate-userspace just built. They may match as built, or with that
   recipe's prespecialization edits (`src/prespecialization-26B131.json`) taken back out and the file re-signed:
   that is the cache of an image from before r75. Likewise r77's model gates (three one-byte renames in `.01`,
   planned by golden-gate-userspace's `model_gates.py` on the artifact's family) may be taken back out; step 4 then
   renames them in the new family, so B keeps Sidecar, AirPlay to Mac and Continuity Camera. Any combination of the
   two groups may be out, per file; the record lists which. Either way the rebuilt family already carries every other
   in-cache repair of golden-gate-userspace (trackpad V3, EndpointSecurity selectors, CoreRoutine, MPSMatrix,
   audiomxd, Swift conformance references, the code identity carrier). The record says which one matched.
4. **Cryptex.** A new raw image gets the current OS cryptex's files with the x86_64 family replaced as a whole
   (Apple's arm64e cache and DriverKit's cache are untouched). Each file takes the old main file's owner, mode and
   flags and is re-signed with 4 KiB pages if it is not already (a file the model gates changed always is). Every Swift metadata reference is checked, then
   the image is sealed and installed as `os.dmg` and `os.clone.dmg`. Rosetta's cache directory still links to it.
5. **Retirement and gate.** The roots are removed from System. The build stops if any image of the new family is
   still a file on System (`/usr/lib/dyld` aside), unless an `N` pattern of `src/providers.txt` names it: images no
   process loads on this Mac, kept as Apple ships them and listed in the record (`left_on_disk_not_loaded`). The one
   pattern is 27.x's 14 Apple-silicon `AGXMetal*.bundle` drivers (Metal here loads only the HD 4000 driver), as in
   golden-gate-userspace r78.

The record is `System/Library/CoreServices/golden-gate-shared-cache.json` (root:wheel 0644, written after retirement).
Its `providers` field is a stable interface for later recipes (golden-gate-live-text-service reads it): `{root:
sha256}` for every retired root, the root an install path without the leading slash, the sha256 the bytes this
recipe checked on System in step 2 before removing the file.

**Composition with golden-gate-userspace.** On the B image, golden-gate-userspace still builds its cryptex (with the
r75 edits, its gate passes because the providers are still on disk then); this recipe replaces that cryptex's
x86_64 family. What B drops: the r75 prespecialization edits and their gate (B overrides nothing), and every
in-cache edit of `edits/cache-*.json` as applied on the B image (the artifact carries those repairs from the image
it was made from, which step 3 proves current). No input row drops; `codeid_carrier` and the rest still feed the
cryptex build that step 3 compares against. The compat libraries (`io80211_compat`, `wifip2p_compat`,
`corewifi_compat`) are still installed, then folded in and removed from System by this recipe.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/golden-gate-shared-cache/tests`: a stand-in artifact in the
  generator's format, and both 26B131 rows' artifacts when they are in the store (roots, inputs, model gate sites).
- `python3.14 -I patches/golden-gate-shared-cache/src/fold.py check SYSTEM ARTIFACT` (read-only): steps 1 and 2 on
  a mounted System.
- Build log: `golden-gate-shared-cache: removed /PATH` per root and one JSON line (input match, files, 4 KiB
  re-signs, Swift references, images, seconds).
- On the image: `shasum -a 256` of `/System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld/dyld_shared_cache_x86_64*`
  equals the artifact's files (modulo 4 KiB re-signs listed in the record); no root of `retired-roots.txt` exists;
  with `SWIFT_DEBUG_ENABLE_LIB_PRESPECIALIZED_LOGGING=1` no "Disabling prespecialized metadata" line.

## Validation

NEEDS VALIDATION: r11 (the folded image compacted before the seal) not built yet; r10 not built by golden-run. This recipe's `fold.py install` ran on a copy of an aae0ab3 image with
the 31C0215A family and row `hao_runtime_fold`'s payload, and that image booted in QEMU to sshd with loginwindow and
WindowServer up. The fold sets take row `hao_runtime_fold` (recipe hao-runtime-fold), whose payload must name this
family's cache UUID.

## Revisions
- r11 2026-11-19 The folded OS cryptex is copied into an image sized to what it holds before it is sealed
  (golden-gate-userspace's compact_image, r95): it was made at logical size + 2 GiB, about 26 GB on 26B131, and the
  golden-image guard allocates the whole image, which brought back the 13 GB r95 removed. Host tests: tests/test_fold.py
  Compact.
- r10 2026-11-18 Row `rebuilt_cache`: rebuilt-cache-fold-in-26B131-20261118-5aacad92 (cache UUID 31C0215A), from
  tools/shared-cache r86 on the System of release/v0.0.1 aae0ab3: no prebuilt loaders, the dylibs set at the cache base.
- r9 2026-11-17 Status product (was experiment): it ships in the release profiles. Declaration only; `validated` stays false until a boot proves the re-cut. Nothing it installs changes.
- r8 2026-11-17 Declares its scope (`requires`, docs/requires.md): the rebuilt 26B131 cache family. Nothing it installs changes.
- r7 2026-11-17 Row `rebuilt_cache`: rebuilt-cache-fold-in-26B131-20261117-2b471bea (cache UUID 954aa5de), made from
  main's providers: libhelloblur (golden-gate-hello) and gpu-client (live-text f479d6c7) had changed, so step 2 refused
  the 2026-11-14 family. Same input cache, roots and other 24 providers.
- r6 2026-11-16 Declares its pack (base): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r5 2026-11-16 Row `rebuilt_cache` hashes the artifact without its build.json (`# hash: without build.json`), so a
  producer run elsewhere matches it. Installed bytes unchanged.
- r4 2026-11-15 Declares its installed files (patch.json files). Installed bytes unchanged.
- r3 2026-11-15 Row `rebuilt_cache`: rebuilt-cache-fold-in-26B131-20261114-86dd72e (status experiment). Reads the generator's provider-provenance schema 1 (by install path). Step 3 may also take r77's model gates out, and step 4 then renames them in the new family. The last gate skips the reviewed `N` pattern (the 14 Apple-silicon AGXMetal drivers). Set dev-bs-test-fold takes hao-runtime-fold.
- r2 2026-11-14 Seven more reviewed companions (metal): libhelloblur and the six /usr/lib/hao-live-text dylibs; MTLCompiler 3802 stays on disk. Nothing installed changes on existing sets.
- r1 2026-11-14 New: the 26B131 B image's OS cryptex gets the rebuilt x86_64 family with the on-disk providers folded in, after checking the reviewed roots, the provider bytes and the input cache; the roots leave System and no cache image may stay on disk.
