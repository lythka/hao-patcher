# golden-gate-hello

Plays the first-boot cursive **hello** (Language Chooser) and **Welcome to Mac** (Setup Assistant) on the Intel HD 4000
under macOS 27 (Golden Gate; 26A428 and 26B131).

## Why
- OCLP's Metal wrapper skips hello deliberately: `_helloSetup` swizzles `+[LCAUtilities shouldSkipHello]` to return
  YES.
- Apple's hello fragment shaders (`textFragment`, `textFragment_watchPreview`) crash the HD 4000 compiler in
  `SelectionDAG::LegalizeOp`, in 27.0 as in Tahoe. The other hello functions compile.
- The edited Metal wrapper is OCLP's, from the hardware overlay; the recipe runs in set order after
  [golden-gate-userspace](../golden-gate-userspace/README.md), like every fix.

## How it works
`src/kc-hook.sh` installs three files as root:wheel with the `restricted` flag, like Apple's:

| File on System | Change | Source |
|---|---|---|
| `System/Library/PrivateFrameworks/SetupAssistantSupportUI.framework/Versions/A/Resources/default.metallib` | the build's own library rewritten by [cache/metallib](../../cache/metallib/README.md) (`profiles/hello-golden-gate.txt`) so `textFragment` compiles; Apple's own shader, not a substitute. Installed only when the System's copy has the sha256 the row's `SOURCES` names, otherwise the hook stops with `MISSING/unsupported` | row `hello_metallib_golden_gate` (System-rooted folder) |
| `System/Library/Frameworks/Metal.framework/Versions/A/Metal` (OCLP's wrapper) | `src/edit-metal.py`: `shouldSkipHello` returns NO, Setup Assistant is not swizzled, `LC_LOAD_WEAK_DYLIB /usr/lib/libhelloblur.dylib`; re-signed ad hoc | made in the hook |
| `usr/lib/libhelloblur.dylib` (new) | blur cache and Continue-button warm-up | row `libhelloblur` |

`edit-metal.py` checks every byte it replaces and stops on any other wrapper. The same edits are declared in
[`edits/metal.json`](edits/metal.json) for [tools/edit-engine](../../tools/edit-engine/README.md), anchored on the
wrapper's symbols; `tests/test_edits.py` proves the engine writes the same bytes as the script. The image needs AMFI relaxed
(`amfi_get_out_of_my_way=1`, set by [hao-opencore](../hao-opencore/README.md)). The `libhelloblur` row is a prebuilt
artifact; its builder is not part of this repository.

## Verify
```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-hello/tests   # HELLO_OCLP_METAL=PATH adds OCLP's wrapper
```
Boot with NVRAM `prev-lang:kbd` cleared so the Mac starts at the Language Chooser: hello draws there, then Welcome in
Setup Assistant, with no MTLCompilerService crash report. The hook log lists the SHA-256 of the three files.

## Validation
NEEDS VALIDATION: on a MacBookPro9,2 the Language Chooser played hello and MTLCompilerService compiled its pipelines
without a crash; frame rate on the HD 4000 and `textFragment_watchPreview` are untested.

## Revisions
- r4 2026-11-12 Declare the Metal wrapper edits in `edits/metal.json` and the three files in `patch.json` `files`; the hook is unchanged.
- r3 2026-11-11 README and hook comment: runs in set order; golden-gate-userspace no longer replaces System. Recipe unchanged.
- r2 2026-11-11 The hello library row is a System-rooted folder with `SOURCES`; the hook refuses a System library the rewrite was not made from. 26B131 library (the rewrite touches the same eight functions as 26A428's).
- r1 2026-11-10 Initial public release.
