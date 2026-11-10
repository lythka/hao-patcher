# golden-gate-windowserver-filter

Adapts two native WindowServer rendering paths on Golden Gate (26A428 x86_64): the display-inversion query made
while preparing the Siri bubble's color filter, and deferred backdrop clipping before software rendering. The
adapter library loads through the display-state shim installed by `golden-gate-userspace` and acts only in
WindowServer with the exact 26A428 x86_64 SkyLight and QuartzCore.

## Root cause

**Display inversion.** QuartzCore's `prepare_one_filter` handles the `colorInvertDisplayAware` filter atom by
asking the render context's `OGL::Display` adapter for its native display and calling
`CA::WindowServer::Display::actual_inverse_color() const` (vtable slot 124). Apple's x86_64 base and
VirtualDisplay implementations return false. SkyLight's legacy render path, however, passes its stack-allocated
Metal render context to `CARenderOGLRenderDisplayWithOptions`, and that context's first pointer is
`quartzcore_vtable`: a 14-entry C API table filled by `WSInitialize`, not a C++ display vtable. Slot 124 reads
past its end into adjacent globals, which are zero, so WindowServer calls address zero and crashes. The type
confusion is in Apple's 26A428 x86_64 code; it is not an OCLP MetalOld or hao fault.

**Deferred backdrops.** SkyLight's software `layer_tile_traverse` calls `CARenderUpdateAddedAllContexts` before
dispatching drawing workers. When the update's will-clip-backdrops flag is set, QuartzCore defers completion of
backdrop invalidations, and the software path reaches drawing without completing that pass. WindowServer then
aborts in `CA::OGL::prepare_layers_roi` with the finalized bit clear and a null invalidation array.

## How it works

`src/filter_shim.c` interposes `CARenderOGLRenderDisplayWithOptions` and `CARenderUpdateAddedAllContexts`. It
requires the WindowServer `__SLSERVER,__slserver` marker and both image UUIDs (QuartzCore
`0AD33CC9-75A1-359D-BC41-4EE25BE4F599`, SkyLight `02A9A202-2F39-3204-86DE-A09EA6AE8DEE`); every other process
and caller passes through.

- Inversion: when the context carries SkyLight's exact display-callback and API-table pointers, the shim
  substitutes, for that call only, a stack table with the original fourteen entries and a false-returning
  function at entry 124, calls Apple's render function synchronously and restores the pointer. The context is
  otherwise unchanged, so callback writes survive. This supplies Apple's default non-inverted answer; the filter's
  own color matrix and `inputReversed` handling are kept, and Siri and color effects stay enabled.
- Backdrops: for the identified software caller, after the original call and only if completion is still
  deferred, the shim calls `CARenderUpdateClipBackdropInvalidations(update, NULL)`. QuartzCore treats null as an
  infinite clip region, keeps all invalidations, builds the array and publishes completion itself. The shim does
  not set the finalized bit, suppress the assertion or touch the drawing workers. The caller return address and
  flag offset are valid only for the UUIDs above.

No executable pages or global tables are modified.

The WindowServer launcher's 48 bytes of load-command padding are already used by `dss_shim.dylib`, so
`src/kc-hook.sh` adds a dependency on `/usr/lib/windowserver_filter.dylib` to **that dylib**
(`src/load_shim.py`), re-signs it and installs the filter library. Existing display-state interposition and
launcher entitlements are preserved. `src/install_dss.py` installs the display-state shim;
`golden-gate-userspace` uses it through input row `display_install_helper`. Run this patch after `golden-gate-userspace`.

## Build

`make` builds the shim into a new artifact folder (`windowserver-filter-26A428-DATE-HASH12/` with the x86_64
ad-hoc-signed dylib and `build.json`); point input row `windowserver_filter_shim` at the dylib. The
[display-fixes](../../tools/display-fixes/README.md) tool builds this library together with the display-state shim
and can prepare and install both outside the image pipeline.

```sh
python3.14 patches/golden-gate-windowserver-filter/make /path/to/artifacts
```

Needs Xcode clang/SDK and codesign. Compiler or linker changes can change the output bytes.

## Verify

```sh
python3.14 -I -m unittest discover -s patches/golden-gate-windowserver-filter/tests
```

The native x86_64 harness reproduces the out-of-bounds read without the adapter, then checks the false answer, all
fourteen callbacks, context writes, restoration, reentry and passthrough with it. The backdrop test covers every
flag value with matching and unmatched callers. Gate tests cover UUID and architecture mismatches and a
non-WindowServer process.

On a built image, `otool -L /usr/lib/dss_shim.dylib` lists `/usr/lib/windowserver_filter.dylib` and
`codesign --verify --strict` succeeds for both dylibs. On the running system, the unified log contains
`windowserver-filter: first legacy render completed with inversion adapter`, and WindowServer keeps its PID through
Setup and login with no new crash report at `prepare_one_filter` or `prepare_layers_roi`.

## Validation

NEEDS VALIDATION. Host tests pass, and in QEMU WindowServer stays on its first launch with the backdrop adapter
installed. Correct rendering on the HD 4000, the Siri bubble and display inversion under accessibility settings
are not yet proven; restart rendering artifacts after a WindowServer crash are outside this patch.

## Revisions
- r4 2026-11-10 Initial public release.
