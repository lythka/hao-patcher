# golden-gate-windowserver-filter

Adapts two native WindowServer rendering paths on Golden Gate (x86_64): the display-inversion query made while
preparing the Siri bubble's color filter, and deferred backdrop clipping before software rendering. The adapter
library loads through the display-state shim installed by `golden-gate-userspace` and acts only in WindowServer
with an x86_64 SkyLight and QuartzCore listed in `src/profiles.h` (26A428 and 26B131).

## Root cause

**Display inversion.** QuartzCore's `prepare_one_filter` handles the `colorInvertDisplayAware` filter atom by
asking the render context's `OGL::Display` adapter for its native display and calling
`CA::WindowServer::Display::actual_inverse_color() const` (vtable slot 124 in 26A428, 126 in 26B131). Apple's x86_64 base and
VirtualDisplay implementations return false. SkyLight's legacy render path, however, passes its stack-allocated
Metal render context to `CARenderOGLRenderDisplayWithOptions`, and that context's first pointer is
`quartzcore_vtable`: a 14-entry C API table filled by `WSInitialize`, not a C++ display vtable. The slot reads
past its end into adjacent globals, which are zero, so WindowServer calls address zero and crashes. The type
confusion is in Apple's x86_64 code (unchanged from 26A428 to 26B131); it is not an OCLP MetalOld or hao fault.

**Deferred backdrops.** SkyLight's software `layer_tile_traverse` calls `CARenderUpdateAddedAllContexts` before
dispatching drawing workers. When the update's will-clip-backdrops flag is set, QuartzCore defers completion of
backdrop invalidations, and the software path reaches drawing without completing that pass. WindowServer then
aborts in `CA::OGL::prepare_layers_roi` with the finalized bit clear and a null invalidation array.

## How it works

`src/filter_shim.c` interposes `CARenderOGLRenderDisplayWithOptions` and `CARenderUpdateAddedAllContexts`. In
the WindowServer process (`__SLSERVER,__slserver` marker) it reads the loaded SkyLight and QuartzCore UUIDs and
looks each up in `src/profiles.h`. Without a match the adapters stay off and the unified log names what is
missing (`windowserver-filter: MISSING: x86_64 SkyLight image` or `windowserver-filter: unsupported: no profile
for SkyLight UUID ...`); with one it logs `windowserver-filter: adapters enabled for SkyLight BUILD, QuartzCore
BUILD`. Every other process and caller passes through.

- Inversion: when the context carries SkyLight's exact display-callback and API-table pointers, the shim
  substitutes, for that call only, a stack table with the original API entries and a false-returning function at
  the profile's inverse slot, calls Apple's render function synchronously and restores the pointer. The context
  is otherwise unchanged, so callback writes survive. This supplies Apple's default non-inverted answer; the
  filter's own color matrix and `inputReversed` handling are kept, and Siri and color effects stay enabled.
- Backdrops: for the identified software caller, after the original call and only if completion is still
  deferred, the shim calls `CARenderUpdateClipBackdropInvalidations(update, NULL)`. QuartzCore treats null as an
  infinite clip region, keeps all invalidations, builds the array and publishes completion itself. The shim does
  not set the finalized bit, suppress the assertion or touch the drawing workers.

No executable pages or global tables are modified.

### Profiles

Each build's values are image-relative and valid only for its UUIDs. `src/derive.py` finds them by symbol name in
that build's x86_64 SkyLight and QuartzCore (local symbols survive `ipsw dyld macho -x` extraction) and checks
each against the instruction that uses it, so a new build needs no hand-read offsets:

```sh
python3.14 -I patches/golden-gate-windowserver-filter/src/derive.py --build BUILD --dsc /path/dyld_shared_cache_x86_64
```

It prints the two lines to add to `src/profiles.h`, or stops with `MISSING: <what>`.

| Value | Found as | 26A428 | 26B131 |
|---|---|---|---|
| API table | `_quartzcore_vtable`; entry count from `WSInitialize`'s `cmpq $0x70` fill loop | `0x398e4a40`, 14 | `0x3ddcb470`, 14 |
| Display callbacks | `metal_core_animation_display_vtable`, passed in `%rdx` to the render call in `__MetalCompositeLayer_block_invoke` | `0x398d85b0` | `0x3ddbb848` |
| Software caller | return address of `layer_tile_traverse`'s only `CARenderUpdateAddedAllContexts` call | `0x361271` (+0x59) | `0x366e9b` (+0x59) |
| Inverse slot | index of `Display::actual_inverse_color` in `CA::WindowServer::Display`'s vtable; `prepare_one_filter` calls it | 124 (`*0x3e0`) | 126 (`*0x3f0`) |
| Update flags | `testb $0x1, OFF` before `finalize_backdrop_invalidations` in `CARenderUpdateClipBackdropInvalidations2`; `prepare_layers_roi` tests `$0x4, OFF` | `0x543` | `0x553` |

From 26A428 to 26B131 (SkyLight 953.5.5 to 957.8.8, QuartzCore 1223.2.3 to 1223.10.17) the code of both paths
is unchanged in shape; in QuartzCore `actual_inverse_color` moved two vtable slots and the `CA::Render::Update`
flag word moved 0x10 bytes (0x540 to 0x550; bits 0 and 2 of its fourth byte keep their meaning).

### Installation

The WindowServer launcher's 48 bytes of load-command padding are already used by `dss_shim.dylib`, so
`src/kc-hook.sh` adds a dependency on `/usr/lib/windowserver_filter.dylib` to **that dylib**
(`src/load_shim.py`), re-signs it and installs the filter library. Existing display-state interposition and
launcher entitlements are preserved. The edit is declared in `edits/dss-shim.json` (one required `LC_LOAD_DYLIB`,
timestamp 2, versions 1.0.0, then an ad-hoc signature with the shim's own identifier) and the installed library in
`patch.json` `files`; `tests/test_edits.py` proves the engine writes the same bytes as `load_shim.py`. The hook is
unchanged. `src/install_dss.py` installs the display-state shim;
`golden-gate-userspace` uses it through input row `display_install_helper`. Run this patch after `golden-gate-userspace`.

## Build

`make` builds the shim into a new artifact folder (`windowserver-filter-BUILD-DATE-HASH12/`, BUILD being the last
profile, with the x86_64 ad-hoc-signed dylib and `build.json`, which lists the supported builds and UUIDs); point
input row `windowserver_filter_shim` at the dylib. One library serves every build in `src/profiles.h`. The
[display-fixes](../../tools/display-fixes/README.md) tool builds this library together with the display-state shim
and can prepare and install both outside the image pipeline.

```sh
python3.14 patches/golden-gate-windowserver-filter/make /path/to/artifacts
```

Needs Xcode clang/SDK and codesign. Compiler or linker changes can change the output bytes.

## Verify

```sh
python3.14 -I -m unittest discover -s patches/golden-gate-windowserver-filter/tests
# WINDOWSERVER_FILTER_DSS_SHIM=PATH (26B131 dss_shim.dylib): the edit spec also runs on the real shim
```

The native x86_64 harness reproduces the out-of-bounds read without the adapter at slots 124 and 126, then checks
the false answer, all fourteen callbacks, context writes, restoration, reentry and passthrough with it. The
backdrop test covers every flag value at both flag offsets with matching and unmatched callers. Gate tests cover
the profile table (26A428 keeps its validated values), architecture mismatch and a non-WindowServer process. With
`WINDOWSERVER_FILTER_DSC="26A428=/path/cache 26B131=/path/cache"` (needs `ipsw`) the profile test re-derives
every listed build from its own shared cache and requires the lines in `src/profiles.h`.

On a built image, `otool -L /usr/lib/dss_shim.dylib` lists `/usr/lib/windowserver_filter.dylib` and
`codesign --verify --strict` succeeds for both dylibs. On the running system, the unified log contains
`windowserver-filter: adapters enabled for SkyLight BUILD, QuartzCore BUILD` and
`windowserver-filter: first legacy render completed with inversion adapter`, and WindowServer keeps its PID through
Setup and login with no new crash report at `prepare_one_filter` or `prepare_layers_roi`.

## Validation
Laptop 2026-11-11, image 4d1c48c (26B131): desktop, menus, Dock, Liquid Glass, login window and the Spotlight panel draw correctly.
Earlier: Host tests pass, and in QEMU WindowServer stays on its first launch with the backdrop adapter
installed (26A428, r4). r5: the 26A428 profile reproduces r4's values exactly; the 26B131 profile is derived and
checked statically only: no 26B131 image has run it (OPEN). Correct rendering on the HD 4000, the Siri bubble and display inversion under accessibility settings
are not yet proven; restart rendering artifacts after a WindowServer crash are outside this patch.

## Revisions
- r7 2026-11-12 Declare install attributes in patch.json `files` (root:wheel 0755 and `restricted` on both libraries), as the hook sets them, so overlays carry them; the hook is unchanged. Keeps r5's validation: what it installs is unchanged (validated_digest).
- r6 2026-11-12 Declare the display-state shim's load command and the installed library in edits/dss-shim.json and patch.json files; the hook is unchanged. Keeps r5's validation: what it installs is unchanged (validated_digest).
- r5 2026-11-11 Per-UUID profiles (`src/profiles.h`) derived by symbol (`src/derive.py`) replace the pinned 26A428
  offsets; adds 26B131. r4 was silently inert there (UUID gate), and its slot 124 and flag byte 0x543 would be wrong:
  26B131 QuartzCore calls `actual_inverse_color` at slot 126 and keeps the flags at 0x553. A missing image or
  unlisted UUID is now logged.
- r4 2026-11-10 Initial public release.
