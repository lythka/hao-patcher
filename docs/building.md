# Building an image

The base is macOS 27.2.1 (26B131), with Tahoe 25G342 as the donor; the 27.0 (26A428) assembly
`mbp92-golden-first.json` is older and no longer maintained. Tahoe supplies only the donor files
the inputs list.

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
python3.14 -I pipeline/patches.py plan pipeline/sets/golden-gate-26B131.txt pipeline/targets/mbp92.json 25G342 \
  --locks pipeline/inputs/golden/26B131:pipeline/inputs/25G342:pipeline/inputs/common   # 26B131, as golden-run looks up rows
```

### Optional cache recipes

`golden-gate-shader-caches`, `golden-gate-icon-caches` and `golden-gate-font-registry` install
caches made by [`cache/seeds`](../cache/seeds/README.md) from a harvest of a running laptop;
`golden-gate-user-seed` copies their per-user parts into new accounts. Their patch.json has
`"optional": "caches"`. Without these files the image still builds; first boot and each new
account's first login are slower.

- A cache recipe whose input row is missing, or whose `store/` path is not there, is skipped
  with one line naming the recipe and the row. A row that is there but fails its SHA256 stops
  the build.
- `golden-gate-user-seed` (`"needs_any": "caches"`) is skipped when no cache recipe runs.
- To leave them out even when the files exist: `golden-run --skip-optional caches`, or
  `"skip_optional": ["caches"]` in the assembly.
- `store/runs/<job>/optional.json` (and `optional` in `build.json`) lists which ran and which
  were skipped, and why.

## 2. Build

```sh
python3.14 -I pipeline/golden-run pipeline/assemblies/mbp92-26B131.json --repo /path/to/image-repository
```

The [assembly](../pipeline/assemblies/mbp92-26B131.json) selects target, OS and donor
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
`golden-gate-userspace` builds the OS cryptex (with the code identity carrier in the x86_64 cache's
Security.framework) and installs the Wi-Fi compat libraries and the display shim;
`golden-gate-native-identities` runs last, after every recipe that re-signs a program ad hoc, and
lists those programs with their Apple originals for the carrier. Recipes get `TAHOE_DRIVERKIT_ARTIFACT`,
`TAHOE_DONOR_TOOL` and `TAHOE_NATIVE_ARTIFACT` from the runner. Each set entry has a comment saying
what it adds and why.

`golden-run` refuses a set unless:
- the first two entries are `darwin27-kernel`, then `golden-gate-userspace` (later recipes
  edit their output);
- no recipe uses `src/hook.sh`;
- no `oclp-hook` entry follows a `post` entry.

### Output

The guest detaches the image. The host mounts the stopped VM's workspace read-only and commits
the image with [`ssdimg`](../tools/ssdimg), with no copy through the shared folder.

With `hao-opencore` in the set, `boot-efi.zip` sits beside `image.json`. It holds the final ESP,
a manifest of model, OS build, KC and every EFI file, and the OpenCorePkg and OCLP license texts
(`LICENSES/`). Install it after restoring the matching image. Don't regenerate a stock OCLP EFI instead: it drops the OpenCore additions.

If export was interrupted, resume with `golden-run --capture-record RECORD`. This needs a
successful assembly record and a stopped VM.

| Location | Contents |
|---|---|
| `--repo` | Image repository |
| `store/runs/` | Build record: inputs, plan, optional recipes run/skipped, source identities, logs |
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
