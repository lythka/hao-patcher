# golden-gate-metal27

Dev only, in no default set: every process on the image, WindowServer included, uses macOS 27.2.1's own x86_64
Metal (383.1) on the Intel HD 4000 instead of OCLP's wrapper over MetalOld (13.2.1, 306). The pieces are
[tools/metal-adapter](../../tools/metal-adapter/README.md)'s, which ran compute, render and reflection on the laptop
in one test process (r4); this recipe installs them for the whole system as a trial. Pack
[`metal27-trial`](../../packs/metal27-trial.json) (opt in, never detected); set `dev-metal27` for an image variant.

## What it installs

| File on System | What | Replaces |
|---|---|---|
| `System/Library/Frameworks/Metal.framework/Versions/A/Metal` | Metal27: 27.2.1's Metal cut from the 26B131 x86_64 cache (pinned `dbb5e8e8…`), plus `LC_REEXPORT_DYLIB @loader_path/libHaoMetalShim.dylib`, ad hoc signed; the laptop bundle's file byte for byte (`e0d38ca0…`) | OCLP's wrapper, with [golden-gate-hello](../golden-gate-hello/README.md)'s edits |
| `…/Metal.framework/Versions/A/libHaoMetalShim.dylib` | the shim, installed build: the ivar offsets OCLP's driver and 31001 bind, the three descriptor structs in MetalOld's layout for the driver, the in-process compiler switch (on by default) | new |
| `System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/32023/MTLCompiler`, `…/32024/MTLCompiler` | the stand-in, installed build: 27's in-process compiles routed to OCLP's 31001 (AIR downgrade, frontend) and 3802 + the Intel plugin, 3802's reflection in 27's layout | new (cache images only) |

All four come from row `metal27_installed`, a `tools/metal-adapter/make.py installed` result whose `build.json`
names every file's hash, the commit and the inputs. All are root:wheel 0755 `restricted`, as Apple's.

**How a cache image gets replaced.** Metal and both MTLCompiler paths are images of the x86_64 dyld cache.
dyld loads the file at such a path instead of the cache's copy because it counts as an internal install
([golden-gate-dyld-internal](../golden-gate-dyld-internal/README.md); before hao-opencore r52 the `ic_csr_internal=1`
boot argument did this for every process); OCLP's wrapper is loaded the same way today. Metal27 exports what the cache's Metal exports (it is that image), so every cache image
bound to Metal binds to it; nothing in the cache links MTLCompiler 32023/32024 (27's Metal `dlopen`s them).

**The compiler, for every process.** 27's Metal compiles through `MTLCompilerService` unless
`MTLEnvVarAggregator::isInternalBuild` and `MTL_MONOLITHIC_COMPILER=1`. The image's service is OCLP's 13.2.1
build, which knows only 3802 and 31001 and crashes on 27's 32023 requests (laptop run 2, `xpc-*`). So the installed
shim's constructor, in every process that loads Metal, sets the cached `isInternalBuild` answer and
`MTL_MONOLITHIC_COMPILER=1` (unless already set) before Metal reads them, and every process compiles in process
through the stand-in. No launchd environment and no per-process setting is needed; `HAO_METAL27_INTERNAL=0` in a
process's environment turns it off there (it then gets the XPC path, which fails).

Both installed builds are silent (`HAO_SHIM_LOG=1`, `HAO_STANDIN_LOG=1` print to stderr). A shim refusal (another
Metal, MetalOld loaded, the flip's instruction pattern not where it should be) goes to the unified log, subsystem
`org.hao.metal27`, and leaves the process alone instead of ending it.

## What stays from OCLP's stack

| Piece | Under 27's Metal |
|---|---|
| OCLP 3802 MTLCompiler + `AppleIntelHD4000GraphicsMTLDriver` and its plugin chain | used as they are; the stand-in calls 3802 in process |
| OCLP 31001 MTLCompiler and GPUCompiler (AIR downgrade, frontend) | used as they are (helper preloaded by absolute path) |
| `libmetalold-selectors.dylib`, `libmetal-framebuffer.dylib` (weak dylibs in the driver) | unchanged; both loaded into the r4 laptop run with the driver, and render passed through the framebuffer hook |
| [golden-gate-metal-libraries](../golden-gate-metal-libraries/README.md) metallibs, `runtime.igil64.split`, `iCBE.dylib` | unchanged: the same 31001 → 3802 path compiles them |
| `MetalOld.dylib` | stays on disk; nothing loads it (only the wrapper linked it) |
| OCLP's `MTLCompilerService` and [metal-compiler-compat](../metal-compiler-compat/README.md)'s `compat.dylib` | unused: no process compiles through the service. Runtime-generated shaders (MPSGraph, Espresso: Live Text) lose compat's repairs |
| golden-gate-hello's wrapper edits, `libhelloblur.dylib` | gone with the wrapper: Setup's own hello logic runs; nothing weak-loads `libhelloblur`, so [golden-gate-live-text-shared](../golden-gate-live-text-shared/README.md)'s runtime is not attached |
| Moraea's per-app swizzles (`_coreimageSetup`, `_webglSetup`, `_glassSetup`, …) | gone with the wrapper; 27's Metal has the methods they replaced, the 22 `addImp` selectors natively |
| [golden-gate-shader-caches](../golden-gate-shader-caches/README.md) | MetalOld's caches; 27's Metal keys its own, so they are misses (first draws compile) |

## Live trial on the bench laptop (`hao patch`)

On the host, from this branch with `store/` present:

```sh
python3.14 -I pipeline/materialize.py pipeline/sets/dev-metal27.txt pipeline/targets/mbp92.json 25G342 \
    --out OUT --packs metal27-trial --golden 26B131
mkdir LAPTOP_DIR && cp -R OUT/metal27-trial LAPTOP_DIR/      # only the overlay: not OUT/deferred.json
```

`OUT/metal27-trial/manifest.json` must list the four files with `build.json`'s hashes. Copy `LAPTOP_DIR` and
`tools/hao` to the laptop (a tarball; `tools/hao` must include this branch's live classifier), then, as root:

```sh
hao patch --probe                                         # csr must include 0x10 (e.g. 0x813) and 0x802
hao patch LAPTOP_DIR/metal27-trial --live --dry-run       # 4 to write, nothing listed as "later"
hao patch LAPTOP_DIR/metal27-trial --live --i-have-a-backup
reboot
```

After the reboot, over SSH:

- `pgrep -l WindowServer` and the login window or desktop on screen;
  `lsof -p $(pgrep -n WindowServer) | grep -E 'Metal.framework|MTLCompiler.framework|GPUCompiler.framework'`: dyld
  opened the on-disk Metal and shim and, once WindowServer has compiled, the 32023 or 32024 stand-in, 3802 and
  31001 (a cache image is never an open file, so a stand-in path listed here was loaded from disk); no
  `MetalOld.dylib`;
  `ls /Library/Logs/DiagnosticReports /var/log/DiagnosticMessages 2>/dev/null | grep -i -E 'WindowServer|MTLCompiler'`: no new report;
- `log show --last boot --predicate 'subsystem == "org.hao.metal27"'`: no refusal, no stand-in fault;
- apps drawing: Finder, Dock and its Apps grid, Control Center, Safari, Maps (3D), Photos; then
  `ls -t /Library/Logs/DiagnosticReports ~/Library/Logs/DiagnosticReports | head`;
- the adapter's own checks against the installed Metal, with no `DYLD_*` (the r4 bundle's test program, unpacked in
  the user's home): `HAO_SHIM_LOG=1 HAO_STANDIN_LOG=1 bin/metal-adapter-test add1 lib/kernels-27.metallib`, then
  `render-color lib/render-27.metallib` and, with `HAO_REFLECTION=1`, `render-texture lib/render-11.metallib`: PASS,
  the shim's flip line and the stand-in's requests in the output.

**Rollback:** `hao patch --revert --live --i-have-a-backup`, then reboot: the snapshot that ran before the patch boots
again, with OCLP's wrapper. If WindowServer does not come up, the revert runs the same way over SSH (dev-access). If
the Mac does not boot at all: the OpenCore picker's previous snapshot is not offered, so boot the Recovery or another
disk and push the bench image again.

## Image variant

`pipeline/assemblies/dev-26B131-metal27.json` is `dev-26B131.json` with set `dev-metal27` (dev-bs-test plus this
recipe, before golden-gate-native-identities) and its own image repository. `src/kc-hook.sh` runs `src/install.py`,
which refuses a System other than the one it was made for (bases in `src/bases.json`: OCLP's wrapper with or without
hello's edits; OCLP's 3802, 31001, the 31001 helper and the HD 4000 driver present; no other file at the stand-in
paths) and writes the stand-ins, the shim, then Metal. To go back on such an image: apply the `metal-3802` pack's
`Metal` (OCLP's wrapper with hello's edits) with `hao patch --live`; the stand-ins and the shim are then unused.

## Verify

```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-metal27/tests
GOLDEN_GATE_METAL27_ARTIFACT=store/artifacts/metal27-installed-26B131-20261113-d399c6f3 \
GOLDEN_GATE_METAL27_OCLP_METAL=OCLP_ARTIFACT/payload/13.2.1-25/System/Library/Frameworks/Metal.framework/Versions/A/Metal \
    python3.14 -I -B -m unittest discover -s patches/golden-gate-metal27/tests
python3.14 -I -B -m unittest discover -s tools/metal-adapter/tests     # METAL_ADAPTER_INSTALLED=ROW_FOLDER adds the payload
```

The declaration (four whole files, install order), the opt-in pack and that no default set selects it; that the
hello-edited wrapper and OCLP's are the accepted bases; `install.py --check`'s refusals on synthetic folders; with
the row, `build.json` against its files and the bundle's Metal.

## Validation

NEEDS VALIDATION: never installed. The pieces passed on the laptop in one test process (tools/metal-adapter r4);
the installed builds passed the host replay (same requests as r4, silent). Not yet booted as the system's Metal.

## Revisions
- r2 2026-11-13 README: dyld loads cache roots because golden-gate-dyld-internal makes it an internal install (no ic_csr_internal boot argument since hao-opencore r52). No file change.
- r1 2026-11-13 New dev-only trial: 27.2.1's own Metal for every process (Metal27, the installed shim and stand-in from tools/metal-adapter d399c6f3), pack metal27-trial, set dev-metal27.
