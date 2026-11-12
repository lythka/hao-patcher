# metalold-selectors

Adds the Metal selectors that newer macOS frameworks send but OCLP's legacy Metal
stack (MetalOld 306 with the HD 4000 driver) does not answer. A dylib loaded by the
driver bundle adds them to MetalOld's base classes, each with the answer a device
lacking the feature gives. This fixes a class of `unrecognized selector` crashes,
for example USDZ Quick Look and WindowServer's desktop effects.

## Why

Tahoe's and Golden Gate's Metal clients (CoreRE, MetalFX, CMImaging, MPS,
QuartzCore, RenderBox, Tungsten, libswiftMetal and others) are compiled against
Metal 373 or later and send selectors Metal 306 never had. The newer `_MTLDevice`
answers most of these from its feature table; OCLP's MetalOld path has neither those
answers nor the driver's, and callers do not guard the calls. Examples:

- RealityKit's Quick Look extensions call
  `-[MTLIGAccelDevice supportsDynamicAttributeStride]` while building materials.
- 27.0's MPSCore sets `forwardProgressUsage` on every compute pipeline descriptor.
- Residency-set APIs (`newResidencySetWithDescriptor:error:`) are called directly.

A survey of the legacy stack against Metal 373's protocols found 686 selectors not
answered by the class that should answer them; 35 of those are called from product
frameworks, mostly without a `respondsToSelector:` guard. The rest are called only
by Metal debugging and replay tools. Details and tooling: [build/](build/README.md).

## What it changes

| File on System | Change |
|---|---|
| `usr/lib/libmetalold-selectors.dylib` (new) | x86_64 dylib from the `metalold_selectors` input, ad hoc signed. At load it adds each selector in its table that the class does not already answer; it never overrides a method. |
| `System/Library/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver` | One `LC_LOAD_WEAK_DYLIB /usr/lib/libmetalold-selectors.dylib` in the zero padding after the load commands, then re-signed ad hoc. |

The driver edit is declared in [`edits/driver.json`](edits/driver.json) for
[tools/edit-engine](../../tools/edit-engine/README.md); `tests/test_edits.py` proves the engine writes the same bytes
as `src/add_weak_dylib.py`.

Both are installed root:wheel 0755 with the `restricted` flag. Because both are ad hoc
signed, the boot arguments must relax AMFI ([hao-opencore](../hao-opencore/README.md)
does).

The driver bundle is the right host: it is not part of the shared cache, MetalOld
`dlopen`s it to create the device, and every missing selector is sent to an object
that exists only afterwards. The shim is therefore present exactly when the classes
are, and no shared-cache artifact changes. `src/add_weak_dylib.py` refuses unless the
padding is zero and large enough.

What the shim answers, in short: capability queries answer NO (except where the stack
has the capability, such as tier-1 argument buffers); factories for object kinds the
stack lacks return nil with `NSFeatureUnsupportedError`; renamed selectors forward to
their Metal 306 names; residency-set calls are no-ops; dynamic-attribute-stride
encoder calls forward to the static variants; `forwardProgressUsage` and
`shaderValidation` are stored and returned. Selectors with no honest answer are left
out so the call still fails loudly. The full list is in
[build/src/metalold_selectors.m](build/src/metalold_selectors.m).

`METALOLD_SELECTORS=0` in a process's environment disables the shim for that process.

## When it runs

`src/kc-hook.sh` runs in set order like every other recipe. It needs only OCLP's HD 4000 Metal driver bundle, which
the hardware overlay (a base layer) installs, so nothing requires it to run before the kernel collections. The set
lists it just before [golden-gate-metal-framebuffer](../golden-gate-metal-framebuffer/README.md), which adds a second
weak dylib to the same driver binary; that keeps the load-command order of the earlier builds.

## Builds

The `metalold_selectors` input (rows in `pipeline/inputs/25G332` and `25G342`) was built and surveyed for 27.0
(26A428). It is not yet checked on 27.2.1 (26B131): the dev-26B131 assembly uses the same artifact, and nobody has
re-run the survey against 26B131's Metal clients.

## Build

```sh
python3.14 -I patches/metalold-selectors/build/make OUTPUT_PARENT
```

See [build/README.md](build/README.md) for the survey options and outputs.

## Verify

- The build log shows the kc-hook's two SHA-256 lines (step `recipe metalold-selectors`).
- In the image, `otool -L` on the driver lists `/usr/lib/libmetalold-selectors.dylib
  (weak)`, and `codesign -vvv` accepts both files.
- On the target, in a user session: `qlmanage -p` of a system USDZ file shows the
  preview, `log show --predicate 'subsystem == "metalold-selectors"'` has
  `N Metal selectors added` lines, and no RAQL* or WindowServer crash report appears.
- On the host: `python3.14 -I -m unittest discover -s patches/metalold-selectors/build/tests`.
- The declared edit: `python3.14 -I -B -m unittest discover -s patches/metalold-selectors/tests`
  (`METALOLD_SELECTORS_INPUT=PATH`, OCLP's HD 4000 driver, adds the real file).

## Upstream

OCLP is the natural home: the shim is a list of what MetalOld lacks and the honest
answer for each. Its table adds to MetalOld's base classes and does not depend on the
HD 4000 driver, so it could ship in the 3802 Metal payload for every legacy GPU.

## Validation
Laptop 2026-11-11, image 4d1c48c (26B131): the accelerated desktop and the Metal paths of Maps, Safari and Preview run on the HD 4000.
Earlier: Host tests cover every answer, the kill switch and the Mach-O, and
on the target a native probe of `setForwardProgressUsage:` fails without the shim and
succeeds with it. Quick Look of USDZ files and WindowServer's desktop effects on a
built image remain open, and nothing has been checked on 26B131.

## Revisions

- r4 2026-11-12 Declare the driver's weak load command in `edits/driver.json` and the two files in `patch.json` `files`; the hook is unchanged. Keeps r3's validation: what it installs is unchanged (validated_digest).
- r3 2026-11-11 `src/hook.sh` became `src/kc-hook.sh` (same commands): the Golden Gate runner runs every recipe once, in set order; the sets list it before golden-gate-metal-framebuffer, the order it already had in effect. README: built for 26A428, not yet checked on 26B131. Build validation pending.
- r2 2026-11-10 Initial public release.
