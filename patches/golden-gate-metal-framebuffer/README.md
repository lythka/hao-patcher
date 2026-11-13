# golden-gate-metal-framebuffer

Restores Metal fragment framebuffer reads (color attachment inputs) on the legacy
Intel graphics stack. Golden Gate's RenderBox symbols (Wi-Fi, Control Center, Setup
icons) read their attachments this way; the legacy backend accepts such shaders
but does not implement the reads. The adapter recognizes AIR input metadata, not
applications, framework exports or shader state.

## How it works

[build/](build/README.md) builds the `metal_framebuffer` input,
`libmetal-framebuffer.dylib`. The kc-hook runs in set order, after
`metalold-selectors` (both add a weak dependency to the same driver binary), installs
the dylib and adds a weak dependency on it to the legacy HD 4000 Metal driver from
OCLP's hardware overlay; the dylib hooks pipeline factories without creating a device
from the driver's initializer.

Before pipeline creation the adapter lowers specialized framebuffer inputs into
attachment-texture reads and builds a new Metal library whose hash is derived from
its content, so Metal produces fresh reflection and cache keys. Encoders bind the
actual attachments and order each primitive; wrapper buffers preserve base vertex,
base instance and primitive IDs.

The driver edit is declared in [`edits/driver.json`](edits/driver.json) for
[tools/edit-engine](../../tools/edit-engine/README.md), as an edit of `metalold-selectors`' output (it runs first);
`tests/test_edits.py` proves the engine writes the same bytes as `src/add_weak_dylib.py`.

Because runtime shader keys change, shader-pack records for these pipelines must be
regenerated from actual requests and appended to the pack; old shader binaries are
never relabelled.

## Builds

The `metal_framebuffer` input (rows in `pipeline/inputs/25G332` and `25G342`,
artifact `metal-framebuffer-26A428-...`) was built and probed for 27.0 (26A428). It is
not yet checked on 27.2.1 (26B131): the dev-26B131 assembly uses the same artifact,
and nobody has rerun the probe matrix against 26B131's RenderBox shaders.

## Verify

Run the host and legacy-compiler tests and the offscreen GPU probe matrix in
[build/](build/README.md). On a built image, check that WindowServer and
iconservicesagent map `/usr/lib/libmetal-framebuffer.dylib`, that the menu-bar and
Control Center symbols render, and that no process loading it crashes.

Declared edit: `python3.14 -I -B -m unittest discover -s patches/golden-gate-metal-framebuffer/tests`
(`GOLDEN_GATE_METAL_FRAMEBUFFER_INPUT=PATH`, the driver as `metalold-selectors` leaves it, adds the real file).

## Validation

NEEDS VALIDATION. The adapter passes the HD 4000 probe matrix and renders the real
Wi-Fi symbol correctly, and Setup renders with it installed. The full desktop
(Control Center, login window) and shader-pack records for the transformed requests
remain open. Nothing has been checked on 26B131.

## Revisions
- r7 2026-11-13 Input rows record their content sha256 (folder rows sealed with `lock.py seal`); the same artifacts at the same paths, so what this recipe installs is unchanged.
- r6 2026-11-12 Declare install attributes in patch.json `files` (root:wheel 0755 and `restricted` on the driver and the library), as the hook sets them, so overlays carry them; the hook is unchanged.
- r5 2026-11-12 Declare where each installed file is inside its row ("source") for pipeline/materialize.py; the hook is unchanged.
- r4 2026-11-12 Declare the driver's weak load command in `edits/driver.json` and the two files in `patch.json` `files`; the hook is unchanged.
- r3 2026-11-11 README: runs in set order after metalold-selectors; built for 26A428, not yet checked on 26B131. Recipe unchanged.

- r2 2026-11-10 Initial public release.
