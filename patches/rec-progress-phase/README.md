# rec-progress-phase

Keeps the boot progress bar from jumping back when a NetBoot hands over to the Recovery's progress window. A NetBoot
loader can set Apple's install-phase NVRAM variables so one bar spans the whole boot; the kernel draws the "Boot 1"
range (48-82 %). Apple's "Installer Progress" draws the start of the current phase when its window opens, and that
phase is still "Boot 1", so the bar drops from 82 % to 48 %.

`src/iasphase.sh` (LaunchDaemon `local.iasphase`, once at boot) moves `IASCurrentInstallPhase` from "Boot 1" to
"Language Chooser" before the window reads it. Any other phase (a normal boot) is left alone. Stage `recovery`.

## Verify
`/var/log/iasphase.log` in a NetBooted Recovery reads `phase Boot 1 -> Language Chooser`, and the bar continues
from where the kernel's bar stopped.

## Validation
NEEDS VALIDATION: the script installs in every Recovery build; it has not been checked in a NetBoot of a Recovery
built from this repository.

## Revisions
- r1 2026-11-10 Initial public release.
