# golden-gate-av1-encoder-x86

VideoToolbox offers Apple's AV1 software encoder (`AV1EncoderSW.bundle`) to arm64 programs only, so an x86_64
program never loads it on a CPU without AVX2. One declared `plist.set` on the bundle's `Info.plist`
([edits/av1-encoder-info.json](edits/av1-encoder-info.json)): `CMClassImplementations[0].CMExecutableArchitectures`
becomes `["arm64"]` (was `["x86_64", "arm64"]`). Nothing else in the bundle changes. Fixes diag#0053.

## Root cause

Measured on 26B131 (AV1EncoderSW x86_64, UUID 2BA74B90, file a562da07):
- `osascript -e "clipboard info"` with an image on the clipboard converts it through ImageIO's HEIF writer, CMPhoto and
  `VTTileCompressionSessionEncodeTile`, which picks the AV1 software encoder.
- The encoder is libaom. Its kernels are picked by CPU (cpuid, then tables of function pointers), but its once-only table
  setup (+0x158b50, the second `pthread_once` at +0x19d063) is AVX2 code with no CPU check. Its first loop begins
  `vpmovsxwd (%rcx,%rdx),%ymm3` at +0x158b7f, a 256-bit integer instruction that needs AVX2. Ivy Bridge has AVX but not AVX2,
  so the first AV1 encode dies with SIGILL. The crash report's instruction bytes match the binary at that address.
- The crash report's two AV1EncoderSW frames (+0x3d6c6, +0x3b191) are function pointers from the setup's tables that
  the frame-pointer walk read off the stack, not the faulting code.
- With the encoder unlisted for x86_64, the HEIF writer has no AV1 encoder: `clipboard info` lists every other flavor
  and leaves out HEIC.

Haswell and newer CPUs have AVX2. That is why the recipe is in the Ivy Bridge pack (`packs/intel-hd4000.json`).

## How it works

`src/kc-hook.sh` runs `src/arm64_only.py`, which writes the plist with that one key changed, in its own format and key
order, as edit-engine's `plist.set` writes it. It refuses a plist that is not the x86_64 AV1 software encoder. The file
is put back root:wheel 0644 `restricted`, like Apple's.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/golden-gate-av1-encoder-x86/tests`. With `AV1_INFO_PLIST` set to
  the 26B131 file, it also checks the recorded output: 6695f08f... becomes bd656487....
- The build log prints `golden-gate-av1-encoder-x86: HASH PATH`.
- On the laptop, `plutil -p /System/Library/Video/Plug-Ins/AV1EncoderSW.bundle/Contents/Info.plist` shows
  `CMExecutableArchitectures => ["arm64"]`. With an image on the clipboard, `osascript -e "clipboard info"` as the logged-in
  user exits 0 and lists the flavors, and no osascript SIGILL report appears.

## Validation

Laptop 2026-11-19, a MacBookPro9,2 with image 8305fdd (26B131, built from fb6542df with r4): AV1EncoderSW's Info.plist lists CMExecutableArchitectures ["arm64"]; with a PNG on the clipboard, `osascript -e "clipboard info"` as the logged-in user exits 0 and lists the types, and no osascript crash report appears.

## Revisions
- r5 2026-11-19 Validated r4 on hardware (Validation). Nothing it installs changes.
- r4 2026-11-17 Status product (was experiment): it ships in the release profiles. Declaration only; `validated` stays false until a boot proves the re-cut. Nothing it installs changes.
- r3 2026-11-17 Declares its scope (`requires`, docs/requires.md): only a CPU without AVX2 needs the arm64-only encoder entry. Nothing it installs changes.
- r2 2026-11-16 Declares its pack (intel-hd4000): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r1 2026-11-16 New: AV1EncoderSW's Info.plist lists arm64 only, so x86_64 programs on a CPU without AVX2 no longer load the encoder's AVX2 setup (diag#0053).
