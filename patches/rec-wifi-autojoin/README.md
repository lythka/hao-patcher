# rec-wifi-autojoin

Lets the Recovery rejoin the Wi-Fi network it saved in NVRAM (at boot, after wake and when Wi-Fi is turned on). The
stock Recovery saves the network but never auto-joins it on this Mac: `airportd` refuses with "Already associated",
because the legacy Broadcom driver keeps reporting the last SSID while disassociated. Stage `recovery`; needs
[rec-wifi](../rec-wifi/README.md).

## How it works
- `wifijoin.dylib` (`src/wifijoin.m`) is loaded only into `airportd` and replaces `-[CWInterface ssidData]`: it
  returns Apple's answer while the link is up and nil while it is down (`SIOCGIFMEDIA` without `IFM_ACTIVE`).
- `src/wifijoin.sh` (LaunchDaemon `local.wifijoin`, once at boot) restarts `airportd` with the dylib inserted.
  launchd drops `DYLD_*` from a job's own environment, so it uses `launchctl debug --environment
  DYLD_INSERT_LIBRARIES=...` followed by `kickstart -k`. If `airportd` restarts later it runs unmodified.
- `make.sh` builds the dylib for x86_64 and signs it ad hoc (the Recovery boots with `amfi=0x80`).

## Verify
Join a network in the Recovery and reboot: it rejoins without asking for the password. `/var/log/wifijoin.log`
records the hook.

## Validation
NEEDS VALIDATION: the dylib builds and installs; the method checks were written against 25G241's `airportd`, and
auto-join in a booted 25G332 Recovery has not been tested (on a mismatch the dylib changes nothing).

## Revisions
- r1 2026-11-10 Initial public release.
