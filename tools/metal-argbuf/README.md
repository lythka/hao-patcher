# metal-argbuf

Native Metal argument buffers (tier 1) for the Intel HD 4000 (Gen7) under OCLP's legacy Metal stack
(MetalOld + `AppleIntelHD4000GraphicsMTLDriver`), on Golden Gate (macOS 27.2.1, 26B131). No software fallback and no
spoofing: shaders read argument buffers on the GPU, and the driver binds what they refer to.

**Status: installed by the recipe [golden-gate-metal-argbuf](../../patches/golden-gate-metal-argbuf) (26B131); not yet in
a booted image.** Proven on the laptop GPU through the real Metal
API, with the shim loaded by the driver bundle itself (live swap, not an image):

| Feature | Probe | Result |
|---|---|---|
| encoders from descriptors and from `MTLFunction newArgumentEncoderWithBufferIndex:` | probe10 A, B | CORRECT |
| nested argument buffers (`newArgumentEncoderForBufferAtIndex:`), `constantDataAtIndex:`, two ABs per dispatch | probe10 C, D | CORRECT |
| vertex + fragment argument buffers (device and constant pointers, textures) | probe11, probe14 | CORRECT |
| samplers in argument buffers, also beside a function's own sampler | probe12 | CORRECT |
| top of every slot range (texture slot 127, buffer slot 63), both render stages and compute | `AGB_HANDLE_BASE=90,18`, probe14 bases up to 94,22 | CORRECT |
| inline structs in argument buffers (constants + texture inside) | probe16 | CORRECT |
| ASTC textures (created as RGBA8, decoded with libate at upload; replaceRegion and blit) | probe17 | CORRECT |

Apps on the laptop (27.2.1), with the shim loaded by the driver bundle (2026-11-12):

| App | Works |
|---|---|
| Weather | conditions, hourly, air quality, 10-day; precipitation, temperature, air-quality and wind maps (first launches aborted in argument-buffer code, then in MetalOld's texture validation of WeatherMaps' ASTC 8x8 KTX textures; both fixed) |
| Safari WebGPU | compute (storage buffer) and render (uniform bind group) read back exactly; without the shim the GPU process aborts in `newIndirectArgumentEncoderWithLayout:` from `WebGPU::Device::createBindGroupLayout` |

Not argument buffers, same with and without the shim: Maps (explore, satellite, 3D buildings) works without it,
thanks to cache/metallib's VectorKit repair, although VectorKit contains argument-buffer encoders; Safari never shows WebGL/WebGPU canvases on screen, and
WebGL draws are dropped; SceneKit's `default.metallib` (macOS and iOSSupport) is missing from the metallib repair
profile (the 31001 downgrader crashes on its depth sampler-form reads); RealityKit needs placement heaps
(`MTLHeapTypePlacement`, which MetalOld refuses).

| Piece | Where |
|---|---|
| Compiler side: iCBE wrapper = IGIL rewrite + AIR hook | [src/compiler](src/compiler) |
| Device side: layout, encoder, draw-time binding (`libargbuf.dylib`) | [src/device](src/device) |
| Slot contract shared by both | [src/argbuf_config.h](src/argbuf_config.h) |
| GPU test programs | [probes](probes) |
| Host harness scripts | [harness](harness) |
| Host tests (IGIL rewrite on captured IGIL) | [tests](tests) |
| Build | [make](make) |

## Why: the Weather abort

Weather (27.2.1) aborts as soon as it draws with VFX:

- VFX 233.40.3 (x86_64, iOSSupport, from 27's Rosetta-cryptex dyld cache) function VFX+0x171670 builds 12
  `MTLArgumentDescriptor`s (data types 58 texture and 60 pointer, ids about 11 to 34, one 3D texture member) and calls
  `newArgumentEncoderWithArguments:` unconditionally when not in a VM: a `dispatch_once` at VFX+0x6d3d90 sets the
  flag when `sysctlbyname("kern.hv_vmm_present")` fails or returns 0. It is called from VFX+0xb4dc10.
- MetalOld: `-[_MTLDevice newArgumentEncoderWithArguments:structType:]` -> `newIndirectArgumentBufferLayoutWithStructType:`
  (the `MTLIOAccelDevice` base returns nil) -> `setStructType:withDevice:` (sent to nil) -> `newArgumentEncoderWithLayout:`
  -> `-[MTLIOAccelDevice newIndirectArgumentEncoderWithLayout:]`, which is a bare `abort()`. OCLP's
  `MTLIGAccelDevice` overrides none of these.
- Tahoe's VFX 203.100.3 never calls `newArgumentEncoderWithArguments:` (the selref exists, nothing loads it), so
  Weather worked on Tahoe. The always-on argument-buffer path is new in 27.
- `metalold-selectors` answering `supportsArgumentBuffers` YES is not the trigger; VFX asks
  `argumentBuffersSupport` / `supportsFamily:`.

What VFX 233 uses (selector references, x86_64 26B131 cache; the cache's selrefs are chained values, target =
0x7ff800000000 + (value & 0xFFFFFFFFF)):

- `newArgumentEncoderWithArguments:` (2 sites), `newArgumentEncoderWithBufferIndex:` (3, in `VFXREForEachDrawCall`),
  `newArgumentEncoderForBufferAtIndex:` (1), `setArgumentBuffer:offset:` (8), `constantDataAtIndex:` (39).
- `setSamplerState:atIndex:`, among others at VFX+0x195d16, where material properties are encoded with
  `setTexture:atIndex:` + `setSamplerState:atIndex:` + `usedResources`: samplers are stored in argument buffers.
- `useResource(s):usage:(stages:)`, `useHeap(s)`, residency sets.
- `setArgumentBuffer:startOffset:arrayElement:` has **no** call site: VFX uses no arrays of argument buffers.
- Embedded shader source: `re_scene_argument_buffer` at buffer 19 (texture2d<half> x3, texture2d_array<half> x7,
  texture1d<half>, texturecube_array<half> x2, depth2d<float>, `constant` struct pointers, `constant half*`, a nested
  `constant VirtualEnvironmentProbeLighting::TextureArgumentBuffer&`); `re_entity_argument_buffer` at buffer 18
  (texture2d_array<half>, `constant BreakthroughGPUData*`, texture2d<ushort>, texture2d<half>). These are render
  shaders. VFX also ships `*_no_argument_buffer` kernels.

## The HD 4000 Metal stack, as measured

OCLP payload 11.7.10, GPUDriversIntel 16.5.14.

```
MetalOld.dylib (_MTLDevice, MTLIOAccelDevice, encoders)
  AppleIntelHD4000GraphicsMTLDriver (MTLIGAccelDevice, MTLIGAccel{Compute,Render}CommandEncoder)
    + weak dylibs: libmetalold-selectors, libmetal-framebuffer, (this) libargbuf
MTLCompilerService
  libMTLIntelCompilerPlugin.dylib  MTLIntelCompiler::generateIGIL(llvm::Module*, ...) -> legacy PassManager::run
    libIGIL-Metal.dylib            LLVM target igil_64_GEN7: AIR -> IGIL text (dlopen'd; has symbols)
    GPUCompiler 3802 libLLVM.dylib the plugin's LLVM (exports the LLVM-C and C++ APIs)
    GPUCompiler 31001 LLVM         also loaded (OCLP's metallib downgrader) -- never resolve LLVM with RTLD_DEFAULT
  iCBE.dylib                       IGIL text -> USC -> Gen7 binary (AppleIntelHD4000GraphicsGLDriver.bundle/Contents/MacOS;
                                   the image already ships a repaired copy, sha256 25ea0603...)
```

The plugin `dlopen`s libIGIL-Metal (`LLVMInitializeIGILTarget*` by name) and iCBE (`Create`, `Delete`, ...); it tries
`/Library/GPUBundles/...` before `/System/Library/Extensions/...` for both. iCBE's `Create` returns a
`TC::CICBETranslationBlock`; vtable from the vptr: [0] `Translate(const STB_TranslateInputArgs*,
STB_TranslateOutputArgs*)`, [1] `FreeAllocations`, [2],[3] destructors; input args start with `pInput` (IGIL text) and
`InputSize` (strlen + 1). The plugin's dump code (`checkForDump`/`dumpIGIL`) is never called; it also reads
`MTL_AIR_INJECTION_ENABLE`/`MTL_IGIL_INJECTION_ENABLE` (not examined). Environment variables of client processes do
not reach MTLCompilerService.

### What the IGIL target does with argument buffers

Compute (`igil_64`, 64-bit pointers):

| Source | IGIL emitted | Problem / fix |
|---|---|---|
| `device T*` member | 64-bit pointer from the AB (C0), then `LD_RAW_PTR rD, rP, u0` (placeholder surface) | backend selects the surface from the high word: works once declared |
| `constant T*`, nested `constant S&` | the same with `LD_RAW_PTR rD, rP, C0` | a C surface ignores the high word (reads the outer AB): retarget to the UAV range |
| texture read | handle (low 32 bits), waterfall loop, `LD_UAV_TYPED rD, rC, u[rI.x]` (no range) | iCBE rejects: rewrite to `LOAD ..., t[r], 2D` over a declared range |
| texture sample | `SAMPLE_L ..., t[rI.x], s..., 2D/3D/CUBE` | needs a declared t range |
| sampler member | `s0[rS.x]`, no `DCL_SAMPLER` | needs a declared s range |
| any texture/sampler call in a function that also has its own texture/sampler argument or constexpr sampler | result replaced by `zeroinitializer` in **IGIL TextureTypeEncode** | wrapped (below) |
| texture write | `STORE_UAV_TYPED_L u[rI.x]` | not handled yet |

Graphics (vertex/fragment, lowered with a **32-bit pointer data layout**, `e-p:32...`):

- Any argument buffer crashed MTLCompilerService: `IGILair_ldst_handling::ProcessFunction` picks a constant-load
  intrinsic by the loaded type (i8/i16/i32/half/float and vectors; `i64`, `<2 x i32>` too) and a pointer-typed load
  matches none -> `IGILIntrinsicInfo::getDeclaration(id 0)` -> `strlen(NULL)`.
- Member offsets are computed with 4-byte pointers (members at bytes 0, 8, 16 were read at 0, 4, 8). Byte-offset or
  shadow-struct edits made offline are folded back by instcombine under the 64-bit layout before the switch to 32
  bits, so the fix has to run at the start of the IGIL pass manager (the AIR hook).
- After the fix, constant-buffer reads are `LD_RAW r, byteoffset, C0` (4 dwords: pointer low in `.x`, high in `.y`);
  a read through a constant pointer becomes `MOV rD, C-1[rI.x]` (rI = byte address >> 4, "C-1" = unknown buffer) and
  through a device pointer `LD_RAW rD, rA.x, u0` (u0 undeclared). `LD_RAW_PTR` is rejected in graphics.
- Pixel shaders accept `LD_RAW` from a static UAV or CB, and from `u[r]` / `C[r]` indexed ranges.
- **Vertex shaders ignore a dynamic UAV index** (they read the first UAV whatever the value; a constant index works;
  an indexed CB range fails outright), so vertex shaders select the surface with an `IF_ICMP.eq` chain. Dynamic
  texture indexing does work in vertex shaders.

Fixtures in [tests/fixtures](tests/fixtures): `t1`, `t5`, `kab` (compute, every member kind), `ks` (AB samplers),
`kp`/`k13-c*` (the TextureTypeEncode drop, captured without the wrapper), `k13-c7-kept` (own sampler + AB sampler,
captured with the wrapper), `forms/` (backend acceptance of single instruction forms).

### Hardware/driver model

- **Pointer format: `(binding-table index << 32) | byte offset`.** `setBuffer:offset:` offsets live in the surface
  base. The compute backend picks the surface from the high word on every UAV access.
- **Binding table (compute): constant buffers, then textures, then UAVs by register number.** `uN` has index
  `nCB + nTex + N`; a read through an undeclared index returns (1,0,0,0). iCBE rejects UAV registers in the 150s.
- One declared `2D FLOAT` t range serves every texture kind (2D, 3D, cube, cube array, 2D array, 1D, half, ushort
  read, depth sample, probe7); the backend takes the dimension from the instruction.
- Slots measured on the GPU: textures up to 127 in compute, vertex and fragment; buffers 40..63 in compute, vertex
  and fragment; Metal's 31-buffer limit is API-level only. Samplers: 16 per stage (Gen7 and Metal alike).
- IGIL syntax: accepted `IADD r.x, r.x, imm`, hex immediates, `IF_ICMP.eq a, b; ... ENDIF;`, `IF_CND.nz r;`;
  rejected 3-operand `IMUL`; `ICMP` yields all ones. New declarations need real, consecutive `KERNEL_ARGUMENT`s.
- **MetalOld's on-disk pipeline cache key ignores the compiler.** Clear `$(getconf DARWIN_USER_CACHE_DIR)/com.apple.metal`
  after every compiler change (per user), and kill MTLCompilerService after swapping iCBE.

### Argument-buffer layout

`MTLCreateStructTypeFromArgumentDescriptors` (exported by MetalOld) packs members in index order: texture, sampler and
pointer members 8 bytes, 8-aligned; resource arrays stride 8; constants at natural size/alignment (float4: 16). The
compute IGIL target reads exactly those offsets (probe9 + `kab.igil`); graphics does after the AIR hook. Reflection's
`bufferStructType` gives offset -1 for resources and 0 for constants, so the device rebuilds descriptors from any
struct type and reuses MetalOld's packer. MetalOld's `_MTLIndirectArgumentBufferLayout`/`_MTLIndirectArgumentEncoder`
are abstract (`encodedLength` -1); both public paths end in the device's
`newIndirectArgumentBufferLayoutWithStructType:` + `setStructType:withDevice:` + `newIndirectArgumentEncoderWithLayout:`.

## Design

Gen7 has no bindless heap: every argument-buffer resource gets a fixed slot in each pipeline's binding table, and the
driver binds into those slots per draw.

**Slot contract** ([src/argbuf_config.h](src/argbuf_config.h)):

| Member | Value written into the argument buffer | Bound by the device at | Range |
|---|---|---|---|
| texture | 64-bit, low word = handle h | texture slot `AGB_TEX_SLOT + h` | 32..127 (96) |
| buffer / nested AB | `((AGB_BUF_BTI + h) << 32) \| offset` | buffer slot `AGB_BUF_SLOT + h`, offset 0 | 40..63 (24), BTI 160.. |
| sampler | 64-bit sampler handle h | sampler slot `AGB_SAMP_SLOT + h` | 10..15 (shared with clients) |

Texture and buffer handles are assigned once per layout *structure* (a canonical key), dense and process-wide;
sampler handles once per distinct sampler object.

**Compiler side** (`iCBE.dylib`, [src/compiler](src/compiler)): a wrapper that re-exports the real iCBE (installed
beside it as `iCBE-real.dylib`).

- `icbe_wrap.c`: overrides `Create`/`Delete`; the proxy translation block runs `igil_rewrite` on the IGIL text before
  the real `Translate`. Its constructor installs the AIR hook (the plugin loads iCBE before its first compile).
- `air_hook.c`: rebinds the plugin's import of `llvm::legacy::PassManager::run(Module&)` (a lazy/non-lazy symbol
  pointer, no code patched) so `air_transform` sees each module first; points the **IGIL TextureTypeEncode** pass's
  `runOnFunction` vtable slot (found through libIGIL-Metal's local symbols) at `air_tte_run`. All LLVM symbols come
  from the GPUCompiler 3802 libLLVM handle.
- `air_pass.c` (LLVM-C API): on graphics functions (the operands of the `air.vertex`/`air.fragment` named metadata)
  every GEP into constant memory whose type contains pointers becomes an i8 GEP with the 64-bit layout's offset, and
  every pointer load from constant memory becomes a volatile `<2 x i32>` load + bitcast + `inttoptr`.
  `air_tte_run` records the uses of every call that takes a texture/sampler, runs the pass, and restores the uses it
  replaced with `zeroinitializer`.
- `igil_rewrite.c`, compute and resources: taint analysis (loaded values; `abt` = argument-buffer contents, followed
  through private-memory spills); texture range (`LD_UAV_TYPED u[r]` -> `LOAD t[r+tbase], 2D`; other `t[r]` offset);
  sampler range (`DCL_SAMPLER` after the function's own; `s0[r]` offset); buffer range `DCL_UAVRAW u(ufix+k)` with
  `ufix = BUF_BTI - nCB - nTexDecl` (the texture range is declared whenever buffers are, to keep `ufix` low);
  constant-pointer accesses (C surface, tainted address) retargeted to `u(ufix)`.
- `igil_rewrite.c`, graphics: traces each `C-1[...]` / undeclared-`u0` read back to the `LD_RAW` that loaded the
  pointer, and reads from the UAV range selected by its high word: indexed `u[h+ubase]` in pixel shaders, an
  `IF_ICMP.eq` chain in vertex shaders.

While developing, `LLVMGetNamedMetadataNumOperands` crashed because `RTLD_DEFAULT` had resolved it in the 31001
LLVM; resolved from the 3802 handle it works.

**Device side** (`libargbuf.dylib`, [src/device](src/device)): loaded by the MTL driver bundle (a weak load command,
like `metalold-selectors`), or injected into a test program (`DYLD_INSERT_LIBRARIES` + `agb_install()`).

- Adds `newIndirectArgumentBufferLayoutWithStructType:` and `newIndirectArgumentEncoderWithLayout:` to
  `MTLIGAccelDevice`. `AGBLayout` repacks through MetalOld's packer; `AGBEncoder` (conforms to `MTLArgumentEncoder`)
  writes the values above and records each encoded argument buffer (buffer, base offset) on the `MTLBuffer`.
- Draw-time binding: hooks on `set{,Vertex,Fragment}Buffer(s)`, `...BufferOffset`, `...Bytes` track indices 0..30 per
  stage; every `draw*`/`dispatch*` method of the driver encoders goes through one assembly trampoline
  ([argbuf_tramp.s](src/device/argbuf_tramp.s)) that calls `agb_pre` and tail-jumps to the original. `agb_pre` binds
  each bound argument buffer's recorded resources into the fixed slots (recursing into nested ones) through the
  driver's own setters, caching per encoder and stage.
- Layouts are packed here, not by MetalOld (`MTLCreateStructTypeFromArgumentDescriptors` asserts on inline structs):
  index order, resources 8/8, constants natural size/alignment (float3 = 16), inline structs natural C layout,
  nested argument indices relative to the parent member (probe15), arrays of structs by stride.
- ASTC ([astc.m](src/device/astc.m)): `newTextureWithDescriptor:` (device, heap) creates ASTC textures as RGBA8
  (sRGB as RGBA8 sRGB) and tags them; `replaceRegion` (slice form; the 4-argument form forwards to it) and both blit
  `copyFromBuffer:...toTexture:` variants decode the blocks with libate (`at_encoder_decompress_texels`) first.
- Switches: `AGB_LOG=1` (stderr copy of the log), `AGB_DISABLE`, `AGB_HANDLE_BASE=T,B` (tests: start handles high),
  `AGB_ASTC_DISABLE`, `AGB_TRACE_TEXTURES=1` (log every texture descriptor; `open -a App --env VAR=1` reaches apps).

## What was changed on the laptop (live only, nothing in the image; overlays die on reboot)

- `AppleIntelHD4000GraphicsGLDriver.bundle/Contents/MacOS/iCBE.dylib` = the wrapper, `iCBE-real.dylib` = the image's iCBE.
- `AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver` = the image's driver +
  `LC_LOAD_WEAK_DYLIB` of `libargbuf.dylib` beside it (every newly started Metal process gets the shim).
- Test programs in `/private/var/tmp/argbuf/`. `laptop-swap rm` each path to restore the image.

## Build and test

```sh
tools/metal-argbuf/make NEW_OUT --icbe ORIGINAL_ICBE [--runtime STOCK_REPLAY_RUNTIME] [--driver ORIGINAL_MTL_DRIVER]
python3.14 -I -B -m unittest discover -s tools/metal-argbuf/tests
```

`--driver` writes the driver binary with the weak load (uses `patches/metalold-selectors/src/add_weak_dylib.py`).

**Host harness** (no laptop; the `cache/shaders` Rosetta replay runtime, default
`store/artifacts/shader-replay-runtime-r38-58517acfb9fa/runtime`, override with `ARGBUF_RT`; `make --runtime` builds
the wrapped copy for `ARGBUF_RT_WRAP`). Kernels: `xcrun -sdk macosx metal -target air64_v25-apple-macosx13.0
-std=metal3.0` (Xcode's default AIR is too new for the 3802 reader). AIR text: `air-opt -S` / `air-as` from the Metal
toolchain.

- `run.py` / `run-wrap.py LIB FN OUT`: compile a compute function (stock / wrapped); `run-render.py JOBS OUT [stock]`:
  render pipelines (job line `id<TAB>lib<TAB>vfn<TAB>lib<TAB>ffn<TAB>c0.pixelFormat=125`).
- `dbg.py`/`dbg2.py`: capture / replace compute IGIL with lldb; `bt-render.py`: crash backtrace; `ids.py`: intrinsic
  ids from `IGILair_ldst_handling`; `dumpval.py`: raw words of an `llvm::Value` at an address; `annotate.py`: label
  selector refs in `otool -tV` output.
- Wrapper switches (harness only, the environment does not reach the laptop's MTLCompilerService):
  `ICBE_WRAP_DUMP_DIR` (every IGIL program in/out), `ICBE_WRAP_INJECT_DIR` (replace a program by `DIR/NAME.igil`),
  `ARGBUF_IR_DUMP_DIR` (AIR before/after the hook), `ARGBUF_LLVM_OPTIONS` (e.g. `-print-after-all`),
  `ARGBUF_AIR_DISABLE`, `ARGBUF_TTE_DISABLE`.
- VFX: `ipsw extract --dyld --dyld-arch x86_64` from the 26B131 IPSW, then `ipsw dyld extract CACHE
  /System/iOSSupport/System/Library/PrivateFrameworks/VFX.framework/Versions/A/VFX`.

**On the laptop** (hold the lock): `laptop-swap put` the wrapper over `iCBE.dylib` (with `iCBE-real.dylib` beside
it) and, for whole-system use, the `--driver` output plus `libargbuf.dylib` into the MTL driver bundle;
`killall MTLCompilerService`; clear the Metal cache; run probes from `/private/var/tmp/argbuf/` (with
`DYLD_INSERT_LIBRARIES=.../libargbuf.dylib` when the driver is not swapped).

## Probes

| Probe | What |
|---|---|
| probe | array<texture2d,8> indexed per thread, stock compiler |
| probe3/4/7 | hand-encoded argument buffers (slots from argbuf_config.h): per-thread texture choice; texture + pointers; 9 texture kinds |
| probe5/6 | pointer format and binding-table order (historical, fixed slots) |
| probe8 | texture slots up to 127 (compute) |
| probe9 | layout from descriptors vs reflection |
| probe10 | real API, compute: descriptor/reflection encoders, nested AB, constants + second AB |
| probe11 | render: vertex AB (device + constant pointer), fragment AB (2 textures + constant pointer) |
| probe12 | AB samplers: linear/nearest, beside the function's own sampler, two ABs with different samplers |
| probe14 | render stages one at a time (vertex buffer, fragment buffer, vertex texture); run with `AGB_HANDLE_BASE` |
| probe15 | `probe15 FILE.metal FUNCTION INDEX`: dump MetalOld's reflection of an argument buffer and the encoder size |
| probe16 | inline struct in an argument buffer (kernels/k17.metal) |
| probe17 | ASTC 8x8 upload through replaceRegion and blit, compared with libate's decode |
| webgpu-check.html | Safari: WebGPU compute (storage buffer) and render (uniform bind group), read back and checked; open it from a file |

## Open problems

1. **Weather/VFX**: loads and renders; maps, effects and long runs not exercised. Watch for handle exhaustion
   (96 textures / 24 buffers per process; logged as "out of ... handles") and unsupported shapes (logged by argbuf
   and icbe_wrap, see Build and test).
8. ASTC: `getBytes` returns RGBA8, texture views with an ASTC format, ASTC HDR, and buffer-backed/IOSurface ASTC
   textures are not handled; textures report `pixelFormat` RGBA8.
2. Handle collisions: two instances of the same layout bound in one draw share handles. Arrays of argument buffers
   are encoded but not bound per element (VFX does not use them).
3. Samplers share Metal slots 10..15 with clients (Gen7 has no spare sampler slots).
4. Writable AB textures (`STORE_UAV_TYPED_L u[r]`) and device-pointer stores in graphics are not rewritten.
   `LD_UAV_TYPED` reads are rewritten as 2D (correct for every kind measured).
5. Argument-buffer contents copied by the CPU or a blit are not seen by the side table.
6. Cost: every rewritten shader declares the whole ranges (96 textures, 24 buffers, 6 samplers); vertex-shader
   pointer reads pay a 24-way compare chain. Not measured.
7. Shipping: a patch recipe that installs the wrapper as `iCBE.dylib` re-exporting the image's repaired iCBE
   (`golden-gate-metal-libraries` installs that one, so order after it), `libargbuf.dylib` + the driver's weak load
   (after `metalold-selectors`/`golden-gate-metal-framebuffer`, which add theirs), and Metal shader-cache
   invalidation.

## Files

| Path | What |
|---|---|
| `src/argbuf_config.h` | slot contract |
| `src/compiler/icbe_wrap.c` | iCBE wrapper (proxy translation block, harness switches) |
| `src/compiler/igil_rewrite.[ch]`, `igil_tool.c` | the IGIL rewrite (pure text, host-testable) and its CLI |
| `src/compiler/air_hook.[ch]`, `air_pass.c` | PassManager::run rebinding, TextureTypeEncode wrapper, AIR legalization |
| `src/device/argbuf.m`, `argbuf_tramp.s` | device side |
| `probes/probe*.m`, `kernels/*.metal` | GPU programs and harness kernels |
| `tests/` | host tests and captured IGIL fixtures |
