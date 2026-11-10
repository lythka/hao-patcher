# rec-wifi

Wi-Fi in the Recovery on the MacBookPro9,2's BCM4331. Tahoe's IO80211 stack does not drive this legacy Broadcom
card. OCLP fixes the installed system with "Modern Wireless" (legacy kexts injected by OpenCore plus a replacement
`IO80211.framework`); the Recovery receives the injected kexts but keeps Apple's framework. `apply.sh` replaces
`System/Library/PrivateFrameworks/IO80211.framework` in the BaseSystem with the root-patched System's copy.

Stage `recovery`. Rejoining a saved network at boot is [rec-wifi-autojoin](../rec-wifi-autojoin/README.md).

## Verify
The Wi-Fi menu in the Recovery lists networks and joins one.

## Validation
NEEDS VALIDATION: the framework copy is part of every Recovery build; Wi-Fi in a booted 25G332 Recovery has not been
tested.

## Revisions
- r1 2026-11-10 Initial public release.
