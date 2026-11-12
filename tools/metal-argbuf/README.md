# metal-argbuf

Native Metal argument buffers (tier 1) for the Intel HD 4000 (Gen7) under OCLP's legacy Metal stack
(MetalOld + `AppleIntelHD4000GraphicsMTLDriver`), on Golden Gate (macOS 27.2.1, 26B131). No software fallback and no
spoofing: shaders read argument buffers on the GPU, and the driver binds what they refer to.

**Status: prototype, not in any image and not yet part of a patch.** Compute shaders work end to end through the
real Metal API on the laptop GPU. Vertex and fragment shaders do not compile yet: the Intel compiler crashes before
any IGIL is produced (see [Open problems](#open-problems)). Weather has not been retried.

| Piece | Where | State |
|---|---|---|
| Compiler side: iCBE wrapper with an IGIL rewrite | [src/compiler](src/compiler) | GPU-verified for compute |
| Device side: layout, encoder, draw-time binding | [src/device](src/device) | GPU-verified for compute |
| Slot contract shared by both | [src/argbuf_config.h](src/argbuf_config.h) | 16..23 textures, 40..47 buffers (needs moving, see below) |
| GPU test programs | [probes](probes) | probe, probe3, probe4, probe7, probe10 CORRECT; probe11 (render) fails to compile |
| Host harness scripts | [harness](harness) | compile, capture IGIL, render jobs, crash backtraces |
| Host tests | [tests](tests) | rewrite rules on captured IGIL |

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
  `setTexture:atIndex:` + `setSamplerState:atIndex:` + `usedResources`: **samplers are stored in argument buffers**.
- `useResource(s):usage:(stages:)`, `useHeap(s)`, residency sets.
- `setArgumentBuffer:startOffset:arrayElement:` has **no** call site: VFX uses no arrays of argument buffers.
- Embedded shader source: `re_scene_argument_buffer` at buffer 19 (texture2d<half> x3, texture2d_array<half> x7,
  texture1d<half>, texturecube_array<half> x2, depth2d<float>, `constant` struct pointers, `constant half*`, a nested
  `constant VirtualEnvironmentProbeLighting::TextureArgumentBuffer&`); `re_entity_argument_buffer` at buffer 18
  (texture2d_array<half>, `constant BreakthroughGPUData*`, texture2d<ushort>, texture2d<half>). These are render
  shaders, so the graphics-stage problem below blocks Weather. VFX also ships `*_no_argument_buffer` kernels.

## The HD 4000 Metal stack, as measured

OCLP payload 11.7.10, GPUDriversIntel 16.5.14.

```
MetalOld.dylib (_MTLDevice, MTLIOAccelDevice, encoders)          <- API, argument-buffer layout/encoder hooks
  AppleIntelHD4000GraphicsMTLDriver (MTLIGAccelDevice, MTLIGAccel{Compute,Render}CommandEncoder)
MTLCompilerService
  libMTLIntelCompilerPlugin.dylib  MTLIntelCompiler::generateIGIL(llvm::Module*, MTLIntelFunctionType, ...)
    libIGIL-Metal.dylib            LLVM target igil_64_GEN7 (AppleIntelGraphicsShared.bundle); AIR -> IGIL text
  iCBE.dylib                       IGIL text -> USC -> Gen7 binary (AppleIntelHD4000GraphicsGLDriver.bundle/Contents/MacOS;
                                   the image already ships a repaired copy, sha256 25ea0603...)
```

The plugin hands IGIL to iCBE through the exported `Create`, which returns a `TC::CICBETranslationBlock`. Its vtable
(from the vptr): [0] `Translate(const STB_TranslateInputArgs*, STB_TranslateOutputArgs*)`, [1] `FreeAllocations`,
[2],[3] destructors. Input args start with `pInput` (IGIL text) and `InputSize` (strlen + 1). Compute and graphics
both call `Translate` first. The plugin's dump code (`checkForDump`/`dumpIGIL`, `MTLIRDumpEnable`,
`MTL_IG_SHADER_DUMP_ENABLE`) is never called in this build; IGIL is captured with lldb instead (harness/dbg.py).
The plugin also reads `MTL_AIR_INJECTION_ENABLE` and `MTL_IGIL_INJECTION_ENABLE` (not examined yet). It tries
`/Library/GPUBundles/AppleIntelHD4000GraphicsGLDriver.bundle/.../iCBE.dylib` before the System path.

### What the IGIL target does with argument buffers (compute)

| Source | IGIL emitted | Backend (iCBE) |
|---|---|---|
| buffer member `device T*` | 64-bit pointer loaded from the AB (C0), then `LD_RAW_PTR rD, rP, u0` (surface operand is a placeholder) | compiles; surface chosen from the pointer's high word |
| buffer member `constant T*`, nested `constant S&` | the same, but `LD_RAW_PTR rD, rP, C0` | compiles, but a C surface **ignores the high word**: reads hit the outer argument buffer |
| texture member, read | handle loaded from the AB (low 32 bits), waterfall loop (`GET_VALUE_FROM_FIRST_ACTIVE_LANE`), `LD_UAV_TYPED rD, rC, u[rI.x]` with no dimension and no declared range | rejected ("IGILtoBinary compilation failed!") |
| texture member, sample with a constexpr sampler | `SAMPLE_L ..., t[rI.x], s0, ..., 2D/3D/CUBE` | needs a declared t range |
| sampler member | `s0[rS.x]`, no `DCL_SAMPLER` | accepted (which sampler it reads is not established) |
| texture member, write | `STORE_UAV_TYPED_L u[rI.x]` | not handled yet |
| AB texture sampled with a sampler **argument** | sample dropped (result folded to 0) | - |
| AB sampler used in a function that has **any sampler of its own** (argument, even unused, or constexpr) | sample dropped, waterfall loop left empty | - |

Fixtures in [tests/fixtures](tests/fixtures): `t1` (texture + device pointer), `t5` (two pointers), `kab` (every
member kind, nested AB), `ks` (AB samplers), `kp`/`k13-c*` (the sampler-drop cases), `forms/` (backend acceptance of
individual instruction forms).

### Hardware/driver model

- **Pointer format: `(binding-table index << 32) | byte offset`.** `setBuffer:offset:` offsets live in the surface
  base. The backend picks the surface from the high word on every UAV access.
- **Binding table: constant buffers, then textures, then UAVs by register number.** So `uN` has binding-table index
  `nCB + nTex + N`. A read through an undeclared index returns (1,0,0,0).
- Gen7 indexes `t[]` and `u[]` by a runtime value (an array<texture2d> indexed per thread from a device buffer worked
  64/64 with the stock compiler).
- **One declared `2D FLOAT` t range serves every texture kind**: 2D, 3D, cube, cube array, 2D array, 1D, half sample,
  ushort read, depth sample (probe7). The backend takes the dimension from the instruction.
- Textures bind at slots up to 127 (probe8, compute). The driver's compute encoder keeps buffers at
  `this+0x48+i*0x30` without a bounds check (about 64 entries) and textures at `this+0xc90+i*8`; buffer slots 40+
  bind and resolve correctly. Metal's public 31-buffer limit is API-level only. Render-encoder limits are **not
  measured yet**.
- Accepted IGIL: `IADD r.x, r.x, imm`, hex immediates, `IF_ICMP.eq a, b; ... ENDIF;`, `IF_CND.nz r;`. Rejected: the
  3-operand `IMUL`. `ICMP` yields all ones (the target ANDs with 1). New declarations need real, consecutive
  `KERNEL_ARGUMENT` numbers after the shader's own (made-up numbers such as 1000 are declared but never bound).
- **MetalOld's on-disk pipeline cache key ignores the compiler wrapper.** Clear
  `$(getconf DARWIN_USER_CACHE_DIR)/com.apple.metal` (per user) after every wrapper change, or a stale pipeline is
  used. Shipping this must invalidate shader caches too.

### Argument-buffer layout

`MTLCreateStructTypeFromArgumentDescriptors` (exported by MetalOld) packs members in index order: texture, sampler
and pointer members are 8 bytes, 8-aligned; resource arrays have stride 8; constants have their natural size and
alignment (float4: 16). The IGIL target reads exactly those offsets: for
`{ texture2d t0 [[id(11)]]; constant float4 *p; texture3d t3; array<texture2d,3> arr; constant Inner &in; uint k;
float4 v; constant float *q; }` it reads 0, 8, 16, 24+8i, 48 (as two 32-bit halves), 56, 64, 80, and inside Inner
0 and 8 (probe9 + `kab.igil`). Reflection's `bufferStructType` gives offset -1 for resources and 0 for constants, so
the device must compute offsets itself; it rebuilds descriptors from any struct type and reuses MetalOld's packer.

MetalOld internals: `_MTLIndirectArgumentBufferLayout` and `_MTLIndirectArgumentEncoder` are abstract
(`encodedLength` -1; the base `setStructType:withDevice:` only builds nested layouts through the device).
`MTLEmulationIndirectArgumentBufferLayout` groups members by kind, which is not the front-end layout. Both public
paths, `newArgumentEncoderWithArguments:` and `MTLFunction newArgumentEncoderWithBufferIndex:` (via
`newArgumentEncoderWithBufferBinding:`), end in the device's `newIndirectArgumentBufferLayoutWithStructType:`,
`setStructType:withDevice:` and `newIndirectArgumentEncoderWithLayout:`. Reference for a native bindless design
(Gen8+, not usable here): Tahoe's x86_64h KBL driver, `MTLIGAccelIndirectArgumentEncoder`, `IGRenderStateBindless`;
`AppleParavirtGPUMetalIOGPUFamily` subclasses the `_MTLIndirect*` classes.

## Design

Gen7 has no bindless resource heap, so the design gives every argument-buffer resource a fixed slot in each
pipeline's binding table and binds into those slots per draw.

**Slot contract** ([src/argbuf_config.h](src/argbuf_config.h)):

| Member | Value written into the argument buffer | Bound by the device at |
|---|---|---|
| texture | 64-bit, low word = handle h | texture slot `AGB_TEX_SLOT + h` |
| buffer / nested AB | `((AGB_BUF_BTI + h) << 32) \| offset` | buffer slot `AGB_BUF_SLOT + h`, offset 0 |
| sampler | 64-bit sampler handle h | sampler slot `AGB_SAMP_SLOT + h` (compiler side not done) |

**Compiler side** ([src/compiler](src/compiler)): `iCBE.dylib` is a wrapper that re-exports the real iCBE (installed
beside it as `iCBE-real.dylib`) and overrides `Create`/`Delete`. The proxy translation block's `Translate` runs
`igil_rewrite` on the IGIL text:

- Flow-insensitive taint: `taint` = loaded from memory; `abt` = loaded from a constant buffer or u surface (argument
  buffer contents), followed through private-memory (`m*`) spills.
- Textures: if there is any `LD_UAV_TYPED ... u[r]` or a `t[r]` with an `abt` index, declare `AGB_TEX_COUNT`
  `DCL_RESOURCE t(tbase+k), 2D, FLOAT` at `LOCATION_INDEX AGB_TEX_SLOT+k` (tbase = highest declared t + 1), rewrite
  `LD_UAV_TYPED` to `IADD rT.x, idx, tbase; LOAD dst, coord, t[rT.x], 2D;` and offset other `t[r]` uses by tbase.
- Buffers: if any pointer access (`LD/BLD/SLD/STORE/BSTORE/SSTORE_RAW_PTR`) has a tainted address, declare
  `AGB_BUF_COUNT` `DCL_UAVRAW u(ufix+k)` at `LOCATION_INDEX AGB_BUF_SLOT+k`, with
  `ufix = AGB_BUF_BTI - nCB - nTexDecl` (including the added textures), so binding-table index `AGB_BUF_BTI+k` is
  the same in every pipeline. An access whose surface is `C<n>` and whose address is tainted is retargeted to
  `u<ufix>` (the constant-pointer fix). The rewrite refuses (leaves the program unchanged) if the shader's own UAVs
  would collide with the fixed range.
- The operand parser keeps commas inside `(...)`/`[...]` (for `SAMPLE_L`'s `r13(0, 0, 0)`). The wrapper logs
  `icbe_wrap: argument buffers: ...` to syslog. `ICBE_WRAP_DUMP=FILE` saves the rewritten IGIL (host harness only;
  the environment does not reach MTLCompilerService).

**Device side** ([src/device](src/device), `libargbuf.dylib`):

- Adds `newIndirectArgumentBufferLayoutWithStructType:` and `newIndirectArgumentEncoderWithLayout:` to
  `MTLIGAccelDevice` (the base keeps its abort).
- `AGBLayout`: repacks any struct type through `MTLCreateStructTypeFromArgumentDescriptors`, one slot per argument
  index (array elements get consecutive indices), nested layouts for `elementIsArgumentBuffer` pointers. Texture and
  buffer handles are assigned once per **layout structure** (a canonical key), dense and device-wide; sampler
  handles per distinct sampler object.
- `AGBEncoder` (conforms to `MTLArgumentEncoder` at runtime): `setArgumentBuffer:offset:`, buffers, textures,
  samplers, `constantDataAtIndex:`, `newArgumentEncoderForBufferAtIndex:`, `encodedLength`, `alignment`. Each
  encoded argument buffer (buffer, base offset) is recorded as an `AGBInstance` in a table associated with the
  `MTLBuffer`.
- Draw-time binding: hooks on `set{,Vertex,Fragment}Buffer:offset:atIndex:`, `...Buffers:offsets:withRange:`,
  `...BufferOffset:atIndex:` and `...Bytes:length:atIndex:` track what is bound at indices 0..30 per stage. Every
  `draw*`/`dispatch*` method of the driver's encoder classes goes through one assembly trampoline
  ([argbuf_tramp.s](src/device/argbuf_tramp.s)) that saves the argument registers, calls `agb_pre`, and tail-jumps
  to the original, so by-value `MTLSize` arguments stay on the stack. `agb_pre` binds each bound argument buffer's
  recorded resources into the fixed slots through the driver's own setters, recursing into nested argument buffers.
  It caches per encoder and stage what it last bound.
- Loading: from the driver bundle (constructor finds `MTLIGAccelDevice`), or in a test program via
  `DYLD_INSERT_LIBRARIES` plus a call to `agb_install()` after creating the device. `AGB_LOG=1` copies its log to
  stderr; `AGB_DISABLE` turns it off.

## What was changed on the laptop (live only, nothing in the image)

- `laptop-swap` overlay: `AppleIntelHD4000GraphicsGLDriver.bundle/Contents/MacOS/iCBE.dylib` = the wrapper (sha256
  ed6b8111...), `iCBE-real.dylib` = the image's iCBE (25ea0603...). `laptop-swap rm` both to restore the image.
  Overlays die on reboot.
- Test programs and `libargbuf.dylib` in `/private/var/tmp/argbuf/`.
- Nothing else: `libargbuf` is injected only into test programs.

## Build and test

```sh
# everything into a new folder; ORIGINAL_ICBE is the image's iCBE (the wrapper re-exports it)
tools/metal-argbuf/make NEW_OUT --icbe ORIGINAL_ICBE [--runtime STOCK_REPLAY_RUNTIME]
# host tests
python3.14 -I -B -m unittest discover -s tools/metal-argbuf/tests
```

**Host harness** (no laptop; uses the `cache/shaders` Rosetta replay runtime, default
`store/artifacts/shader-replay-runtime-r38-58517acfb9fa/runtime`, override with `ARGBUF_RT`; `make --runtime` builds
the wrapped copy, pass it as `ARGBUF_RT_WRAP`). Kernels must be AIR the 3802 reader accepts:
`xcrun -sdk macosx metal -target air64_v25-apple-macosx13.0 -std=metal3.0` (Xcode's default AIR and its MTLB
container are too new).

- `harness/run.py LIB FN OUT` / `run-wrap.py`: compile a compute function through the stock / wrapped runtime.
- `harness/dbg.py LIB FN OUT IGIL`: capture the IGIL at `MTLIntelCompiler::compileComputeFunctionIGILtoBinary`;
  `dbg2.py` swaps in a hand-edited IGIL.
- `harness/run-render.py JOBS OUT [stock]`: render pipelines (job line: `id<TAB>lib<TAB>vfn<TAB>lib<TAB>ffn<TAB>c0.pixelFormat=125`).
- `harness/bt-render.py JOBS OUT`: crash backtrace; `ids.py`: intrinsic IDs `IGILair_ldst_handling` requests.
- `harness/annotate.py BIN DISASM OUT`: label selector/class references in `otool -tV` output (MetalOld).
- VFX: `ipsw extract --dyld --dyld-arch x86_64` from the 26B131 IPSW, then
  `ipsw dyld extract CACHE /System/iOSSupport/System/Library/PrivateFrameworks/VFX.framework/Versions/A/VFX`.

**On the laptop** (hold the lock): copy `probes/*` and `libargbuf.dylib`; install the wrapper with
`laptop-swap put NEW_OUT/iCBE.dylib .../iCBE.dylib 0755` (and `iCBE-real.dylib` beside it once); `killall
MTLCompilerService`; clear the Metal cache; then
`DYLD_INSERT_LIBRARIES=/private/var/tmp/argbuf/libargbuf.dylib AGB_LOG=1 ./probe10`.

## Results (laptop GPU, 2026-11-12)

| Probe | What | Result |
|---|---|---|
| probe | array<texture2d,8> indexed per thread, stock compiler | 64/64 correct |
| probe3 | two AB textures, per-thread member choice (hand-written handles) | CORRECT |
| probe4 | t1 texture + pointer; t5 two pointers, per-thread index, offset 16; t7 2 CBs + plain texture + AB pointers | CORRECT |
| probe5/6 | pointer format and binding-table order | measured (see model) |
| probe7 | 9 texture kinds through one 2D range | CORRECT |
| probe8 | texture slots up to 127 | bind |
| probe9 | layout from descriptors vs reflection | see layout |
| probe10 | real API: descriptor encoder, reflection encoder, nested `constant Inner&`, `constantDataAtIndex:` + a second AB at offset 256 | all CORRECT (nested needed the constant-pointer fix) |
| probe11 | render: vertex AB (device + constant pointer), fragment AB (2 textures + constant pointer) | pipeline fails to compile (compiler crash) |

## Open problems

In order of what blocks Weather:

1. **Graphics stages crash the IGIL target.** Any argument buffer in a vertex or fragment function crashes
   MTLCompilerService (stock compiler, no wrapper): `IGILair_ldst_handling::ProcessFunction` picks a load intrinsic
   by the loaded type (i8/i16/i32/half/float and vectors) and a 64-bit handle/pointer load matches none, so it asks
   `IGILIntrinsicInfo::getDeclaration` for id 0 and `getName` returns NULL (kernels/k15.metal: `vs2`, `ft`, `fk`,
   `fs`; a vertex AB with a device pointer gets past it and is then rejected by iCBE). Fix before IGIL exists:
   `MTLIntelCompiler::generateIGIL(llvm::Module*, ...)` is exported by the plugin, and GPUCompiler 3802's
   `libLLVM.dylib` exports the LLVM-C and C++ APIs, so an in-process IR pass can legalize argument-buffer accesses
   (for example split 64-bit loads into `<2 x i32>`) before the target runs. Then check what the graphics path does
   with the loaded pointers and handles.
2. **Argument-buffer samplers are dropped** whenever the function has a sampler of its own (argument or constexpr).
   The drop happens in IR passes before instruction selection (`IGILTargetLowering::GetSampler` only maps argument
   samplers and returns -1 otherwise). Candidates: `IGILTextureTypeEncode` (`propagateData` of
   `IGILTextureAndSamplerData`), `IGILArrayOfTexturesNonUniformAccess` (builds the waterfall loop),
   `IGILInlineSamplerLowering`. Same IR-hook approach as 1.
3. **Sampler range in the wrapper**: declare `DCL_SAMPLER` s(AGB_SAMP_SLOT..) and offset `s0[r]` like textures
   (capture a plain sampler declaration: `kp.igil`). Gen7 has 16 sampler slots per stage.
4. **Move the reserved slots above Metal's API range.** Textures 16..23 are inside the client's 0..30. Measure the
   render encoder's slot limits first (probe8 covered compute only), then choose texture and buffer ranges and BTI
   (`ufix` must stay above the shader's own UAVs). Size the counts for VFX (about 32 textures, 16 buffers).
5. Handle collisions: two instances of the same layout bound in one draw share handles (logged as a limitation).
   Arrays of argument buffers are encoded but not bound per element (VFX does not use them).
6. Writable AB textures (`STORE_UAV_TYPED_L u[r]` needs a `DCL_UAVTYPED` range and changes `ufix`);
   `LD_UAV_TYPED` reads are rewritten as 2D (the dimension is lost; correct for every kind measured so far).
7. Argument-buffer contents copied by the CPU or a blit are not seen by the side table.
8. Shipping: a patch recipe that installs the wrapper as `iCBE.dylib` re-exporting the image's repaired iCBE
   (`golden-gate-metal-libraries` installs that one, so order the patch after it), `libargbuf.dylib` loaded by the
   MTL driver bundle (another `LC_LOAD_WEAK_DYLIB`, like `metalold-selectors` and `golden-gate-metal-framebuffer`;
   its hooks chain with the framebuffer shim's), and Metal shader-cache invalidation.

## Files

| Path | What |
|---|---|
| `src/argbuf_config.h` | slot contract |
| `src/compiler/icbe_wrap.c` | iCBE wrapper (proxy translation block) |
| `src/compiler/igil_rewrite.[ch]` | the IGIL rewrite (pure text, host-testable) |
| `src/compiler/igil_tool.c` | `igil_tool IN OUT` |
| `src/device/argbuf.m`, `argbuf_tramp.s` | device side |
| `probes/probe*.m` | GPU programs (probe9 layout, probe10 real API, probe11 render) |
| `kernels/*.metal` | harness kernels: `k9ab` layout, `k10` probe10, `k11`-`k13` samplers, `k14`/`k15` render |
| `tests/fixtures/*.igil` | IGIL captured from those kernels; `forms/` backend acceptance cases |
