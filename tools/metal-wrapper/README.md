# metal-wrapper

hao's own `Metal.framework/Versions/A/Metal` for Intel Macs on the legacy Metal stack. It replaces OCLP's Moraea
wrapper. Like that wrapper it re-exports `MetalOld.dylib`, Apple's legacy Metal (Ventura, 306.3.5), which
together with the HD 4000 driver does the actual GPU work. It adds what newer clients import but `MetalOld`
lacks.

- **What ships:** the wrapper is our code and ships prebuilt in HaoSupportPkg.
- **Where `MetalOld` comes from:** it is Apple's binary, carried by OCLP's PatcherSupportPkg. The builder
  downloads it there (tools/acquire); we never redistribute it.

## How it is made

```sh
python3.14 -I tools/metal-wrapper/make.py build tools/metal-wrapper/spec/gap-26B131.json \
    --metalold PATH/MetalOld.dylib --out DIR          # DIR/Metal, DIR/stubs.m, DIR/report.json
python3.14 -I tools/metal-wrapper/make.py generate SPEC OUT.m [--metalold PATH]
python3.14 -I tools/metal-wrapper/make.py ivars PATH/MetalOld.dylib [CLASS...]
```

`make.py` takes the gap table from [spec/](spec/README.md) directly. It builds every required symbol `MetalOld`
lacks that x86_64 code on Intel needs: the 90 Moraea's wrapper provides, plus the 15 that importers outside the
Apple-GPU driver bundles use and nobody provides today. It leaves out 13 that only `AGXMetal*` bundles use; those
never load on an Intel GPU:
`MTLCompileBuiltinFunctionRequestData`, `MTLGenericAddressSpaceDispatchInfo`, `_MTL4DispatchGroupCompilerTask`,
`_MTLIndirectBlendColor`, `_MTLIndirectDepthBiasInfo`, `_MTLIndirectDepthTestBounds`, `_MTLIndirectScissorRects`,
`_MTLIndirectStencilReferenceValues`, `_MTLIndirectViewports`, `_MTLTensorAuxiliaryPlane`,
`_MTLPipelinePerformanceKey{GASNativeInstructionCount,NonSmashableAtomicInstructionCount,SmashableAtomicInstructionCount}`.
A plain SPEC (`{"classes", "functions", "data", "ivars"}`) still works.

For 26B131 that is 105 symbols: 64 classes, 15 functions, 4 data and 22 ivar offsets. 101 are built, 4 ivar offsets
are refused (below).

| Kind | How | 26B131 |
|---|---|---|
| class the SDK declares | `@implementation` with the header's superclass. Properties are synthesized. Every other declared method returns zero, `init…` returns `[super init]`, and NSCopying adopters copy by retain. A superclass `MetalOld` lacks is generated too | 46 + 3 superclasses |
| function, data the SDK declares | the header's prototype returning zero; NSString constants hold their own name | 0 functions, 2 data |
| private name | [src/private.m](src/private.m), always built in; the build refuses a private name it doesn't find | 18 classes, 15 functions, 2 data |
| ivar offset, class `MetalOld` defines | exported with `MetalOld`'s offset if its class has the ivar, read statically from its ObjC metadata and checked at load with `ivar_getOffset`; refused if it doesn't | 0 exported, 4 refused |
| ivar offset, class we define | the ivar in that class's declaration, with 27.2.1's type | 18 |

- **Nothing twice:** names `MetalOld` exports are skipped, and the build fails if the wrapper and `MetalOld` both
  export a symbol.
- **Coverage:** the build fails unless the wrapper plus `MetalOld` export every selected symbol except refused
  ivars. `report.json` lists what came from where.
- **Output:** x86_64, install name `/System/Library/Frameworks/Metal.framework/Versions/A/Metal`, compatibility
  version 1.0.0 (as OCLP's wrapper), signed ad hoc.

### Private names (`src/private.m`)

Signatures and behaviour come from 27.2.1's x86_64 Metal (disassembled read-only from the cache) and from the
importers' call sites. Each is the smallest implementation that is correct for its callers.

| Name | Callers | Returns | Why that is safe |
|---|---|---|---|
| `_MTLTensorDataTypeToString`, `_MTLTensorPlaneTypeToString`, `MTLSparsePageSizeString` | MetalTools debug layer messages | 27.2.1's strings, `@"Unknown"` otherwise | same table |
| `tensorDataTypeBitCount`, `_MTLTensorIsPackedFormatType` | MetalTools debug layer | 27.2.1's values; 0 bits for unknown types (27.2.1 also reports it) | same table |
| `isRGBPixelFormat` | MetalTools, ParavirtualizedGraphicsGPUTask | true for formats 45–48, 95–100, 120–122 | same test |
| `_MTLTensorElementCount`, `_MTLTensorExtentsAreEqual` | MetalTools ML encoder validation | product of extents; equal rank and extents | public `rank`/`extentAtDimensionIndex:` give the numbers 27.2.1's private accessor does |
| `_MTLIsInternalBuild` | IOGPU, MTLCompiler, MetalTools | `os_variant_has_internal_diagnostics("com.apple.Metal")` | same call |
| `MTLRangeAllocatorInitWithStartRange`, `MTLRangeAllocatorGetFreeSize` | IOGPU | one free range `[start, start+length]`; sum of `end−start+1` | `MetalOld`'s allocator struct is 27.2.1's; `MetalOld`'s Destroy frees the array |
| `validateDispatchThreadsPerThreadgroupWithRTPTG` | MetalTools debug encoders | pushes 27.2.1's three messages (level 8) through `MetalOld`'s `_MTLMessageContextPush_` | same checks, same signature in both |
| `_MTLValidateRenderPassDescriptorTileProperties` | MetalTools, pre-fall-2025-SDK apps | YES, no message | 27.2.1's reads Metal 27's private render-pass struct, which `MetalOld` doesn't have; the caller ignores the result |
| `generatePipelineHashWithDescriptor` (C++) | MetalTools shader validation | all-zero 32-byte hash (in memory, as 27.2.1's) | keys MTL4 pipelines named in shader-validation settings; `MetalOld` makes no MTL4 pipelines, a zero hash matches none |
| `newFunctionReflectionWithFunctionDescriptor` | AGX drivers | nil | 27.2.1 returns nil without data |
| `kMetalTextureWriteAccessPattern`, `MTLPipelinePerformanceKeyThreadInvariantSpillsCount` | MetalTools, AGX drivers | 27.2.1's strings | same value |
| `MTLMessageDecoder` | MetalTools shader-log reader | `-decode` → nil | the reader then shows the undecoded format string |
| `MTLFunctionStitchingEarlyReturnNode`, `MTLFunctionStitchingAttributeKernel` | CoreImage, GPU tools | node: `MetalOld`'s function node with an empty name and the condition as its one argument, as 27.2.1 | uses `MetalOld`'s public node API |
| `MTLResourceAddressRangeArray` | ParavirtualizedGraphics | owns a copy of the ranges; count, ranges, copy, equality | as 27.2.1 |
| `_MTLAllocation`, `_MTLLogState`, `_MTLResourceViewPool`, `_MTLTextureViewPool`, `_MTLIndirectDrawMesh*Arguments` | IOGPU, MetalTools, AGX drivers | 27.2.1's superclass and ivars (types and offsets identical), initializers, accessors | nothing on Intel creates them; their importers read the ivars by offset |
| `_MTL4CommandAllocator`, `_MTL4CommandBuffer`, `_MTL4CommandQueue`, `_MTL4CommandEncoder` and its compute, render and ML subclasses; `_MTL4Archive`, `_MTL4ArgumentTable`, `_MTL4BinaryFunction`, `_MTL4Compiler`, `_MTL4CompilerTask` | IOGPU subclasses the first group, AGX drivers the rest | as above; queue init makes the `com.Metal4.SubmissionQueue` dispatch queue as 27.2.1 | `MetalOld`'s devices make no Metal 4 objects. Work methods are left out: subclasses override them, anything else fails loudly |

Their common base `_MTLObjectWithLabel` is `MetalOld`'s; its ivars match 27.2.1's, so every ivar offset above is
the same as 27.2.1's.

### Refused ivar offsets

`MetalOld`'s `_MTLCommandBuffer` and `_MTLCommandQueue` lack four ivars that importers reach by offset. Exporting
any offset would make them read or write unrelated fields, so the wrapper exports none. A class's ivars can't be
added once `MetalOld` has registered it.

| Symbol | Reader | What it does there | What would be needed |
|---|---|---|---|
| `_MTLCommandBuffer._privateData`, `._privateDataOffset` | MetalTools `-[MTLGPUDebugCommandQueue commandBuffer]`, `commandBufferWithDescriptor:` | passes their addresses on the base command buffer as out-parameters, so they would be written | shader validation (`MTL_SHADER_VALIDATION`, the GPU debug layer) is off by default. To support it: a MetalTools fix that keeps the private data in its own `MTLGPUDebugCommandBuffer` |
| `_MTLCommandQueue._privateDataTable` | MetalTools `-[MTLGPUDebugCommandBuffer preCommit]` | reads an object pointer from the base queue and messages it | same layer, same fix |
| `_MTLCommandBuffer._requiresBindingLogState` | IOGPU `-[IOGPUMetalCommandBuffer commit]` | reads a flag from its own `_MTLCommandBuffer` subclass | IOGPU command buffers exist only on IOGPU devices, never with `MetalOld`; nothing |

How a cache image behaves when one of its Metal imports is missing is the gap table's open question
([spec/README.md](spec/README.md#what-this-method-misses)); the laptop check answers it.

### Load-time workarounds (`src/runtime.m`)

These match what OCLP's Moraea wrapper does in today's images, from a read-only analysis of its PatcherSupportPkg
2.0.5 build. That way the first laptop check compares like with like. Each one can be switched off for an A/B
with `HAO_METAL_DISABLE=name,…`. Process checks are substrings of argv[0], as Moraea's are.

| Name | Where | What |
|---|---|---|
| `addimp` | every process | 20 selectors newer clients send that MetalOld's `MTLHeapDescriptorInternal`, `MTLCompileOptionsInternal` and `NSProcessInfo` lack; they answer 0. Moraea's `requireThreadsPerThreadgroup` pair is not added, since no 27.2.1 code sends it |
| `coreimage` | every process | `-[MTLCompiler validateLanguageAndAIRVersionForFunction:completionHandler:]` → YES; `-[MTLCompileOptionsInternal setLanguageVersion:]` → no-op |
| `webgl` | WebKit | ANGLE refuses Metal on MacBookPro models below 13: `angle::IsMetalRendererAvailable()::queriedMachineModel` is pre-set; `supportsFamily:` answers YES for Mac2 (2002); `-[MTLIOAccelDevice registryID]` → 0 |
| `glass`, `safari`, `wallpaper`, `photos` | those processes | validator skips Moraea applies; laptop A/B candidates |
| `keynote`, `pixelmator` | those apps | third-party tweaks kept for parity; candidates to drop |

Two differences from Moraea:

- **No hello skip.** Setup Assistant plays hello.
- **Constants are data.** Moraea exports the `NSDeviceCertification…` and `NSProcessPerformanceProfile…` constants
  as small functions, so a reader gets instruction bytes. Ours come from the SDK headers as data.

Next:

- **`null_resettable` properties:** generated stubs synthesize them, so a getter such as
  `-[MTL4RenderPassDescriptor depthAttachment]` returns nil where Apple's never does.
- **Laptop A/B of the workarounds, then dropping what isn't needed.**
- **libhelloblur:** a weak load of `libhelloblur`, as golden-gate-hello adds to OCLP's wrapper today.
- **A recipe** that installs the wrapper in the `metal-3802` pack.
- **A laptop check.**

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/metal-wrapper/tests
METAL_WRAPPER_METALOLD=PATH/MetalOld.dylib METAL_WRAPPER_BUILT=DIR/Metal \
    python3.14 -I -B -m unittest discover -s tools/metal-wrapper/tests    # with a wrapper built as above
```

These tests need Xcode with the macOS 27 SDK, and Rosetta. On a macOS 27 host every x86_64 process already has the
system's Metal loaded (even CoreFoundation pulls it in). So the tests take our classes and functions by symbol and
assert they come from our image. They build a wrapper from three header classes, one constant and three ivar
offsets against a stand-in `MetalOld` (no Apple binary), then load it under Rosetta:

- **Stubs:** a property round-trip, `copy`, the subclass chain, a stub method call, the constant's value, and the
  `MetalOld` re-export. Names `MetalOld` already exports are skipped.
- **Load-time workarounds:** `runtime.m` built with `HAO_METAL_CLASS_PREFIX="Hao"` against stand-in classes;
  shipping builds use Apple's names, which the Moraea analysis found in both MetalOld and 27.2.1's Metal. The
  libANGLE lookup can only be proven on the laptop.
- **private.m:** the string tables, bit counts, pixel-format test, tensor extents, range allocator, the
  threads-per-threadgroup messages (through a stand-in message context), the zero hash, the decoder's nil; a
  runtime subclass of `_MTL4CommandBuffer` read through our ivar-offset symbols; the encoder and allocation
  superclasses; the early-return node on the stand-in's function node.
- **Ivar offsets:** a stand-in class's unexported ivar is exported at the offset the static reader finds and the
  runtime reports; an ivar it lacks is refused.
- **Gap selection:** from the JSON alone, 105 symbols taken (all 90 of Moraea's) and the 13 exclusions.

With the environment variables set, two more run: the static ObjC reader agrees with every ivar-offset symbol
`MetalOld` exports (1,479), and the built wrapper plus `MetalOld` export every symbol the 26B131 table requires
except the 13 exclusions and the 4 refused ivars.
