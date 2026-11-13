# live-text

Builds Live Text (Vision text recognition, Photos' text selection) for the HD 4000 on a Golden Gate image. Three
patches install what it builds:

| Patch | Stage | Installs | Input row |
|---|---|---|---|
| [`metal-compiler-compat`](../../patches/metal-compiler-compat) | oclp-hook | OCLP's legacy `MTLCompilerService` loading `libhaoMetalCompilerCompat.dylib` | `metal_compiler_compat` (`compiler-service/`) |
| [`golden-gate-live-text-shared`](../../patches/golden-gate-live-text-shared) | oclp-hook | the runtime in `/usr/lib/hao-live-text`, attached through `libhelloblur` | `live_text_framework` (`framework/`) |
| [`golden-gate-live-text-service`](../../patches/golden-gate-live-text-service) | post | OpenCore config without RestrictEvents' `media` block, so `mediaanalysisd` starts | none |

## Why it is needed

Photos asks `mediaanalysisd` (XPC `com.apple.mediaanalysisd.analysis`), through VisionKit, for the text in a
photo; the daemon runs Vision's TextRecognition (Espresso E5 models) natively in x86_64. On the HD 4000:

- x86_64 Espresso has no backend for the models' default compute units (`No available backends on platform
  [intel] for compute units ane`); the policy helper compiles TextRecognition's models for the GPU only.
- The detector's 480x480 BGRA `pixel_buffer` input does not bind; the runtime carries a copy of Apple's
  `cr_td_model_v3_e5` whose `model.mil` takes the same bytes as a uint8 `tensor_buffer` and transposes them
  (weights and every other statement unchanged), and the policy compiles that copy instead.
- MPSGraph's memref bufferization is off for those compiles (the legacy backend cannot run it).
- MPSNDArray's library uses the barrier/atomic ABI the 3802 compiler lacks; HD 4000 devices load a copy repaired
  by `cache/metallib` (`--legacy-sync-only`: 241 of 522 functions on 26B131, the same 241 as 26A428's 521).
- Detector input IOSurfaces are locked and unlocked once before each E5 execution so the GPU reads current pixels.
- Runtime-generated MPSGraph/Espresso shaders need `cache/metallib`'s AIR repairs at compile time: the compiler
  service loads `compat.dylib` at startup.
- RestrictEvents blocks the daemon (`revblock=media`, OCLP's default for 3802-compiler GPUs).

The MPS matrix initializer repair (family 25 skips the native MPSMatrix fallback) is not here: `golden-gate-userspace`
applies it to the x86_64 dyld cache through `tools/metal-fixes`.

## Layout

| Path | Contents |
|---|---|
| `live-text` | `inputs`, `build`, `test` |
| `lib/runtime.py` | the runtime folder: umbrella, helpers, repaired library, detector, `build.json` |
| `lib/framework.py` | `libhelloblur` with one more load command, the umbrella |
| `lib/compiler_service.py` | the service executable with one more load command, re-signed ad hoc, bundle files bound |
| `lib/detector.py` | the detector copy and its two-statement `model.mil` rewrite |
| `lib/macho_edit.py` | appends load commands to an ad hoc x86_64 image and refreshes its code-page hashes |
| `src/` | `config.c` (settings), `policy.mm` (E5 compiles), `input_coherence.mm`, `library.m` (MPSNDArray redirect) |

Every helper is x86_64, ad hoc signed, and loads in every Metal client; none requests a GPU or loads MPSGraph or
Espresso until a TextRecognition model compiles. Only Intel HD Graphics 4000 devices get the library redirect.

## Build

Needs Xcode (clang, Metal tools, SDK) and codesign. Nothing here runs a GPU program or an Apple application.
Run from this folder, every output folder new:

```sh
python3.14 -I live-text inputs --system APPLE_SYSTEM_ROOT \
  --oclp-hardware ../../store/artifacts/oclp-hardware-mbp92-psp2.0.5-20261109-157bf4246bcc --output NEW_INPUTS
python3.14 -I live-text build --inputs INPUTS \
  --startup-library ../../store/artifacts/libhelloblur-d471519b/libhelloblur.dylib --output NEW_BUILD
```

`APPLE_SYSTEM_ROOT` is the target build's System (for 26B131, the 27.2.1 IPSW's filesystem). `inputs` copies
MPSNDArray's `default.metallib`, TextRecognition's `cr_td_model_v3_e5.mlmodelc.bundle` and OCLP's legacy
`MTLCompilerService`, `Info.plist` and `CodeResources` (UUID `3716D20F-B990-3906-B3E5-44E88AE63AF8`). `build` takes
about five minutes, most of it the MPSNDArray pass. Point row `metal_compiler_compat` at `NEW_BUILD/compiler-service`
and row `live_text_framework` at `NEW_BUILD/framework`. The framework installer refuses a System whose MPSNDArray
library or `libhelloblur` is not the one the build was made from.

Every `build.json` names its inputs as the repository does (`store/artifacts/...`), or by file name when they lie
outside it (`lib/paths.py`), never by an absolute path, so the rows can be published.

## Verify

```sh
python3.14 -I live-text test     # or: python3.14 -I -B -m unittest discover -s tests
```

The tests build and run x86_64 fixtures (through Rosetta on Apple silicon): the load-order control behind the
compiler-service edit, the Mach-O editor, the libhelloblur edit, the detector rewrite, the MPSNDArray redirect
(only for an HD 4000 device), the coherence lifetimes, and a full runtime build. `test_runtime.py` then draws two
lines of text and runs `VNRecognizeTextRequest` in an x86_64 process: through Rosetta, stock Vision fails on that
host exactly as on the HD 4000 (no backend for `ane`); with the runtime loaded (`DYLD_INSERT_LIBRARIES`), the
policy compiles the rewritten detector for the GPU, the detector input binds, and both lines come back exactly.
It uses the host's own detector and is skipped without Rosetta. This proves the E5 policy and detector rewrite on
the host's x86_64 Espresso, not the HD 4000 shaders, the compiler service or Photos.

On the laptop see `patches/golden-gate-live-text-shared` (Verify).

## History

Restored from darwin27-live-text-fixes v0.2.0 (the shared, not private, compiler route; 9 Nov 2026), which ran
Vision OCR on the laptop's HD 4000 under 26A428. Changes: the runtime lives in System (`/usr/lib/hao-live-text`,
sealed with the image) rather than a Data-volume alias; the MPS matrix initializer helper is dropped; the private
compiler, compiler bridge and diagnostic probes are not restored.
