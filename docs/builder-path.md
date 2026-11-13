# The builder path

Where hao-patcher is going, and which parts already exist. Today an image is built on the maintainer's Mac
inside an arm64 builder VM (`docs/building.md`). The goal is a release a user downloads and runs on their own Mac,
with no VM, no Apple silicon and no second machine ([building without Apple silicon](#building-without-apple-silicon)). It makes either a USB installer that installs macOS 27 on an Intel
Mac through its Recovery, or a complete macOS 27 disk built straight onto an external SSD that the Intel Mac boots
as is. Anyone can read what each patch changes and why.

## Principles

- **Apple's files are downloaded by the builder, never published.** Every download is pinned by URL, size and
  SHA-256.
- **Every change is declared.** Each file a recipe adds or changes has one origin (Apple, a Tahoe donor, OCLP or
  hao) and an ordered list of edits, each with its bytes and its reason. One engine applies them all.
- **Each file is made once.** A ledger refuses a recipe that edits another recipe's file, unless the chain is
  listed on purpose.
- **Releases are frozen kits.** A release names exact inputs, recipe revisions and expected outputs, and is cut
  only from clean, recorded producers after a laptop boot.
- **A normal boot looks like a stock Mac's.** Only the Apple logo: OpenCore's RELEASE build, no debug text, no
  verbose boot. Diagnosis builds that show logs are explicit opt-ins. The installer's Recovery is meant to look like
  a stock macOS 27 Recovery too.
- **Boot is fast, first boot included.** Every image's boot is measured from the kernel to the first screen, and its
  critical path is named before it counts as good.
- **Hardware support comes in packs.** A generic Intel base, plus packs chosen by OCLP-style detection. Only HD 4000
  (MacBookPro9,2) is proven.

## What exists (host side; tested, not yet used to build an image)

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
| Release cut | Classifies every input as downloadable, reproducible, dirty, harvested or unrecorded; writes `release.json` drafts | `pipeline/release-cut` |
| Contract | Shared checker for the interfaces between the hao repositories; other repositories vendor a pinned copy | [`tools/hao-contract`](../tools/hao-contract/README.md) |
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

## Where it stands

| Command | What it does | State |
|---|---|---|
| `hao detect` | the Mac, its devices and packs | works |
| `hao fetch` / `hao verify` | pinned downloads; checks an installed system against its manifests | works |
| `hao create-installer` | on any Mac: download, unpack, prepare packs, write the USB stick | checks and plan only; writing the stick is not built |
| `hao install` | on the target, from the stick: lay out the disk, copy macOS, add Intel support, apply packs, link the kernel, seal, make it bootable | every step exists in the image build; running it outside the builder VM comes with the kit |
| `hao build-disk` | the same assembly onto an external SSD | checks and plan only, as `install` |
| `hao build-image` | an image file, for development | works, in the builder VM |
| `hao patch` / `--revert` | live updates of packs on the running Mac, undone by snapshot | works on a folder; on the running Mac it waits for a laptop proof of snapshot and revert |
| `hao explain` / `hao inspect` | where a file came from and each change made to it | works |

Every command shows its work at five levels, from progress bars (`--quiet`) through steps (default), files
(`--show`) and edits (`--show-edits`) to a JSON trace (`--log json`), and a failed check stops with what happened,
why it matters, what was left untouched and what to do next.

## Not done yet

- **Installing from these pieces.** Images are still built by the recipes' own hooks by default. `golden-run
  --from-overlays` (opt-in, [building](building.md#installing-from-the-overlays-opt-in)) installs the files of every
  recipe whose declarations are all made on the host from the overlays instead (12 of the 20 recipes of
  `dev-bs-test`, 837 files); the rest, and everything made on the target, still run their hooks. Its first VM build
  matched a hook build except signature padding and 41 empty folders (now carried). The kernel binder is used:
  hao-opencore installs its repairs (equal to the recorded ones on every recorded 26B131 collection). IntelCommpage
  r42 reads its collection addresses from the table the binder writes; it passed a VM build and a QEMU boot, not
  yet the laptop.
- **The kit**, then `hao install` and `hao build-disk` writing through it, proven first in the Tahoe Recovery on the
  laptop.
- **The USB installer:** the Recovery-on-a-stick launcher in Golden Gate's Recovery. Its riskiest unknowns (bundled
  Python, kmutil and the binder, cryptex sealing, volume groups and firmlinks) pass in the laptop's Tahoe Recovery
  ([`tools/recovery-spikes`](../tools/recovery-spikes/README.md)); they run again in Golden Gate's.
- **A first release.** `pipeline/release-cut --dry-run` lists every input a cut would refuse: today one built from
  a checkout with uncommitted changes and one without a build record. Caches harvested from a running laptop never
  ship; a release warms them up on the first boot instead (not built yet).
- **On the laptop:** the live patch's snapshot and revert (`tools/hao/laptop/run-9a.sh`), and the replacement Metal
  stack.

## Verify

```sh
python3.14 -I -B -m unittest discover -s pipeline/tests
python3.14 -I pipeline/patches.py check
python3.14 -I pipeline/ledger.py pipeline/sets/golden-gate-26B131.txt pipeline/targets/mbp92.json 25G342
python3.14 -I pipeline/release-cut --dry-run pipeline/assemblies/mbp92-26B131.json
```
