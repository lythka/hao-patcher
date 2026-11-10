# rec-power

Sets the Recovery's sleep policy at every boot (the Recovery keeps `pmset` settings only in RAM). On AC power the
Mac never sleeps and the display turns off after 10 minutes, so long work is not interrupted; on battery it sleeps
after 20 minutes idle and the display turns off after 5 minutes.

Installs `/usr/local/ssdtools/power.sh` and the LaunchDaemon `local.power` (RunAtLoad, log `/var/log/power.log`).
Stage `recovery`.

## Verify
`pmset -g custom` in the Recovery shows `sleep 0` under AC Power and `sleep 20` under Battery Power.

## Validation
NEEDS VALIDATION: the daemon installs in every Recovery build; the policy has not been read back in a booted Recovery
built from this repository.

## Revisions
- r1 2026-11-10 Initial public release.
