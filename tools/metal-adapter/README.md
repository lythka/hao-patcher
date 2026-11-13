# metal-adapter

Lets macOS 27.2.1's own x86_64 `Metal.framework` (383.1) drive the Intel HD 4000 (MacBookPro9,2) through OCLP's
legacy compilers, instead of OCLP's `MetalOld` + our [metal-wrapper](../metal-wrapper/README.md). The fallback, and
what every image ships today, is still the wrapper.

**Status: laptop test only.** No recipe installs any of it. 27's Metal is loaded with `DYLD_FRAMEWORK_PATH` inside a
sandboxed test process; nothing on the system is changed.

| Piece | Source | What |
|---|---|---|
| Metal27 | (input) | 27.2.1's own x86_64 Metal, cut from the 26B131 Rosetta dyld cache with hao-tools' `cache-dylib`, under Apple's install name, plus `LC_REEXPORT_DYLIB` of the shim ([src/add_reexport.py](src/add_reexport.py)) |
| `libHaoMetalShim.dylib` | [src/shim.c](src/shim.c), [src/ivars.inc](src/ivars.inc) | the 44 ivar offsets OCLP's 11.7.10 HD 4000 driver (31) and 31001 MTLCompiler (13) bind from Metal and 27's Metal no longer exports, filled from the ObjC runtime; a `copyAndNullifyRefsInDescriptor` stub; the `isInternalBuild` flip (below) |
| compiler stand-in | [src/standin.c](src/standin.c) | `MTLCompiler.framework/Versions/{32023,32024}/MTLCompiler`, the in-process compiler 27's Metal `dlopen`s; routes every request to OCLP's 31001 and 3802 |
| test program | [src/test.m](src/test.m), [src/kernels.metal](src/kernels.metal) | one compute kernel on the GPU, checked against a CPU reference |
| `q2log.dylib` | [src/q2log.c](src/q2log.c) | passive: echoes LLVM/MTLCompiler `os_log` faults to stderr, private strings included |
| runner | [run.sh](run.sh) | the laptop run: manifest check, one sandboxed process per case, summary |
| builder | [make.py](make.py), [inputs.json](inputs.json) | the bundle, and the host replay runtime |

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
     OCLP 3802 + the Intel plugin. 3802 accepts 27's type-1 header as it is.
   - plugin-less requests (13 frontend, 15 specialization, …) → OCLP 31001 as they are. Type 13 gets three 27
     options rewritten (`-fmetal-math-mode=fast`, `-fmetal-math-fp32-functions=fast`, `-std=metal4.1`) and, with
     `HAO_FE_MODULE_CACHE`, its module cache moved (the sandbox denies the user cache dir).
   - 31001's `libLLVM.dylib` links `libLLVM_helper.dylib` by a relative path. The stand-in loads the helper by its
     absolute path first, and retries the 31001 `dlopen` once with cwd `/` if that is not enough.
3. **Why Apple's install name.** On macOS 27 every x86_64 process already loads `/System/.../Metal` (Foundation →
   … → ImageIO → Metal). A second Metal under another name would duplicate every Metal class. The bundle's copy
   replaces it inside the test process only, so OCLP's driver and 31001 MTLCompiler (which link `/System/.../Metal`)
   load unmodified from their installed paths and bind the shim through Metal27's re-export.

## Inputs

Every input is read-only and checked against [inputs.json](inputs.json) before use; `make.py inputs` checks them
alone. Nothing Apple's is in this repository or built from anything but these.

| Input | Where | Pin |
|---|---|---|
| 26B131 x86_64 dyld cache | `System/Library/dyld` of the 26B131 IPSW's Rosetta cryptex (input row `ipsw_27_0`), mounted read-only; `--cache DIR` | sha256 of the 7 cache files and `.map`; the unsigned Metal cut `dbb5e8e8…` |
| hao-tools `tools/cache-dylib` | a hao-tools checkout next to this repository, or `CACHE_DYLIB=DIR` | r6, commit `7e5dd2a6`, sha256 of `cache_dylib.py`, `check.py`, `dsc.py` |
| type-7 template | built from the framing in `inputs.json`; or `--template FILE`, the MetalOld capture r2 shipped (sha256 `ce4640d4…`) | see below |
| OCLP's compilers, HD 4000 driver, plugin chain | on the laptop, at their installed paths, only read | `run.sh` records their sha256 in `system.txt` |
| a cache/shaders replay runtime | host replay only (`make.py host-runtime --from RT`) | the runtime's own `sources.json` |

**The template.** r2 shipped MetalOld's captured type-7 request for `add1` (3,504 bytes). The stand-in only uses its
framing: it replaces the name and the module, and shifts the five header words that point at the module end. The
default template is that framing with an 8-byte placeholder module (248 bytes, `inputs.json`), so no captured AIR
is needed. The requests sent on the wire are byte-identical either way (tests).

## Building the laptop bundle

On the Apple-silicon host, with Xcode (macOS 27 SDK) and the cache mounted:

```sh
CACHE_DYLIB=PATH/hao-tools/tools/cache-dylib \
python3.14 -I tools/metal-adapter/make.py bundle OUT --cache ROSETTA_CRYPTEX/System/Library/dyld --tar
# OUT/metal27-adapter-test/, OUT/metal27-adapter-test.tgz (+ .sha256), OUT/work/ (cut, cache-dylib report and check)
```

`make.py` cuts Metal27 and refuses unless the cut matches the pin and `check.py` reports 0 errors, builds the
shim with Metal27's UUID, the stand-in at both paths, the test program and `q2log`, builds the kernels (27.2
default target and `-mmacos-version-min=11.0`), signs every x86_64 binary ad hoc, then makes the framework
symlinks: `fw/Metal.framework/Versions/A/{Resources,XPCServices}` point at the running system's own
`/System/Library/Frameworks/Metal.framework/Versions/A/…` (without them 27's Metal finds no `MTLCompilerService`).
`MANIFEST.sha256` lists every binary, the metallibs, the template and `run.sh`.

**Reproducing r2.** `make.py bundle OUT --cache … --template R2_TEMPLATE` from the commit that added this tool gave
r2's `MANIFEST.sha256` exactly, except `bin/q2log.dylib`: r2 built it without an install name, so it carried the
scratch build path; this builder names it `@loader_path/q2log.dylib`. It is loaded by path through
`DYLD_INSERT_LIBRARIES`, so the install name is never used. Metal27 (`e0d38ca0…`, UUID `0A0F6749…`), the shim,
both stand-ins, the test program and both metallibs were byte-identical.

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
`writes-check.txt` shows for Metal's user cache dir. No root.

| run | Metal in the process | compile path | device |
|---|---|---|---|
| `control-*` | system: OCLP wrapper + MetalOld | XPC `MTLCompilerService` | `MTLCreateSystemDefaultDevice` |
| `adapter-*` | bundle Metal27 + shim | in process: stand-in → 31001 downgrade → 3802 + Intel plugin | default, then by hand if nil |
| `adapter-manual-*` | same | same | by hand: driver bundle + `-initWithAcceleratorPort:` |
| `xpc-*` | bundle Metal27 + shim, no flip | the system's x86_64 `MTLCompilerService` | as `adapter` |

`summary.txt` has one line per run, `RESULT kernel=K status=S …` then `TEARDOWN …`, and a verdict.

| status | meaning |
|---|---|
| PASS | ran on the GPU; every output matched |
| FAIL | ran, wrong values (`first_bad`, `got`, `want`, `maxerr`) |
| NO-DEVICE / NO-LIBRARY / NO-PIPELINE | that step returned nil; the Metal error text follows |
| GPU-ERROR | the command buffer did not complete |
| CRASH signal=N / TIMEOUT | caught in the test process; backtrace in the log |
| SETUP-ERROR | the wrong Metal was loaded for the run (e.g. `DYLD_*` lost) |
| COMPILE-OK | host replay only (`HAO_COMPILE_ONLY=1`): the pipeline was created, nothing ran |

Log prefixes in `<run>/log.txt`: `[shim]` (Metal image and UUID, ivar offsets, the flip), `[standin]` (every
request and reply; raw dumps in `<run>/dump/`), `[q2log]`, `[test]`.

## Host replay

The host (Apple silicon, x86_64 under Rosetta) has no HD 4000. cache/shaders' replay interposer (`ip.dylib`) fakes
its IOAccelerator, so OCLP's driver creates an `MTLIGAccelDevice` and the whole compile path runs; nothing executes
on a GPU. The adapter needs a runtime whose OCLP files link `/System/.../Metal` (so Metal27 serves them), not the
runtime's own MetalOld:

```sh
python3.14 -I tools/metal-adapter/make.py host-runtime HOSTRT --from REPLAY_RUNTIME [--laptop-paths]
```

`REPLAY_RUNTIME` is a runtime made by `cache/shaders/lib/prepare.py` (r38 was used). `--laptop-paths` restores OCLP
31001 libLLVM's relative helper path, as installed on the laptop. Then run the bundle's test program with the
replay environment ([host-replay.sh](host-replay.sh)); it prints the same `RESULT` lines, with `COMPILE-OK`.

## Results so far

**Host replay (2026-11-12, Q2).** 27.2's Metal compiled `add1`, `saxpy_long_name` and an MSL source kernel to
`MTLIGComputePipelineState` (maxTotalThreadsPerThreadgroup 1024) for metallibs at the 27.2 and 11.0 targets: 6/6
COMPILE-OK, the same as MetalOld. The downgraded module is identical to MetalOld's, and 3802's reply too except
one statistics byte that also differs between two MetalOld runs. A kernel with threadgroup barriers crashes in the
Intel backend exactly as under MetalOld (the known AIR repair, not the adapter).

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

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/metal-adapter/tests
```

The tests need no Apple binary: inputs and their refusal, the template's framing, `add_reexport` on a dylib built
here, and `run.sh`'s syntax.
