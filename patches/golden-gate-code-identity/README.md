# golden-gate-code-identity

Lets the privacy service (`tccd`), the Login Items service (`backgroundtaskmanagementd`) and the Wi-Fi daemon
(`airportd`) see the original Apple identity of translated system programs. hao-runtime gives each arm64-only
Apple program a generated x86_64 entry slice that is signed ad hoc, so code-signing queries on that slice lose the
program's Apple identity. Visible
symptoms: Find My shows an unexpected Bluetooth permission prompt, Apple's Dock tile plugins appear as newly
added third-party background items, and no Wi-Fi network can be joined: Setup Assistant's join times out after 80 s,
`networksetup -setairportnetwork` fails with -3900, and airportd logs `CWFIsCodesignedByApple ...
SecCodeCheckValidityWithErrors() returned -67050`.

## How it works

`src/identity.c` builds `/usr/lib/gg-code-identity.dylib`, an interposer loaded only by the native `tccd`,
`backgroundtaskmanagementd` and `airportd`. When a code-signing query names an executable whose full SHA-256 and
path match the fixed map built from the image's hao-runtime payload, it selects the preserved Apple-signed arm64e
slice and asks Security.framework to verify the original signature against `anchor apple`. Unlisted, modified or
invalid code uses the ordinary API. Callers' validity checks and requirements are unchanged, explicit architecture
queries pass through, and no TCC grants are added. Bundle aliases resolve only to the bundle's declared main
executable.

Running programs (dynamic identity) are covered only in the observers `make` marks dynamic, today `airportd`.
airportd decides whether a Wi-Fi client is Apple's with CoreWiFi's `CWFIsCodesignedByApple(audit token)`:
`SecCodeCreateWithAuditToken(token, 0)`, then `SecCodeCheckValidityWithErrors(code, 0, "anchor apple")`. The
shim interposes `SecCodeCheckValidity` and `SecCodeCheckValidityWithErrors`. Only when the ordinary check failed
on the requirement alone (`errSecCSReqFailed`, default flags) does it look further: the guest must still pass the
ordinary validity check without a requirement (validly signed, and the kernel's cdhash equals the file at its
path), the file must be an exact map entry, its original slice must satisfy `anchor apple` and the caller's own
requirement, and the guest and file are checked again afterwards. Anything else returns the original result.

`make` builds the dylib and the edited observers (`src/macho.py` keeps their original signed slices; the native
slice gains the dylib dependency). `tccd` and `backgroundtaskmanagementd` come from the pristine System; `airportd`
is Tahoe's x86_64 one from the native donor root the image installs (27.x has no Intel airportd), so it replaces
the payload's translated airportd in the map. `src/kc-hook.sh` runs `src/install.py`, which installs them only
over the exact originals the artifact was built from. The observers' original arm64e slices stay byte-identical.

Limits: static identity at these three consumers and dynamic identity at airportd only. Other daemons that
check a running client's signature (for example sharingd, bluetoothd, locationd, or tccd's own audit-token checks)
are unchanged, as are kernel platform flags and other signature checkers. A process running the exact mapped file
is treated as that Apple program even though its ad hoc native slice lacks the platform-binary protections of the
original. Entitlement checks are unaffected (the payload keeps each program's entitlements).

## Build

```sh
python3.14 patches/golden-gate-code-identity/make PAYLOAD ORIGINAL_SYSTEM /path/to/artifacts \
  --native-donors NATIVE_DONORS/payload/system
```

`PAYLOAD` is the hao-runtime payload the image uses; `ORIGINAL_SYSTEM` is the pristine Golden Gate System root of
the same build; `NATIVE_DONORS` is the Tahoe native donor artifact the image installs (row `tahoe_native_donors`). The
generator verifies every original executable hash against the payload manifest, builds the fixed map, and records
replacement hashes and source inputs in `build.json`. The artifact contains Apple binaries and stays outside git;
point input row `code_identity` at it.

When the image includes the Live Text runtime, add `--live-text-runtime /path/to/runtime/artifact`. The generator
then reconstructs the Photos launcher from the pristine executable and the payload's signed stub (using the
payload's `hao_fat.py`), checks source, stub, bundle files and result against the payload manifest, applies the
runtime artifact's dependency editor (`src/live_text.py`) and checks that every original arm64e slice is
unchanged. Only Photos' executable and its main-bundle alias receive the final hash, which replaces the old one.
The dependency root must be the artifact's canonical `/Library/hao-live-text/rN` folder with a matching load path,
so each runtime version yields its own Photos hash. Without the option, generation is unchanged.

## Verify

```sh
IDENTITY_FIXTURE=/path/to/embedded/findmydevice-user-agent \
  python3.14 -I -m unittest discover -s patches/golden-gate-code-identity/tests
```

An exact embedded Find My fixture must fail `anchor apple` normally and pass through the shim. Changing its native
code, arm64e code, pathname or original source must prevent substitution.

`DynamicTests` need no Apple input: they build a fat guest from an ad hoc x86_64 test program and the host's
Apple-signed arm64e `/usr/bin/true` slice, run it, and check it by audit token as CoreWiFi does. A named observer
must turn -67050 into 0; the same observer at another path, an unlisted copy, a different native program beside the
same Apple slice, and a requirement the original does not meet must all keep -67050.

To check the Login Items framework itself on the target (never on the build host), build the read-only probe:

```sh
clang -arch x86_64 -fobjc-arc -framework Foundation tests/btm_probe.m -o btm-probe
codesign -f -s - btm-probe
```

Run it on the same application paths normally and then with `DYLD_INSERT_LIBRARIES` pointing at the identity
dylib. It calls `BTMCodeSignInfo` from the image's framework and prints the system-app classification and
attribution; it registers nothing and changes no permission decisions.

Wi-Fi on the target: Setup Assistant's network join and, as root, `networksetup -setairportnetwork en1 SSID
PASSWORD` succeed; `log show --predicate 'process == "airportd"'` shows `gg-code-identity: verified running ...`
for the client and no `CWFIsCodesignedByApple ... -67050`; airportd stays native and running.

On a fresh Setup with a normal user: Find My runs without the Bluetooth prompt, unrelated apps still get ordinary
permission prompts, Login Items attributes App Store, Calendar, FaceTime, Phone and System Settings to Apple, and
both observers stay native and running. Existing records may need ordinary re-registration; never erase user
decisions to get a clean result.

## Validation

NEEDS VALIDATION. r7's airportd observer and dynamic identity are host-tested only; a Wi-Fi join on the target is
not yet proven. Host tests pass. On the target, the shim turns Find My's `anchor apple` result from failure to
success and makes `BTMCodeSignInfo` classify Calendar, FaceTime and Find My as system apps; correct Login Items
attribution after ordinary registration and a full image with this revision are not yet proven.

## Revisions
- r7 2026-11-11 Load the shim in Tahoe's airportd and validate running mapped clients (audit-token identity) against their original Apple slice, so macOS 27 programs can join Wi-Fi. Boot validation pending.
- r6 2026-11-11 Generate the identity map from the 26B131 System and matching hao payload. Boot validation pending.
- r5 2026-11-10 Initial public release.
