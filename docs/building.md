# Building an image

The base is macOS 27 (26A428; 26B131 in development). Tahoe supplies only the donor files the
inputs list.

## Requirements

- Apple silicon Mac, Xcode command-line tools, Python 3.14. The image is 80 GiB and the
  workspace 160 GiB, both sparse.
- Builder VM: [`tools/ssdvm`](../tools/ssdvm/README.md), `pipeline/vm/`. All formatting, APFS
  writes and kernel-collection builds run in it, never on the host.
- Inputs from [Build inputs](inputs.md).

## 1. Prepare the inputs

1. Download the [archives](inputs.md#downloads) and check their SHA256.
2. Build the [prepared inputs](inputs.md#prepared-inputs) with their tools (e.g.
   [`tahoe-donors`](../tools/tahoe-donors/README.md),
   [`oclp-hardware`](../tools/oclp-hardware/README.md)).
3. Point the rows in `pipeline/inputs/` at them ([Input rows](inputs.md#input-rows)).

`plan` prints a set's run order and every input it needs:

```sh
python3.14 -I pipeline/patches.py plan pipeline/sets/golden-gate.txt pipeline/targets/mbp92.json 25G332
python3.14 -I pipeline/patches.py plan pipeline/sets/dev-bs-test.txt pipeline/targets/mbp92.json 25G342  # 26B131
```

## 2. Build

```sh
python3.14 -I pipeline/golden-run pipeline/assemblies/mbp92-golden-first.json --repo /path/to/image-repository
```

The [assembly](../pipeline/assemblies/mbp92-golden-first.json) selects target, OS and donor
builds, image sizes, donor rows and the patch set.

### Layer order

Defined by `LAYERS` in [`golden-image.py`](../pipeline/stages/golden-image.py). Each layer goes
on top of the previous ones. Layers 1–6 are the same for every set.

| # | Layer | Adds | Why |
|---|---|---|---|
| 1 | `layout` | Raw image, GPT, APFS container, System volume | |
| 2 | `golden-system` | macOS 27 System from `filesystem.dmg` | Copied while System is the only volume, so APFS keeps its identity |
| 3 | `group-volumes` | Data, Preboot, Recovery, VM; ESP mounted | |
| 4 | `golden-finalize` | 27's Data template and firmlinks; Tahoe and KDK Intel kexts; Darwin 27 x86_64 kernel; Tahoe native roots | 27 has no Intel kexts and lacks some x86_64 code. Later layers read the kernel |
| 5 | `boot-and-esp` | Tahoe Intel Preboot/Recovery files; OpenCore ESP (`build-efi.py`) | 27 has no Intel boot files |
| 6 | `hardware` | OCLP overlay: HD 4000 Metal/OpenGL, wireless, audio | Checks the kernel's Darwin version |
| 7 | `recipes` | Each `oclp-hook` entry's `src/kc-hook.sh`, in set order, System writable | |
| 8 | `snapshot` | Preboot update, bless, System snapshot | |
| 9 | `posts` | Each `post` entry's `apply.sh`, in set order, System read-only | |
| 10 | `efi-bundle` | Final ESP and manifest | |

The donor artifact decides which frameworks the Tahoe native roots replace. The 26B131 one
keeps 27's Metal, OpenCL, AppleGVA(Core), GPUCompiler, MTLCompiler, IO80211 and
WiFiPeerToPeer. The 26A428 one installs Tahoe's.

In layer 7, `darwin27-kernel` links the Boot and System KCs (the record keeps the BootKC);
`golden-gate-userspace` builds the OS cryptex and installs the Wi-Fi compat libraries and the
display shim. Each set entry has a comment saying what it adds and why.

`golden-run` refuses a set unless:
- the first two entries are `darwin27-kernel`, then `golden-gate-userspace` (later recipes
  edit their output);
- no recipe uses `src/hook.sh`;
- no `oclp-hook` entry follows a `post` entry.

### Output

The guest detaches the image. The host mounts the stopped VM's workspace read-only and commits
the image with [`ssdimg`](../tools/ssdimg), with no copy through the shared folder.

With `hao-opencore` in the set, `boot-efi.zip` sits beside `image.json`. It holds the final ESP
and a manifest of model, OS build, KC and every EFI file. Install it after restoring the
matching image. Don't regenerate a stock OCLP EFI instead: it drops the OpenCore additions.

If export was interrupted, resume with `golden-run --capture-record RECORD`. This needs a
successful assembly record and a stopped VM.

| Location | Contents |
|---|---|
| `--repo` | Image repository |
| `store/runs/` | Build record: inputs, plan, source identities, logs |
| `store/tmp/` | Workspace and intermediate disks |

Boot the image in [QEMU](../tools/qemu-vm/README.md) before installing it on hardware.

## Recovery

`pipeline/recovery TARGET BUILD recovery` builds the offline Recovery from the Tahoe BaseSystem
and a root-patched System; the `rec-*` recipes add hardware support and the installer. The
System comes from a `stock` image, which only the legacy Tahoe-first runner `pipeline/run`
builds. That is the only reason the legacy runner is kept.

## Tests

The host tests use fixtures and start no VM:

```sh
python3.14 -I -B -m unittest discover -s pipeline/tests
python3.14 -I -B -m unittest discover -s tools/tahoe-donors/tests
python3.14 -I -B -m unittest discover -s tools/oclp-hardware/tests
```

Other suites: [CONTRIBUTING.md](../CONTRIBUTING.md).
