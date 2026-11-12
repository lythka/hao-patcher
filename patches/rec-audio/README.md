# rec-audio

Sound in the Recovery (speakers, headphones, HDMI/DisplayPort audio). Apple's Recovery has no AppleHDA stack for this
Mac.

`apply.sh` copies `AppleHDA.kext` (which contains its HAL plug-in), `IOAudioFamily.kext` and `AppleGFXHDA.kext` from
the root-patched System into the BaseSystem. `kexts.txt` adds the HDA chain (IOAudioFamily, IOHDAFamily,
AppleHDAController, DspFuncLib, AppleHDA, AppleHDAHardwareConfigDriver, AppleMikeyDriver, AppleGFXHDA) to the rebuilt
`BaseSystemKernelExtensions.kc`; [rec-display-ethernet](../rec-display-ethernet/README.md) describes the rebuild.
Stage `recovery`.

Declared in `patch.json` `files` (paths relative to the Recovery BaseSystem's root: the three kext bundles, whole, and the kernel collection in [`edits/kc.json`](edits/kc.json), a `kc.link` declaration); [`tests/test_edits.py`](tests/test_edits.py) checks it against `apply.sh` and `kexts.txt`. No byte edits, so no real-input test.

## Verify
`kextstat | grep AppleHDA` in the Recovery lists the driver, and the volume keys
([rec-brightness-keys](../rec-brightness-keys/README.md)) play the feedback sound.

Declaration test:
```sh
python3.14 -I -B -m unittest discover -s patches/rec-audio/tests
```

## Validation
NEEDS VALIDATION: every listed bundle ID exists in the 25G332 System and the kernel collection builds; audio in a
booted Recovery has not been tested.

## Revisions
- r2 2026-11-12 Declare the three kext bundles and the kernel collection link in `patch.json` `files` and `edits/kc.json`; the recipe is unchanged.
- r1 2026-11-10 Initial public release.
