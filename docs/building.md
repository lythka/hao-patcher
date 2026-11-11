# Building an image

hao-patcher installs **macOS 27 first** (27.0 26A428 by default; 27.2.1 26B131 in
development), then adds the Intel components MacBookPro9,2 needs. Tahoe provides only
the donor files the image lists; it is not installed as the base system.

## Requirements

- An Apple silicon Mac with the Xcode command-line tools, Python 3.14 and enough free
  disk space. The recipe reserves an 80 GiB target image and a 160 GiB sparse
  workspace. These are maximum sizes, not the expected disk usage.
- An arm64 macOS virtual machine for disk operations, run by
  [`tools/ssdvm`](../tools/ssdvm/README.md) and managed by `pipeline/vm/`.
- The downloads and prepared inputs in [Build inputs](inputs.md).

Formatting, APFS operations and kernel-collection builds run only inside the VM,
never on the host.

## 1. Prepare the inputs

1. Download the archives in [Build inputs](inputs.md#downloads) and check their
   SHA256 sums.
2. Build each prepared input with the tool listed in
   [Prepared inputs](inputs.md#prepared-inputs). For example,
   [`tools/tahoe-donors`](../tools/tahoe-donors/README.md) extracts the Tahoe
   donors, and [`tools/oclp-hardware`](../tools/oclp-hardware/README.md) builds the
   hardware overlay.
3. Point the rows under `pipeline/inputs/` at your files (see
   [Input rows](inputs.md#input-rows)).

List every input that the patch set uses, in run order:

```sh
python3.14 -I pipeline/patches.py plan pipeline/sets/golden-gate.txt \
  pipeline/targets/mbp92.json 25G332
python3.14 -I pipeline/patches.py plan pipeline/sets/dev-bs-test.txt \
  pipeline/targets/mbp92.json 25G342      # the 26B131 development set
```

## 2. Build

```sh
python3.14 -I pipeline/golden-run pipeline/assemblies/mbp92-golden-first.json \
  --repo /path/to/new-image-repository
```

The [assembly recipe](../pipeline/assemblies/mbp92-golden-first.json) selects the
target, the Golden Gate and donor builds, image sizes, the patch set and the donor
inputs. The default `golden-gate` set contains only compatibility fixes.

### Layer order

The build runs inside a fresh builder VM clone
([`golden-image.py`](../pipeline/stages/golden-image.py), list `LAYERS`). Each layer
lands on top of the ones before it. The base layers are fixed and the same for every
set:

| # | Layer | What it adds | Why |
|---|---|---|---|
| 1 | `layout` | New raw image, GPT, APFS container, System volume | Empty target |
| 2 | `golden-system` | Golden Gate's System volume, from the build's `filesystem.dmg` | The OS itself; copied while System is the only volume so APFS keeps its identity |
| 3 | `group-volumes` | Data (grouped with System), Preboot, Recovery, VM; the ESP mounted | macOS volume layout |
| 4 | `golden-finalize` | Data template and firmlinks (27's own); Tahoe `System/Library/Extensions` and the KDK's Intel kexts; the Darwin 27 x86_64 kernel file; Tahoe native roots | 27 ships no x86_64 kexts and lacks some x86_64 programs and plug-ins. The kernel file is installed here, once, because the next layers read it |
| 5 | `boot-and-esp` | Tahoe's Intel Preboot and Recovery files; the OpenCore ESP from `build-efi.py` | 27 has no Intel boot files; the ESP already holds the OpenCore DEBUG binaries |
| 6 | `hardware` | OCLP's MacBookPro9,2 overlay (HD 4000 Metal/OpenGL, wireless, audio) | Legacy hardware support; it checks the kernel file's Darwin version |

Which frameworks the Tahoe native roots replace depends on the donor artifact. The
26B131 artifact (`minimal-native-25G342-26B131`) keeps 27.x's Metal, OpenCL,
AppleGVA, AppleGVACore, GPUCompiler, MTLCompiler, IO80211 and WiFiPeerToPeer under
OCLP's overlay; the 26A428 artifact installs Tahoe's copies of those first.

Then come the set's recipes, **strictly in set-file order**. Each `oclp-hook` recipe
runs once, through its own `src/kc-hook.sh SYSTEM`, with System writable:

7. `darwin27-kernel` links the Boot and System kernel collections around the kernel
   file and the Intel kexts. The runner keeps a copy of this exact BootKC in the
   build record.
8. `golden-gate-userspace` builds the combined OS cryptex (x86_64 dyld cache, Tahoe
   DriverKit, cache repairs) and installs the IO80211/WiFiPeerToPeer compatibility
   libraries and the display shim.
9. Every other `oclp-hook` entry, in the order the set lists them, each with a
   comment saying what it adds and why (`pipeline/sets/*.txt`).

After the recipes:

10. `snapshot`: Preboot update, bless and the System snapshot.
11. `posts`: each `post` entry's `apply.sh` in set order, with System read-only, such
    as the OpenCore configuration (`hao-opencore`).
12. `efi-bundle`: the final ESP and its manifest.

`pipeline/golden-run` refuses a set that breaks this, before staging further:
the first two `oclp-hook` entries must be `darwin27-kernel` then
`golden-gate-userspace` (every later recipe edits what they produce), a recipe may not
use `src/hook.sh` (the Tahoe-first runner's OCLP hook), and no `oclp-hook` entry may
follow a `post` entry. `patches.py plan` therefore prints the true run order.

The guest detaches the completed image and flushes its workspace. After the VM
stops, the host mounts that workspace **read-only** and captures the image directly
with [`ssdimg`](../tools/ssdimg). This avoids copying an 80 GiB raw disk through
the VM shared folder. Formatting, kernel collections and all writes to APFS
volumes remain inside the builder VM.

A full build also creates `boot-efi.zip` beside `image.json`. It contains the
prepared ESP payload plus a manifest identifying the model, OS build, kernel
collection and every EFI file. This is the final EFI after our OpenCore additions,
for installation after restoring the matching OS image. Installing it does not
require rerunning OCLP root patching. Regenerating a stock OCLP EFI would discard
our additions. Intermediate builds without `hao-opencore` do not publish this
installation archive.

If assembly completed but export/capture was interrupted, stop that builder and
resume capture with `python3.14 -I pipeline/golden-run --capture-record RECORD`.
The command requires the successful assembly record and refuses a running VM.

## 3. Check the output

| Location | Contents |
|---|---|
| `--repo` | The image repository |
| `store/runs/` | Build record: selected inputs, patch plan, source identities and logs |
| `store/tmp/` | Per-run workspace and intermediate disk images |

Read the build record, check the filesystem results, then boot the image in
[QEMU](../tools/qemu-vm/README.md) before installing it on hardware.

## Recovery

`pipeline/recovery TARGET BUILD recovery` builds the offline Recovery installer
from the Tahoe BaseSystem and a root-patched System image. The `rec-*` recipes in
`patches/` add its hardware support and the image installer. A complete end-user
installation workflow for Golden Gate images is still in development.

The Recovery build takes its root-patched System from a `stock` image, which only
the older Tahoe-first runner, `pipeline/run`, builds. That runner installs Tahoe and
runs OCLP's own root patcher; it is kept only for this. Golden Gate images are built
with `pipeline/golden-run`.

## Tests

The host tests use fixtures and do not start a VM:

```sh
python3.14 -I -B -m unittest discover -s pipeline/tests
python3.14 -I -B -m unittest discover -s tools/tahoe-donors/tests
python3.14 -I -B -m unittest discover -s tools/oclp-hardware/tests
```

[CONTRIBUTING.md](../CONTRIBUTING.md) lists the remaining test suites.
