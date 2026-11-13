# hao

The user commands of the hao builder (workflow plan, step 9a on): `hao patch` (live updates and revert), `hao explain`,
`hao inspect`, and the builder and installer commands below (`detect`, `fetch`, `verify`, `build-image`, `build-disk`,
`create-installer`, `install`).

Every command has the five detail levels of the console mockups (`src/console.py`): `--quiet` (progress and the one
question), default (one line per step with its check), `--show` (one line per file or repair), `--show-edits` (origin
and reason of each change), `--log json` (every event as a JSON line). A failed check stops the command with what
happened, why it matters, what was left untouched and what to do next (exit 1).

## The builder and installer commands

```sh
hao detect [--out mac.json]                     # on the target: model, board id, GPU/Wi-Fi/audio, patch sets, packs
hao fetch RELEASE.json [--cache DIR] [--verify] [--host-check]
hao verify [ROOT] [--manifests OVERLAY_DIR|MANIFEST]...
hao build-image REPO --model MacBookPro9,2 [--overlay-system ROOT] [--dry-run]
sudo hao build-disk /dev/diskN --model MacBookPro9,2 [--dry-run]     # output (b): an external SSD
sudo hao create-installer /dev/diskN --model MacBookPro9,2 [--dry-run]  # output (a): the USB stick
hao install /dev/diskN [--dry-run]              # on the target, from the stick's Recovery
```

- **detect** (`src/detect.py`, read-only) reads `sysctl`, `ioreg` (board id, PCI devices). Patch sets come from the
  model's recorded OCLP profile (`tools/oclp-hardware/profiles`), else from `data/detect-rules.json` (PCI ids of the
  proven laptop's devices only; an unknown GPU, Wi-Fi or audio device is named in a note). Packs follow from
  `packs/*.json` (`pipeline/patches.py select_packs`); `any_intel` packs only on an Intel Mac. The profile file is what
  `--profile` takes; `--model` builds the same profile from the recorded one without the Mac.
- **fetch** runs `tools/acquire` (content-addressed cache, default `~/Library/Caches/hao`); `--verify` checks only,
  `--host-check` runs `tools/acquire/host-check` first.
- **verify** (`src/verify.py`, read-only) checks every file of the pack manifests (sha256, mode, owner, flags) under
  ROOT (default `/`); manifests come from overlay folders or `ROOT/private/var/db/hao/manifests`.
- **build-image** resolves the Mac, the release assembly of its target (`TARGET-GOLDEN`, or `--assembly`) and the
  recipes per pack, then runs `pipeline/golden-run` (builder VM; `--overlay-system` adds `--from-overlays`). It builds
  every pack the target detects; a profile with other packs is refused (golden-run has no pack choice yet). It stops
  while a fix proven by a live swap on the laptop is in no recipe (`pipeline/swaps.py`, below the list);
  `--allow-unresolved-swaps` builds anyway.
- **build-disk** and **install** (`src/builder.py`) resolve the same plan and check the disk with `src/disks.py`
  (ssdinstall's checks: whole, physical disk, not the running system's or the inputs', big enough; `external` for
  build-disk, `internal` for install), then ask for the typed confirmation (`--confirm` gives it ahead). Then they
  build from the relocatable kit (`pipeline/kit.py`, workflow plan step 16): build-disk stages it
  (`golden-run --stage-kit`, or takes `--kit`), install takes the stick's (`--kit`, required); both run
  `pipeline/stages/golden-assemble.sh` onto the disk, where golden-image.py's DiskTarget checks it again under the
  same policy, and build-disk keeps the sealed cryptex images for the next build (`golden-run --collect-kit`).
  Before staging anything they need what the builder VM gives the assembly: root, SIP's filesystem protection off
  (`CSR_ALLOW_UNRESTRICTED_FS`) and AMFI relaxed (`amfi_get_out_of_my_way=1`, or the NetBoot Recovery's
  `amfi=0x80`), because the build makes firmlinks with an ad hoc entitled tool. The installer's Recovery starts that
  way; on a Mac's everyday macOS they stop before writing and say so. Our dev host never runs them against its own
  disks: they run in the builder VM or on the laptop.
- **create-installer** checks the stick and shows its plan; writing the stick (Recovery, launcher, kit partition)
  is not built yet, so it stops after the checks.

## hao patch

Applies pack overlays (one pack's final files + `manifest.json` with each file's sha256, mode, owner and flags) to
a folder root, or to the running Mac (`--live`):

- writes only files whose full sha256 differs from the overlay's, and checks each before (unchanged since
  planning) and after (the written file has the manifest's sha256)
- sets each file's mode, owner and flags as the manifest says, also on a file whose content is already right
  (`attrs`); owners are `user:group` by name (looked up in ROOT's own `private/etc/passwd` and `group`, then
  this system's) or by number (`248:248`, as `tar --numeric-owner` keeps them); flags are chflags names
  (`restricted`, `schg`, `uchg`, `sunlnk`, `hidden`, `nodump`). Folders it creates get the manifest's `dirs`
  mode and owner (a cache folder owned by a system account), else root:wheel 0755; every missing `dirs` folder
  is created, empty ones too (a tar's folder entries, as `tar -x` makes them), and gets its `mtime` once the
  files inside it are written
- owners and system flags need root: without root they are left as they are and the record says which
  (`not_set`), so a run as a normal user is never a proof of attributes
- backs up every replaced file and records every new one (before writing it), so `--revert` restores replaced
  files and their attributes and removes files and (empty) folders the patch added, and a failure part way puts
  back everything already written (record state `rolled-back`)
- takes a lock of its own (`STATE/lock`)
- live, it writes nothing of `installer`-phase packs, nor any file only the installer can make take effect:
  kernel collection members (`System/Library/Extensions`, `Library/Extensions`, the KCs, the kernel), images of
  the dyld shared cache (from the `.map` beside the running system's cache), the cryptex, Preboot, the booter
  and the EFI partition. They are listed as needing Recovery; `--installer` applies them (installer or on-disk
  Recovery). A cache image is written live when dyld loads on-disk roots: the installed dyld carries
  golden-gate-dyld-internal's edit, or (older images) the system reports `CSR_ALLOW_APPLE_INTERNAL` (0x10,
  `ic_csr_internal=1`). dyld then loads the file at its path instead of the cache's copy (an on-disk root, as
  OCLP's Metal wrapper is), so it takes effect like any other file
- the target-side steps of `deferred.json` (beside the overlays, or `--deferred`) are reported in their order
  after the writes: `needs-recovery`, or `not-run` (live-capable, but hao has no live runner for any yet);
  `stale` marks one made from the installed files (native-identities) when the patch wrote files
- the same detail levels as the edit engine: `--quiet`, default, `--show`, `--log json`

```sh
python3.14 -I tools/hao/hao patch OVERLAY... --root ROOT --dry-run
python3.14 -I tools/hao/hao patch OVERLAY... --root ROOT
python3.14 -I tools/hao/hao patch --list --root ROOT
python3.14 -I tools/hao/hao patch --revert --root ROOT [--id hao-YYYYMMDD-HHMMSS]
```

State (records and backups) defaults to `ROOT/private/var/db/hao`.

### The running Mac (`--live`)

For an Intel Mac booted through OpenCore with SIP's filesystem protection and authenticated root off (csr
`0x2` and `0x800`). Every system command goes through `src/macos.py`'s `Host`; the tests answer them from
laptop-shaped `diskutil` fixtures and check the exact sequence.

```sh
sudo hao patch --probe [--log json]                      # read-only: volumes, snapshots, SIP, which steps work
sudo hao patch OVERLAY... --live --dry-run               # read-only plan against the running system
sudo hao patch OVERLAY... --live --i-have-a-backup       # write, snapshot; takes effect at the next boot
sudo hao patch --revert --live --i-have-a-backup         # boot the snapshot that ran before
sudo hao patch --list --live
```

1. The System and Data volumes come from `diskutil info -plist /` (booted snapshot, container, volume group),
   `diskutil apfs list -plist` (roles) and `listSnapshots` (the System volume is the one of the group that holds
   the booted snapshot), never from the device name.
2. It refuses unless it runs as root on x86_64, SIP allows both, nothing else (softwareupdate, OCLP,
   laptop-swap) has the System volume mounted, and no live patch or revert happened since this boot.
3. It plans against the running system, mounts the System volume read-write (`mount -o nobrowse -t apfs`) at
   `/private/var/run/hao-live-system`, checks the plan again against it, and takes a named snapshot
   (`apfs_systemsnapshot -s hao-pre-ID`).
4. System paths are written to the mounted volume (they arrive with the reboot); paths under a firmlink
   (`/usr/share/firmlinks`: `private/`, `Library/`, ...) to the Data volume, at once.
5. `bless --mount MNT --create-snapshot` (as the image build makes its snapshot; `--bless oclp` gives OCLP's
   Intel form with `--folder .../CoreServices --bootefi`); the new snapshot's name is recorded, with the one
   booted before. Running processes and launchd jobs that use a written file are listed (best effort, `lsof`).

Revert restores the files on the volume and tags the snapshot booted before the patch as the next boot's root
(`apfs_systemsnapshot -r NAME`), else the `hao-pre` snapshot, else blesses a new snapshot of the restored volume.
`bless --last-sealed-snapshot` is no option: an image hao built has no sealed snapshot. Nothing to write means no
mount, no snapshot and no record. State is `/private/var/db/hao`.

Not proven yet: the live backend refuses to write without `--i-have-a-backup` until the step 9a laptop run
(`laptop/run-9a.sh`, steps in its header) proves it, including whether the snapshots survive a boot.

Not done yet:

- **Deferred steps.** Overlays come from `pipeline/materialize.py`. What only the target can make (kernel
  collections, the cryptex, HaoExec's stamp, native-identities after the overlays) is listed in order in
  `deferred.json`; hao reports those steps and runs none yet.
- **Signing on end users' Macs.** Replaced files load on the dev image because it boots with
  `amfi_get_out_of_my_way=1`. That is open for the owner.

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/hao/tests
```
