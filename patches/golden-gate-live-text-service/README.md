# golden-gate-live-text-service

Lets `mediaanalysisd` start, so Photos' Live Text gets an answer: removes only the `media` token from RestrictEvents'
`revblock` in the image's OpenCore config. Only for an image that carries the Live Text runtime.

## Root cause

Photos asks VisionKit for the text in a photo; VisionKit sends the request to `mediaanalysisd`
(`com.apple.mediaanalysisd.analysis`). The daemon never starts: on the laptop (26B131, image bf10de4)
NVRAM `4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102:revblock` is `media`, launchd logs `posix_spawn(...mediaanalysisd),
error 0x1 - Operation not permitted` every 10 s, and the job's runs climb with exit 78. OCLP's
`efi_builder/misc.py` (`_re_generate_block_arguments`) adds `media` when the setting `disable_mediaanalysisd` is on,
which our target sets (`pipeline/targets/mbp92.json`, OCLP's default for 3802-compiler GPUs: the daemon crashed
analysing large libraries). [RestrictEvents](https://github.com/acidanthera/RestrictEvents/blob/9265e787bbd288ef6f7d5cee2cb77e751377b4ef/RestrictEvents/RestrictEvents.cpp)
reads `revblock` once (boot argument first, then NVRAM; default `auto`, which does not block media) and returns
EPERM for the daemon's exact path when the value contains `media`.

## How it works

`apply.sh` (post, after `hao-opencore`) runs `src/config.py` on `$ESP/EFI/OC/config.plist`:

- NVRAM/Add `revblock`: `media` removed, other tokens kept; `media` alone becomes `none`.
- A `revblock=` boot argument (it takes precedence) is filtered the same way.
- `revblock` stays in NVRAM/Delete (OCLP's base config lists it; added if missing), so OpenCore replaces the
  firmware's stale `media` at the next boot.
- RestrictEvents.kext and `revpatch` (`sbvmm,f16c`) are unchanged.
- After `hao-opencore`, so it sees the final boot-args: `hao-opencore` rewrites boot-args (drops `-v`,
  `debug=0x100`, `startup_debug=1`; adds `-lilubetaall`, `amfi_get_out_of_my_way=1`, ...) but never sets `revblock`,
  and OCLP puts `revblock` only in NVRAM/Add (26B131 image adfd15a: boot-args without `revblock`).

It refuses unless the image's System has an intact runtime in `/usr/lib/hao-live-text` and `libhelloblur` loads
it (`golden-gate-live-text-shared`): without the runtime the daemon would run Vision on the HD 4000 without the
repairs. In a fold-in image (`golden-gate-shared-cache` moved both libraries into the rebuilt shared cache and
removed the files), it accepts that recipe's record `/System/Library/CoreServices/golden-gate-shared-cache.json`
instead: its `providers` must list `libhelloblur` and `LiveText.dylib`, the latter with the runtime manifest's hash
(the fold checked those bytes on disk before removing them). Exactly one of the two on disk is refused.

Why not `"disable_mediaanalysisd": false` in the target: that changes the EFI of every MacBookPro9,2 image built from
this target (the golden-gate set and the Recovery too), including images without the runtime; this recipe unblocks
the daemon only in a set that also installs the repair, and leaves OCLP's setting and the rest of its config
untouched. Both routes clear the stale NVRAM the same way (OCLP's NVRAM/Delete already lists `revblock`).

The edit is declared in `edits/config.json` (`plist.set` on `EFI/OC/config.plist`, in the image's EFI partition):
`revblock` becomes `none`. `src/config.py` computes that value and also handles other tokens, a `revblock=` boot
argument and a missing NVRAM/Delete entry; the spec covers OCLP's MacBookPro9,2 config, where none of those occur,
and `tests/test_edits.py` proves the same bytes on it.

## Verify

```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-live-text-service/tests
python3.14 -I -B -m unittest discover -s patches/golden-gate-live-text-service/tests   # LIVE_TEXT_SERVICE_INPUT, LIVE_TEXT_SERVICE_IMAGE_CONFIG: real configs
```

In the image's exported `boot-efi/payload/EFI/OC/config.plist`: `4D1FDA02-...:revblock` is `none`. On the laptop
after the first boot of the image: `nvram 4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102:revblock` prints `none`,
`launchctl print gui/501/com.apple.mediaanalysisd` no longer shows exit 78, and there is no
`Operation not permitted` for mediaanalysisd in the log. The rest of the laptop checks are in
`golden-gate-live-text-shared` (Verify). Restoring an image without this recipe restores OCLP's block at next boot.

## Validation

Laptop 2026-11-19, a MacBookPro9,2 with image 8305fdd (26B131, built from fb6542df with r7): `nvram 4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102:revblock` prints `none`, and mediaanalysisd runs with no crash report. On the release-EFI image 8acc07f the boot log has no `Operation not permitted` for mediaanalysisd. Background analysis load on the HD 4000 is unmeasured.

## Revisions
- r8 2026-11-19 Validated r7 on hardware (Validation). Nothing it installs changes.
- r7 2026-11-17 `requires`: os 26B131, donor 25G332/25G342, gpu intel-ivb (the HD 4000), cpu and firmware any; replaces `applies`. status product: it ships in the release profiles (what it installs is unchanged; `validated` stays false until the re-cut's laptop boots prove it).
- r6 2026-11-16 Declares its pack (metal-3802): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.

- r5 2026-11-15 The runtime check also accepts a fold-in image, where LiveText.dylib and libhelloblur are in the rebuilt shared cache: golden-gate-shared-cache's record must list both, LiveText at its manifest hash. Config bytes unchanged.
- r4 2026-11-13 `apply.sh` runs Python as `$OCLP_PYTHON` (the kit's CPython for the Mac the assembly runs on, arm64 or x86_64), falling back to the builder VM's share copy; same Python version and bytes written.
- r3 2026-11-12 Declare the config edit in edits/config.json; the hook is unchanged.
- r2 2026-11-11 Checks the runtime in System (/usr/lib/hao-live-text/LiveText.dylib) on the read-only System instead of a Data alias; selected by dev-bs-test after hao-opencore.
- r1 2026-11-09 Remove only the mediaanalysisd compatibility block for images carrying the shared GPU OCR repair.
