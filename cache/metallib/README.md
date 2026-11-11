# cache/metallib: Metal shader repairs for the HD 4000

Produces the shader inputs that let OCLP's legacy Intel Metal stack (MetalOld, the 31001 AIR downgrader and the
MTLCompiler 3802 backend) compile the Golden Gate (macOS 27) system shader libraries on the HD 4000:

- **Repaired metallibs**: Apple's own libraries, rewritten module by module so the old compiler accepts them. Every
  exported Apple function is kept. Input row `metallib_repairs_golden_gate`.
- **Extended Intel shader runtime**: OCLP's `runtime.igil64.split` plus the SIMD/quad helpers 27.0 shaders call, and
  `iCBE.dylib` with its active-channel-mask emitter repaired. Input row `metal_quad_runtime_golden_gate`.
- **Compiler-entry translation** (opt-in, not installed in the image): dylibs that apply the same repairs to shaders
  compiled at run time, for private test processes.

Both rows are installed by [`golden-gate-metal-libraries`](../../patches/golden-gate-metal-libraries/README.md),
which also explains why each repair is needed. [`tools/metal-fixes`](../../tools/metal-fixes/README.md) wraps the
commands below. Apple binaries are inputs only; nothing from them is committed.

## Usage

Repaired libraries, from the read-only mounted Golden Gate System (the `golden_gate_userspace` filesystem):

```sh
python3.14 -I cache/metallib/make SYSTEM NEW_OUTPUT --work NEW_WORK \
  --paths cache/metallib/profiles/metallib-repairs-golden-gate.txt
```

`profiles/hello-golden-gate.txt` selects the first-boot hello library (row `hello_metallib_golden_gate`) the same way.

`NEW_OUTPUT` is System-rooted and holds `SHA256SUMS`, `SOURCES` (the sha256 of SYSTEM's own copy of each library,
which the output replaces) and `build.json` (revision, commit, SYSTEM's `ProductBuildVersion` as `system_build`,
every input's sha256 and recipe). A listed library missing from SYSTEM stops the build (`MISSING/unsupported`). `--overlay DIR` (repeatable) takes a library from an existing System-rooted artifact instead of `SYSTEM`; the
last overlay holding the path wins. `--air-lowerer DIR` names a checkout of the
[MetallibSupportPkg](https://github.com/dortania/MetallibSupportPkg) fork with AIR 2.8 support, needed only for the
`lower` step.

Extended runtime, from OCLP's original split runtime, its 3802 `libLLVM.dylib` and the original `iCBE.dylib`:

```sh
python3.14 -I cache/metallib/make runtime-build ORIGINAL_SPLIT NEW_OUTPUT \
  --llvm LEGACY_LLVM --compiler ORIGINAL_ICBE
```

Without `--compiler` only the quad-shuffle helpers are added: the collective helpers give wrong results with the
unrepaired mask emitter. Every original runtime block is preserved and duplicate definitions refuse. The same
inputs give identical runtime bytes.

### Profile format

A profile lists one library per line as `RECIPE PATH` (PATH relative to the System
root). RECIPE is one or more steps joined by `+`, at most one of them a rewrite:

| Step | What runs |
|---|---|
| `golden-gate` | The compatibility suite plus the synchronization ABI repair, assembled with `metal-as` so repaired intrinsic signatures survive. Used for every Golden Gate library. |
| `rewrite` | The compatibility suite (`lib/compatibility.py`) for Tahoe (AIR 2.8) libraries. |
| `half-vector` | Only the half-vector integer pass (`lib/mkfix_half_vec_int.py`). |
| `reads` | Only the sampler-form read repair (`lib/sampler_reads.py`). |
| `lower` | AIR 2.8 / Metal 4.0 to AIR 2.5 / Metal 3.0 with the MetallibSupportPkg fork, before any rewrite. |

## How it works

`lib/build_metallib.py` disassembles each distinct AIR module, runs the passes, reassembles it and splices it back
into the original MTLB container (`lib/mtlb.py`), keeping container metadata, HDYN/RLST/ILST payloads and UUIDs.
Modules shared by many exports are assembled whole, and their reachable metadata graph is compared with the
original. `lib/compatibility.py` refuses output that still contains a known unsupported pattern.

| Pass | Fixes |
|---|---|
| `legacy_sync.py` | 27.0 three-argument barriers and atomics with an extra memory-flags operand, rewritten to the legacy runtime signatures. Only `seq_cst` barriers and relaxed atomics (including compare-exchange) convert; anything else refuses. |
| `mkfix_half_vec_int.py` | Integer ops on `bitcast <N x half> to <N x i16>` (N >= 3), scalarized to i16 lanes. |
| `null_compare.py` | Device-pointer comparisons with null, which crash legalization; validity is threaded as an integer. |
| `half2_load.py`, `private_half2.py`, `inline_private.py` | Dynamically indexed private `<2 x half>` loads. |
| `read_sampler.py`, `texture_coords.py`, `sampler_reads.py` | Depth sampler-form reads and modules mixing 16- and 32-bit coordinate overloads, which the downgrader cannot lower. |
| `winograd_matrix.py`, `winograd_offsets.py` | Apple GPU matrix instructions in MPS Winograd kernels, lowered to ordered FMAs over the existing shared tiles; bounded dynamic sample offsets become constant-offset selections. |
| `collective_runtime.py`, `quad_runtime.py`, `compiler_mask.py` | Runtime helpers (SIMD/quad reductions, scans, shuffles, XOR, broadcast, ballot) built from Intel lane-ID and shuffle intrinsics; the iCBE mask-emitter repair. |
| `block_reads.py` | Diagnostic only: texture block reads expanded to sampler reads. No profile uses it; native results differ from Apple's. |

The helper bodies in `quad-shuffle/`, `shader-runtime-helpers/` and `shader-simd-reductions/` are hand-written LLVM IR
with CPU semantic tests. Helper semantics follow the
[Metal Shading Language specification](https://developer.apple.com/metal/Metal-Shading-Language-Specification.pdf),
section 6.10.3; the mask-emitter encoding follows Intel's
[command enumerations](https://www.x.org/docs/intel/KBL/intel-gfx-prm-osrc-kbl-vol02b-commandreference-enumerations.pdf).

### Compiler-entry translation

`lib/runtime/` builds three dylibs around the 3802 compiler plugin entry. `compat.dylib` clones each module, applies
the synchronization, private-pointer, null-compare, half-vector and block-read repairs, verifies the clone and passes
it on; a refused clone leaves the caller's module unchanged. `gpu-client.dylib` supplies per-axis sampler modes to the
driver in the GPU client process, and `sampler-query.dylib` answers the matching compiler-side query. Each checks the
UUIDs of the compiler, LLVM, IGIL or driver image it patches.

```sh
python3.14 -I cache/metallib/make translation-build NEW_OUTPUT [--llvm LEGACY_3802_LIBLLVM]
python3.14 -I cache/metallib/make translation-package NEW_BUNDLE \
  --runtime REPLAY_RUNTIME --payload HELPER_RUNTIME --libraries REPAIRED_LIBRARIES
python3.14 -I cache/metallib/make translation-environment NEW_BUNDLE \
  --role compiler|gpu-client --cache PRIVATE_CACHE_ROOT [--reviewed-mps-dispatch]
```

The environment command prints JSON for a clean child process environment. Block-read translation is enabled with
`COMPAT_BLOCK_READS=per-axis-mip0` and `COMPAT_CLIENT_ABI=axis-st-v1`; the MPS SIFT logical-SIMD32 pass with
`COMPAT_LOGICAL32=mps-sift-32x1x1`, which asserts a 32x1x1 dispatch rather than enforcing it. Everything is off by
default. Use a fresh private shader cache whenever a translation changes.

### Replay runtime

Host tests and controls compile shaders through a Rosetta replay runtime: copies of OCLP's MetalOld, the 3802/31001
compilers and the HD 4000 driver, prepared from an OCLP-patched System by `cache/shaders/lib/prepare.py` with
`cache/shaders/profiles/ivybridge.json` and driven by `cache/shaders/lib/harness.py` (sources in `cache/shaders/src`).
Nothing on the source System is modified.

## Controls

These commands build Apple GPU controls on the host and, where noted, fixtures to run on the HD 4000; each writes
`build.json` with source, toolchain and input hashes. Native runs print the library name before each JSON result.

| Command | Checks |
|---|---|
| `make winograd-check OUT` | Winograd matrix and sample-offset lowering against Apple's operations. |
| `make collective-check OUT` | Collective helpers against Apple builtins; `intel/` fixtures for native runs, verified with `collectives/verify.py`. |
| `make quad-check OUT`, `make quad-verify --data --outputs --log --report` | Quad shuffle/XOR/broadcast helpers, bit for bit. |
| `make block-read-check OUT`, `make block-read-verify ...` | The diagnostic block-read expansion. |
| `make translation-native-runtime OUT --runtime --payload` | A private native compiler copy for translation controls. |
| `make translation-block-check OUT`, `make translation-block-verify ...` | Per-axis block-read translation against Apple output. |
| `make mps-sift {build,reference,native,inputs,compare,width8,logical32,compile}` | MPSImageSIFT multifetch replay on recorded dispatches (`mps-sift/`). |

On Apple silicon hosts every x86_64 binary is ad-hoc signed as it is built: unsigned ones hang in exec.

## Verify

```sh
/opt/homebrew/bin/python3.14 -I -B -m unittest discover -s cache/metallib/lib
```

Tests that need Apple binaries are skipped unless `METALLIB_RUNTIME` (a prepared replay runtime),
`METALLIB_DEPLOYMENT_PAYLOAD` and `METALLIB_DEPLOYMENT_LIBRARIES` name them. To check a rebuild, compare its
`SHA256SUMS` with the selected artifact's.

## Limitations

- Successful compilation does not prove correct pixels. GPU results are checked per helper and per kernel family,
  not for every application workload; performance is not measured.
- Known remaining failures: texture atomic max, implicit imageblocks, rasterization-rate coordinate mapping, indirect
  visible-function tables, CoreRE manual vertex fetch, and MPSImage multifetch block reads in the image.
- The compiler-entry translation runs only in private test processes; the image installs only the offline repairs.
- The all-0 / all-1 function-constant specializations used by host sweeps are diagnostic controls, not a claim about
  every application's constants.

## Revisions

- r54 2026-11-11 `metallib-repairs-golden-gate.txt` adds VectorKit (`default.metallib`, `metal_libraries/AlloyCommonLibrary.metallib`) and VFX, which OCLP otherwise leaves as its 26.0 copies (27.2 VectorKit asks for `Icon::shadow_vertex` and other functions they lack). `legacy_sync.py` also converts `air.atomic.*.xchg` (27.2 VFX `particle_fill_grid` crashed IGIL's uniform analysis).
- r53 2026-11-11 Profiles name Golden Gate's libraries for any 27.x build (`metallib-repairs-golden-gate.txt`, new `hello-golden-gate.txt`); outputs record `SOURCES` and `system_build` so installers can refuse a library they were not made from; a listed library missing from SYSTEM stops the build.
- r52 2026-11-10 Initial public release.
