# darwin27-kernel

Boots the MacBookPro9,2 on Apple's macOS 27.0 (26A428) x86_64 kernel. The 27.0 IPSW still ships
`System/Library/Kernels/kernel` for Intel (Darwin 27.0.0, xnu-13432.1.9~1/RELEASE_X86_64, UUID
8f0fa4f8-8dc2-3768-a7eb-04e58c6d1b88), but no x86_64 kexts, so the image links it with Tahoe's Intel drivers.

## How it works
The kc-hook runs after OCLP's root patch. It installs the kernel unchanged and builds the Boot and System kernel
collections for it with Tahoe's `kmutil` (the `tahoe_kmutil` input, re-signed ad hoc so it runs in the builder),
then copies the Boot collection to Preboot. The target keeps Golden Gate's own `/usr/bin/kmutil`.

The early-boot repairs in [hao-opencore](../hao-opencore/README.md) are bound to the exact BootKC this recipe produces
(sha256 6494d8e3...). The recipe, its build path `/.darwin27-kc-build` (linked into the BootKC) and the Tahoe
`kmutil` must therefore not change without regenerating those repairs
([tools/kernel-foundation](../../tools/kernel-foundation/README.md)).

## Inputs
- `darwin27_kernel`: the kernel file (sha256 ce09da26...), copied unchanged from the verified IPSW
  (`tools/kernel-foundation/make kernel`).
- `tahoe_kmutil`: Tahoe's collection linker, used only in builder scratch space.

## Verify
On the built image, `System/Library/Kernels/kernel` has sha256 ce09da26... and the BootKC has sha256 6494d8e3... in
both System and Preboot (hao-opencore's `repairs.py` stops the build otherwise). On the Mac, `uname -v` reports
Darwin 27.0.0 and `kmutil showloaded` lists the Ethernet and HD 4000 drivers.

## Validation
NEEDS VALIDATION: the collection recipe reproduces BootKC 6494d8e3, and this kernel has booted Tahoe userspace to SSH
on a MacBookPro9,2 and Golden Gate userspace to launchd in QEMU; a hardware boot of a Golden Gate image to SSH with
the current revision is open.

## Revisions
- r3 2026-11-10 Initial public release.
