# rec-display-ethernet

Gives the Recovery an accelerated display at native resolution and the built-in Ethernet. Apple's Recovery has
framebuffers only for newer GPUs, so on the MacBookPro9,2 it runs on the firmware's boot framebuffer. OCLP restores
the Ivy Bridge framebuffer and HD 4000 kexts in the root-patched System; this patch brings them into the BaseSystem.
Stage `recovery`.

## How it works
- `apply.sh` copies `AppleIntelFramebufferCapri.kext` and `AppleIntelHD4000Graphics.kext` from the root-patched
  System into the BaseSystem's `/System/Library/Extensions`, where user space looks for them.
- `kexts.txt` lists `com.apple.driver.AppleIntelFramebufferCapri`, `com.apple.driver.AppleIntelHD4000Graphics` and
  `com.apple.iokit.AppleBCM5701Ethernet` (Broadcom 57765).

Every recovery patch may ship a `kexts.txt`. [pipeline/stages/recovery.sh](../../pipeline/stages/recovery.sh)
rebuilds `BaseSystemKernelExtensions.kc` with the System's own `kmutil` from the BaseSystem's kext list plus all
`kexts.txt` files; no kext is modified. `kmutil` leaves out kexts that the boot kernel collection already holds
(BCM5701 is one of them).

## Verify
In the Recovery, `kextstat | grep -E 'Capri|HD4000'` lists both drivers and the screen runs at native resolution.

## Validation
NEEDS VALIDATION: every listed bundle ID exists in the 25G332 System and the kernel collection builds; the display has
not been checked in a booted Recovery built from this repository.

## Revisions
- r1 2026-11-10 Initial public release.
