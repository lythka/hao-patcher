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
repairs.

Why not `"disable_mediaanalysisd": false` in the target: that changes the EFI of every MacBookPro9,2 image built from
this target (the golden-gate set and the Recovery too), including images without the runtime; this recipe unblocks
the daemon only in a set that also installs the repair, and leaves OCLP's setting and the rest of its config
untouched. Both routes clear the stale NVRAM the same way (OCLP's NVRAM/Delete already lists `revblock`).

## Verify

```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-live-text-service/tests
```

In the image's exported `boot-efi/payload/EFI/OC/config.plist`: `4D1FDA02-...:revblock` is `none`. On the laptop
after the first boot of the image: `nvram 4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102:revblock` prints `none`,
`launchctl print gui/501/com.apple.mediaanalysisd` no longer shows exit 78, and there is no
`Operation not permitted` for mediaanalysisd in the log. The rest of the laptop checks are in
`golden-gate-live-text-shared` (Verify). Restoring an image without this recipe restores OCLP's block at next boot.

## Validation

NEEDS VALIDATION. Config tests pass on authored configs and on OCLP 3.0.0's MacBookPro9,2 values. Not yet built into
an image or booted; background analysis load on the HD 4000 is unmeasured.

## Revisions

- r2 2026-11-11 Checks the runtime in System (/usr/lib/hao-live-text/LiveText.dylib) on the read-only System instead of a Data alias; selected by dev-bs-test after hao-opencore.
- r1 2026-11-09 Remove only the mediaanalysisd compatibility block for images carrying the shared GPU OCR repair.
