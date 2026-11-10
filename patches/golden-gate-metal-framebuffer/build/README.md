# Metal framebuffer adapter

Builds `libmetal-framebuffer.dylib`, which implements Metal fragment color-attachment
inputs on the legacy Intel graphics stack, and an offscreen probe that checks it pixel
by pixel. Selection is by AIR metadata only: no application names, shader-state
overrides or preference changes. [patches/golden-gate-metal-framebuffer](../README.md)
installs the dylib.

## Usage

```sh
python3.14 -I patches/golden-gate-metal-framebuffer/build/metal-framebuffer build NEW_BUNDLE
NEW_BUNDLE/probe NEW_BUNDLE/probe.metallib NEW_RESULTS all
# Original library, lowered automatically for each pipeline's sample count:
METAL_FRAMEBUFFER_PROBE=1 DYLD_INSERT_LIBRARIES=NEW_BUNDLE/encoder.dylib \
  NEW_BUNDLE/probe NEW_BUNDLE/probe.metallib NEW_RESULTS native
python3.14 -I patches/golden-gate-metal-framebuffer/build/metal-framebuffer \
  lower-air SPECIALIZED.ll NEW.ll [--multisample]
python3.14 -I -m unittest discover -s patches/golden-gate-metal-framebuffer/build/tests
# Also exercise the real MetalOld/3802 reflection and persistent cache reuse:
METAL_FRAMEBUFFER_RUNTIME=PREPARED_SHADER_RUNTIME \
  python3.14 -I -m unittest discover -s patches/golden-gate-metal-framebuffer/build/tests
```

The probe runs on the target (an HD 4000 Mac), not on the build host. Routes:
`native`, `texture-control`, `lowered-control`, `all`; an optional last argument
selects 1 or 4 samples. Output directories must be new. Pixel, pipeline or command
failures exit nonzero.

Process-local probe controls:

- `FB_PROBE_FACTORY=async`, `FB_PROBE_UNRETAINED=1`, `FB_PROBE_PARALLEL=1`: asynchronous
  pipeline creation, unretained command buffers, ordered parallel child encoders.
- `FB_PROBE_CONSTANTS=1` selects a conditional, dynamic-attachment function from
  `constants.metallib`; `FB_PROBE_SOURCE=1` compiles `src/probe.metal` as a live
  source library instead.
- `FB_PROBE_IDENTITIES=1` runs the 60-case base-vertex/base-instance/primitive-ID
  regression (attachment 0, RGBA32Float).
- `FB_PROBE_SUBRESOURCE=mip` or `slice` renders into mip 1 or array slice 2 (single sample).

`METAL_FRAMEBUFFER_LLVM` overrides the path of the LLVM library used for lowering.

## Inputs and outputs

Inputs: `src/`, Python 3.14, Xcode clang, the Metal tools and the macOS SDK. The build
targets x86_64 macOS 13 and MSL 3.0; executables are ad hoc signed. At runtime the
adapter uses the legacy stack's own GPUCompiler 32023 LLVM library, whose parser
accepts the original AIR (the older 31001 parser rejects attribute 70). Runtime
lowering needs no Python, Xcode, external process or host path.

The bundle holds the signed `probe`, the native `lower` CLI, the probe-only
`encoder.dylib`, the system `libmetal-framebuffer.dylib`, the original and
function-constant probe libraries, lowered single- and multi-sample libraries with
manifests, and `build.json` (revision, commit, source and toolchain identities,
commands, output hashes). No Apple binary is copied. Timestamps and signatures vary
between builds. The runtime and the CLI share one native transform; `lib/lower.py`
is only the host command interface.

Probe results: identity, JSON verdicts and every complete 16x16 RGBA float32
readback, pixel-major then sample-major. A CPU oracle applies the same arithmetic
and format quantization at every write. Float comparisons are exact; UNORM allows
2^-23, far below one UNORM step.

## How it works

The probe covers 720 cases per route: attachments 0/1/2; RGBA8Unorm, RG16Float,
RGBA16Float, RGBA32Float; 1 or 4 samples; clear, load or previous draw; single,
repeated and overlapping direct/indexed/instanced lists and strips, with nonzero
offsets. The texture control alternates two textures in separate passes as an
independent algorithm.

The native frontend reads `MTLFunction.bitcodeData` after specialization and before
pipeline creation. It wraps the original arithmetic, samples the color inputs at
fragment/sample coordinates, allocates unused resource slots and emits a new MTLB
named by the SHA-256 of its AIR. Existing output metadata is preserved, so Metal
builds public reflection and every cache key from the transformed bytes. The
original function and library are unchanged; single- and multi-sample variants are
cached on the original function. Function constants and live source use the same
path. The AIR version is read from its metadata header with a linear search.

The encoder finds the generated reflection arguments, retains the pass, binds the
actual attachments (including mip and slice views) and issues a texture barrier
before each primitive. Direct and indexed list and strip draws are split in instance
order; odd strip triangles keep their winding. Wrapper buffers carry the original
base vertex, base instance and primitive IDs. Client texture, buffer and byte
bindings are tracked and restored on pipeline changes. Generated views are retained
until GPU completion, including with unretained command buffers. Parallel children
inherit the parent pass. Synchronous and asynchronous pipeline factories are both
hooked.

The system dylib loads as a dependency of the legacy driver and creates no device
from a driver initializer; the probe dylib needs the explicit opt-in above.
Source-built libraries get fresh hashes; existing cache records are never
relabelled or removed.

## Verify

Host tests cover metadata selection, preservation of arithmetic and outputs,
distinct metadata IDs, input shapes, slot collisions, sample coordinates,
specialization and linear-time lookup on large modules. On the HD 4000, the probe
compares 1,440 native/control cases, 720 generated-shader control cases and 60 draw
identity cases with the CPU oracle. The optional legacy replay test checks new
reflection, new single/multi-sample hashes, and zero compiler requests on a second
process's warm run.

## Provenance

Apple's [resource synchronization](https://developer.apple.com/documentation/metal/resource-synchronization)
documentation and the [Metal Shading Language specification](https://developer.apple.com/metal/Metal-Shading-Language-Specification.pdf)
define the operation and its builtins. The legacy replay test reuses the modeled
device and real compiler from `cache/shaders`. No upstream compiler code is vendored.

## Limitations

The validated scope is direct and parallel rendering as used by RenderBox. Indirect
GPU draws, tessellation, indirect command buffers, layered render targets, binary
archives, integer pixel formats, per-instance vertex step rates above one and
performance are not covered. Indirect draws and layered fragment inputs fail
explicitly; other unsupported API routes are not yet intercepted. This is not
complete Metal feature conformance.

## Revisions

- r4 2026-11-10 Initial public release.
