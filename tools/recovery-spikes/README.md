# recovery-spikes

Workflow plan step 15: the end-user installer's riskiest unknowns, tried in the test laptop's NetBoot Recovery (macOS
26 Tahoe Recovery 25G241, x86_64, root SSH) before the installer is built ([builder path](../../docs/builder-path.md)).
Each spike runs the build's own code where it can (`golden-layout.py`, `install.py`, `kc-hook.sh`'s kmutil lines,
`kc-binder`) against image files on RAM disks. The internal disk is only read: the kmutil spike mounts its System
volume read-only to copy the installed driver tree.

Run by the holder of the laptop lock, with the laptop in the NetBoot Recovery:

```sh
LAPTOP=root@192.168.68.58 tools/recovery-spikes/run.sh survey    # then python, cryptex, group, kmutil
tools/recovery-spikes/run.sh all      # the five in order; stops after python if python fails
tools/recovery-spikes/run.sh clean    # undo every RAM disk, image and mount the spikes left
```

Each spike checks its inputs against [`inputs.lock`](inputs.lock) (`pipeline/lock.py` format) and stages them, refuses
unless the laptop runs Recovery `RECOVERY_BUILD` as root (`remote/common.sh gate`), makes a RAM disk
`/Volumes/HAOSPIKE-SPIKE`, copies the inputs (`laptop-ssh --put`; the laptop checks every sha256 again), runs, copies
`results/` back to `store/runs/recovery-spikes-DATE/SPIKE-TIME/`, cleans the laptop (also after a failure or ^C)
and prints the verdict (`verdict.txt`, `verdict.json`). Environment variables are listed in `run.sh`'s header.

## Spikes

| Spike | Question | Inputs | RAM disk | Time |
|---|---|---|---|---|
| `survey` | build, memory, tools, disks; read-only | none | none | 1 min |
| `python` (a) | does the bundled Python run there | `python_x86_64`, code | 384 MB | 3 min |
| `cryptex` (c) | can a cryptex be rebuilt and sealed there | + `golden_app_cryptex`, 25G332 `newfs_apfs`, `apfs_prepare_cryptex` | 1152 MB | 5 min |
| `group` (d), `fl` | do volume groups and firmlinks work on x86_64 | + `fl`, `fl_noent` | 1536 MB | 3 min |
| `kmutil` (b) | can the Darwin 27 collections be linked and bound there | + `tahoe_kmutil` (signed ad hoc on the host), `darwin27_kernel` | 768 MB + the root's | 10 min |

Each RAM disk needs 1 GB of free memory beside it, or setup refuses (a Recovery out of memory needs someone at the
laptop). `survey`'s verdict lists which RAM disks fit.

### survey
`SURVEY key value` lines: build, product, arch, model, memsize, `mem_available_mb`, boot-args, csr, tools on PATH,
sha256 of the Recovery's `newfs_apfs`, `apfs_prepare_cryptex`, `kmutil`, `df`, internal disks, APFS volumes, mounts.
PASS: arch is x86_64. It writes nothing.

### python (a)
Unpacks python-build-standalone 3.12.14 and `code.tar` (the install-time tools from the working tree, plus
kc-binder's `stage`). Expected:
```
CHECK upload PASS 2 files match the host's sha256
CHECK python-starts PASS 3.12.14 x86_64 darwin
CHECK imports PASS 41 modules
CHECK tools-present PASS codesign ditto hdiutil diskutil bless tar install chflags plutil shasum
CHECK ctypes-libc PASS getxattr setxattr listxattr fsctl
CHECK ctypes-corefoundation PASS CFArray of 2 CFNumbers, count 2
CHECK apfs-framework PASS APFSContainerVolumeGroupAdd resolved
CHECK diskutil-plist PASS root diskNsM macOS Base System
CHECK suite:edit-engine PASS Ran 48 tests ...; OK
CHECK suite:kc-binder PASS Ran 27 tests ...; OK (skipped=4)
CHECK suite:hao PASS Ran 41 tests ...; OK
CHECK suite:darwin27-kernel PASS Ran 7 tests ...; OK (skipped=1)
END python rc=0
```
PASS: all of them. FAIL names the check: a missing module or tool (e.g. no `codesign` means the edit engine cannot
sign in the Recovery), no `APFSContainerVolumeGroupAdd` (grouping must use `-groupWith`), or a suite that fails on
this Python. `INFO ssl` reports whether downloads find CA certificates.

### cryptex (c)
`install.py`'s App cryptex rebuild without the hao stubs: Golden Gate's `app.dmg` mounted read-only,
`new_image` (`newfs_apfs -i`), `replace_tree`, `FILL_MB` (256) MB of random files, `seal_image`
(`apfs_prepare_cryptex`, then `Sealed`), mounted again and compared. Twice: with the Recovery's own APFS tools
(`recovery`), then with Tahoe 25G332's carried as inputs (`carried`). Expected:
```
CHECK seal:recovery PASS Sealed after apfs_prepare_cryptex; ... root hash ...
CHECK readback:recovery PASS N entries as copied, 16 filler files intact
CHECK seal:carried PASS ...
CHECK readback:carried PASS ...
RESULT seal_mb_s:recovery N
```
PASS: the Recovery's own tools seal. PARTIAL: only the carried 25G332 tools seal (the installer must carry them).
FAIL: neither. `seal_mb_s` scales to the ~4-5 GB combined OS cryptex.

### group (d) and the fl entitlement
Two APFS containers in raw image files, built as `golden-image.py` builds the target (`partitionDisk GPT APFS`, a
System-role volume replacing the placeholder). A: `golden-layout.py seed` and `group` (the private
`APFSContainerVolumeGroupAdd`), Data mounted, `fl set`/`fl get` of one firmlink (the build's tool, signed ad hoc
with `com.apple.private.apfs.set-firmlink`), `fl-noent` tried for comparison, then Preboot, Recovery and VM
volumes. B: `diskutil apfs addVolume ... -role D -groupWith SYSTEM`. Expected:
```
CHECK seed-altlink PASS golden-layout.py seed rc=0
CHECK group-add PASS APFSContainerVolumeGroupAdd via golden-layout.py group: <UUID>
CHECK group-add-readback PASS System diskNs2 and Data diskNs1 report group <UUID>
CHECK fl-entitled PASS rc=0 set .../SpikeFirm -> 'SpikeFirm' rc=0 errno=0()
CHECK fl-readback PASS rc=0 get ... target='SpikeFirm' ...
INFO fl-unentitled refused, as expected without the entitlement; ...
CHECK other-roles PASS roles Data Preboot Recovery System VM
CHECK group-with PASS diskutil apfs addVolume -role D -groupWith ...: System and Data report <UUID>
```
PASS: all. PARTIAL: one way of grouping works, or grouping works and `fl` does not (`fl-entitled` "killed by
signal 9" means AMFI refused the ad hoc entitlement: the installer needs the `amfi=0x80` boot-arg the NetBoot
Recovery has, or another way to make firmlinks). FAIL: neither grouping works.

### kmutil (b)
The internal System volume read-only; its driver tree (`System/Library/Extensions`, `DriverExtensions`,
`Library/Extensions`, ...) copied into an APFS volume in a raw image on a second RAM disk; the row kernel installed;
then `kc-hook.sh`'s two `kmutil create` lines unchanged (Tahoe's kmutil, `-R ROOT`, build path
`/.darwin27-kc-build`), `members.py`, and `kc-binder bind` on the new BootKC with the bundled Python. The Recovery's
own `/usr/bin/kmutil` links the same root for comparison (INFO). `KC_SCOPE=boot` (or `auto` when memory is short)
copies only the installed BootKC's member bundles and links only the Boot collection. Expected:
```
CHECK internal-system-found PASS System volumes on internal disks: ['diskNsM']
CHECK internal-readonly PASS diskNsM at .../mnt/internal-system, writable False
CHECK internal-bootkc PASS .../BootKernelExtensions.kc 203 members, sha256 ...
CHECK memory PASS scope full (asked auto)
CHECK tree-copied PASS N paths, ~1400 MB, tar rc 0/0
CHECK kmutil-boot:tahoe PASS rc=0, 203 members, 64 MB, sha256 ...
CHECK kmutil-sys:tahoe PASS rc=0, N members, N MB
CHECK members-equal-internal PASS 203 members
CHECK kc-bind PASS kc-binder bind rc=0
INFO bootkc-identical-internal yes|no (...)
CHECK host-bind-equal PASS laptop and host bind outputs equal      (added on the host)
```
PASS: all, both collections. PARTIAL: boot scope (only the Boot collection was linked). FAIL: any of them, or the
internal volume was not read-only. `bootkc-identical-internal` compares bytes with the installed BootKC (equal only
when the image on the laptop was built from the same kernel and kmutil and nothing changed its tree afterwards).

## Files

| File | Runs on | Purpose |
|---|---|---|
| `run.sh` | host | the driver above |
| `lib/spikes.py` | host | stage inputs and `code.tar`, RAM disk sizes, verdicts (and the kmutil host re-bind) |
| `inputs.lock` | host | the inputs, with sha256; rows mirroring a pipeline row are kept equal by the tests |
| `remote/common.sh` | laptop | gate, memory, RAM disk setup, clean (state file `/private/var/tmp/hao-spikes/SPIKE.state`) |
| `remote/survey.sh` | laptop | the survey |
| `remote/spike.sh` | laptop | re-check uploads, unpack Python and code, start `NAME_spike.py` |
| `remote/spikelib.py` | laptop | output protocol, commands log, scratch devices |
| `remote/*_spike.py` | laptop | the four spikes |

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/recovery-spikes/tests
```

Host tests use fixtures and stubs only (verdicts, staging, helpers, the driver through a stub `laptop-ssh`, the
python spike on the host's Python with a stub `diskutil`); they attach, format and mount nothing. A passing suite
proves none of the laptop answers.

## Validation

NEEDS VALIDATION: not yet run on the laptop.
