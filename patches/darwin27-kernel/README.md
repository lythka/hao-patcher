# darwin27-kernel

Boots the MacBookPro9,2 on Apple's own macOS 27 x86_64 kernel: 27.0 (26A428) or 27.2.1 (26B131), whichever build the
assembly selects. The 27.x IPSWs still ship `System/Library/Kernels/kernel` for Intel (26A428: Darwin 27.0.0,
xnu-13432.1.9~1/RELEASE_X86_64, UUID 8f0fa4f8-8dc2-3768-a7eb-04e58c6d1b88), but no x86_64 kexts, so the image links
it with Tahoe's Intel drivers.

## How it works
This is the first entry of every Golden Gate set (the runner refuses a set that does not start with it and then
[golden-gate-userspace](../golden-gate-userspace/README.md)): every later recipe edits the collections and userspace
these two produce.

The kernel file itself is a base layer: `pipeline/stages/golden-pristine.sh finalize` installs `inputs/darwin27_kernel`
before OCLP's hardware overlay, which reads its Darwin version. The kc-hook checks that the installed kernel is that
same file (it does not copy it again), builds the Boot and System kernel collections around it with Tahoe's `kmutil`
(the `tahoe_kmutil` input, re-signed ad hoc so it runs in the builder) over the image's Intel driver tree (Tahoe
Extensions, KDK kexts, OCLP's hardware kexts), then copies the Boot collection to Preboot. The target keeps Golden
Gate's own `/usr/bin/kmutil`. The runner keeps a copy of this exact BootKC in the build record
(`guest-output/guest/BootKernelExtensions.kc`), from which the `dev-golden-bootstrap` set derives `darwin27_boot_kc`.

The early-boot repairs in [hao-opencore](../hao-opencore/README.md) are bound to the BootKC this recipe produces for
each build (row `darwin27_boot_kc`: 26A428 sha256 6494d8e3..., 26B131 c8ae8b05...). The recipe, its build path
`/.darwin27-kc-build` (linked into the BootKC) and the Tahoe `kmutil` must therefore not change without regenerating
those repairs ([tools/kernel-foundation](../../tools/kernel-foundation/README.md)).

## Inputs
- `darwin27_kernel`: the kernel file, copied unchanged from the verified IPSW (`tools/kernel-foundation/make kernel`):
  26A428 sha256 ce09da26..., 26B131 sha256 3c931751... (row in `pipeline/inputs/golden/26B131`).
- `tahoe_kmutil`: Tahoe's collection linker, used only in builder scratch space.

## Verify
On the built image, `System/Library/Kernels/kernel` has the `darwin27_kernel` row's sha256 and the BootKC has the
`darwin27_boot_kc` row's sha256 in both System and Preboot (hao-opencore's `repairs.py` stops the build otherwise). On
the Mac, `uname -v` reports the build's Darwin 27 version and `kmutil showloaded` lists the Ethernet and HD 4000
drivers.

## Validation
NEEDS VALIDATION: the collection recipe reproduces BootKC 6494d8e3 (26A428) and c8ae8b05 (26B131 bootstrap), and the
26A428 kernel has booted Tahoe userspace to SSH on a MacBookPro9,2 and Golden Gate userspace to launchd in QEMU; r5
(kernel file no longer copied by the hook) has not been built.

## Revisions
- r5 2026-11-11 The kernel file is installed once, by the base layer (golden-pristine.sh finalize, before the hardware overlay that reads it); the kc-hook now checks it is `inputs/darwin27_kernel` instead of copying it again, and creates the System and Preboot KernelCollections folders itself. Runs as an ordinary set entry. Collection recipe unchanged. Build and boot validation pending.
- r4 2026-11-11 Use the measured 26B131 Intel kernel with the 25G342 Tahoe donor kexts. Boot validation pending.
- r3 2026-11-10 Initial public release.
