# metal-adapter

Lets macOS 27.2.1's own x86_64 `Metal.framework` (383.1) drive the Intel HD 4000 (MacBookPro9,2) through OCLP's
legacy compilers and driver, instead of OCLP's `MetalOld` + our [metal-wrapper](../metal-wrapper/README.md). The
fallback, and what every image ships today, is still the wrapper.

**Status: laptop test only.** No recipe installs any of it. 27's Metal is loaded with `DYLD_FRAMEWORK_PATH` inside a
sandboxed test process; nothing on the system is changed. Compute is proven on the laptop (r2). Render aborted on the
laptop in r3 (27's reflection reader, reached through the image's `libmetal-framebuffer` hook); r4 fixes that and is
proven on the host replay as far as the host goes (pipeline state created, reflection identical to MetalOld's), and
waits for its laptop run.

| Piece | Source | What |
|---|---|---|
| Metal27 | (input) | 27.2.1's own x86_64 Metal, cut from the 26B131 Rosetta dyld cache with hao-tools' `cache-dylib`, under Apple's install name, plus `LC_REEXPORT_DYLIB` of the shim ([src/add_reexport.py](src/add_reexport.py)) |
| `libHaoMetalShim.dylib` | [src/shim.c](src/shim.c), [src/ivars.inc](src/ivars.inc), `layouts.inc` (generated at build) | the 44 ivar offsets OCLP's 11.7.10 HD 4000 driver (31) and 31001 MTLCompiler (13) bind from Metal and 27's Metal no longer exports; a `copyAndNullifyRefsInDescriptor` stub; the `isInternalBuild` flip; the driver's view of three private descriptor structs in MetalOld's layout (render) |
| compiler stand-in | [src/standin.c](src/standin.c) | `MTLCompiler.framework/Versions/{32023,32024}/MTLCompiler`, the in-process compiler 27's Metal `dlopen`s; routes every request to OCLP's 31001 and 3802 |
| test program | [src/test.m](src/test.m), [src/kernels.metal](src/kernels.metal), [src/render.metal](src/render.metal) | one compute kernel checked against a CPU reference, or one offscreen draw read back pixel by pixel |
| `q2log.dylib` | [src/q2log.c](src/q2log.c) | passive: echoes LLVM/MTLCompiler `os_log` faults to stderr, private strings included |
| runner | [run.sh](run.sh) | the laptop run: manifest check, one sandboxed process per case, summary |
| builder | [make.py](make.py), [inputs.json](inputs.json), [abi.py](abi.py) | the bundle; the host replay runtime; the descriptor layout table |
| host replay | [host-replay.sh](host-replay.sh), [host-control.sh](host-control.sh) | the bundle against the fake HD 4000; the same under MetalOld, with its requests captured |

## How it works

1. **In-process compiler.** 27's Metal compiles through `MTLCompilerService` (XPC) unless
   `MTLEnvVarAggregator::isInternalBuild(false)` is true and `MTL_MONOLITHIC_COMPILER=1`. The shim (with
   `HAO_METAL27_INTERNAL=1`) writes the function's cached answer and its guard, both in Metal27's `__DATA_DIRTY`,
   located from the function's own RIP-relative loads. It refuses (exit 70) unless the instruction pattern matches
   exactly once at +0x1a72d8, the only Metal loaded is the bundle's (by UUID), and `MetalOld` is not loaded. No
   code is patched.
2. **The stand-in.** 27's in-process connection `dlopen`s `MTLCompiler.framework/Versions/32023/MTLCompiler` and
   calls `MTLCodeGenServiceCreate/Destroy/SetPluginPath/BuildRequest`, the four entry points OCLP's 3802 also
   exports. `DYLD_FRAMEWORK_PATH` makes that the stand-in, which routes as MetalOld does:
   - plugin-bound type 1 (backend): 27's AIR → OCLP 31001 downgrade (a type-7 request in 13.2.1 framing, built from
     a template with 27's function name and module spliced in) → 27's own request with the downgraded module →
     OCLP 3802 + the Intel plugin. 3802 accepts 27's type-1 header as it is. A render pipeline is two such
     requests, fragment then vertex; nothing else differs from compute.
   - 3802's reply, in 27's reflection layout. The reply is a list of sections (u64 count; per section u64 type, u64
     size, data, padding to 8); type 2 is the stage's reflection (`MTLPSBIN`, version 0x10001), which 27's Metal
     parses only when the pipeline asks for reflection (`MTLPipelineOptionArgumentInfo`). For a vertex or compute
     function (Metal's input-stage reader) 3802 writes, after the arguments and the stage-input attributes, a second
     table (u32 end, u32 count, 36 bytes per entry) that MetalOld 306 reads or skips by its end offset. 27's reader
     (383) has no such table: it reads the next u32 and the trailing data where the table starts, then aborts because
     it did not end at the blob's end. The stand-in cuts that table out (and shrinks the section) when the blob's
     whole tail checks: an end offset pointing at the table, the table's end = its start + 8 + 36 × count, and the
     trailing length ending the blob exactly. Fragment blobs are the same for both readers and are left alone.
     `HAO_STANDIN_REFLECTION=0` turns it off (A/B).
   - plugin-less requests (13 frontend, 15 specialization, …) → OCLP 31001 as they are. Type 13 gets three 27
     options rewritten (`-fmetal-math-mode=fast`, `-fmetal-math-fp32-functions=fast`, `-std=metal4.1`) and, with
     `HAO_FE_MODULE_CACHE`, its module cache moved (the sandbox denies the user cache dir).
   - 31001's `libLLVM.dylib` links `libLLVM_helper.dylib` by a relative path. The stand-in loads the helper by its
     absolute path first, and retries the 31001 `dlopen` once with cwd `/` if that is not enough.
   - One request at a time (a recursive lock): 27's Metal compiles from several threads, and OCLP's services were
     written for `MTLCompilerService`, which serializes.
3. **Descriptor structs (render).** The driver asks a descriptor for its private struct (`-_descriptorPrivate`,
   `-descriptorPrivate`) and reads fields at MetalOld's offsets. Between Metal 306 and 383 three of the structs it
   reads changed: `MTLRenderPipelineDescriptorPrivate` (16 bytes inserted before `textureWriteRoundingMode`, the
   flag bitfield repacked: 0x100 is now the vertex function, not the vertex descriptor), its colour attachments'
   `MTLRenderPipelineAttachmentDescriptorPrivate` (one 64-bit bitfield, every field moved: pixel format from bit 36
   to 42), and `MTLTextureDescriptorPrivate` (`resolvedUsage`, `cpuCacheMode`, `storageMode` 24 bytes later). The
   sampler, render pass and render pass attachment structs only gained fields at the end and need nothing. The
   shim replaces those three methods: a caller inside a `…GraphicsMTLDriver.bundle/` image gets a MetalOld-layout
   copy, rebuilt from 27's struct on each call and kept alive with the descriptor; every other caller gets 27's own
   struct. Without it the first render pipeline aborts in
   `-[MTLIGAccelDevice vertexFunctionKeyWithRenderPipelineDescriptor:…]` (`-[_MTLFunctionInternal attributes]:
   unrecognized selector`: the driver took 27's vertex function for MetalOld's vertex descriptor).
   - [abi.py](abi.py) computes both layouts from each binary's ObjC metadata: a class's `_private` ivar carries the
     whole struct, field names included, in its type encoding. `make.py` runs it on the hash-checked MetalOld and
     Metal cut before compiling the shim and writes `OUT/work/layouts.inc`, one entry per moved field (bytes, or bits
     of a bitfield). The table carries Apple's private type encodings, so it is generated on the building machine
     and never committed (`.gitignore`). Bitfield runs are 32-bit units (27's getters read
     `logicOp` at bit 32, not 29); two flags grew from 1 to 2 bits and keep 27's getters' meaning
     (`blendingEnabled`: `== 1`; `alphaToCoverageEnabled`, `alphaToOneEnabled`: nonzero).
   - The shim checks 27's encoding of each `_private` against the table at load and leaves a class alone if it
     differs. `HAO_METAL27_LAYOUTS=0` turns the translation off (A/B).
   - Writes the driver makes into such a struct go to the copy and are lost on the next call. None was seen on the
     paths the tests take.
4. **Why Apple's install name.** On macOS 27 every x86_64 process already loads `/System/.../Metal` (Foundation →
   … → ImageIO → Metal). A second Metal under another name would duplicate every Metal class. The bundle's copy
   replaces it inside the test process only, so OCLP's driver and 31001 MTLCompiler (which link `/System/.../Metal`)
   load unmodified from their installed paths and bind the shim through Metal27's re-export.

## Inputs

Every input is read-only and checked against [inputs.json](inputs.json) before use; `make.py inputs` checks them
alone. Nothing Apple's is in this repository or built from anything but these. What the repository carries from
Apple's binaries is interface facts only: class, ivar and symbol names with numbers ([src/ivars.inc](src/ivars.inc),
the getter offsets in the tests), the `isInternalBuild` instruction pattern, and the type-7 request framing.
Tables copied out of Apple binaries (`layouts.inc`) are generated on the building machine.

| Input | Where | Pin |
|---|---|---|
| 26B131 x86_64 dyld cache | `System/Library/dyld` of the 26B131 IPSW's Rosetta cryptex (input row `ipsw_27_0`), mounted read-only; `--cache DIR` | sha256 of the 7 cache files and `.map`; the unsigned Metal cut `dbb5e8e8…` |
| hao-tools `tools/cache-dylib` | a hao-tools checkout next to this repository, or `CACHE_DYLIB=DIR` | r6, commit `7e5dd2a6`, sha256 of `cache_dylib.py`, `check.py`, `dsc.py` |
| type-7 template | built from the framing in `inputs.json`; or `--template FILE`, the MetalOld capture r2 shipped (sha256 `ce4640d4…`) | see below |
| OCLP's compilers, HD 4000 driver, plugin chain | on the laptop, at their installed paths, only read | `run.sh` records their sha256 in `system.txt` |
| OCLP's `MetalOld.dylib` | OCLP hardware artifact, `payload/13.2.1-25/System/Library/Frameworks/Metal.framework/Versions/A/MetalOld.dylib`; `--metalold FILE` | sha256 `90a1ff7a…`; with the Metal cut, `abi.py` generates `OUT/work/layouts.inc` from it (never committed) |
| a cache/shaders replay runtime | host replay only (`make.py host-runtime --from RT`) | the runtime's own `sources.json` |

**The template.** r2 shipped MetalOld's captured type-7 request for `add1` (3,504 bytes). The stand-in only uses its
framing: it replaces the name and the module, and shifts the five header words that point at the module end. The
default template is that framing with an 8-byte placeholder module (248 bytes, `inputs.json`), so no captured AIR
is needed. Rebuilt from laptop run 2's own type-1 requests, the stand-in sends byte-identical type-7 requests with
either template (tests).

## Building the laptop bundle

On the Apple-silicon host, with Xcode (macOS 27 SDK) and the cache mounted:

```sh
CACHE_DYLIB=PATH/hao-tools/tools/cache-dylib \
python3.14 -I tools/metal-adapter/make.py bundle OUT --cache ROSETTA_CRYPTEX/System/Library/dyld \
    --metalold OCLP_ARTIFACT/payload/13.2.1-25/System/Library/Frameworks/Metal.framework/Versions/A/MetalOld.dylib --tar
# OUT/metal27-adapter-test/, OUT/metal27-adapter-test.tgz (+ .sha256), OUT/work/ (cut, cache-dylib report and check,
# layouts.inc, AIR, host/metal-adapter-test-r38 for host-control.sh)
python3.14 -I tools/metal-adapter/abi.py show OCLP_METALOLD OUT/work/Metal27.cut MTLRenderPipelineDescriptorPrivate
# one struct's two layouts, field by field
```

`make.py` cuts Metal27 and refuses unless the cut matches the pin and `check.py` reports 0 errors, generates
`layouts.inc` with `abi.py`, builds the shim with Metal27's UUID, the stand-in at both paths, the test program and
`q2log`, builds the kernels and render shaders (27.2 default target and `-mmacos-version-min=11.0`), signs every
x86_64 binary ad hoc, then makes the framework symlinks: `fw/Metal.framework/Versions/A/{Resources,XPCServices}`
point at the running system's own `/System/Library/Frameworks/Metal.framework/Versions/A/…` (without them 27's
Metal finds no `MTLCompilerService`). `MANIFEST.sha256` lists every binary, the metallibs, the template and `run.sh`.

**Reproducing r2.** `make.py bundle OUT --cache … --template R2_TEMPLATE` at the commit that added this tool gave
r2's `MANIFEST.sha256` exactly, except `bin/q2log.dylib`: r2 built it without an install name, so it carried the
scratch build path; this builder names it `@loader_path/q2log.dylib`. It is loaded by path through
`DYLD_INSERT_LIBRARIES`, so the install name is never used. Metal27 (`e0d38ca0…`, UUID `0A0F6749…`), the shim,
both stand-ins, the test program and both metallibs were byte-identical. The host replay of that rebuild gave 6/6
COMPILE-OK with request dumps byte-identical to laptop run 2's.

## On the laptop

Copy only the tarball and unpack it in the test user's home; `scp -r` or Finder would break the symlinks into
`/System`.

```sh
scp OUT/metal27-adapter-test.tgz LAPTOP:
# on the laptop, as the logged-in user (no sudo)
tar -xzf ~/metal27-adapter-test.tgz -C ~ && cd ~/metal27-adapter-test && ./run.sh
```

`run.sh` checks `MANIFEST.sha256` and refuses on any mismatch, records the OCLP files it will use (`system.txt`),
then runs each case as its own process under `sandbox-exec`, with every file write denied except into
`results/<timestamp>/`, and a 120 s watchdog; after two timeouts it skips the rest. `DYLD_*` go through
`/usr/bin/env`'s argv (SIP-protected binaries drop them from their own environment). Afterwards
`rm -rf ~/metal27-adapter-test*` removes everything; nothing outside the results folder is written, which
`writes-check.txt` shows for Metal's user cache dir. No root. 28 processes, about 2–4 minutes.

On the image, OCLP's HD 4000 driver carries two weak dylibs (`/usr/lib/libmetal-framebuffer.dylib` from
[golden-gate-metal-framebuffer](../../patches/golden-gate-metal-framebuffer/README.md), `libmetalold-selectors.dylib`
from [metalold-selectors](../../patches/metalold-selectors/README.md)); they load into the adapter's process with the
driver. The framebuffer dylib hooks the device's render pipeline factories and asks every render pipeline for
argument reflection, so on the laptop every render case goes through 27's reflection reader, with or without
`HAO_REFLECTION`. `system.txt` records both dylibs and whether the driver names them (by `grep`: `otool`
would ask to install the developer tools on the laptop).

| run | Metal in the process | compile path | device |
|---|---|---|---|
| `control-*` | system: OCLP wrapper + MetalOld | XPC `MTLCompilerService` | `MTLCreateSystemDefaultDevice` |
| `adapter-*` | bundle Metal27 + shim | in process: stand-in → 31001 downgrade → 3802 + Intel plugin | default, then by hand if nil |
| `adapter-manual-*` | same | same | by hand: driver bundle + `-initWithAcceleratorPort:` |
| `adapter-nolayouts-render-color` | same, `HAO_METAL27_LAYOUTS=0` | same | default |
| `adapter-noreflection-render-color` | same | same, `HAO_STANDIN_REFLECTION=0` (3802's reflection unchanged) | default |
| `control-reflect-*`, `adapter-reflect-*` | as `control-*`, `adapter-*`, with `HAO_REFLECTION=1` | as `control-*`, `adapter-*` | as `control-*`, `adapter-*` |
| `xpc-*` | bundle Metal27 + shim, no flip | the system's x86_64 `MTLCompilerService` | as `adapter` |

`HAO_REFLECTION=1` makes the test program create its pipeline with `MTLPipelineOptionArgumentInfo` and print every
argument of the reflection (`[test] reflection STAGE name=… type=… index=…`, buffer and texture details).

Cases (`KERNEL` argument of the test program):

| case | what | checked |
|---|---|---|
| `add1`, `saxpy_long_name` | compute kernels from `lib/kernels-{27,11}.metallib` | add1 exact over 1024 floats; saxpy within 2e-3 relative |
| `source` | compute kernel compiled from MSL at run time | exact over 1024 floats |
| `render-color` | `tri_vertex` (positions from `setVertexBytes`) + `solid_fragment` (colour from `setFragmentBytes`) into a 64×64 RGBA8 managed texture cleared to blue | the lower-left half (x < y) is (255, 51, 153, 255), the rest (0, 0, 255, 255), ±1 per channel; the diagonal is not checked |
| `render-texture` | `tri_vertex` + `tex_fragment`: a 4×4 texture (`replaceRegion`), nearest sampler (`setFragmentSamplerState`) | every pixel equals texel (x/16, y/16) |
| `render-source` | as `render-color`, shaders compiled from MSL at run time (type 13, then two type-1 requests) | as `render-color` |

Render read-back: a blit `synchronizeResource:` of the managed target, then `getBytes:`. On the host's own GPU
(M4 Max), through the system Metal and through Metal27 + shim (27's XPC compiler), all three render cases PASS with
maxdiff 0, so the expected values are right.

`summary.txt` has one line per run, `RESULT kernel=K status=S …` then `TEARDOWN …`, two verdicts (compute,
render) and a reflection line (`adapter-reflect-*` PASS with the same argument reflection as `control-reflect-*`).

| status | meaning |
|---|---|
| PASS | ran on the GPU; every output matched |
| FAIL | ran, wrong values (`first_bad`, `got`, `want`, `maxerr` / `maxdiff`) |
| NO-DEVICE / NO-LIBRARY / NO-PIPELINE | that step returned nil; the Metal error text follows |
| GPU-ERROR | the command buffer did not complete, or a texture, sampler or queue could not be made |
| CRASH signal=N / TIMEOUT | caught in the test process; backtrace in the log |
| SETUP-ERROR | the wrong Metal was loaded for the run (e.g. `DYLD_*` lost) |
| COMPILE-OK | host replay only (`HAO_COMPILE_ONLY=1`): the pipeline was created, nothing ran |

Log prefixes in `<run>/log.txt`: `[shim]` (Metal image and UUID, ivar offsets, the three layouts, the flip),
`[standin]` (every request and reply; raw dumps in `<run>/dump/`), `[q2log]`, `[test]` (for render also two
pixels: `pixel(0,63)`, `pixel(63,0)`).

## Host replay

The host (Apple silicon, x86_64 under Rosetta) has no HD 4000. cache/shaders' replay interposer (`ip.dylib`) fakes
its IOAccelerator, so OCLP's driver creates an `MTLIGAccelDevice` and the whole compile path runs, up to the
pipeline state. The fake device makes no textures, so nothing past that (draws, read-back) can run on the host.
The adapter needs a runtime whose OCLP files link `/System/.../Metal` (so Metal27 serves them), not the runtime's
own MetalOld:

```sh
python3.14 -I tools/metal-adapter/make.py host-runtime HOSTRT --from REPLAY_RUNTIME [--laptop-paths]
tools/metal-adapter/host-replay.sh HOSTRT OUT/metal27-adapter-test RUNS NAME render-texture render-27.metallib
tools/metal-adapter/host-control.sh REPLAY_RUNTIME OUT/metal27-adapter-test OUT/work/host/metal-adapter-test-r38 \
    RUNS NAME render-texture render-27.metallib
```

`REPLAY_RUNTIME` is a runtime made by `cache/shaders/lib/prepare.py` (r38 was used). `--laptop-paths` restores OCLP
31001 libLLVM's relative helper path, as installed on the laptop. `host-control.sh` runs the same case under the
runtime's MetalOld with the stand-in in capture mode (`HAO_STANDIN_ROUTE=3802`), so MetalOld's 3802 requests and
replies land next to the adapter's for a byte comparison.

## Results so far

**Host replay, compute (2026-11-12, Q2 and r2).** 27.2's Metal compiled `add1`, `saxpy_long_name` and an MSL
source kernel to `MTLIGComputePipelineState` (maxTotalThreadsPerThreadgroup 1024) for metallibs at the 27.2 and
11.0 targets: 6/6 COMPILE-OK, the same as MetalOld. The downgraded module is identical to MetalOld's, and 3802's
reply too except one statistics byte that also differs between two MetalOld runs. A kernel with threadgroup
barriers crashes in the Intel backend exactly as under MetalOld (the known AIR repair, not the adapter).

**Laptop run 1 (r1, 2026-11-12 16:04): aborted.** Control 5/5 PASS. The adapter made the real HD 4000 device
(`MTLIGAccelDevice`, llvmVersion 3802), loaded the library, reached the stand-in and loaded OCLP 3802 and the
Intel plugin. Then every adapter run aborted (signal 6) in the stand-in: `dlopen` of 31001 failed with
`Library not loaded: System/Library/PrivateFrameworks/GPUCompiler.framework/Versions/31001/Libraries/libLLVM_helper.dylib`.
OCLP's 31001 `libLLVM.dylib` (payload 13.2.1-25) links its helper by a relative path, which dyld resolves against
the current directory: `/` in `MTLCompilerService`, the bundle folder in the test. The host never hit it because
its runtime had the helper relinked to `@loader_path`. The requests were not the cause: the laptop's dumps were
byte-identical to the host's.

**Laptop run 2 (r2, 2026-11-12 16:42): PASS (compute).** r2 preloads the helper by its absolute path and moves the
frontend module cache. Results:

| run | result |
|---|---|
| control (5) | PASS, `Intel HD Graphics 4000`, threadExecutionWidth 32, maxTotalThreadsPerThreadgroup 1024 |
| `adapter-add1`, `-saxpy`, `-source`, `-11-add1`, `-11-saxpy` | PASS, same device and values (add1 exact over 1024 floats, saxpy maxerr 5.34e-07, same as control), teardown OK |
| `adapter-manual-*` | NO-DEVICE (expected: the IOAccelerator has `MetalPluginName` but no `MetalPluginClassName`) |
| `xpc-*` | NO-PIPELINE `XPC_ERROR_CONNECTION_INTERRUPTED`: the image's `MTLCompilerService` is OCLP's 13.2.1 build, whose `CompilerPluginInterface(int)` knows only 3802 and 31001; for 27's 32023 it calls address 0 |

Verdict: macOS 27's own Metal ran both kernels on the HD 4000 with correct results, through 27.2.1's
`MTLCreateSystemDefaultDevice`, in process.

**Host replay, render (r3, 2026-11-12).** All six render cases (`render-color`, `render-texture`, `render-source`
for metallibs at the 27.2 and 11.0 targets) give `MTLIGRenderPipelineState` through 27's Metal, and the same under
MetalOld. Each stage's downgraded module is identical to MetalOld's, and 3802's replies are the same size and
differ only in the same few bytes that also differ between two MetalOld runs (a 4-byte value near the start and
two 5-byte values near the end, process-specific). Without the descriptor translation (`HAO_METAL27_LAYOUTS=0`)
the first pipeline aborts in the driver's vertex function key. The compute cases still give 6/6 COMPILE-OK.

**Laptop run 3 (r3, 2026-11-12 17:55): compute PASS again, render aborted.** Control render 3/3 PASS (maxdiff 0).
Every adapter render case (27.2 and 11.0 targets, and `nolayouts`) compiled both stages through 31001 and 3802,
then aborted (signal 6) inside 27's Metal, called from `libmetal-framebuffer.dylib`'s pipeline hook. The hook is
not the cause: it asks for `MTLPipelineOptionArgumentInfo` reflection, and 27's input-stage reflection reader
aborts on 3802's vertex reflection (the second table, see How it works). The host replay reproduces it with the
image's two driver dylibs added to its driver (the abort, the same stack), and without them with `HAO_REFLECTION=1`
alone, for compute too: every adapter pipeline that asks for reflection aborted, render or compute.

**Host replay, reflection (r4, 2026-11-12).** With the stand-in's cut: all ten cases (`add1`, `saxpy_long_name`,
`source`, `render-color`, `render-texture`, `render-source`; 27.2 and 11.0 targets) give their pipeline with
`HAO_REFLECTION=1`, and every argument's reflection (name, type, index, active, access, buffer alignment, size and
data type, texture type and data type, array length) is identical to MetalOld's for the same case
(`host-control.sh`). With the image's framebuffer and selector dylibs on the replay driver, all render and compute
cases give their pipeline; `HAO_STANDIN_REFLECTION=0` aborts as on the laptop. Fragment replies are not changed;
vertex replies lose 44 bytes (one table entry), compute replies 8 (an empty table).

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/metal-adapter/tests
METAL_ADAPTER_METALOLD=OCLP_METALOLD METAL_ADAPTER_METAL27=OUT/work/Metal27.cut \
METAL_ADAPTER_BUNDLE=OUT/metal27-adapter-test \
METAL_ADAPTER_R2_RESULTS=LAPTOP_RESULTS/20261112-164152 METAL_ADAPTER_TEMPLATE=R2_TEMPLATE \
METAL_ADAPTER_R3_RESULTS=LAPTOP_RESULTS/20261112-175530 \
    python3.14 -I -B -m unittest discover -s tools/metal-adapter/tests
```

Without Apple binaries (Xcode needed for the compiled parts): inputs and their refusal; the template's framing;
`add_reexport` on a dylib built here; `run.sh`'s syntax; the stand-in's type-7 build, module splice, refusal of an
unknown layout and type-13 rewriting, on synthetic requests, built natively from `src/standin.c`; its reflection cut
on synthetic replies (vertex and compute cut; fragment, newer versions, a wrong table count and a reply that is not
a section list left alone); `abi.py`'s
parser, layout (unions, objects, 32-bit bitfield units), field mapping and table output on encodings written in
the test.

With the variables: `METAL_ADAPTER_METALOLD` and `METAL_ADAPTER_METAL27` (checked against `inputs.json`) generate
`layouts.inc` in a temporary folder, recompute it from its own encodings, and check its bit positions against what
both binaries' getters read and the driver's read offsets landing on the right fields.
`METAL_ADAPTER_BUNDLE` runs a Rosetta probe that makes texture, render pipeline and colour
attachment descriptors through 27's public API and reads their private structs through the shim, from a dylib in a
folder named like a driver bundle, checking every value at MetalOld's offset (and that ordinary callers still get
27's struct). `METAL_ADAPTER_R2_RESULTS` makes the stand-in rebuild laptop run 2's own type-7 and spliced type-1
requests from its type-1 requests and 31001 replies, byte for byte, with the built template (and the captured one,
with `METAL_ADAPTER_TEMPLATE`). `METAL_ADAPTER_R3_RESULTS` runs the reflection cut on laptop run 3's own 3802
render replies: each vertex reply loses only its table, each fragment reply is left alone.

Host replay of the reflection matrix: each case with `HAO_REFLECTION=1` through `host-control.sh` and
`host-replay.sh`, then the `[test] reflection` lines of the two logs compared. To reproduce the image's driver, add
the two weak dylibs to a copy of the host runtime's driver with
`patches/golden-gate-metal-framebuffer/src/add_weak_dylib.py` (the `metalold_selectors` and `metal_framebuffer`
input rows' dylibs, by their absolute path) and sign it ad hoc.
