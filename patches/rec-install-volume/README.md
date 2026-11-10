# rec-install-volume

Puts this repository's Recovery on the image's own APFS Recovery volume in place of Apple's, so the Mac's built-in
recovery (Cmd-R, OpenCore's Recovery entry, `nvram recovery-boot-mode=unused`) starts a Recovery that works on this
Mac: Apple's utilities plus the set's `rec-*` patches, including a Reinstall macOS that installs the image from a
USB disk ([rec-reinstall](../rec-reinstall/README.md)). Apple's stock Recovery lacks those drivers and fixes, and its
Reinstall macOS downloads stock macOS, which does not start on this Mac.

Stage `post`, any model and build. The Recovery comes in as the input row `enduser_recovery`.

## How it works
1. [pipeline/recovery](../../pipeline/recovery) `TARGET BUILD SET` builds the Recovery from the build's own
   BaseSystem and the set's `recovery` patches and writes `OUT/volume/`: `BaseSystem.dmg`, an unsigned
   `BaseSystem.chunklist` for it ([pipeline/stages/chunklist.py](../../pipeline/stages/chunklist.py)) and the
   Recovery's `patches.txt`.
2. The row `pipeline/inputs/BUILD/enduser_recovery` names that folder.
3. `apply.sh` runs with the Recovery volume mounted read-write at `REC`, finds the single
   `<group>/BaseSystem.dmg` and replaces the dmg and its chunklist in place. The folder never holds a second
   `*.dmg`, because OpenCore boots the first one it finds. The post log records the Recovery's patch list, the dmg
   size and the chunklist's SHA-256.

OpenCore checks a Recovery chunklist's signature only with `Misc.Security.DmgLoading = Signed`; with `Any` it
accepts the unsigned chunklist. The kernel checks chunklists only for `root-dmg=` boots.

## Verify
- `python3.14 -m unittest discover -s pipeline/tests -k chunklist`: `chunklist.py create` reproduces Apple's chunk
  table for Apple's BaseSystem.dmg.
- The build's post log shows `rec-install-volume: ... chunklist sha256 ...`, equal to the SHA-256 of
  `OUT/volume/BaseSystem.chunklist`.
- On a read-only mount of the image's Recovery volume, `python3 -I pipeline/stages/chunklist.py verify
  <group>/BaseSystem.dmg <group>/BaseSystem.chunklist` reports 0 bad chunks.
- On the Mac, `nvram recovery-boot-mode=unused` followed by a boot of the internal OpenCore, or Cmd-R through
  OpenCore, reaches Recovery with Reinstall macOS.

## Limitations
The chunklist is unsigned, so nothing proves the dmg's origin at boot. The row names one target's Recovery per build.
To remove the patch, drop it from the set and rebuild; Apple's dmg returns from the pristine stage.

## Validation
NEEDS VALIDATION: the Recovery builds and its generated chunklist verifies (0 bad chunks); booting it from the image's
Recovery volume, Cmd-R and a full reinstall have not been tested.

## Revisions
- r3 2026-11-10 Initial public release.
