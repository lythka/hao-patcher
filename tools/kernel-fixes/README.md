# kernel-fixes

Kernel and platform repairs that let the Darwin 27 (macOS 27) x86_64 kernel run on a
MacBookPro9,2 with Tahoe's Intel kexts. The `kernel-fixes` command builds the IntelCommpage kext,
generates the OpenCore kernel patches for early VM setup, binds both to a specific kernel
collection and installs them into an offline ESP. Other models require separate validation.

## How it works

macOS 27 ships no x86_64 kexts, so the image runs the 27.0 kernel with Tahoe (25G330) kexts. The
gaps between them are closed in two places:

- **IntelCommpage.kext** (source in [`../kernel-foundation/src`](../kernel-foundation/src)) is
  loaded by OpenCore alongside Lilu. It restores the Intel commpage from the macOS 26.5 kernel,
  adapts the 27.0 executable and fixed-address mapping paths, maps 27.0's Sandbox operation and
  MIG routine numbering onto Tahoe's Sandbox kext, registers the AMFI, Sandbox and
  EndpointSecurity policy hooks those kexts expect, snapshots IOSurface property input, and
  fixes up misaligned legacy-SSE memory operands in user code.
- **Early-VM repairs** are OpenCore `Kernel/Patch` entries generated from a captured layout of
  the built Boot kernel collection (`early_vm.py`, `pv_bootstrap.py` in
  [`../kernel-foundation`](../kernel-foundation)).

Every address, prefix and table these need comes from the reviewed definitions in
[`profiles/26A428/`](profiles/26A428). Each definition binds the exact target and donor
binaries by hash or UUID, so a build with different Apple inputs stops instead of guessing
offsets. `profile.json` lists the definitions with their sha256. `sandbox-layout` and
`iosurface-layout` regenerate two of them from new inputs; the others (AMFI, entry points,
rename, early VM) come from binary analysis and have no generator.

`vendor/` holds the minimal third-party source the kext compiles against: four Lilu headers,
a subset of MacKernelSDK headers and XNU's `vm_shared_region.c`, with upstream commits and file
hashes in [`vendor/provenance.json`](vendor/provenance.json). No Apple binary, kernel collection
or Lilu binary is included; the image's OpenCore loads Lilu at boot.

Requirements: Python 3.9 or later, Xcode command-line tools (clang, ld, codesign) and Git.

## Usage

Run from any directory; every input is given explicitly.

Build IntelCommpage:

```sh
python3 -I tools/kernel-fixes/kernel-fixes commpage-kext \
  --kernel /path/to/kernel-27.0 --boot-kc /path/to/BootKernelExtensions.kc \
  --donor-amfi /path/to/AppleMobileFileIntegrity \
  --donor-sandbox /path/to/Sandbox \
  --donor-endpoint /path/to/EndpointSecurity \
  --out /path/to/new-commpage-artifact
```

The output holds the signed `IntelCommpage.kext`, the generated sources and objects in `build/`,
and `build.json` with input and output hashes.

Build the kernel repairs and install them into a mounted, writable offline ESP:

```sh
python3 -I tools/kernel-fixes/kernel-fixes build-repairs \
  --boot-kc /path/to/BootKernelExtensions.kc \
  --commpage-artifact /path/to/commpage-artifact --output /path/to/new-repairs

python3 -I tools/kernel-fixes/kernel-fixes install-kernel \
  --esp-root /path/to/esp --boot-kc /path/to/BootKernelExtensions.kc \
  --artifact /path/to/repairs
```

`install-kernel` adds only the repairs that match the collection's hash and copies the kext; the
rest of the ESP's OpenCore config is kept. It never writes a raw disk or changes the boot device.

Other subcommands (each takes `--help`):

| Command | Purpose |
|---|---|
| `sandbox-layout`, `iosurface-layout` | regenerate those profile definitions from new Apple inputs |
| `repair-binding profile`, `repair-binding bind` | rebind the repairs to a freshly linked collection (below) |
| `early-kexts SYSTEM ESP ROOTS_FILE` | inject Tahoe display and network kexts missing from the collections ([`patches/hao-opencore`](../../patches/hao-opencore)) |
| `sandbox-rule SYSTEM_SB OUTPUT_SB` | add the disk-space Sandbox rule ([`patches/darwin27-sandbox-disk-space`](../../patches/darwin27-sandbox-disk-space)) |

## Per-image collection binding

Each image links its own Boot kernel collection. `kmutil` gives it a new enclosing UUID and
embeds the build directory in codeless-kext paths, while the compiled members stay identical.
`repair-binding` lets the reviewed repairs follow such a collection without a new capture:

```sh
python3 -I tools/kernel-fixes/kernel-fixes repair-binding profile \
  --artifact /path/to/original-repairs \
  --boot-kc /path/to/original-BootKernelExtensions.kc \
  --metadata-kc /path/to/reference-BootKernelExtensions.kc \
  --abi-header /path/to/commpage-artifact/build/commpage_abi.hpp \
  --output /path/to/new-binding-profile

python3 -I tools/kernel-fixes/kernel-fixes repair-binding bind \
  --profile /path/to/binding-profile/profile.json \
  --artifact /path/to/original-repairs \
  --boot-kc /path/to/image/BootKernelExtensions.kc \
  --output /path/to/new-image-repairs
```

`profile` records the original collection's members and the metadata differences of the
reference collection (`metadata-review.json`); its output is the `kernel_repair_binding` input.
`bind` accepts a collection only if every fileset member (UUID, load commands, segment bytes)
matches the original and the prelink metadata differs only in the enclosing UUID and the
build-directory prefix of codeless bundle paths. A changed code layout stops the bind.
[`patches/hao-opencore`](../../patches/hao-opencore) runs `bind` for every image build.

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/kernel-fixes/tests
python3.14 -I -B -m unittest discover -s tools/kernel-foundation/tests
```

The tests build and sign small host harnesses for the kext's adapters, check that
`build-repairs` and `install-kernel` reproduce the generator's patches and keep unrelated config
entries, and check the vendored source and profile definitions against their recorded hashes.
They never load a kext, boot a VM or touch a disk. Tests that need Apple binaries are skipped
unless these variables name them: `DARWIN27_BOOT_KC`, `DARWIN27_TARGET_KERNEL`,
`DARWIN27_IOSURFACE_DONOR`, `DARWIN27_TAHOE_SANDBOX`, `DARWIN27_ROSETTA_CACHE`.

## Limitations

- Kernel-only recipes are selected by kernel hash (26A428 and 26B131). A missing or ambiguous identity stops with `MISSING`. `complete-profile` finishes the recipe against the new Boot KC, target cache and Tahoe kexts; it does not need an older OS image. New kernels require a measured recipe and fresh boot testing.
- Host tests do not show that a kext and repair combination boots; test it in QEMU
  ([`tools/qemu-vm`](../qemu-vm/README.md)) and then on hardware.

Third-party terms: [NOTICE.md](NOTICE.md).

## Completing a kernel recipe

`port-profile` is a maintainer tool: it compares symbols and relocation-masked
instruction sequences to derive a new kernel recipe. Its output records every
comparison and marks collection-dependent definitions pending. It never installs
an unfinished recipe. To build with an existing recipe:

```sh
python3.14 -I tools/kernel-fixes/kernel-fixes complete-profile \
  --profile tools/kernel-fixes/profiles/26B131 --kernel KERNEL --boot-kc BOOT_KC \
  --dyld-cache X86_CACHE --donor-sandbox SANDBOX --donor-iosurface IOSURFACE \
  --out NEW_PROFILE
```

Pass `--profile NEW_PROFILE` and `--dyld-cache X86_CACHE` to `commpage-kext`.
The commpage implementation comes from the vendored XNU source. A separate older
kernel binary is not needed. The builder verifies the target kernel and Sandbox
cache identity before compiling.

## Revisions
- r42 2026-11-13 `build-repairs` records its inputs (capture, collection, commpage artifact) as the repository names them (`store/...`), or by file name when outside it, instead of absolute paths: a published artifact's build.json names no home folder. The repairs and the kext copy are unchanged.
- r40 2026-11-11 Select kernel recipes by identity; derive 26B131 definitions from checked symbols/code; complete collection layouts from the current Boot KC and bind Sandbox to the actual userspace cache. Remove the unused older-kernel input. Host tests and static comparisons pass; 26B131 boot validation pending.
