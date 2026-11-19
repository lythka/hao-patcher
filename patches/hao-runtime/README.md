# hao-runtime

Installs the hao runtime payload so ARM64-only Golden Gate programs run on the Intel Mac. hao is developed in
[hao-runtime](https://github.com/lythka/hao-runtime); this patch only decides where in the build the payload enters the
image.

## How it works
The input row `hao_runtime` selects a complete payload built from hao-runtime; its `build.json` identifies the runtime
commit and the exact target files. The kc-hook runs after [golden-gate-userspace](../golden-gate-userspace/README.md)
and runs the payload's `hao-install.py` against the offline System. The installer copies the runtime libraries, embeds
an x86_64 launcher slice into each selected ARM64-only executable, applies the payload's dyld repair and records the
installation in `usr/lib/hao/install.json`. Native Intel donors stay native.

Programs in the App and OS cryptexes (Safari's agents, Safari's and WebKit's XPC services) have manifest entries under
`System/Volumes/Preboot/Cryptexes/{App,OS}`, where the running system grafts those images. On the offline System
volume these are empty graft points, so `install.json` lists them as `missing`; golden-gate-userspace embeds them
into the cryptex images it builds (`src/cryptex_stubs.py`), before this recipe runs. The payload's `stub-identities`
lists them with the rest.

The `HaoExec` kext and the boot configuration are installed separately by [hao-opencore](../hao-opencore/README.md).
Use a payload built without the optional AOT preseed caches for the default profile.

## Declared files
`patch.json` `files` lists what the installer writes: `usr/libexec/hao`, the three libraries and three data files
under `usr/lib/hao`, the whole folders `usr/lib/hao/if` (interface units) and `usr/lib/hao/aot` (AOT caches), the fixed
`usr/lib/dyld` and the `usr/lib/hao/install.json` record, each from row `hao_runtime` with the mode the installer gives
it. The embedding into the 963 ARM64-only programs of the manifest is not declared one spec per file (hundreds of
`hao.embed-stub` specs): `tests/test_edits.py` derives the list from the payload's `embed/manifest.json`, checks that
the 26 cryptex programs are exactly golden-gate-userspace's `embed-*.json` specs and that no other recipe declares an
embedded program, and runs the payload's own installer on a scratch copy of the programs to prove the declared files
are exactly what it writes. Not declared: the empty `private/var/db/hao` folder it creates, and `HaoExec.kext`
(hao-opencore installs it).

## Verify
```sh
python3.14 -I -B -m unittest discover -s patches/hao-runtime/tests
# optional: HAO_RUNTIME_PAYLOAD = the hao_runtime row folder; HAO_RUNTIME_SYSTEM = a pristine 26B131 System volume
```
Check the payload's source and OS identities in its `build.json` and the installed `usr/lib/hao/install.json`, then
boot the image in [QEMU](../../tools/qemu-vm/README.md) before installing it on hardware. hao's own unit tests live in
hao-runtime and do not replace image boot and application checks.

## Validation
NEEDS VALIDATION: r92 (row hao-runtime 5a7d3fc, the JIT arena): its libhao was shown working on a MacBookPro9,2 running image 5e30343; the validation record waits for the acceptance run with this payload (its libhao, AOT made by it).

Laptop 2026-11-19, a MacBookPro9,2 with image 8305fdd (26B131, built from fb6542df with r90): hao runs the image's ARM programs to a logged-in desktop; 965 AOT files are in place; fma64 equals native arm64; Control Center, Notification Center, Font Book, Stocks and Tips run with no refusal. Open and tracked on their own: Weather (#9, #62), gamed (#18), the AOT job's done marker (#78).

## Revisions
- r92 2026-11-19 Row hao_runtime (26B131, A family) is hao-runtime 5a7d3fc: the RC runtime 1691020 with the JIT arena (Maps refusal, #84), libhao 582796eb, shown working on a MacBookPro9,2 running image 5e30343 with that libhao in place. The payload changes: libhao and the AOT files the Mac makes from it.
- r91 2026-11-19 Validated r90 on hardware (Validation). Nothing it installs changes.
- r90 2026-11-17 Row hao_runtime (26B131, A family) is hao-runtime af5aa59, release/v0.0.1's security batch: AOT format 8 (older caches refused, baseline CPU), hardened stubs for library-validated programs, libhao by absolute path, launch constraints carried; hao-install installs exactly the manifest and stages about 3.5 GB in TMPDIR first.
- r89 2026-11-17 Status product (was experiment): it ships in the release profiles. Declaration only; `validated` stays false until a boot proves the re-cut. Nothing it installs changes.
- r88 2026-11-17 Declares its scope (`requires`, docs/requires.md): hao refuses a CPU below x86-64-v2 (hao-runtime's release notes). Nothing it installs changes.
- r87 2026-11-16 Declares its pack (base): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r86 2026-11-16 Row `hao_runtime` (26B131) -> hao-runtime main 1547dcb payload (89aa3ccc, manifest dded6037): libhao c7a162e2 (Dock, gamed, Freeform save, Books, Float16, `--aot --check`, `--auto`) and the new System/Library/LaunchDaemons/hao.aot.plist (declared), which runs `hao --aot --auto` after boot. Not yet run on a laptop.
- r85 2026-11-16 (v0.0.1 release line only) Row `hao_runtime` -> hao-runtime 7b17cd8 payload (6d90370a): the 5ae3d5e binaries, archive with tools/hao-runtime-on-mac.
- r84 2026-11-16 Row `hao_runtime` (26B131) -> hao-runtime 5ae3d5e payload (80a10da1, manifest 41dc029b): 155b7b1 plus the Music fix; only libhao.dylib changes (a4d0d2ad). Music opens on a dev image with this libhao.
- r83 2026-11-16 Row `hao_runtime` (26B131) -> hao-runtime 155b7b1 payload (0d19b199, manifest cb5fd3b9): `hao --aot` with no FILE caches the default set in the new usr/lib/hao/aot-list (declared); libhao 82dabec5. Not yet run on a laptop.
- r82 2026-11-16 Row `hao_runtime` (26B131) -> hao-runtime 0e75f64 payload (4f9784a2, manifest 6c448c23): the 1a290d7 binaries with reproducible records (no build times, root-relative paths); release asset archive bbc55887. Installed binaries unchanged.
- r81 2026-11-16 Row `hao_runtime` (26B131): its sha256 leaves out root/usr/lib/hao/aot (52b89e0a), which the user's Mac makes after the first boot; same payload, installed bytes unchanged.
- r80 2026-11-16 Row `hao_runtime` (26B131) -> hao-runtime 1a290d7 payload for the unfolded cache (manifest d6054d9c, libhao d6b9b712).
- r79 2026-11-14 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime 79bc70e tree (hao-runtime-26B131-20261114-79bc70ead081): guest dlopen resolves @rpath, @loader_path and @executable_path from the calling image (Xcode starts instead of aborting at launch). No recipe change.
- r78 2026-11-14 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime bd08b64 tree (hao-runtime-26B131-20261114-bd08b64a1563; libhao ffa4eeb6, the bytes proven on laptop 1 for Control Center): Finder (diag#0032), mds, the Control Center warm and cold-open fixes, native stack per callback level (diag#0005). No recipe change.
- r77 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime 399e78e tree (hao-runtime-26B131-20261113-399e78e32376, tag v0.1.0-beta.1): Optional witness nils returned to native as a callback result (diag#0001). No recipe change.
- r76 2026-11-13 Documentation only: Validation states what was shown on the laptop. The hook and what it installs are unchanged.
- r75 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime 1de47eb tree (hao-runtime-26B131-20261113-1de47eb87a7f): the ControlCenter GridSizes and Displayable refusals fixed (2b19233) and the stub skeletons (1de47eb). No recipe change.
- r74 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built from a clean hao-runtime 9dce413 tree (hao-runtime-26B131-20261113-9dce4138ddfa): the ControlCenter crash fix and f66403c's float-HFA stack fix. No recipe change.
- r73 2026-11-13 Input rows record their content sha256 (folder rows sealed with `lock.py seal`); the same artifacts at the same paths, so what this recipe installs is unchanged.
- r72 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 397b267 (hao-runtime-26B131-20261113-397b2675d037). No recipe change.
- r71 2026-11-13 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 43af852 (hao-runtime-26B131-20261113-43af85296aa9). No recipe change.
- r70 2026-11-12 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 0a2c2aa (hao-runtime-26B131-20261112-0a2c2aad4755). No recipe change.
- r69 2026-11-12 Declare install attributes in patch.json `files` (`restricted` on usr/lib/dyld and usr/libexec/hao, as hao-install.py leaves them), as the hook sets them, so overlays carry them; the hook is unchanged.
- r68 2026-11-12 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 273ba98 (hao-runtime-26B131-20261112-273ba980b829). No recipe change.
- r67 2026-11-12 Declare where each installed file is inside its row ("source") for pipeline/materialize.py; the hook is unchanged.
- r66 2026-11-12 Declare the installed files and folders in patch.json and its test (the embedding stays a manifest-derived list); the hook and what it installs are unchanged.
- r65 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime d9ea9bd (4e68804 + the Swift value walk passes values under 8 bytes, which aborted Weather, StocksWidget, NewsTag and NewsToday2 on 7d0b612). No recipe change.
- r64 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 4e68804 (746f1ec + JIT TBL/EXT/integer and FRINT forms, native-call cache ownership fix, measured Swift value rules for the Stocks/News widgets and Weather, five review fixes); same async provider, units and AOT recipe. No recipe change.
- r63 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 746f1ec (12685dd + the native-call cache signal/fork fix) with the 26B131 async contract provider. No recipe change.
- r62 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 12685dd (5651d79 + x86-64-v2 build and a startup CPU-feature check). No recipe change.
- r61 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 5651d79 (native-call cache, JIT vector FP and LD1/ST1, scalar FP and conversions, round-to-odd FP32 FMA; u3 units + the optional-data contract). No recipe change.
- r60 2026-11-11 Input row `hao_runtime` (26B131) now names the payload built with hao-runtime 790720d (CoreUtils va_list rows, Optional-witness fix, u3 interface units). No recipe change.
- r59 2026-11-11 26B131 payload rebuilt with hao-runtime 7415a0b from a root that includes the App and OS cryptexes: 963 programs embedded (937 before; the 26 new ones are in the cryptexes, embedded by golden-gate-userspace r49), and `usr/lib/hao/stub-identities` (hao-runtime 53d26c3). Boot validation pending.
- r58 2026-11-11 Select the 26B131 payload with regenerated interface units and AOT; async provider and URLRequest.httpBody adapter remain open. Boot validation pending.
- r57 2026-11-10 Initial public release.
