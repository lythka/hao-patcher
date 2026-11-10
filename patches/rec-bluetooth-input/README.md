# rec-bluetooth-input

Makes external keyboards, mice and trackpads (USB and Bluetooth) work in the Recovery and turns Bluetooth on at boot.
Apple's Recovery has only the safe-boot HID drivers in its kernel collection and leaves the Bluetooth controller off.
Stage `recovery`.

## How it works
- `apply.sh` copies `AppleHIDKeyboard.kext`, `AppleHIDMouse.kext`, `IOBluetoothHIDDriver.kext` and
  `AppleBluetoothMultitouch.kext` from the root-patched System; `kexts.txt` adds the HID and multitouch drivers to
  the rebuilt kernel collection (see [rec-display-ethernet](../rec-display-ethernet/README.md)).
- `btpower` (`src/btpower.c`, LaunchDaemon `local.btpower`, once at boot) calls
  `IOBluetoothPreferenceSetControllerPowerState(1)` until the controller is on, for at most 60 s. This needs the
  Bluetooth TCC allowance, granted by the ad-hoc entitlements in `src/bt.entitlements` (the Recovery boots with
  `amfi=0x80`). Pairing is Apple's cable pairing: connect the Magic Keyboard, Mouse or Trackpad once by cable.
- `make.sh` builds `btpower` for x86_64 at every Recovery build and signs it with the entitlements.

## Verify
`/var/log/btpower.log` in the Recovery reads `btpower: controller on`; a Magic Keyboard connected by cable pairs.

## Validation
NEEDS VALIDATION: `btpower` builds and the drivers enter the kernel collection; input devices have not been tested in
a booted Recovery built from this repository.

## Revisions
- r1 2026-11-10 Initial public release.
