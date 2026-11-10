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

The `HaoExec` kext and the boot configuration are installed separately by [hao-opencore](../hao-opencore/README.md).
Use a payload built without the optional AOT preseed caches for the default profile.

## Verify
Check the payload's source and OS identities in its `build.json` and the installed `usr/lib/hao/install.json`, then
boot the image in [QEMU](../../tools/qemu-vm/README.md) before installing it on hardware. hao's own unit tests live in
hao-runtime and do not replace image boot and application checks.

## Validation
NEEDS VALIDATION: earlier payloads booted Golden Gate through translated launchd to the desktop, and a Golden
Gate-first image reached SSH; the current profile needs a fresh image and application pass.

## Revisions
- r57 2026-11-10 Initial public release.
