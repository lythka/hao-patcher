# Building an image

hao-patcher installs **macOS 27.0 (26A428) first**, then adds the Intel components
MacBookPro9,2 needs. Tahoe provides only the donor files the image lists; it is not
installed as the base system.

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

List every input that the patch set uses:

```sh
python3.14 -I pipeline/patches.py plan pipeline/sets/golden-gate.txt \
  pipeline/targets/mbp92.json 25G332
```

## 2. Build

```sh
python3.14 -I pipeline/golden-run pipeline/assemblies/mbp92-golden-first.json \
  --repo /path/to/new-image-repository
```

The [assembly recipe](../pipeline/assemblies/mbp92-golden-first.json) selects the
target, the Golden Gate and donor builds, image sizes, the patch set and the donor
inputs. The default `golden-gate` set contains only compatibility fixes.

The build runs these steps inside a fresh builder VM clone
([`golden-image.py`](../pipeline/stages/golden-image.py)):

1. Create a new APFS disk and copy Golden Gate's System volume into it, then add
   the Data, Preboot, Recovery and VM volumes.
2. Add the Tahoe extension tree, the Kernel Debug Kit's Intel kernel extensions,
   the Tahoe native donors and the Golden Gate x86_64 kernel.
3. Install the Tahoe boot and Recovery files and the EFI partition.
4. Apply the OCLP hardware overlay.
5. Run each `oclp-hook` patch's `hook.sh`.
6. Build the kernel collections around the Darwin 27 kernel (`darwin27-kernel`).
7. Install the Golden Gate userspace repairs and Tahoe DriverKit
   (`golden-gate-userspace`).
8. Run each remaining patch's `kc-hook.sh`, including hao (`hao-runtime`).
9. Update Preboot, bless the System volume and create its snapshot.
10. Run the `post` patches against the read-only System, such as the OpenCore
    configuration (`hao-opencore`).

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
