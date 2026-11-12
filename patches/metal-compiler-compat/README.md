# metal-compiler-compat

OCLP's legacy `MTLCompilerService` (the 3802 compiler the HD 4000 uses) loads `cache/metallib`'s `compat.dylib` at
startup, so every shader it compiles gets the AIR repairs the HD 4000 compiler needs, including the shaders MPSGraph
and Espresso generate at run time for Vision text recognition (Live Text). Client applications are unchanged.

## How it works

`tools/live-text` (`lib/compiler_service.py`) appends `LC_LOAD_DYLIB
/System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/3802/Libraries/libhaoMetalCompilerCompat.dylib` to
the service executable (UUID `3716D20F-B990-3906-B3E5-44E88AE63AF8`, from OCLP's hardware overlay) and re-signs it ad
hoc with its identifier, entitlements and flags. `Info.plist` and `CodeResources` stay byte-identical and stay bound
in the new signature's special slots. The library must be in the service's initial closure, before MTLCompiler
resolves its plugin entry: a dependency added to a library loaded later does not interpose (authored control in
`tools/live-text/tests/test_compiler_service.py`). `compat.dylib` checks the plugin and LLVM image UUIDs it was
written for and stays inactive otherwise.

`src/kc-hook.sh` (after the OCLP overlay; in dev-bs-test before `golden-gate-live-text-shared`) runs
`src/install.py`, which refuses unless the System's service executable, `Info.plist` and `CodeResources` are the
files the artifact was made from, then writes the library and then the executable.

`patch.json` `files` declares the service executable and the library as whole files from the row (the hook edits
nothing in place). `tests/test_edits.py` checks them against `install.py` and, with real inputs, rebuilds both with
`tools/live-text` and compares the bytes with the row.

## Input

Row `metal_compiler_compat` is `compiler-service/` of a `tools/live-text build` (26B131:
`store/artifacts/live-text-26B131-20261111-dd1c86b`). The service is OCLP's, the same for 26A428 and 26B131.

## Verify

```sh
python3.14 -I -B -m unittest discover -s patches/metal-compiler-compat/tests
python3.14 -I -B -m unittest discover -s tools/live-text/tests
```

`METAL_COMPILER_COMPAT_ROW`, `METAL_COMPILER_COMPAT_SOURCE` and `METAL_COMPILER_COMPAT_ADAPTER` (see
`tests/test_edits.py`) add the rebuild-and-compare check.

On the image, the hook log lists the two hashes from `build.json` `files`. On the laptop:
`vmmap $(pgrep -n MTLCompilerService) | grep libhaoMetalCompilerCompat` once any app has compiled a shader, no
`[compat] refused` line in the service's log, and no new MTLCompilerService crash report while Photos, Maps and the
Dock's Apps grid draw.

## Validation

NEEDS VALIDATION. Host tests pass. The same edit (from the 26A428 build of the same sources) ran on the laptop on
2026-11-09: 132 backend requests, no error replies, the shared-compiler Vision fixture recognized both lines. Not
yet built into an image or booted with 26B131.

## Revisions

- r3 2026-11-12 Declare the two installed files in patch.json `files` (whole-file origins) and test them in tests/test_edits.py; the hook is unchanged.
- r2 2026-11-11 Restored from darwin27-live-text-fixes v0.2.0 into tools/live-text; row metal_compiler_compat for 26B131 (selected by dev-bs-test).
- r1 2026-11-09 Add an unselected shared compiler-service repair recipe that leaves client applications unchanged.
