# rec-startup-guard

Prevents the Recovery's Startup Disk from leaving the Mac unbootable. Startup Disk runs `bless --setBoot` on the
internal macOS, which points the firmware (`efi-boot-device`, and `Boot0080`/`BootOrder` when OpenCore's boot
variable routing is off) at macOS's own `boot.efi`. This Mac cannot start that without OpenCore. Stage `recovery`.

## How it works
- `bootguard` (`src/bootguard.c`, IOKit NVRAM, built by `make.sh`) checks `efi-boot-device` every second and once
  more at shutdown. When it names a `boot.efi` on an internal volume, it points it at the internal ESP's
  `\EFI\OC\OpenCore.efi`, whose default entry is that macOS. Other choices (NetBoot, an external disk, a one-time
  `bless --nextonly`) are left alone.
- `src/bootguard.sh` (LaunchDaemon `local.bootguard`) tries the ESP of each internal, non-removable disk
  (`src/internal-esp`) for `\EFI\OC\OpenCore.efi` and runs `bootguard` with that partition's UUID, so a second
  internal drive does not hide the boot drive. With no internal OpenCore it exits.

[rec-reinstall](../rec-reinstall/README.md) uses `internal-esp` and `bootguard` to set the startup disk after an
install.

## Verify
In the Recovery choose Startup Disk, select the internal macOS and restart: OpenCore starts (no prohibited sign), and
`/var/log/bootguard.log` shows the rewrite.

## Validation
NEEDS VALIDATION: `bootguard` builds and installs in every Recovery build; the redirect has not been tested in a
booted Recovery built from this repository.

## Revisions
- r1 2026-11-10 Initial public release.
