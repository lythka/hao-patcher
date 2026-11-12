# rec-reinstall

Turns the Recovery's **Reinstall macOS** entry into an installer for a hao-patcher image on a USB disk. The entry
keeps its name, icon and place, but instead of downloading stock macOS (which does not start on this Mac) it writes
the image to the internal disk, verifies it, grows the APFS container to fill the disk and makes the internal
OpenCore the startup disk. Stage `recovery`, any model and build; needs
[rec-startup-guard](../rec-startup-guard/README.md) in the same set. The Recovery reaches the image's own Recovery
volume through [rec-install-volume](../rec-install-volume/README.md).

## How it works
- Recovery Springboard reads `System/Installation/CDIS/Recovery Springboard.app/Contents/Resources/Utilities.plist`
  and starts the button marked `IsInstallAssistant` by its `Path` (for 25G332,
  `/Install macOS Tahoe.app/Contents/MacOS/InstallAssistant -RunInOSIEnv`) without a signature or bundle-ID check.
  `apply.sh` reads that path from the plist and replaces only that file, keeping its owner and mode; Apple's
  Info.plist and icon stay, so the menu looks the same.
- The replacement `InstallAssistant` (`src/Reinstall.m`, AppKit) lists image bundles and internal disks through
  `ssdinstall`, asks for the erase confirmation, warns when not on AC power, runs
  `ssdinstall install BUNDLE /dev/diskN --yes --startup` with a progress bar and offers Restart. Without a USB
  bundle it explains how to attach one and offers Try Again; it never offers Apple's downloader.
- `ssdinstall` ([tools/ssdpush](../../tools/ssdpush/README.md)) checks the bundle's hashes, streams its frames into
  `ssdpush-agent` (BLAKE2b per chunk with read-back), relocates the backup GPT, re-reads every chunk, grows the APFS
  container and runs `startup-internal`. It accepts only physical, internal, fixed whole disks and refuses the disk
  that holds the bundle.
- `src/startup-internal` leaves a NetBoot firmware default untouched. Otherwise it restarts `bootguard` so it watches
  the new ESP, blesses the internal macOS, lets `bootguard` redirect that to `\EFI\OC\OpenCore.efi` and reads
  `efi-boot-device` back.

## Build
`make.sh OUT` (run by [pipeline/recovery](../../pipeline/recovery)) builds `InstallAssistant` and `ssdinstall` for
x86_64 and signs them ad hoc, and builds `ssdpush-agent` with `tools/ssdpush/build.sh` (zstd source from
`store/cache/zstd` or `$ZSTD`). Installed files:

| Path in the BaseSystem | Source |
|---|---|
| the installer button's `Path` | `src/Reinstall.m` |
| `usr/local/ssdtools/ssdinstall` | `tools/ssdpush/ssdinstall.c` |
| `usr/local/ssdtools/startup-internal` | `src/startup-internal` |
| `usr/local/ssdtools/bin/ssdpush-agent` | `tools/ssdpush/agent.c` with zstd 1.5.7 (BSD/GPLv2 dual license) |

The installed files are declared in `patch.json` `files` as whole files (nothing is byte-edited), with paths in the Recovery BaseSystem root: the first is the replaced InstallAssistant, whose path `apply.sh` reads from `Utilities.plist` (declared as 25G332's `Install macOS Tahoe.app/Contents/MacOS/InstallAssistant`); `tests/test_files.py` checks them against `apply.sh` and `make.sh`.

## The USB disk
Any FAT32, exFAT, APFS or HFS+ volume with a bundle folder at its top level (`/Volumes/<USB>/<bundle>/IMAGE`).
Write one from an image repository:

```sh
tools/bin/ssdimg -C /path/to/image-repository bundle "/Volumes/USB/<name>" \
    --name "<name shown in Reinstall macOS>" --model MacBookPro9,2 --macos-version VERSION --macos-build BUILD
tools/bin/ssdimg bundle-check "/Volumes/USB/<name>"
```

A bundle holds `IMAGE` (key=value metadata and hashes), `index.bin` (64 bytes per frame), `stream.NNN` (frames in
2 GiB files so FAT32 can hold them), `head.bin` (MBR and GPT) and `tail.bin`; the format is in
[tools/ssdimg/streamfile.py](../../tools/ssdimg/streamfile.py). Its size is the image's compressed chunks, about
20 GB for a full image, so use a 32 GB disk.

## Verify
- On the build Mac: `zsh patches/rec-reinstall/test.sh WORK [-k]` builds the three x86_64 binaries, makes a small
  GPT/APFS disk with a random file, commits it to an `ssdimg` repository, writes and checks a bundle, installs it into
  a larger file with a verify pass and again with `--delta`, and compares the file. `-k` keeps `WORK/bundle` as a
  test bundle.
- In the Recovery, non-destructively: copy `src/recovery-check.sh` and a test bundle to a USB disk and run
  `sh /Volumes/USB/recovery-check.sh /Volumes/USB/<test bundle>` in Terminal. It lists disks and images, installs
  into a file in RAM with verify and delta passes, checks that the USB disk is refused, and prints the `bootguard`
  state and `efi-boot-device`.
- Full test: Reinstall macOS with a real bundle; the internal disk boots the image to Setup Assistant.
- Host: `python3.14 -I -B -m unittest discover -s patches/rec-reinstall/tests` checks the declared files against `apply.sh`.

## Safety
Reinstall erases the internal disk the user picks and confirms. Only internal fixed whole disks are offered, the disk
holding the bundle is refused, the bundle index is hash-checked before any write, every chunk is hashed before
writing and re-read at the end, and the exit status is 0 only when everything verified. Bundles are not signed:
anyone who can put a bundle on a USB disk can have it installed once the user confirms the erase. To remove the
patch, drop it from the set; Apple's InstallAssistant returns with the next Recovery build.

## Validation
NEEDS VALIDATION: `test.sh` passes on the build Mac and `apply.sh` finds and replaces 25G332's installer; a full
Reinstall from the Recovery menu on a MacBookPro9,2 has not been run.

## Revisions
- r4 2026-11-12 Declare the installed files in `patch.json` `files` (Recovery BaseSystem root); the recipe is unchanged.
- r3 2026-11-10 Initial public release.
