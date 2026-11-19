# golden-gate-hd4000-planes

Makes plane textures of planar IOSurfaces read and write the right rows on the HD 4000. Without it, the picture
Photos shows while a thumbnail zooms to the full view has wrong colour in bands, with green blocks, for about half a
second (diag#0025). The same fault is the suspected cause of the lock-screen bands (diag#0024).

## The fault

The HD 4000 Metal driver addresses a texture made from plane N > 0 of an IOSurface
(`-newTextureWithDescriptor:iosurface:plane:`) with plane 0's row pitch. When the two pitches are equal (420f, 420v:
most video) nothing goes wrong. When they differ, every reader (blit, `getBytes`, shader sample) and every GPU
writer of that plane uses the wrong rows. An example is 444f at 1182x664: plane 0 has 1216 bytes per row, plane 1
has 2368.

Measured on the laptop with the plane-texture probe, phase D: 444f,
444f-cv and 420f with plane 1 padded fail; 444f with plane 0 padded to plane 1's pitch, and 420f, pass. Photos
fills such a 444f surface on the GPU at every zoom and hands it to Core Animation's render server (WindowServer),
which reads it.

## What it changes

| File on System | Change |
|---|---|
| `usr/lib/libhd4000-planes.dylib` (new) | x86_64 dylib from the `hd4000_planes` input, ad hoc signed. |
| `System/Library/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver` | One more `LC_LOAD_WEAK_DYLIB /usr/lib/libhd4000-planes.dylib` in the zero padding after the load commands (after metalold-selectors', metal-framebuffer's and metal-argbuf's), then re-signed ad hoc. |

Both are installed root:wheel 0755 with the `restricted` flag, by `src/kc-hook.sh`, in set order after
`golden-gate-metal-argbuf`.

Every process that uses Metal on the HD 4000 loads the driver, so it also loads the library: WindowServer, Photos
and the others. The library wraps `-[MTLIGAccelDevice newTextureWithDescriptor:iosurface:plane:]`. Only for a plane
whose pitch differs from plane 0's, it returns a *shadow*: an ordinary managed texture with the same descriptor,
kept in step with the plane on the CPU, where the surface's own pitch applies:

- It is filled from the plane when made, and again before a command buffer is committed whenever the surface's
  seed has moved (another writer, in this process or another, changed it).
- A shadow made with ShaderWrite or RenderTarget usage is copied back into the plane after every completed command
  buffer whose result differs, under `IOSurfaceLock`/`Unlock`, so the seed moves and readers in other processes
  refill.
- `-iosurface` and `-iosurfacePlane` of a shadow answer the surface and the plane, as the driver's own texture does.

Other planes and surfaces go to the driver unchanged. `HD4000_PLANES=0` in a process's environment turns the
library off there; `HD4000_PLANES_LOG=1` logs each shadow (subsystem `org.hao.hd4000-planes`).

Why the texture side and not padding plane 0 at allocation (which phase D shows would also read right): the
surface is allocated by whichever process creates it, such as Photos through CoreVideo or the video decoder
service, some of which never load the Metal driver. A library the driver loads arrives by `dlopen`, where dyld does
not interpose. Padding would also change the layout every CPU writer and the hardware decoder rely on.

Costs: a CPU copy of the plane each time it changes, only for surfaces whose plane pitches differ (1.5 MB for
Photos' 1182x664 444f). A written shadow is read back and hashed after every command buffer while it is alive.
A reader whose surface is changed without its seed moving would stay stale.

## Build

`python3.14 -I patches/golden-gate-hd4000-planes/build/make store/artifacts` builds
`hd4000-planes-YYYYMMDD-<source sha256 12>/libhd4000-planes.dylib` and `build.json`. The same source and compiler
give the same bytes: the r1 artifact (fee66603...) is the file that ran on laptop 1.

## Verify

- Host: `python3.14 -I -B -m unittest discover -s patches/golden-gate-hd4000-planes/build/tests`. It builds the
  library and checks the bytes, install name and exported surface. With `HD4000_PLANES_PROBE=PATH` (research's
  ycbcr-plane-texture probe) it also runs phase D with the library inserted. Shadows must appear for exactly 444f,
  444f-cv and 420f-p1pad, and the result must be PASS.
- Build log: the kc-hook prints the two sha256 lines (step `recipe golden-gate-hd4000-planes`). The library
  matches the row, and `otool -L` on the driver lists `/usr/lib/libhd4000-planes.dylib (weak)` last.
- Target: `log show --predicate 'subsystem == "org.hao.hd4000-planes"'` has a `hd4000-planes on` line from
  WindowServer. Research's probe with `YCBCR_PROBE_ONLY_D=1` passes all five cases, and fails again with
  `HD4000_PLANES=0`. Zooming a photo in Photos shows no colour bands or green blocks.

## Validation

Laptop 2026-11-19, a MacBookPro9,2 with the release-EFI image 8acc07f (26B131, built from fb6542df with r7): the log has `hd4000-planes on` from WindowServer, which maps the library. Research's probe (3bbe01f9) with `YCBCR_PROBE_ONLY_D=1` passes all five cases; with `HD4000_PLANES=0` it fails 444f, 444f-cv and 420f-p1pad again. The Photos zoom was not run.

## Revisions
- r8 2026-11-19 Validated r7 on hardware (Validation). Nothing it installs changes.
- r7 2026-11-17 The mode argument in src/kc-hook.sh is quoted, like its paths, so no expansion in an install-time script can split at a space. No installed byte changes.
- r6 2026-11-17 Declares its scope (`requires`, docs/requires.md): changes the HD 4000's Metal driver. Nothing it installs changes.
- r5 2026-11-16 Declares its pack (intel-hd4000): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r4 2026-11-15 Declares its installed files (patch.json files). Installed bytes unchanged.
- r3 2026-11-15 Row hd4000_planes hashes the published file only (`# hash: release-files`), so a store made from a release matches it. Installed bytes unchanged.
- r2 2026-11-14 Wording: the probe is named without its repository. Installed bytes unchanged.
- r1 2026-11-14 Initial recipe: libhd4000-planes.dylib and its weak load in the HD 4000 Metal driver (diag#0025).
