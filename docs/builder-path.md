# The builder path

Where hao-patcher is going, and which parts already exist. Today an image is built on the maintainer's Mac
inside an arm64 builder VM (`docs/building.md`). The goal is a release a user downloads and runs on their own Mac,
with no VM, no Apple silicon and no second machine. It makes a USB installer that installs macOS 27 on an Intel Mac,
and anyone can read what each patch changes and why.

## Principles

- **Apple's files are downloaded by the builder, never published.** Every download is pinned by URL, size and
  SHA-256.
- **Every change is declared.** Each file a recipe adds or changes has one origin (Apple, a Tahoe donor, OCLP or
  hao) and an ordered list of edits, each with its bytes and its reason. One engine applies them all.
- **Each file is made once.** A ledger refuses a recipe that edits another recipe's file, unless the chain is
  listed on purpose.
- **Releases are frozen kits.** A release names exact inputs, recipe revisions and expected outputs, and is cut
  only from clean, recorded producers after a laptop boot.
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
| Contract | Shared checker for the interfaces between the hao repositories, vendored and pinned | [hao-assets](https://github.com/lythka/hao-assets), `tools/hao-contract` |
| Metal | Our own legacy-Metal wrapper and MetalOld, generated or extracted and declared | [`tools/metal-wrapper`](../tools/metal-wrapper/README.md), `patches/metal-metalold` |

`validated_revision` and `validated_digest` let a recipe keep a boot test's proof across revisions that only change
its declarations. Moving an input row clears that proof.

## Not done yet

- **Installing from these pieces.** Images are still built by the recipes' own hooks. The pieces above are proven
  equal to them on real inputs, not yet used in their place, except the kernel binder: hao-opencore installs its
  repairs (equal to the recorded ones on every recorded 26B131 collection; not yet through a VM build or a boot).
  IntelCommpage r42 reads its collection addresses from the table the binder writes. Its table equals r41's
  compiled values on every recorded 26B131 collection, but the new kext has not been booted.
- **The USB installer:** the Recovery-on-a-stick launcher, disk layout on the target, and kernel collections linked
  and bound there.
- **A first release.** Inputs built from uncommitted trees or harvested from a running laptop block a cut until
  they are rebuilt or approved.
- **On the laptop:** the live patch's snapshot and revert (`tools/hao/laptop/run-9a.sh`), and the replacement Metal
  stack.

## Verify

```sh
python3.14 -I -B -m unittest discover -s pipeline/tests
python3.14 -I pipeline/patches.py check
python3.14 -I pipeline/ledger.py pipeline/sets/golden-gate-26B131.txt pipeline/targets/mbp92.json 25G342
python3.14 -I pipeline/release-cut --dry-run pipeline/assemblies/mbp92-26B131.json
```
