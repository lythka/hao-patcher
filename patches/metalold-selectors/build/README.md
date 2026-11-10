# metalold-selectors generator

Builds `libmetalold-selectors.dylib`, which adds to OCLP's legacy Metal stack
(MetalOld 306 with the Intel HD 4000 driver) the Metal selectors newer frameworks send
and the stack does not answer, each with the answer a device lacking the feature
gives. [patches/metalold-selectors](../README.md) installs it. The `survey/` tools
enumerate the gap and check the artifact against the real legacy stack.

## Usage

```sh
python3.14 -I patches/metalold-selectors/build/make OUTPUT_PARENT          # dylib + build.json
python3.14 -I patches/metalold-selectors/build/make OUTPUT_PARENT \
    --survey LEGACY_RUNTIME --protocols TARGET_PROTOCOLS.json              # + survey-before/after.json
python3.14 -I -m unittest discover -s patches/metalold-selectors/build/tests
```

`LEGACY_RUNTIME` is a `cache/shaders` replay runtime. The survey inputs are made once
per target macOS build with [ipsw](https://github.com/blacktop/ipsw):

```sh
ipsw dyld macho TARGET_CACHE /System/Library/Frameworks/Metal.framework/Versions/A/Metal --objc > metal-objc.txt
python3.14 -I patches/metalold-selectors/build/survey/parse_protocols.py metal-objc.txt TARGET_PROTOCOLS.json
```

The call-site analysis over an image's shared cache finds which missing selectors
are actually sent, by whom, and whether the call is guarded:

```sh
ipsw dyld imports IMAGE_CACHE /System/Library/Frameworks/Metal.framework/Versions/A/Metal   # Metal importers
# For each importer: `ipsw dyld macho IMAGE_CACHE IMG --objc --objc-refs` (the "@selectors refs" section)
# into SCRATCH/selrefs-addr/<name>.txt; `ipsw dyld extract` and `otool -tV` into SCRATCH/extract and SCRATCH/asm.
python3.14 -I patches/metalold-selectors/build/survey/callsites.py SCRATCH MISSING_SELECTORS.json callsites.tsv
```

## Inputs and outputs

- In: `src/metalold_selectors.m`, Xcode clang and a macOS 26 or later SDK. The dylib
  targets macOS 13 and links only Foundation; it must not link Metal, because it loads
  inside Metal. Optional: a `cache/shaders` replay runtime (`runtime.json`, `fw/`,
  `bundles/`: MetalOld and the driver under Rosetta) and the target protocols JSON.
- Out: `OUTPUT_PARENT/metalold-selectors-YYYYMMDD-<hash12>/` (hash12 = SHA-256 of the
  source, clang and SDK versions) holding `libmetalold-selectors.dylib` (x86_64
  `MH_DYLIB`, install name `/usr/lib/libmetalold-selectors.dylib`, ad hoc signed),
  `build.json` (unit, revision, commit, dirty flag, inputs, toolchain, output hashes,
  survey summary) and, with `--survey`, the two survey JSON files.
- Survey JSON: `classes` (each legacy class adopting a target protocol: its chain,
  adopted protocols, their closure over the target's protocol inheritance, and missing
  selectors with protocol and required flag), `class_missing` (target classes present
  by name on the legacy stack and the methods they lack), `missing_selectors`,
  `implemented_elsewhere` (missing selectors another legacy class answers) and
  `absent_classes`.

## How it works

The dylib defines one donor class per MetalOld base class (`MOSDevice` ->
`_MTLDevice`, `MOSCommandQueue`, `MOSCommandBuffer`, `MOSCommandEncoder`, the indirect
command classes, `MOSTexture` -> `MTLIOAccelTexture`, `MOSPipelineState`,
`MOSPipelineDescriptor` -> the four pipeline descriptors, `MOSDepthStencilState`,
`MOSSharedEvent`, `MOSHeap`, `MOSArchitecture`). A constructor walks the table and,
for each donor method whose selector the target class does not answer
(`class_getInstanceMethod`, which includes superclasses), adds the donor's IMP with
`class_addMethod`. Driver subclasses such as `MTLIGAccelDevice` inherit the additions.
An existing method is never replaced. Forwarding to a Metal 306 selector goes through
`id<MOSLegacy>` so the compiler emits the right call (`objc_msgSend_stret` for
`MTLSize`). `METALOLD_SELECTORS=0` in the environment turns the constructor off.
`metalold_selectors_attach_prefixed(prefix)` performs the same attach on classes
named `prefix + name`; it is the test seam, because the host's own Metal owns the real
class names in a test process.

Rules for an answer:

- A capability query answers NO unless the legacy stack has the capability
  (`supportsArgumentBuffers` YES for tier 1; `requiresBFloat16Emulation` YES, as Tahoe
  computes it for GPUs below Apple9).
- A factory for an object kind the stack lacks returns nil, with
  `NSCocoaErrorDomain`/`NSFeatureUnsupportedError` when the call takes an `NSError **`.
- A renamed or extended selector forwards to its Metal 306 name when the receiver
  answers it (for example `newIOFileHandleWithURL:` -> `newIOHandleWithURL:`,
  `allocatedSize` -> `currentAllocatedSize`).
- A call that needs an object the device never hands out (residency sets) is a no-op.
- A dynamic-attribute-stride encoder call forwards to the static call; the device
  answers `supportsDynamicAttributeStride` NO, so a conforming caller passes
  `MTLAttributeStrideStatic`.
- Where Tahoe's own base class has a concrete answer (`shaderValidation` Disabled,
  `IOSurfaceSharedEvent` returns self), that answer is used. Descriptor properties
  such as `forwardProgressUsage` and `shaderValidation` are stored and returned.
- Where no honest answer is known, the selector is left out so the call still fails
  loudly: `featureQueries`, `getCompilerConnectionManager:`, shader sample buffers,
  resource index reservation, `supportsResourceHeaps`, `setupMPSFunctionTable:`,
  `isSystemDefaultDevice`, bindless depth-stencil states in argument encoders,
  depth-bounds testing.

The survey (`survey/survey.m`, x86_64, run in the replay runtime's environment from
`cache/shaders/lib/harness.py`) loads the Metal wrapper and the driver bundle,
optionally the dylib, then for every class takes the protocols it adopts, closes them
over the target's protocol inheritance and asks `class_respondsToSelector` for every
method. `survey/callsites.py` finds the code call sites: each importer's
selector-reference slots, every rip-relative load of such a slot in `otool -tV`
output, the enclosing function (`LC_FUNCTION_STARTS`), and whether that function also
calls `respondsToSelector:`, `instancesRespondToSelector:` or `conformsToProtocol:`
(guarded) or `supportsFamily:`/`supportsFeatureSet:` (gated). A Swift image that only
carries a copy of a whole protocol has the selector references but no loads in code,
which is why the call-site pass exists.

## Verify

`tests/test_shim.py` builds the dylib and `tests/fake_stack.m` (stand-in classes with
the Metal 306 selectors the shim forwards to), runs them under Rosetta with the
constructor off and the prefixed attach, and checks 35 answers (NO, nil with error,
forwarded arguments, no-ops, stret, round trips, no override), idempotence, the kill
switch, the Mach-O (x86_64 dylib, install name, no Metal link, ad hoc signature),
`build.json`, and `parse_protocols.py` on a sample. With
`METALOLD_SELECTORS_LEGACY_RUNTIME` and `METALOLD_SELECTORS_TARGET_PROTOCOLS` set, it
also runs the real survey. Against a replay runtime and Metal 373.7's protocols,
`MTLIGAccelDevice` misses 74 device selectors before the shim and 12 after (the
deliberately omitted ones above), and every selector with a product-framework call
site is answered by its receiver.

## Provenance

The answers were read from Tahoe's own `Metal` (disassembled with `ipsw dyld
extract --objc` and `otool -tV`). The method-donor pattern is the Objective-C
runtime's documented `class_addMethod` use. Dependencies: Xcode clang, Rosetta for the
tests, `ipsw` and `otool` for the survey, and `cache/shaders/lib/harness.py` for the
legacy runtime environment.

## Limitations

- The survey's protocol surface comes from Metal 373.7 of a Tahoe 26.7 build; the
  call-site side uses the target image's own cache.
- `newTextureWithBytesNoCopy:...` answers nil. QuartzCore, RenderBox and Tungsten each
  call it in one function without a local guard; they run normally, so the call is
  presumably gated elsewhere.
- The guard analysis is per function: a guard in a caller is not seen, so
  "unguarded" overstates.
- `build.json` reports `dirty: true` whenever the checkout has uncommitted files; the
  input hashes are the record.

## Revisions

- r3 2026-11-10 Initial public release.
