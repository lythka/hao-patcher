# golden-gate-metal-libraries

Installs Golden Gate's own MPSImage, MPSNeuralNetwork, RenderBox, MetalFX, CoreRE,
Tungsten, VectorKit (with its AlloyCommon library) and VFX shader libraries, repaired for the HD 4000 compiler, plus an extended Intel
shader runtime. Every exported Apple function is preserved. This is a partial
compatibility repair, not full Metal 4.1 support.

## Why

macOS 27 ships shader libraries compiled for a newer AIR than OCLP's legacy Intel
Metal stack understands, and its frameworks expect those exact libraries:

- **Synchronization ABI.** 27.0 AIR adds explicit order and scope operands to
  barriers and an extra memory-fence operand to atomics. The legacy runtime declares
  the old signatures; linking produces bitcast calls that crash IGIL's uniform
  analysis. The repair restores the old signatures for sequentially consistent
  barriers and relaxed atomics (including compare-exchange), keeping both memory
  orders, values, scope and volatility.
- **Library/framework pairing.** OCLP keeps Tahoe's RenderBox and Tungsten
  libraries, but the 27.0 frameworks use different layouts. RenderBox 27.0 has 109
  exports (Tahoe's has 82) and moves `_shader_state` bits; Tungsten 27.0 inserts
  `color_conversion_available` at function-constant index 3, shifting the later
  constants. With the Tahoe libraries, pipelines compile but draw wrong results
  (for example NaN color in Photos thumbnails). OCLP's 26.0 VectorKit library lacks
  functions 27.2's VectorKit asks for (`Icon::shadow_vertex`, so Maps logs "ShadowShader
  has nil functions" and draws icons without drop shadows; `Flyover::*`;
  `Textured::texture_blend*`), and its VFX library is the 26.0 one. This patch installs
  the libraries of the build being made (26A428 or 26B131).
- **Visible-function references.** The 31001 downgrader resolves
  `air.visible_function_references` before removing RenderBox's unused
  custom-blend path, so ordinary pipelines fail with `unresolved visible function
  reference: custom_blend`. The repair drops the eager registration and inlines the
  internal call paths; real custom blending still links the supplied helper.
- **Missing runtime helpers.** MPS and MetalFX kernels call SIMD and quad helpers
  (quad shuffle-down, quad XOR and broadcast overloads, collective reductions,
  vector sums and extrema, 64-bit ballot) that the legacy runtime lacks. The
  `metal_quad_runtime_golden_gate` input adds them to OCLP's split runtime using the
  existing Intel lane-ID and shuffle intrinsics, keeping every original helper block,
  and repairs the compiler's split-SIMD16 active-mask emission in `iCBE.dylib`.
- **Unsupported instructions.** Winograd convolutions use Apple GPU matrix
  instructions, and several MetalFX and Winograd kernels pass dynamic offsets to a
  texture pass that requires constants. Matrix operations become ordered float/half
  FMAs over the existing shared tiles; bounded dynamic offsets become selections
  among constant-offset samples, keeping the requested pixel and residency status.

Recipes, source and limits are in [cache/metallib/README.md](../../cache/metallib/README.md).

## Inputs

- `metallib_repairs_golden_gate`: a System-rooted tree of repaired `*.metallib`
  files, made from the build's own System by `cache/metallib/make` with
  `profiles/metallib-repairs-golden-gate.txt`. Its `SOURCES` lists the sha256 of each
  System library a repair was made from, `build.json` the build (`system_build`).
  `src/install_libraries.py` replaces a library only when the System's copy matches
  `SOURCES`, or, for a copy OCLP installed (RenderBox, Tungsten, VectorKit, AlloyCommon,
  VFX), when the System is that build; otherwise it stops with `MISSING/unsupported` before writing anything.
- `metal_quad_runtime_golden_gate`: the extended `runtime.igil64.split` and
  `iCBE.dylib`, made by `cache/metallib/make runtime-build`.

The kc-hook runs in set order after `golden-gate-userspace`. Shader-pack records keyed
to the old libraries must be re-keyed from captured requests before an image uses this
patch.

### Deliberate supersede of OCLP's Metal libraries

OCLP's hardware overlay (a base layer) writes Tahoe's RenderBox, Tungsten, VectorKit
(`default.metallib`, `metal_libraries/AlloyCommonLibrary.metallib`) and VFX libraries
(patch sets "Metal 3802 .metallibs Tahoe" and "Tahoe Graphics" in
`tools/oclp-hardware/profiles/mbp92.json`); this recipe then replaces those its
artifact holds with the OS build's own libraries, repaired (26B131: all five). The
overlay is not trimmed: its installer is the frozen copy inside the `oclp_hardware`
artifact and has no per-destination skip, and its profile is generated from OCLP and
not edited by hand. The extra writes are cheap,
and the overlay's record (`OpenCore-Legacy-Patcher.plist`) still lists them, which is
how `install_libraries.py` recognises OCLP's copies.

## Limitations

Texture atomics, imageblocks, texture block reads, rasterization-rate mapping and
CoreRE indirect calls are not implemented. 27.2 VectorKit's nine new `Flyover::*`
functions (tile and bindless paths) and `DaVinci::resetLM` still crash the 3802
compiler, as do VFX's ray-tracing, layered and simdgroup kernels and `vfx_ssao_trace`
(derivatives in a kernel). Constant-offset sample lowering costs
extra samples per call. Floating-point reduction order is preserved, but general
floating association and GPU performance are not measured.

## Verify

- `python3.14 -I pipeline/patches.py check`, the `cache/metallib` host tests and
  `python3.14 -I -m unittest discover -s patches/golden-gate-metal-libraries/tests`.
- Compare the installed files with the input artifacts' `SHA256SUMS`, and the export
  lists with the original libraries.
- On an Apple GPU: `python3.14 -I cache/metallib/make winograd-check NEW_OUTPUT` and
  `quad-check NEW_OUTPUT` compare the generated replacements with the originals.
- On the target: open Photos, the Dock's Apps grid, the Setup Location and
  Analytics panes, and Maps (no "nil functions" from `VectorKit.GeoGL`, icon drop
  shadows, satellite/Flyover); check for correct pixels and no new MTLCompilerService crash
  reports.

## Validation

NEEDS VALIDATION. Host compiler sweeps show no regressions, the added runtime helpers
match reference GPU results on the HD 4000, and Photos thumbnails render with the
matching Tungsten library. A complete image with every library, shader-cache
re-keying and full-workload performance remain open.

## Revisions
- r16 2026-11-11 26B131 artifact adds VectorKit, AlloyCommon and VFX (cache/metallib r54), superseding OCLP's 26.0 copies; VFX `particle_fill_grid` atomic exchange repaired.
- r15 2026-11-11 README: runs in set order after golden-gate-userspace (which no longer replaces System); documents that replacing OCLP's RenderBox and Tungsten libraries is a deliberate supersede. Recipe unchanged.
- r14 2026-11-11 Installs a repaired library only over the System library it was made from (`SOURCES`), or over OCLP's copy on the build it was made from; 26B131 libraries.

- r13 2026-11-10 Initial public release.
