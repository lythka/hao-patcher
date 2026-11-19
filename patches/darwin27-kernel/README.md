# darwin27-kernel

Boots the Intel Mac or PC on Apple's own macOS 27 x86_64 kernel: 27.0 (26A428) or 27.2.1 (26B131), whichever build the
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
Gate's own `/usr/bin/kmutil`. The files are declared in `patch.json` `files`: the kernel as an origin of row `darwin27_kernel`, each collection as a
`kc.link` spec ([edits/boot-kc.json](edits/boot-kc.json), [edits/system-kc.json](edits/system-kc.json)) naming kmutil's
inputs as the hook passes them, the member list, and the two Preboot copies; `tests/test_edits.py` checks the specs against
`src/kc-hook.sh`. The runner keeps a copy of this exact BootKC in the build record
(`guest-output/guest/BootKernelExtensions.kc`), from which the `dev-golden-bootstrap` set derives `darwin27_boot_kc`.

The early-boot repairs in [hao-opencore](../hao-opencore/README.md) are bound to the BootKC this recipe produces for
each build (row `darwin27_boot_kc`: 26A428 sha256 6494d8e3..., 26B131 c8ae8b05...). The recipe, its build path
`/.darwin27-kc-build` (linked into the BootKC) and the Tahoe `kmutil` must therefore not change without regenerating
those repairs ([tools/kernel-foundation](../../tools/kernel-foundation/README.md)).

## Inputs
- `darwin27_kernel`: the kernel file, copied unchanged from the verified IPSW (`tools/kernel-foundation/make kernel`):
  26A428 sha256 ce09da26..., 26B131 sha256 3c931751... (row in `pipeline/inputs/golden/26B131`).
- `tahoe_kmutil`: Tahoe's collection linker, used only in builder scratch space.
- `tahoe_kc_builder`: Tahoe's x86_64 `libKernelCollectionBuilder.dylib`, cut out of its OS cache by
  `pipeline/unpack/kc-builder`. Used only on an Intel build host, where the hook's `kmutil` copy loads it
  (`src/kmutil_builder.py`); the builder VM does not read it.

## Verify
Host tests: `python3.14 -I -B -m unittest discover -s patches/darwin27-kernel/tests` (`DARWIN27_KERNEL_KERNEL` and
`DARWIN27_KERNEL_BOOT_KC` add the real-input check that the BootKC links the row kernel).

On the built image, `System/Library/Kernels/kernel` has the `darwin27_kernel` row's sha256, and the BootKC is the same
file in System and Preboot. A BootKC from the builder VM has the `darwin27_boot_kc` row's sha256; one linked on an
Intel host with Tahoe's builder differs, and hao-opencore binds its repairs from that BootKC (`kc_bind.py`). On
the Mac, `uname -v` reports the build's Darwin 27 version and `kmutil showloaded` lists the model's Ethernet and
display drivers.

## Validation

Laptop 2026-11-19, image 8305fdd (26B131, built from fb6542df with r15): boots to the desktop with automatic login, no panic. The same day a hands-free install of the fb6542df release kit on a second laptop booted its kernel and brought the network up.

## Revisions
- r16 2026-11-19 Validated r15 on hardware (Validation). Nothing it installs changes.
- r15 2026-11-18 README names no model or GPU (S15). Nothing it installs changes.
- r14 2026-11-17 boot-kc.json's reason names no GPU (GEN1, S15). Declaration only.
- r13 2026-11-17 Status product (was experiment): it ships in the release profiles. Declaration only; `validated` stays false until a boot proves the re-cut. Nothing it installs changes.
- r12 2026-11-17 Declares its scope (`requires`, docs/requires.md): its rows are the 26A428 (25G332) and 26B131 (25G342) kernel collections. Nothing it installs changes.
- r11 2026-11-16 Declares its pack (base): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r10 2026-11-14 With `prelinked/prelinked.json` in its folder (a kit for the USB installer: golden-run `--stage-kit` copies the collections a builder VM build linked from the same tree, `pipeline/prelinked_kc.py`), the hook checks that prelinked.json lists exactly the three files, that they were linked around this kernel (`kernel_sha256` against `inputs/darwin27_kernel`, which equals the target's) and each file's sha256, and installs them instead of running kmutil: in the USB installer's Recovery kmutil links a kernel-only collection. Without that folder it links as before; a kernel-only BootKC now stops it with a message (it used to end on grep's status, silently). Both ways the build folder is removed first, so a resumed install finds none from the failed run. Builds in the builder VM install the same bytes as r9.
- r9 2026-11-13 Documentation only: Inputs lists `tahoe_kc_builder` (added in r8). The hook is unchanged.
- r8 2026-11-13 On an Intel host (`uname -m` x86_64: a hao image, Golden Gate's Recovery) kmutil links in the running system's libKernelCollectionBuilder, Golden Gate's, which refuses x86_64 there ("unsupported arch: x86_64", "Unexpected __HIB segment"). The hook now makes its kmutil copy a thin x86_64 kmutil that loads Tahoe's builder from the new row `tahoe_kc_builder` (`src/kmutil_builder.py`, no developer tools needed). The builder VM (arm64) path is unchanged.
- r7 2026-11-12 Declare the kernel's install mode, 0644 (golden-pristine.sh installs it `-m 644`); the hook is unchanged. Keeps r5's validation: what it installs is unchanged (validated_digest).
- r6 2026-11-12 Declare the kernel, both kernel collections (kc.link) and their Preboot copies in `patch.json` `files` and edits/; the hook is unchanged.
- r5 2026-11-11 The kernel file is installed once, by the base layer (golden-pristine.sh finalize, before the hardware overlay that reads it); the kc-hook now checks it is `inputs/darwin27_kernel` instead of copying it again, and creates the System and Preboot KernelCollections folders itself. Runs as an ordinary set entry. Collection recipe unchanged. Build and boot validation pending.
- r4 2026-11-11 Use the measured 26B131 Intel kernel with the 25G342 Tahoe donor kexts. Boot validation pending.
- r3 2026-11-10 Initial public release.
