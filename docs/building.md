# Building an image

The base is macOS 27.2.1 (26B131), with Tahoe 25G342 as the donor; the 27.0 (26A428) assembly
`mbp92-golden-first.json` is older and no longer maintained. Tahoe supplies only the donor files
the inputs list.

## Requirements

A user makes the USB installer from a release with `hao`: no Apple silicon Mac and no virtual machine (the
[builder path](builder-path.md); its end-to-end proof on an Intel Mac is in progress). This page is the maintainers'
build, which still uses the builder VM:

- Maintainers: an Apple silicon Mac, Xcode command-line tools, Python 3.14. The image is 80 GiB and the workspace
  160 GiB, both sparse. Building our own assets from source also needs Xcode 27.
- Builder VM (maintainers): [`tools/ssdvm`](../tools/ssdvm/README.md), `pipeline/vm/`. All formatting, APFS
  writes and kernel-collection builds run in it, never on the host. Build the launcher with
  `tools/ssdvm/build.sh store/vm/bin`; each build runs in an APFS clone of the VM at `store/vm/base`
  (made with `ssdvm install`, see its README; `pipeline/vm/clones.py` provisions each clone). Provisioning drives the
  clone's screen with `pipeline/vm/vmctl`, which needs Pillow and cryptography in `store/cache/vmctl-venv`
  (`python3.14 -m venv store/cache/vmctl-venv && store/cache/vmctl-venv/bin/python3 -m pip install pillow cryptography`;
  `VMCTL_PYTHON` names another).
- Inputs from [Build inputs](inputs.md). One is a third-party program: blacktop's
  [ipsw](https://github.com/blacktop/ipsw) 3.1.737, which decodes the IPSW's encrypted disk images. `hao fetch`
  downloads and checks it (input row `ipsw_tool`); instead, `brew install blacktop/tap/ipsw` at that version works
  too. Without either, making `golden_gate_userspace` stops and says so.

## 1. Prepare the inputs

1. Download the [archives](inputs.md#downloads) and check their SHA256.
2. Build the [prepared inputs](inputs.md#prepared-inputs) with their tools (e.g.
   [`tahoe-donors`](../tools/tahoe-donors/README.md),
   [`oclp-hardware`](../tools/oclp-hardware/README.md)).
3. Point the rows in `pipeline/inputs/` at them ([Input rows](inputs.md#input-rows)).

### From a release (developers)

A release (`release.json` on [hao-assets](https://github.com/lythka/hao-assets/releases)) pins every input; our own
files are the members of its one archive, `hao-RELEASE.tar`. On an Apple silicon Mac with the builder VM's base at
`store/vm/base` and its tools (Requirements), with the release's four files downloaded into `ASSETS`
(`gh release download TAG -R lythka/hao-assets -D ASSETS`):

```sh
git clone https://github.com/lythka/hao-patcher && cd hao-patcher && git checkout COMMIT   # release.json "hao_patcher"
mkdir -p ../store && ln -s ../store store                                                 # an empty store
python3.14 -I tools/hao/hao fetch ASSETS/release.json --cache ../fetch --assets-from ASSETS
python3.14 -I pipeline/place ASSETS/release.json ../fetch
python3.14 -I pipeline/make-on-mac ASSETS/release.json --cache ../fetch
tools/ssdvm/build.sh store/vm/bin
python3.14 -I tools/hao/hao build-image ../image --model MacBookPro9,2 --assembly ASSEMBLY      # release.json "assembly"
```

- `fetch` downloads Apple's and the third-party files from their own servers and takes our files out of
  `ASSETS/hao-RELEASE.tar`, each checked by size and sha256.
- `place` puts each where its input row reads it and checks it. It ends by listing what is still missing: the
  rows in the release's `made_on_mac` and the harvested caches. The harvested ones are optional and the build
  skips them (Optional cache recipes, below).
- `make-on-mac` makes the made-on-Mac rows from Apple's files (it streams what it needs from the IPSW) and checks each
  against its row; `create-installer` runs the same `place` and `make-on-mac`. It needs the release's pinned Metal
  tools: set `HAO_METAL_LLVM` to the unpacked `metal_llvm` folder, as `sh hao` does.
- `build-image` needs the release's `assembly`: without it, it takes the target's default assembly.

`plan` prints a set's run order and every input it needs:

```sh
python3.14 -I pipeline/patches.py plan pipeline/sets/golden-gate-26B131.txt pipeline/targets/mbp92.json 25G342 \
  --locks pipeline/inputs/golden/26B131:pipeline/inputs/25G342:pipeline/inputs/common   # 26B131, as golden-run looks up rows
```

### Optional cache recipes

`golden-gate-shader-caches`, `golden-gate-icon-caches`, `golden-gate-font-registry` and
`golden-gate-user-template` install caches and templates made by [`cache/seeds`](../cache/seeds/README.md) from a
harvest of a running laptop; `golden-gate-user-seed` copies their per-user parts into new accounts. Their patch.json has
`"optional": "caches"`. Without these files the image still builds; first boot and each new
account's first login are slower.

- A cache recipe whose input row is missing, or whose `store/` path is not there, is skipped
  with one line naming the recipe and the row. A row that is there but fails its SHA256 stops
  the build.
- `golden-gate-user-seed` (`"needs_any": "caches"`) is skipped when no cache recipe runs.
- macOS changes some of these files after install (a Metal shader cache grows on first GPU use). Their `files`
  entries say so with `"content": "seed"` and a `"reason"`; both go into the recipe's install manifest
  (`private/var/db/hao/manifests/RECIPE.json`, and the overlay `manifest.json`), and `hao verify` then checks that
  such a file is there with its mode, owner and flags, not its bytes. `patches.py check` refuses any other
  `content` value and a seed without a reason.
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

### Choosing a profile

An image is named by its profile ([layout](layout.md)): `alpha-v0.1.0` is the MacBookPro9,2 image, `generic` the
software-graphics image for Intel hardware without its own target (target `generic`: model iMac20,1, no board id, no
GPU, Ivy Bridge CPU flags, an OCLP profile with no patch sets; first proof an x86_64 QEMU VM run with
`-cpu IvyBridge`). `hao build-image REPO --profile NAME` builds the one assembly whose `patch_set` is NAME
(`--assembly` must agree); without `--profile` the assembly follows from the Mac's target as before
(`TARGET-GOLDEN`). `--profile` still takes a `hao detect` file: a path, or a name ending in `.json`.

`hao create-installer` offers the generic image only when the release carries it: as the last answer to "Which
Mac", and as a second question when This Mac is a model the release cannot install on (stopping is the default).
`--profile generic` answers it ahead. The generic image is never picked for an unknown Mac on its own; choosing it is
the consent the proof gate otherwise asks for with `--unproven`, and the install record and the USB installer's
launcher keep it as unproven.

`release-cut --write OUT ASSEMBLY [ASSEMBLY ...]` cuts several profiles from one commit into one release: an asset
two profiles share is one file, every file is listed once in `SHA256SUMS`, and `release.json` `profiles` names each
profile's assembly, target, set, recipes and the `rows` (downloads, assets, made-on-Mac rows) it uses. The top level
stays the first profile's image, so a reader that knows no `profiles` sees the MacBookPro9,2 image. create-installer
works from the chosen profile's view of the release (`~/Library/Caches/hao/releases/RELEASE-PROFILE.json`): it
downloads, makes and puts on the USB installer only that profile's rows.

The generic image is `pipeline/profiles/generic.json` (the OS layer plus the opt-in model pack `generic`, whose
`opencore-generic` runs right before `hao-opencore`), its set `pipeline/sets/generic.txt` and its assembly
`pipeline/assemblies/generic-26B131.json`. Its target selects no OCLP patch sets, so `golden-run` stages no
`hardware` input and golden-image's step `hardware` installs nothing (the configuration's `hardware` is null); an
assembly whose `hardware` input disagrees with its target's patch sets is refused. The base ESP is always the one
`stages/build-efi.py` makes from the target's `efi_build` (never a harvested ESP or another target's): the generic
target's is `"base": "qemu_reference"`, the `qemu_reference_efi` tree (OpenCore tested on OVMF, started from
`EFI/BOOT/BOOTx64.efi`) with the pinned OpenCore release's binaries, not OCLP's iMac20,1 build (the 5K iMac's
Apple-firmware boot layout, which non-Apple firmware cannot start). See [layout](layout.md#target-esp-base).
`qemu_reference_efi` is made on the user's Mac by `pipeline/unpack/qemu-reference` from the rows `opencore_release`,
`oclp_source`, `ocbinarydata_hfsplus` and `virtualsmc_release`, with the tracked `pipeline/unpack/qemu-reference.plist`
(the tested config without a machine identity: opencore-generic makes one per image).

Before writing anything, `golden-run`, `hao build-image --here`, `tools/qemu-vm` and the builder VM's start check the
free disk space (`pipeline/diskguard.py`): a build needs about 175 GB (staged inputs ~82 GB, the workspace ~66 GB, the
image commit ~23 GB; an assembly can say more with `disk_need_gb`), a kit alone 90 GB, a QEMU run the size of the disks
it boots, and each refuses when it would leave less than 150 GB free (`HAO_DISK_FLOOR_GB`). `HAO_DISK_CHECK=COMMAND`
hands the decision to a host-wide guard (`COMMAND check --need GB --who NAME --what TEXT`, exit 3 = refused).

`golden-run` (also with `--stage-kit`, which the USB installer's kit comes from) and `release-cut --write` refuse to start
while a live swap proven on a laptop has no resolution. The swapper records the proof when a bench pass or the owner
shows the swap works: `python3.14 -I pipeline/swaps.py proven LAPTOP-PATH --evidence "bench ITEM"` (or
`"owner: THEIR WORDS"`), which adds a `proven` line to the swap ledger. The refusal names each path, its sha256, who
swapped it and who proved it. It is resolved by a line in `pipeline/live-swaps.tsv` (`recipe NAME`, `row NAME`, or
`dropped: WHY`); there is no override. `swaps.py status` lists every swap (`OPEN`, `PROVEN-OPEN`, `resolved`).

The VM runs the same `pipeline/stages/golden-assemble.sh` that `hao install` runs in the target's Recovery.
That Recovery (Tahoe 25G342) has fewer commands than a full macOS. The ones it has are listed in
`pipeline/stages/recovery-commands-25G342.txt`. `install`, `shasum`, `cmp` and `readlink` are missing, and the
kit's `recovery-tools.py` stands in for them. `rsync` is missing too, so the assembly uses `ditto`.
`pipeline/tests/test_recovery_commands.py` checks every command the install path runs against that list.
`golden-run --recovery-commands` builds in the VM with only those commands on PATH, as the USB installer's install does.
The assembly also holds a sleep assertion for its whole run (`caffeinate`, or `recovery-tools.py awake`).
Copies onto the FAT ESP carry data only (`FAT_COPY` in `golden-image.py`: `ditto --norsrc --noextattr --noacl`):
the Recovery runs no `doubleagentd`, which keeps a FAT volume's extended attributes, so a copy with attributes fails
there. `pipeline/tests/test_fat_esp.py` runs those copies on a real FAT32 disk image, with only the Recovery's
commands on PATH (`HAO_TEST_KIT=KIT` adds a staged kit's ESP).

The Recovery cannot link the kernel collections: its `kmutil` links a kernel-only collection for the target volume
(it reads none of its kexts). So a builder VM build stores the Boot and System collections it linked in
`store/cache/kc/KEY/` ([`prelinked_kc.py`](../pipeline/prelinked_kc.py): KEY covers the input rows and sources that
shape System up to `darwin27-kernel`). `golden-run --stage-kit` copies the entry with the kit's key into the kit's
`darwin27-kernel` hook folder (`prelinked/`), and the hook checks each file's sha256 and installs them instead of
linking. `hao create-installer` refuses a kit without them: build the image once in the VM first. Producing them
without the VM is open (see the VM-free build plan).

The Recovery mounts the USB installer's HAO volume `noowners`, so every file the install copies from the kit arrives owned
by `_unknown` (99:99). `golden-image.py` gives such files root:wheel after the base layers, the recipes and the
posts, and records the counts in `build.json` `owners_fixed`. kmutil refuses kexts with those owners, which is why
the Recovery linked a kernel-only collection before. In the builder VM there are none.

A failed assembly can continue at its failed step, on the same disk, when that step can run again over what the
failed attempt left (`RESUMABLE` in `golden-image.py`: `boot-and-esp` and `hardware`):
`golden-image.py CONFIG --resume WORK`, `golden-assemble.sh ... --resume WORK`, or
`hao install /dev/diskN --resume /Volumes/HAO/hao-install-DATE` (confirmed with RESUME, not ERASE). It finds the
volumes again by their UUIDs, refuses a kit whose configuration differs (set, target, inputs; the files a
kit carries are not compared), and skips nothing that did not complete. The record's `resumed`
names the run it continued.

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
| 7 | `recipes` | Each `oclp-hook` entry's `src/kc-hook.sh` (or, with `--from-overlays`, its files from the overlays), in set order, System writable | |
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
`TAHOE_DONOR_TOOL` and `TAHOE_NATIVE_ARTIFACT` from the runner. Every hook and post also gets the target:
`HAO_TARGET_MODEL` (its `model`) and `HAO_TARGET_DATA` (the kit's `target-data.json`, its `platform_data`, `{}` when
none; [layout.md](layout.md#target-platform-data)). Each set entry has a comment saying
what it adds and why.

The sealed OS and App cryptex images are kept between builds in `store/cache/cryptex` (the two newest, about 13 GB
each). Their key covers the rows they are made from, the code that makes them and the builder's APFS tools
([golden-gate-userspace](../patches/golden-gate-userspace/README.md), "Cryptex cache"); a build with the same key
copies them (sha256-checked) instead of rebuilding. `build.json` `cryptex_cache` says `hit`, `miss` or `stored`;
`--no-cryptex-cache` always builds them.

### Planning a build from the last one

Every build records `fingerprints` in `build.json`: the base (what every step before the set's recipes reads) and,
per set entry, its staged code and the identity of each input row (only the declared parts of a folder row the
recipe reads in part, `patch.json` `input_reads`). With `--from PREVIOUS` (the image repository the last build
committed, or its record) the run also records `incremental`: the entries that changed since that build and the saved
point it could start from (the image just before an `oclp-hook` recipe, or before the snapshot, keyed on everything
that ran up to there). A different base, no saved point at or before the first change, or a record without
fingerprints means a full build. The guest cannot start from a point yet, so every build still runs in full
(`incremental.used` false). `pipeline/incremental.py plan PREVIOUS NEW` prints the same plan for two records.

`golden-run` refuses a set unless:
- the first two entries are `darwin27-kernel`, then `golden-gate-userspace` (later recipes
  edit their output);
- no recipe uses `src/hook.sh`;
- no `oclp-hook` entry follows a `post` entry.

### Installing from the overlays (opt-in)

```sh
python3.14 -I pipeline/golden-run pipeline/assemblies/dev-26B131.json --repo /path/to/image-repository \
  --from-overlays --overlay-system /path/to/golden-gate-fs
```

`--overlay-system` is the build's Golden Gate file system (its `filesystem.dmg` attached read-only): the edits
start from its files. Before the VM starts, the host materialises the set
([`materialize.py`](../pipeline/materialize.py); the edit-engine store is kept in `store/cache/edit-engine-store`,
`--overlay-store` to change it) and [`overlays.py`](../pipeline/overlays.py) plans, in set order, which recipes
install their files from the overlays instead of running their hook or `apply.sh`: those whose every declared
file was made on the host. A recipe with anything made on the target (kernel collections, the cryptex, HaoExec's
stamp, `kc.bind`, generated records, native-identities), without declarations, or writing a file before such a
recipe edits it, runs its hook as before. `--overlay-hook NAME` keeps one more on its hook. The guest installs
each recipe's files at its turn ([`golden-overlays.py`](../pipeline/stages/golden-overlays.py)): staged sha256
checked, the file an edit starts from checked, mode, owner and flags (and a tar member's mtime) as the manifest
says. The build record keeps `overlay-plan.json`, `overlay-report.json` and the timings (`build.json`
`overlays`). Without the flag nothing changes.

Before trusting such a build, compare it with a hook build of the same commit (`pipeline/overlays.py tree` on
each image's volumes, mounted read-only, then `overlays.py compare`): only signature-only differences (files
signed on the host have other CDHashes), native-identities' observed CDHashes, and what differs between two hook
builds anyway may remain.

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
