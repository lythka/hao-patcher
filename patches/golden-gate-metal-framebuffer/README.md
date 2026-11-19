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

Laptop 2026-11-19, a MacBookPro9,2: image 8305fdd (26B131, built from fb6542df with r13) reached the desktop with WindowServer, Dock and Control Center up, and none of them nor iconservicesagent wrote a crash report over a 48-app sweep. The release-EFI image 8acc07f (same recipes) drew Setup Assistant, the menu-bar and Control Center symbols and the desktop with 0 crash reports. On the release-EFI image 8acc07f: WindowServer and iconservicesagent map `libmetal-framebuffer.dylib` (4 regions each).

## Revisions
- r14 2026-11-19 Validated r13 on hardware (Validation). Nothing it installs changes.
- r13 2026-11-17 Row `metal_framebuffer`: lowering always uses the stack's own libLLVM (no environment override). Same command at fe579412, same bytes.
- r12 2026-11-17 `requires`: os 26A428/26B131, donor 25G332/25G342, gpu intel-ivb (the HD 4000), cpu and firmware any; replaces `applies`. status product: it ships in the release profiles (what it installs is unchanged; `validated` stays false until the re-cut's laptop boots prove it).
- r11 2026-11-16 Declares its pack (intel-hd4000): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r10 2026-11-16 Rows `metal_framebuffer` are the 2845387b build of the current source, which the same command reproduces byte for byte; against f8ff3547 only internal argument and variable names differ.
- r9 2026-11-15 build/tests: the AIR version lookup's linearity test compares 4x the input against the base size instead of a fixed 5 s, so it holds on a loaded host. What the recipe installs is unchanged.
- r8 2026-11-14 Input row `metal_framebuffer` now hashes only the files a release can give a user's Mac (`# hash: release-files`: its published files plus the made-on-Mac ones, pipeline/lock.py scope()), so its sha256 changed. The same artifact, so what this recipe installs is unchanged.
- r7 2026-11-13 Input rows record their content sha256 (folder rows sealed with `lock.py seal`); the same artifacts at the same paths, so what this recipe installs is unchanged.
- r6 2026-11-12 Declare install attributes in patch.json `files` (root:wheel 0755 and `restricted` on the driver and the library), as the hook sets them, so overlays carry them; the hook is unchanged.
- r5 2026-11-12 Declare where each installed file is inside its row ("source") for pipeline/materialize.py; the hook is unchanged.
- r4 2026-11-12 Declare the driver's weak load command in `edits/driver.json` and the two files in `patch.json` `files`; the hook is unchanged.
- r3 2026-11-11 README: runs in set order after metalold-selectors; built for 26A428, not yet checked on 26B131. Recipe unchanged.

- r2 2026-11-10 Initial public release.
