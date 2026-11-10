# golden-gate-metal-framebuffer

Restores Metal fragment framebuffer reads (color attachment inputs) on the legacy
Intel graphics stack. Golden Gate's RenderBox symbols (Wi-Fi, Control Center, Setup
icons) read their attachments this way; the legacy backend accepts such shaders
but does not implement the reads. The adapter recognizes AIR input metadata, not
applications, framework exports or shader state.

## How it works

[build/](build/README.md) builds the `metal_framebuffer` input,
`libmetal-framebuffer.dylib`. The kc-hook installs it after `golden-gate-userspace`
and adds a weak dependency on it to the legacy HD 4000 Metal driver; the dylib
hooks pipeline factories without creating a device from the driver's initializer.

Before pipeline creation the adapter lowers specialized framebuffer inputs into
attachment-texture reads and builds a new Metal library whose hash is derived from
its content, so Metal produces fresh reflection and cache keys. Encoders bind the
actual attachments and order each primitive; wrapper buffers preserve base vertex,
base instance and primitive IDs.

Because runtime shader keys change, shader-pack records for these pipelines must be
regenerated from actual requests and appended to the pack; old shader binaries are
never relabelled.

## Verify

Run the host and legacy-compiler tests and the offscreen GPU probe matrix in
[build/](build/README.md). On a built image, check that WindowServer and
iconservicesagent map `/usr/lib/libmetal-framebuffer.dylib`, that the menu-bar and
Control Center symbols render, and that no process loading it crashes.

## Validation

NEEDS VALIDATION. The adapter passes the HD 4000 probe matrix and renders the real
Wi-Fi symbol correctly, and Setup renders with it installed. The full desktop
(Control Center, login window) and shader-pack records for the transformed requests
remain open.

## Revisions

- r2 2026-11-10 Initial public release.
