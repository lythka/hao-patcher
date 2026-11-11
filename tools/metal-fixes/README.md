# metal-fixes

One command for the Golden Gate (macOS 27) Metal repairs on MacBookPro9,2 (Intel HD
4000): it builds the artifacts that the Metal input rows select, installs them into
an offline System, and runs their tests. The repairs themselves live in
[`cache/metallib`](../../cache/metallib/README.md),
[`patches/golden-gate-metal-libraries`](../../patches/golden-gate-metal-libraries) and
[`patches/golden-gate-metal-framebuffer`](../../patches/golden-gate-metal-framebuffer);
the MPS initializer repair is in `lib/` here.

## Build

Requires macOS, Python 3.12 or later, and the Xcode command-line and Metal tools.
Rosetta runs the x86_64 test programs on Apple silicon. Building compiles and signs
code but never runs a GPU program. Every output folder must be new. Run from this
folder:

```sh
python3.14 -I metal-fixes metallibs --system APPLE_SYSTEM_ROOT \
  --output NEW_LIBRARIES --work NEW_WORK
python3.14 -I metal-fixes hello --system APPLE_SYSTEM_ROOT \
  --output NEW_HELLO --work NEW_WORK
python3.14 -I metal-fixes runtime --input ORIGINAL_RUNTIME_SPLIT \
  --llvm MATCHING_3802_LIBLLVM --compiler ORIGINAL_ICBE --output NEW_RUNTIME
python3.14 -I metal-fixes compiler-runtime --output NEW_COMPILER_ADAPTERS [--llvm LIBLLVM]
python3.14 -I metal-fixes framebuffer --output NEW_FRAMEBUFFER
python3.14 -I metal-fixes mps-plan --cache X86_DYLD_CACHE_DIRECTORY
python3.14 -I metal-fixes mps-apply --cache X86_DYLD_CACHE_DIRECTORY --output NEW_CACHE_FAMILY
```

| Command | Output | Input row |
|---|---|---|
| `metallibs` | The six stock libraries listed in `cache/metallib/profiles/metallib-repairs-golden-gate.txt`, rewritten for the HD 4000 compiler (`cache/metallib/make`); `SOURCES` records the System library each was made from | `metallib_repairs_golden_gate` |
| `hello` | SetupAssistantSupportUI's hello library (`profiles/hello-golden-gate.txt`), same layout | `hello_metallib_golden_gate` |
| `runtime` | OCLP's Intel runtime bitcode with the added quad and collective helpers, and the repaired `iCBE.dylib` | `metal_quad_runtime_golden_gate` |
| `compiler-runtime` | Optional compiler and client adapter dylibs | none |
| `framebuffer` | `libmetal-framebuffer.dylib` and its regression fixtures (not run) | `metal_framebuffer` |
| `mps-plan` | The MPS initializer edits for a cache, as JSON | used by `patches/golden-gate-userspace` (`metal_fix_source`) |
| `mps-apply` | A copy of the cache family with the MPS repair, re-signed with 4 KiB pages, and `build.json` | none |

Each artifact's manifest records the hashes of its input files and the source
commit. Apple libraries, runtimes and caches are inputs you supply.

## MPS initializer repair

MPSCore assigns Intel GPUs device family 25 or 26. In the x86_64
`MPSNDArrayMatrixMultiplication` initializer, families 18 to 25 skip creating the
native `MPSMatrixMultiplication` fallback, yet family 25 then selects the code path
that needs it. Messages to the missing object are dropped, so the matrix
multiplication silently does nothing.

`lib/mps_matrix_initializer.py` matches an exact 86-byte context at `__TEXT+0x2ee43`
of MPSNDArray (UUID `530AFC24-8A84-3B7A-9601-9608EA68391C`) and changes
`add eax,-26; cmp eax,-9; ja skip` to `add eax,-25; cmp eax,-8; ja skip`. Only
family 25 changes behaviour. Any other build, relocated code, duplicate or mixed
state is refused; an already repaired cache needs no edits. `lib/dyld_cache.py`
resolves the image through the cache's mappings.

## Install

In the builder VM, as root, against an offline System mounted writable:

```sh
python3.14 -I metal-fixes install --system SYSTEM_MOUNT \
  --libraries NEW_LIBRARIES --runtime NEW_RUNTIME [--framebuffer NEW_FRAMEBUFFER]
```

This runs the `kc-hook.sh` of the two Metal patches from a temporary copy. The
library recipe installs the repaired metallibs, runtime bitcode and `iCBE.dylib`;
the framebuffer recipe installs its library, adds a weak load command to the
driver and re-signs it. Creating the snapshot and booting are left to the image
build.

## Test

```sh
python3.14 -I metal-fixes test
```

Runs the `cache/metallib` CPU and compiler tests, the framebuffer lowerer tests
and this folder's tests, including the MPS initializer checks for every family
from 0 to 30 and the full `mps-apply` path. Tests that need a supplied legacy
runtime are skipped without it. No GPU workload runs. The framebuffer target
tests in `patches/golden-gate-metal-framebuffer/build/tests` need their own
environment.

## Limitations

The MPS initializer repair matches only the binaries named above; the library installers refuse a System library
other than the one an artifact's `SOURCES` names. Rebuilt artifacts
reproduce the selected runtime and framebuffer bytes, but acceptance on hardware
is per patch; see each patch's Validation section.

Licensing: see [NOTICE.md](NOTICE.md).

## Revisions

- r1 2026-11-11 `metallibs` uses the Golden Gate profile for any 27.x System; `hello` added.
