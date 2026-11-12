# rec-progress-phase

Keeps the boot progress bar from jumping back when a NetBoot hands over to the Recovery's progress window. A NetBoot
loader can set Apple's install-phase NVRAM variables so one bar spans the whole boot; the kernel draws the "Boot 1"
range (48-82 %). Apple's "Installer Progress" draws the start of the current phase when its window opens, and that
phase is still "Boot 1", so the bar drops from 82 % to 48 %.

`src/iasphase.sh` (LaunchDaemon `local.iasphase`, once at boot) moves `IASCurrentInstallPhase` from "Boot 1" to
"Language Chooser" before the window reads it. Any other phase (a normal boot) is left alone. Stage `recovery`.

Both installed files are new and declared in `patch.json` `files`, with paths in the Recovery BaseSystem root: `usr/local/ssdtools/iasphase.sh` and `System/Library/LaunchDaemons/local.iasphase.plist`; `tests/test_files.py` checks them against `apply.sh`.

## Verify
`/var/log/iasphase.log` in a NetBooted Recovery reads `phase Boot 1 -> Language Chooser`, and the bar continues
from where the kernel's bar stopped.
`python3.14 -I -B -m unittest discover -s patches/rec-progress-phase/tests` checks the declared files against `apply.sh`.

## Validation
NEEDS VALIDATION: the script installs in every Recovery build; it has not been checked in a NetBoot of a Recovery
built from this repository.

## Revisions
- r2 2026-11-12 Declare the installed files in `patch.json` `files` (Recovery BaseSystem root); the recipe is unchanged.
- r1 2026-11-10 Initial public release.
