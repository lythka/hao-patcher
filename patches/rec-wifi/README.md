# rec-wifi

Wi-Fi in the Recovery on the MacBookPro9,2's BCM4331. Tahoe's IO80211 stack does not drive this legacy Broadcom
card. OCLP fixes the installed system with "Modern Wireless" (legacy kexts injected by OpenCore plus a replacement
`IO80211.framework`); the Recovery receives the injected kexts but keeps Apple's framework. `apply.sh` replaces
`System/Library/PrivateFrameworks/IO80211.framework` in the BaseSystem with the root-patched System's copy.

Stage `recovery`. Rejoining a saved network at boot is [rec-wifi-autojoin](../rec-wifi-autojoin/README.md).

The replaced folder is declared in `patch.json` `files` as `System/Library/PrivateFrameworks/IO80211.framework` (Recovery BaseSystem root, copied whole from the root-patched System; origin OCLP); `tests/test_files.py` checks it against `apply.sh`.

## Verify
The Wi-Fi menu in the Recovery lists networks and joins one.
`python3.14 -I -B -m unittest discover -s patches/rec-wifi/tests` checks the declared files against `apply.sh`.

## Validation
NEEDS VALIDATION: the framework copy is part of every Recovery build; Wi-Fi in a booted 25G332 Recovery has not been
tested.

## Revisions
- r2 2026-11-12 Declare the installed files in `patch.json` `files` (Recovery BaseSystem root); the recipe is unchanged.
- r1 2026-11-10 Initial public release.
