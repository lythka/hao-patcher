# metal-wrapper

hao's own `Metal.framework/Versions/A/Metal` for Intel Macs on the legacy Metal stack. It replaces OCLP's Moraea
wrapper. Like that wrapper it re-exports `MetalOld.dylib`, Apple's legacy Metal (Ventura, 306.3.5), which
together with the HD 4000 driver does the actual GPU work. It adds what newer clients import but `MetalOld`
lacks.

- **What ships:** the wrapper is our code and ships prebuilt in HaoSupportPkg.
- **Where `MetalOld` comes from:** it is Apple's binary, carried by OCLP's PatcherSupportPkg. The builder
  downloads it there (tools/acquire); we never redistribute it.

## How it is made

`make.py` generates every stub from the SDK's own Metal headers, through clang's syntax tree, so each signature is
Apple's:

| Kind | Stub |
|---|---|
| class | `@implementation` with the header's superclass. Properties are synthesized. Every other declared method returns zero, `init…` returns `[super init]`, and NSCopying adopters copy by retain |
| function | the header's prototype, returning zero |
| data | NSString constants hold their own name; anything else is zero |

- **Skipped names:** anything `MetalOld` already exports, so nothing is defined twice.
- **Private names:** names the SDK headers don't declare must come from an `--extra` source file; the build
  refuses otherwise.
- **Output:** the build is x86_64 with install name `/System/Library/Frameworks/Metal.framework/Versions/A/Metal`
  and compatibility version 1.0.0 (as OCLP's wrapper), and is signed ad hoc.

```sh
python3.14 -I tools/metal-wrapper/make.py build SPEC.json --metalold PATH/MetalOld.dylib --out DIR [--extra private.m]
python3.14 -I tools/metal-wrapper/make.py generate SPEC.json OUT.m [--metalold PATH]
```

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

SPEC is `{"classes": [...], "functions": [...], "data": [...]}`. The full list for 26B131 comes from the gap
analysis in `spec/`: what x86_64 code in 27.2.1 imports from Metal, minus what `MetalOld` exports.

Next:

- **The full 26B131 spec.**
- **Laptop A/B of the workarounds, then dropping what isn't needed.**
- **libhelloblur:** a weak load of `libhelloblur`, as golden-gate-hello adds to OCLP's wrapper today.
- **A recipe** that installs the wrapper in the `metal-3802` pack.
- **A laptop check.**

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/metal-wrapper/tests
```

These tests need Xcode with the macOS 27 SDK, and Rosetta. On a macOS 27 host every x86_64 process already has the
system's Metal loaded (even CoreFoundation pulls it in). So the stub test takes our classes by symbol and asserts
they come from our image. The load-time test builds `runtime.m` with `HAO_METAL_CLASS_PREFIX="Hao"` against
stand-in classes; shipping builds use Apple's names, which the Moraea analysis found in both MetalOld and 27.2.1's
Metal. The libANGLE lookup can only be proven on the laptop. They build a wrapper from three header classes and
one constant against a stand-in `MetalOld`, with no Apple binary. They then load it in an x86_64 process under
Rosetta: a property round-trip, `copy`, the subclass chain, a stub method call, the constant's value, and the
`MetalOld` re-export. A separate test checks that names `MetalOld` already exports are skipped.
