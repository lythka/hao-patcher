# golden-gate-metal-argbuf

Native Metal argument buffers and ASTC textures for the HD 4000, which OCLP's legacy Metal stack lacks
(MetalOld aborts in the argument-encoder calls). Weather and Safari's WebGPU need them. There is no software
fallback and no VM spoofing. The source, slot contract and analysis are in
[tools/metal-argbuf](../../tools/metal-argbuf/README.md).

## How it works

The `metal_argbuf` input is built by `tools/metal-argbuf/make` from the repaired iCBE that
`golden-gate-metal-libraries` installs. The kc-hook runs in set order after that recipe and after
`golden-gate-metal-framebuffer`:

- Compiler side: the installed `iCBE.dylib` in the GL driver bundle becomes `iCBE-real.dylib`, and the wrapper
  is installed as `iCBE.dylib`. The wrapper re-exports the real iCBE, rewrites IGIL for argument-buffer
  resources, and hooks the AIR pass pipeline in MTLCompilerService. The hook refuses to run unless the installed
  iCBE is byte-identical to the one the wrapper was linked against.
- Device side: `libargbuf.dylib` goes beside the HD 4000 Metal driver, which gets one more `LC_LOAD_WEAK_DYLIB`
  for it (added at hook time and re-signed ad hoc, like `metalold-selectors`). It implements argument-buffer
  layouts and encoders, and binds the resources on each draw and dispatch. ASTC textures are created as RGBA8
  and decoded when uploaded. This is format emulation at upload, not per draw.

Only built for 26B131.

## Verify

- The build log shows the kc-hook's four SHA-256 lines (step `recipe golden-gate-metal-argbuf`). For the
  dylibs they match the `metal_argbuf` artifact.
- In the image, `otool -L` on the MTL driver lists `.../AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/libargbuf.dylib
  (weak)` after the metalold-selectors and metal-framebuffer dylibs, and `codesign -vvv` accepts the driver,
  `iCBE.dylib` and `libargbuf.dylib`.
- On the target: Weather opens and its four maps render. `tools/metal-argbuf/probes/webgpu-check.html` in Safari
  reads back compute and render results.
- On the target, `probe18` (from `tools/metal-argbuf/make`, in `probes/`) prints `destroyed N/N` for textures and
  render pipelines, and its footprint stays flat.
- Host: `python3.14 -I -B -m unittest discover -s patches/golden-gate-metal-argbuf/tests` (the driver's edits spec
  gives the same bytes as `add_weak_dylib.py`; `GOLDEN_GATE_METAL_ARGBUF_INPUT` = the driver after
  golden-gate-metal-framebuffer runs it on the real file).
- On the host: `python3.14 -I -B -m unittest discover -s tools/metal-argbuf/tests`.

## Validation

Laptop 2026-11-19, a MacBookPro9,2 with the release-EFI image 8acc07f (26B131, built from fb6542df with r10; the build log's libargbuf.dylib is d991fe8d): Quick Look of a `.usdz` started RAQLPreviewExtension with no `validateWithDevice` abort and no RAQL crash report (r10's need). On image 8305fdd (same recipes) the seven argument-buffer probes pass, and `probe18` prints `destroyed 200/200` for textures and render pipelines with a flat footprint. Open and tracked separately: the model draws black (#26); Weather crashes in a runtime fault, so its maps were not seen (#9/#62); WebGPU was not read on this image (#23).

## Revisions
- r11 2026-11-19 Validated r10 on hardware (Validation). Nothing it installs changes.
- r10 2026-11-17 Row `metal_argbuf` is a full make at the stack commit (tools/metal-argbuf as d977ced2). libargbuf emulates placement heaps and shared-storage heaps, which MetalOld refuses on the HD 4000. RealityKit asks for both at engine init (diag#0019). It also reports ASTC textures' ASTC format to the app. Need: on a MacBookPro9,2 running these bytes (d991fe8d), Quick Look of a `.usdz` no longer aborts (before, RAQLPreviewExtension and RAQLThumbnailExtension aborted in `validateWithDevice:`), and the placement and shared-heap probes pass there. The model still draws black (open). iCBE.dylib and iCBE-real are unchanged.
- r9 2026-11-17 Row `metal_argbuf` is a full make at fe579412: the iCBE wrapper reads no path from the environment (its dump and replace switches exist only in a `make --diagnostics` build). libargbuf and iCBE-real are unchanged. Same command, same bytes.
- r8 2026-11-17 `requires`: os 26B131, donor 25G342, gpu intel-ivb (the HD 4000), cpu and firmware any; replaces `applies`.
- r7 2026-11-16 Declares its pack (intel-hd4000): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.

- r6 2026-11-15 libargbuf from d9fa28e7 (fix 26c6afc6): the texture and render-pipeline factory hooks return the driver's +1 result without retaining it again, so textures and pipelines are freed when apps release them (before, none was: 100 created, 0 destroyed; after, 100 and 100, in a lifecycle control on the host; tools/metal-argbuf tests pass). Laptop memory numbers: pending, on the first image with r6. patch.json declares what it installs (the driver's weak load as edits/driver.json, the three dylibs from row metal_argbuf). Wrapper and iCBE-real as r5.
- r5 2026-11-14 libargbuf from 547557ae: requests MetalOld refuses but current Apple drivers accept are adapted where the HD 4000 can honour them (multisample/depth-stencil render targets made Private; separate Depth32Float + Stencil8 built packed; a CPU-written MSAA stencil emulated through a staging texture and per-bit stencil passes), each logged; AGB_SURVEY=1 logs every texture/pipeline/pass shape (off by default). Ends Weather's precipitation-map abort (diag#0002). Wrapper and iCBE-real as r4.
- r4 2026-11-14 Wrapper from d9cff656: the IGIL rewrite also handles constant-pointer raw loads (`LD_RAW .., C-1`: a scalar read through an argument-buffer pointer, e.g. a WebGPU uniform `{vec4f, f32}`), which the backend refused (CompilerError 2) and which hung the GPU in Safari (diag#0016). iCBE-real and libargbuf unchanged.
- r3 2026-11-14 Input row `metal_argbuf` now hashes only the files a release can give a user's Mac (`# hash: release-files`: its published files plus the made-on-Mac ones, pipeline/lock.py scope()) (its sha256 is the same: the folder holds only those files). The same artifact, so what this recipe installs is unchanged.

- r2 2026-11-13 Input rows record their content sha256 (folder rows sealed with `lock.py seal`); the same artifacts at the same paths, so what this recipe installs is unchanged.
- r1 2026-11-12 Initial recipe.
