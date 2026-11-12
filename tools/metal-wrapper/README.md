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

SPEC is `{"classes": [...], "functions": [...], "data": [...]}`. The full list for 26B131 comes from the gap
analysis in `spec/`: what x86_64 code in 27.2.1 imports from Metal, minus what `MetalOld` exports.

Next:

- **The full 26B131 spec.**
- **Which of Moraea's per-app workarounds to keep.** They are WebGL/ANGLE, CoreImage, Glass, Keynote and
  Pixelmator. Hello's needs nothing: we simply don't skip it.
- **libhelloblur:** a weak load of `libhelloblur`, as golden-gate-hello adds to OCLP's wrapper today.
- **A recipe** that installs the wrapper in the `metal-3802` pack.
- **A laptop check.**

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/metal-wrapper/tests
```

These tests need Xcode with the macOS 27 SDK, and Rosetta. They build a wrapper from three header classes and
one constant against a stand-in `MetalOld`, with no Apple binary. They then load it in an x86_64 process under
Rosetta: a property round-trip, `copy`, the subclass chain, a stub method call, the constant's value, and the
`MetalOld` re-export. A separate test checks that names `MetalOld` already exports are skipped.
