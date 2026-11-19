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
`store/artifacts/live-text-26B131-20261113-fa444101`). The service is OCLP's, the same for 26A428 and 26B131.

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

Laptop 2026-11-19, a MacBookPro9,2: image 8305fdd (26B131, built from fb6542df with r12): the shared-compiler Vision fixture recognized both lines, and no MTLCompilerService crash report was written over a 48-app sweep (Photos and Maps included). The release-EFI image 8acc07f (same recipes) drew Setup's shader effects with 0 crash reports (r11's fault gave 17+ per boot), and `hao verify` found MTLCompilerService, the 31001 `libLLVM.dylib` and the compat library as installed. On the release-EFI image 8acc07f: all 14 MTLCompilerService processes map `libhaoMetalCompilerCompat.dylib`, and the boot log has no `[compat] refused` line.

## Revisions
- r13 2026-11-19 Validated r12 on hardware (Validation). Nothing it installs changes.
- r12 2026-11-18 Row metal_compiler_compat is the same tools/live-text build at 46289f6d: build.json records compat_revision 59 (was 58), the dylib is unchanged; `tools/asset-build` now reproduces the row from source.
- r11 2026-11-17 OCLP's GPUCompiler 31001 `libLLVM.dylib` names `libLLVM_helper.dylib` by an absolute path (`edits/libllvm-31001.json`, `src/llvm_helper_path.py`, re-signed ad hoc as `libLLVM`). Reason (S18): MTLCompilerService runs hardened (code-signing flags 0x22010801), dyld refuses OCLP's relative helper path there ("relative path not allowed in hardened program"), so its dlopen of the 31001 MTLCompiler failed and the service called a null pointer on every 31001 request (Setup's shader effects vanished, 17+ crashes per boot). Evidence (2026-11-17, MacBookPro9,2 running these bytes): a restricted dlopen probe fails before and loads after, no crash after the swap, and the services hold the 31001 and 3802 compilers.
- r10 2026-11-17 `requires`: os 26B131, donor 25G332/25G342, gpu intel-ivb (the HD 4000), cpu and firmware any; replaces `applies`. status product: it ships in the release profiles (what it installs is unchanged; `validated` stays false until the re-cut's laptop boots prove it).
- r9 2026-11-16 Declares its pack (metal-3802): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r8 2026-11-16 Row `metal_compiler_compat` is the f479d6c7 build, reproducible from source with Xcode 27.2: compat.dylib linked with a fixed install name (code identical), build.json holds content hashes only.
- r7 2026-11-14 Input row `metal_compiler_compat` now hashes only the files a release can give a user's Mac (`# hash: release-files`: its published files plus the made-on-Mac ones, pipeline/lock.py scope()) (its sha256 is the same: the folder holds only those files). The same artifact, so what this recipe installs is unchanged.

- r6 2026-11-13 Input row `metal_compiler_compat` (26B131) now names compiler-service/ of live-text-26B131-20261113-fa444101, rebuilt from a clean fa444101 so build.json names inputs as `store/...` paths instead of the builder's home folder (the row can be published). Installed bytes that change: `libhaoMetalCompilerCompat.dylib`: its LC_UUID and ad hoc signature differ, every other byte is identical (ld derived the UUID from the build folder's absolute path, which cache/metallib translation-build used as the install name before tools/live-text sets the final one). MTLCompilerService is byte-identical.
- r5 2026-11-13 Input rows record their content sha256 (folder rows sealed with `lock.py seal`); the same artifacts at the same paths, so what this recipe installs is unchanged.
- r4 2026-11-12 Declare install attributes in patch.json `files` (root:wheel 0755 and `restricted` on the service and the library), as the hook sets them, so overlays carry them; the hook is unchanged.
- r3 2026-11-12 Declare the two installed files in patch.json `files` (whole-file origins) and test them in tests/test_edits.py; the hook is unchanged.
- r2 2026-11-11 Restored from darwin27-live-text-fixes v0.2.0 into tools/live-text; row metal_compiler_compat for 26B131 (selected by dev-bs-test).
- r1 2026-11-09 Add an unselected shared compiler-service repair recipe that leaves client applications unchanged.
