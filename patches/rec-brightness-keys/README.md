# rec-brightness-keys

Makes the display-brightness, keyboard-backlight and volume/mute keys work in the Recovery, with an on-screen level
indicator. In Apple's Recovery the key events reach the HID system but nothing acts on them: the BaseSystem has no
working brightness hotkey path, `corebrightnessd` knows no keyboard backlight on this generation, and the OSD helper
draws an empty window. Stage `recovery`.

## How it works
`brightkeys` (`src/brightkeys.m`, LaunchAgent `local.brightkeys` in the Recovery's GUI session) registers an
IOHIDEventSystemClient keyboard monitor (ad-hoc entitlement `com.apple.private.hid.client.event-monitor`,
`src/brightkeys.entitlements`) and acts on seven usages: top-case display (0x04/0x05) and keyboard light (0x08/0x09),
consumer 0x6F/0x70 (display keys on external Apple keyboards) and 0xE9/0xEA/0xE2 (volume).

- Display: steps brightness through IOKit (`IODisplaySetFloatParameter`). It does not go through
  `corebrightnessd`, whose ambient-light manager would fade the panel back after each change. The backlight driver
  keeps the level in NVRAM `backlight-level`, which the firmware restores.
- Keyboard light: steps the AppleLMUController LED and keeps the level in NVRAM `ssd-kbd-backlight-level`.
- Volume: the default output device through CoreAudio.
- Draws its own bezel.

`make.sh` builds it for x86_64 and signs it ad hoc with the entitlement. The display keys need the framebuffer from
[rec-display-ethernet](../rec-display-ethernet/README.md); the volume keys need [rec-audio](../rec-audio/README.md).

## Verify
Press the brightness, keyboard-light and volume keys in the Recovery: the level changes and the bezel appears.

## Validation
NEEDS VALIDATION: `brightkeys` builds and installs in every Recovery build; the keys have not been tested in a booted
Recovery built from this repository.

## Revisions
- r1 2026-11-10 Initial public release.
