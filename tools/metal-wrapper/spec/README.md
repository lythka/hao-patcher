# Metal wrapper spec

What hao-patcher's own Metal wrapper must export. On Intel, OCLP installs Moraea's wrapper as
`Metal.framework/Versions/A/Metal`. It re-exports MetalOld.dylib (Ventura's Metal 306.3.5) and adds stubs for newer
API. The table here lists every Metal class, function and data symbol that x86_64 code in one macOS build imports.
For each it records whether that build's Metal, MetalOld and Moraea's wrapper export it.

| File | What |
|---|---|
| [gap-26B131.json](gap-26B131.json) | The table for macOS 27.2.1 (26B131) |
| [make_gap.py](make_gap.py) | Generator (stdlib Python plus `ipsw` and `dyld_info`); re-run for each OS build |
| [tests/](tests) | Parser and table tests on small text fixtures and synthetic Mach-O bytes |

```sh
python3.14 -I tools/metal-wrapper/spec/make_gap.py --build 26B131 \
    --cache X86_64_CACHE_DIR/dyld_shared_cache_x86_64 \
    --fs SYSTEM_ROOT --fs rosetta=ROSETTA_CRYPTEX_ROOT \
    --metalold OCLP_METAL/MetalOld.dylib --wrapper OCLP_METAL/Metal \
    --oclp-artifact store/artifacts/oclp-hardware-mbp92-... \
    --out tools/metal-wrapper/spec/gap-26B131.json        # about 1 minute; --raw-dir DIR caches tool output
python3.14 -I -B -m unittest discover -s tools/metal-wrapper/spec/tests
```

`OCLP_METAL` is `payload/13.2.1-25/System/Library/Frameworks/Metal.framework/Versions/A` in the OCLP hardware
artifact. `SYSTEM_ROOT` is the mounted 26B131 system volume. `ROSETTA_CRYPTEX_ROOT` is the mounted Rosetta cryptex,
which holds the x86_64 cache and some x86_64 dylibs such as `libMTLHud.dylib`. Every input is opened read-only.
`ipsw` 3.1.737 produced the 26B131 table.

## Method

1. **Importers.** For the shared cache, the importers are the images `ipsw dyld imports CACHE Metal` lists: 251
   dylibs, 243 with `LC_LOAD_DYLIB` and 8 with `LC_LOAD_WEAK_DYLIB`. None re-exports Metal. On disk, the generator
   walks every regular file under each `--fs` root (skipping symlinks and `System/Library/dyld`). It parses the
   x86_64 slice's load commands itself and keeps the files that load the Metal framework path: 53 x86_64 importers.
2. **References.** For each cache importer, the generator reads the undefined symbols that `ipsw dyld macho -n`
   lists as `(from Metal)`. It adds class names from the importer's ObjC metadata (`-o -r`): superclasses, category
   base classes and `@class refs`, when the name is a class Metal exports and the image does not define. In this
   cache those ObjC sources found nothing the symtab missed, because classrefs are folded into the GOT. For on-disk
   importers it uses `dyld_info -arch x86_64 -imports`, which lists the binds dyld performs.
   - **Class references** are `_OBJC_CLASS_$_X` and `_OBJC_METACLASS_$_X` imports (a metaclass import means the
     importer subclasses X; `subclassed` in the JSON), plus the ObjC metadata above.
   - **C symbols** are every other import. `function` means the symbol is in Metal's `__TEXT,__text`; `data` means
     it is in any other section. Data includes ObjC ivar offsets (`_OBJC_IVAR_$_C.ivar`, flagged `ivar_offset`),
     which importers that subclass Metal classes use.
3. **Why the cache symtab.** A cache image's binds cannot be read per image: its classrefs are folded into the GOT,
   and its GOT entries for other cache images are merged into the cache's shared GOT, leaving the image's own
   `__got` zero. The undefined entries stay in each image's symtab. Cross-check: the cache's dyld patch table
   (`ipsw dyld patches`) is what dyld uses to rebind cache clients when Metal is replaced on disk. It lists 505
   Metal exports used outside Metal, exactly the set found from the importer symtabs (`cache_patch_table_check`).
4. **Exports.** For 27.2.1's Metal the generator takes the external symbols in its cache symtab. It checks them
   against Metal's `LC_DYLD_EXPORTS_TRIE`, read from the cache file: 1,535 names, identical. For MetalOld and the
   wrapper it uses `dyld_info -exports`. The wrapper re-exports MetalOld, so `provided_today` is
   `metalold or moraea_wrapper`.
5. **ipsw naming quirk.** `ipsw` prints ivar offset symbols without their leading underscore
   (`OBJC_IVAR_$_C.x`). The generator restores it, and the export trie confirms the Mach-O names.
6. **Counting.** A symbol is *required* when an importer the OCLP overlay does not replace references it. The
   overlay replaces AppleGVA and AppleGVACore (from `--oclp-artifact`'s `install-map.json`), but every symbol they
   use is used elsewhere too. `strong` counts importers with a non-weak reference; every symbol listed below as
   provided by nobody has at least one. `outside_gpu_driver_bundles` leaves out the Metal drivers in
   `/System/Library/Extensions/*.bundle` (AGXMetal*, AppleParavirtGPU*). Metal loads only the driver for the GPU
   present, and on the Intel target that is OCLP's HD 4000 bundle, so none of those drivers ever loads there.

## Results for 26B131

475 Metal symbols are referenced: 208 classes, 116 functions and 151 data symbols (74 of them ivar offsets). All
are required.

| | Required | Missing from MetalOld | Moraea's wrapper adds | Nobody provides | of which outside GPU driver bundles |
|---|---|---|---|---|---|
| Classes | 208 | 74 | 60 | 14 | 4 |
| Functions | 116 | 15 | 10 | 5 | 5 |
| Data | 151 | 29 | 20 | 9 | 6 |
| (data: ivar offsets) | 74 | 22 | 17 | 5 | 5 |

So 118 required symbols are missing from MetalOld. Moraea's wrapper provides 90 of them, and 28 have no provider
today.

**Nobody provides them, and code that loads on the Intel target uses them.** These are the likely live crash
causes (C symbols are given by their Mach-O names):

| Symbol | Kind | Strong importers |
|---|---|---|
| `MTLTensorAuxiliaryPlaneDescriptor` | class | MPSNDArray (also GPUToolsReplay, GPUToolsCapture, weak) |
| `MTLTensorAuxiliaryPlaneDescriptorMap` | class | MPSNDArray (also GPUToolsReplay, GPUToolsCapture, weak) |
| `MTLTensorBufferAttachments` | class | MetalFX, MPSNDArray, MetalTools (also GPUToolsReplay, GPUToolsCapture, weak) |
| `MTLMessageDecoder` | class | MetalTools |
| `__MTLTensorDataTypeToString` | function | MetalTools |
| `__MTLTensorIsPackedFormatType` | function | MetalTools |
| `__MTLTensorPlaneTypeToString` | function | MetalTools |
| `_tensorDataTypeBitCount` | function | MetalTools |
| `_isRGBPixelFormat` | function | MetalTools, ParavirtualizedGraphicsGPUTask (on disk) |
| `_kMetalTextureWriteAccessPattern` | data | MetalTools |
| `_OBJC_IVAR_$__MTLCommandBuffer._privateData` | ivar offset | MetalTools |
| `_OBJC_IVAR_$__MTLCommandBuffer._privateDataOffset` | ivar offset | MetalTools |
| `_OBJC_IVAR_$__MTLCommandQueue._privateDataTable` | ivar offset | MetalTools |
| `_OBJC_IVAR_$__MTLCommandBuffer._requiresBindingLogState` | ivar offset | IOGPU |
| `_OBJC_IVAR_$__MTL4CommandBuffer._labelTraceID` | ivar offset | IOGPU |

How they are reached: MPSCore, MPSImage, MPSMatrix, MPSNDArray, MPSNeuralNetwork and MPSRayIntersector link
MetalTools, and MetalPerformanceShaders links MPSNDArray, so every MetalPerformanceShaders client loads both.
MetalFX is linked by VFX and the GPU tools. RunningBoard links IOGPU.

**Nobody provides them, but only Apple-GPU driver bundles use them** (AGXMetal*, which never load on Intel):
`MTLCompileBuiltinFunctionRequestData`, `MTLGenericAddressSpaceDispatchInfo`, `_MTL4DispatchGroupCompilerTask`,
`_MTLIndirectBlendColor`, `_MTLIndirectDepthBiasInfo`, `_MTLIndirectDepthTestBounds`, `_MTLIndirectScissorRects`,
`_MTLIndirectStencilReferenceValues`, `_MTLIndirectViewports`, `_MTLTensorAuxiliaryPlane`,
`_MTLPipelinePerformanceKeyGASNativeInstructionCount`,
`_MTLPipelinePerformanceKeyNonSmashableAtomicInstructionCount`,
`_MTLPipelinePerformanceKeySmashableAtomicInstructionCount`.

A wrapper that replaces Moraea's must export, in addition to what MetalOld exports, the 90 symbols Moraea's
wrapper adds (`exported_by.moraea_wrapper` and not `metalold` in the JSON) plus the 15 live symbols above. 44
classes are subclassed by importers (`subclassed`). For each of those, the wrapper must provide a real class (with
its metaclass), not only a symbol. Where an importer reads ivars (`ivar_offset`), that class's ivar offset symbols
are needed too.

## What this method misses

- **Selectors.** The table covers linkage only. A message that a newer framework sends to one of MetalOld's own
  objects (a device, queue, encoder) and that MetalOld does not implement is a separate gap.
  [patches/metalold-selectors](../../../patches/metalold-selectors/README.md) surveys and fills it.
- **Lookups by name.** `NSClassFromString`, `objc_getClass`, `dlsym` and soft-linking create no import.
  `ipsw dyld softlinks` over all 3,943 cache images found no soft-linked Metal symbol, but its detector recognizes
  only the SOFT_LINK macro patterns (it found soft links in 52 images), so other by-name lookups are not ruled out.
  That pass takes about 6 minutes and is not part of the generator.
- **Protocols** are not symbols. Each image carries its own copy of the protocols it uses, so linkage never fails
  on them. Conformance checks against MetalOld's classes are behaviour, not linkage.
- **Semantics.** A symbol being present does not mean it works. A stub class whose methods are missing, or a data
  constant with a different value, still links. The table says what must exist, not how it must behave.
- **ARM-only programs.** 16 on-disk programs that load Metal have no x86_64 slice (Finder, Preview, Music,
  Freeform, Books' BlissReader, iconservicesagent, MTLAssetUpgraderD and others; `arm64_only_not_counted`). They
  run under hao-runtime and are not counted. Their arm64e Metal imports add two symbols beyond the x86_64 table
  (`arm64_only_extra`): `MTLBufferLayoutDescriptor` and `__MTLSystemSupportsRemovableGPUs`. MetalOld exports both.
- **Code outside 26B131.** OCLP's own binaries (MTLCompiler 13.6, the HD 4000 driver bundles), hao-runtime's payload
  and this repo's fix dylibs also run on the target. They are not scanned.
- **Weak references** count as required. With a weak reference, a missing symbol resolves to NULL instead of failing
  the load, so the importer may run until it uses the symbol. No 26B131 reference to a symbol nobody provides is
  weak-only.
- **Missing symbols in the cache.** A cache dylib whose Metal import the on-disk replacement lacks does not fail to
  load the way an on-disk program does. dyld cannot rebind that use through the patch table, so the reference is
  left to dyld's missing-symbol handling. The table does not model which code paths reach it.
