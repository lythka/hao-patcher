# The builder path

For contributors: how a release is built and installed without a VM, and what works today. A user downloads a
release and runs `hao create-installer` on their own Mac (no VM, no Apple silicon, no second machine); the USB
installer installs macOS 27 on the Intel Mac. Development images are still built in an arm64 builder VM
(`docs/building.md`).

## Principles

- **Apple's files are downloaded by the builder, never published.** Every download is pinned by URL, size and
  SHA-256.
- **Every change is declared.** Each file a recipe adds or changes has one origin (Apple, a Tahoe donor, OCLP or
  hao) and an ordered list of edits, each with its bytes and its reason. One engine applies them all.
- **Each file is made once.** A ledger refuses a recipe that edits another recipe's file, unless the chain is
  listed on purpose.
- **Releases are frozen kits.** A release names exact inputs, recipe revisions and expected outputs, and is cut
  only from clean, recorded producers after a laptop boot.
- **Releases use the user path only.** A release's USB installer is made the way a user makes it (`hao create-installer`
  from the release's downloads and assets); the arm64 builder VM is for development builds.
- **A normal boot looks like a stock Mac's.** Only the Apple logo: OpenCore's RELEASE build, no debug text, no
  verbose boot. Diagnosis builds that show logs are explicit opt-ins. The installer's Recovery is meant to look like
  a stock macOS 27 Recovery too.
- **Boot is fast, first boot included.** Every image's boot is measured from the kernel to the first screen, and its
  critical path is named before it counts as good.
- **Hardware support comes in packs.** A generic Intel base, plus packs chosen by OCLP-style detection. Only HD 4000
  (MacBookPro9,2) is proven.

## What exists

| Piece | What it does | Where |
|---|---|---|
| Edit specs and engine | Declared edits (find/replace near a symbol, matcher sites, Mach-O, plist and text operations), checked before and after every write; output signed once | [`tools/edit-engine`](../tools/edit-engine/README.md) |
| Artifact store | Every apply kept by content, with original and patched files side by side; a build reuses a result when spec, input and engine hash the same | `edit-engine apply --store`, `edit-engine inspect` |
| Declarations | Every recipe's `patch.json` `files`: origin, edits, source inside its row, install attributes | [`docs/PATCHES.md`](PATCHES.md), `pipeline/patches.py explain` |
| Acceptance check | Refuses a declaration that changes anything but declarations, whose specs don't write the same bytes as the recipe's own code on real inputs, or whose signatures lack what the hook signs with (identifier, entitlements, kept metadata) | `tools/edit-engine/verify-declaration`, [`DECLARING.md`](../tools/edit-engine/DECLARING.md) |
| Ledger | One origin and ordered writes per declared file; known chains in `pipeline/ledger-expected.json`, one list for every set (a chain is expected by its path and writers) | `pipeline/ledger.py` |
| Packs | Hardware packs with detection and phase (installer only, or live) | [`packs/`](../packs/README.md), `patches.py packs`, `plan --packs` |
| Materialiser | Makes each declared file once into pack overlays (files + manifest with mode, owner, flags and folders); what only the target can make is deferred, in dependency order (`deferred.json`) | `pipeline/materialize.py` |
| Live patch | Applies overlays to a folder or the running Mac (System volume written directly, booted as a new snapshot, reverted to the one booted before): only changed files, checked, locked, rolled back on failure, revertible | [`tools/hao`](../tools/hao/README.md) |
| Kernel binder | Computes the OpenCore kernel repairs and IntelCommpage table from a recipe and the kernel collection just linked, with no compiler on the target. The image build runs it (hao-opencore `kc.bind`) and requires the recorded repairs' bytes where those bind. Elsewhere it writes the table into IntelCommpage, which reads it at load (kernel-foundation r42), so a moved layout needs no rebuilt kext | [`tools/kc-binder`](../tools/kc-binder/README.md) |
| Assembler targets | One assembler over a raw image or a whole internal disk, the disk gated by the installer's safety checks | `pipeline/stages/golden-image.py` |
| Downloads | Pinned, resumable, content-addressed downloads; a host capability matrix | [`tools/acquire`](../tools/acquire/README.md) |
| Release cut | Classifies every input by content (ours, made on the Mac, download, harvested; `pipeline/release-classes.json`, a reason per row) and by build record; writes `release.json` (the contract's release schema) and stages our assets: files as they are, folders as one deterministic tar each | `pipeline/release-cut` |
| Contract | Shared checker for the interfaces between the hao repositories, including `release.json`; other repositories vendor a pinned copy | [`tools/hao-contract`](../tools/hao-contract/README.md) |
| Metal | Our own legacy-Metal wrapper and MetalOld, generated or extracted and declared | [`tools/metal-wrapper`](../tools/metal-wrapper/README.md), `patches/metal-metalold` |

`validated_revision` and `validated_digest` let a recipe keep a boot test's proof across revisions that only change
its declarations. Moving an input row clears that proof.

## Building without Apple silicon

Nobody building or installing a release needs an Apple silicon Mac or a virtual machine. Each part of the build
falls on one side of this line:

| Made once, frozen in a release | Made on the user's Mac, every build |
|---|---|
| Our own code, built with Xcode 27 (which runs only on Apple silicon): hao-runtime's binaries and interface units, the compiled compatibility shims, the kernel binder's recipe. Published in HaoSupportPkg, pinned by hash, never rebuilt by users. | Everything made from Apple's files, which we never publish: the unpacked and decoded system, the Tahoe donors, every declared edit (the artifact store reuses unchanged results), the Metal library repairs (with a bundled LLVM, no Xcode), hao's stubs and ahead-of-time translations, the kernel collections (Tahoe's `kmutil`) and the repairs bound to them, the image itself. |

No build step runs macOS 27's own arm64 programs: its files are only read, edited and signed. What the assembly
needs from the Mac it runs on is the build environment the builder VM has today (System Integrity Protection off,
AMFI relaxed) and the right to format the target disk. A user's everyday macOS gives neither, so the assembly runs
in the installer's Recovery.

**The installer's Recovery is Golden Gate's own.** The macOS 27 BaseSystem, started by OpenCore on our Darwin 27
x86_64 kernel with Tahoe's Intel kexts and hao for its arm64-only programs. It installs the macOS it looks like, with
the same kernel. Its first stage boots to a shell in QEMU (`tools/gg-recovery`); next is fitting it in OpenCore's
4 GiB ramdisk limit. The Tahoe Recovery stays as the fallback, and for Macs with too little memory to hold it.

**The kit.** The assembly reads one staged folder, the kit: Apple's unpacked files, the donor and pack inputs, the
recipes, and an arm64 and an x86_64 CPython. Paths inside it are relative, so the same kit assembles in the builder
VM, on an Intel Mac or in the target's Recovery, onto an image file or a whole disk. Sealed cryptex images are kept
and reused between builds, keyed on their inputs and on the builder's tools.

## Where it stands (2026-11-19, the v0.0.1 candidate)

| Command | What it does | State |
|---|---|---|
| `hao detect` | the Mac, its devices and packs | works |
| `hao fetch` / `hao verify` | a release's pinned downloads and assets; checks an installed system against its manifests | works |
| `hao create-installer` | the user's one command: asks which Mac and which USB drive, downloads, makes the kit, writes the USB installer | works: a fresh Mac made the candidate's USB installer from the release alone |
| `hao install` | on the target, from the USB installer: lays out the disk, copies macOS, adds Intel support, links the kernel, seals, makes it bootable | a hands-free install of the candidate on the MacBookPro9,2 is not yet shown |
| `hao report` | one text file for a GitHub issue, without serial, user or Wi-Fi names | works |
| `hao build-disk` | the same assembly onto an external SSD | host tests only |
| `hao build-image` | an image file, for development | works, in the builder VM |
| `hao patch` / `--revert` | live updates of packs on the running Mac, undone by snapshot | works on a folder; the running Mac waits for a laptop proof |
| `hao explain` / `hao inspect` | where a file came from and each change made to it | works |

Every command shows its work at five levels, from progress bars (`--quiet`) through steps (default), files
(`--show`) and edits (`--show-edits`) to a JSON trace (`--log json`), and a failed check stops with what happened,
why it matters, what was left untouched and what to do next.

**The release.** `pipeline/release-cut` cuts the candidate complete: pinned downloads, our assets with the commit
that made each, and what the user's Mac makes from Apple's files with our public tools (hao-runtime's included).
Apart from four harvested caches for the MacBook Pro (Mid 2012), nothing Apple-derived ships: recipes that install
other harvested Apple files (the wallpaper export, MobileAssets) are in dev profiles only. A final cut also needs every shipped recipe validated or waived by the owner.

## Rebuilding the shared cache after install

A release with cache rebuild inputs can run `sudo hao cache build --release /path/to/release.json`
on the installed Intel Mac. It downloads about 27 MB, requires 55 GB free, and builds and verifies the
release's exact cache family without Xcode. See [the command](commands.md#hao-cache-build) for a dry run.
This currently saves a verified artifact only; cache/runtime activation and revert remain unproven.

## Not done yet

- **Rebuild from source, checked.** That a rebuild from the public tag reproduces the release's files is not yet
  shown (the release's reproduce check).
- **A generic image.** The generic profile cuts and makes a USB installer; installing from it in QEMU to a first
  boot is not yet shown.
- **Installing from the overlays by default.** Images are still built by the recipes' own hooks; `golden-run
  --from-overlays` ([building](building.md#installing-from-the-overlays-opt-in)) is opt-in.
- **On the laptop:** the live patch's snapshot and revert (`tools/hao/laptop/run-9a.sh`).

## Verify

```sh
python3.14 -I -B -m unittest discover -s pipeline/tests
python3.14 -I pipeline/patches.py check
python3.14 -I pipeline/ledger.py pipeline/sets/golden-gate-26B131.txt pipeline/targets/mbp92.json 25G342
python3.14 -I pipeline/release-cut --dry-run pipeline/assemblies/mbp92-26B131.json
```
