# rec-language-list

Shows the Recovery's language list as soon as it is ready instead of after a 60-second timeout. With no NVRAM
`prev-lang:kbd` (a new Mac or after an NVRAM reset), Apple's Language Chooser puts up its list but never ends its
"fake progress" on that one path, so the black progress screen stays on top until an abort timer fires. Stage
`recovery`.

## How it works
- `Language Chooser.app/Contents/MacOS/Language Chooser` becomes `lcwrap` (`src/lcwrap.c`); Apple's binary moves
  next to it as `Language Chooser.real`. The RecoveryOS agent starts the Language Chooser by its fixed path, so a
  wrapper is the hook point. `lcwrap` sets `DYLD_INSERT_LIBRARIES` to `lcfast.dylib` in the same folder (only if
  the file exists) and execs Apple's binary with the same arguments.
- `lcfast.dylib` (`src/lcfast.m`) unsets `DYLD_INSERT_LIBRARIES`, checks that both target methods have the
  expected type encodings (otherwise it changes nothing), and after
  `showLanguagePickerWindowNotifiy:withSpeechDelay:` calls Apple's `finishFakeProgressWindow`, as every other path
  does. It then hides Installer Progress so the list accepts clicks (see the source).
- `make.sh` builds both for x86_64 and signs them ad hoc.

## Verify
Reset NVRAM and boot the Recovery: the language list is clickable about 24 s after the kernel starts.
`/var/log/lcfast.log` records the call.

## Validation
NEEDS VALIDATION: the method encodings were checked against 25G241; on 25G332 `lcfast` either applies or changes
nothing, and this has not been tested in a booted Recovery.

## Revisions
- r1 2026-11-10 Initial public release.
