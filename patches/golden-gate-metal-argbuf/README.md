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
- On the host: `python3.14 -I -B -m unittest discover -s tools/metal-argbuf/tests`.

## Validation

NEEDS VALIDATION. The same files, byte-identical to this artifact, were proven as a live swap on the laptop
(26B131, 2026-11-12): Weather with all four maps, and Safari WebGPU compute and render (A/B: both abort without
them). No image built with this recipe has booted yet.

## Revisions

- r2 2026-11-13 Input rows record their content sha256 (folder rows sealed with `lock.py seal`); the same artifacts at the same paths, so what this recipe installs is unchanged.
- r1 2026-11-12 Initial recipe.
