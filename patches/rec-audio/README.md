# rec-audio

Sound in the Recovery (speakers, headphones, HDMI/DisplayPort audio). Apple's Recovery has no AppleHDA stack for this
Mac.

`apply.sh` copies `AppleHDA.kext` (which contains its HAL plug-in), `IOAudioFamily.kext` and `AppleGFXHDA.kext` from
the root-patched System into the BaseSystem. `kexts.txt` adds the HDA chain (IOAudioFamily, IOHDAFamily,
AppleHDAController, DspFuncLib, AppleHDA, AppleHDAHardwareConfigDriver, AppleMikeyDriver, AppleGFXHDA) to the rebuilt
`BaseSystemKernelExtensions.kc`; [rec-display-ethernet](../rec-display-ethernet/README.md) describes the rebuild.
Stage `recovery`.

## Verify
`kextstat | grep AppleHDA` in the Recovery lists the driver, and the volume keys
([rec-brightness-keys](../rec-brightness-keys/README.md)) play the feedback sound.

## Validation
NEEDS VALIDATION: every listed bundle ID exists in the 25G332 System and the kernel collection builds; audio in a
booted Recovery has not been tested.

## Revisions
- r1 2026-11-10 Initial public release.
