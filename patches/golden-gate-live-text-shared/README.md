# golden-gate-live-text-shared

Live Text on the HD 4000: installs the `tools/live-text` runtime in `/usr/lib/hao-live-text` and attaches it to
every Metal client through `libhelloblur`, so Vision text recognition works in `mediaanalysisd` (which answers
Photos' Live Text requests), in Photos and in any other Metal client. Photos, Metal and every Apple binary keep
their bytes.

## How it works

OCLP's Metal wrapper weakly loads `/usr/lib/libhelloblur.dylib` (`golden-gate-hello`). The artifact's copy of that
library has one more load command, `/usr/lib/hao-live-text/LiveText.dylib`, which needs the helpers:

- `libLiveTextConfig.dylib` sets `HAO_LIVE_TEXT_ROOT` and `VISION_MPSNDARRAY_LIBRARY` in the process.
- `libLiveTextPolicy.dylib` interposes `e5rt_e5_compiler_compile`: for TextRecognition's models only, it compiles
  for the GPU (x86_64 Espresso has no backend for their default compute units), substitutes the runtime's
  detector (input bound as a uint8 tensor buffer and transposed; weights unchanged), and turns MPSGraph's memref
  bufferization off. Other models compile as before.
- `libhao_input_coherence.dylib` locks and unlocks the detector's input IOSurfaces before each execution.
- `libLiveTextLibrary.dylib` makes Intel HD Graphics 4000 devices load the runtime's `MPSNDArray.metallib`
  (`cache/metallib --legacy-sync-only` of the build's own library) instead of Apple's.
- `gpu-client.dylib`: `cache/metallib`'s GPU-client adapter for translated shaders.

None of them requests a GPU, loads MPSGraph or does work until a TextRecognition model compiles. Shader compiles go
to the repaired service of `metal-compiler-compat`. The MPS matrix initializer repair comes from
`golden-gate-userspace` (x86_64 dyld cache).

`src/kc-hook.sh` (after `golden-gate-hello` and `metal-compiler-compat`) runs `src/install.py`, which refuses
unless: the System's `libhelloblur` is the one the artifact was made from (row `libhelloblur`), its MPSNDArray
`default.metallib` is the one the repaired copy was made from (26B131 `ae6566b2...`; a 26A428 System is refused),
`libhaoMetalCompilerCompat.dylib` is installed, the runtime folder holds exactly its manifest's files, and
`/usr/lib/hao-live-text` does not exist. Then it copies the runtime (root:wheel) and replaces `libhelloblur`.

The files this adds to the System are declared in `patch.json` `files`: the runtime's files (origin: row
`live_text_framework`) and `usr/lib/libhelloblur.dylib`, whose one added load command (`LC_LOAD_DYLIB`, timestamp 0,
then an ad-hoc signature with identifier `local.libhelloblur`) is `edits/libhelloblur.json`. `tests/test_edits.py`
proves the spec writes the same bytes as `tools/live-text/lib/framework.py`. Ownership and flags
(root:wheel, `restricted` on libhelloblur) are set by `src/install.py` and are not part of the spec.

## Input

Row `live_text_framework` is `framework/` of a `tools/live-text build` (26B131:
`store/artifacts/live-text-26B131-20261111-dd1c86b`); it is built for one System build and one `libhelloblur`.

## Verify

```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-live-text-shared/tests
python3.14 -I -B -m unittest discover -s tools/live-text/tests
```

`tests/test_edits.py` also runs on the real files: `LIVE_TEXT_SHARED_INPUT` (libhelloblur.dylib) and
`LIVE_TEXT_SHARED_FRAMEWORK` (the row's `framework/` folder).


On the laptop (with `golden-gate-live-text-service` in the image):

1. `launchctl print gui/501/com.apple.mediaanalysisd` shows `state = running` (or a clean exit 0), not
   `runs` climbing with exit 78; `log show --last 10m --predicate 'process == "kernel"' | grep -i mediaanalysisd`
   has no `Operation not permitted`.
2. `vmmap $(pgrep -n mediaanalysisd) | grep hao-live-text` lists `LiveText.dylib`.
3. Photos: open a photo with clear text; the Live Text button appears at the lower right, text highlights on hover,
   and dragging selects text that Copy puts on the pasteboard (`pbpaste`).
4. No new crash reports for mediaanalysisd, MTLCompilerService, Photos or photoanalysisd in
   `/Library/Logs/DiagnosticReports` and `~/Library/Logs/DiagnosticReports`; mediaanalysisd's PID stays stable.
5. Background load: `top -l 3 -o cpu -stats pid,command,cpu,mem | head -20` over the first 30 minutes after login;
   mediaanalysisd may run hot while it analyses the library (OCLP disabled it for that reason), it must not
   crash-loop or exhaust memory.

## Validation

NEEDS VALIDATION. Host tests pass, including Vision OCR through Rosetta with this runtime (the policy and the
detector rewrite on x86_64 Espresso of macOS 27.2). The 26A428 build of the same sources recognized the two-line
fixture on the laptop's HD 4000 on 2026-11-09 and Photos loaded the helpers, but mediaanalysisd was blocked, so
Photos' Live Text was never seen working. Not yet built into an image or booted with 26B131.

## Revisions

- r4 2026-11-12 Declare where each installed file is inside its row ("source"; tar members as ARCHIVE!MEMBER) for pipeline/materialize.py; the hook is unchanged.
- r3 2026-11-12 Declare the runtime files and the libhelloblur load command in edits/libhelloblur.json; the hook is unchanged.
- r2 2026-11-11 Runtime in System (/usr/lib/hao-live-text, umbrella LiveText.dylib: the load command must fit libhelloblur's 64-byte header padding) instead of a Data-volume alias; built for 26B131 by tools/live-text (MPSNDArray repaired from 26B131's library, no matrix helper); refuses another MPSNDArray library or a missing compiler-service repair; selected by dev-bs-test.
- r1 2026-11-09 Add unselected shared-framework OCR image recipe without modifying Photos or Metal.
