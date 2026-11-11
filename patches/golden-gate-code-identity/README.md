# golden-gate-code-identity

Lets the privacy service (`tccd`) and the Login Items service (`backgroundtaskmanagementd`) see the original Apple
identity of translated system programs. hao-runtime gives each arm64-only Apple program a generated x86_64 entry
slice that is signed ad hoc, so code-signing queries on that slice lose the program's Apple identity. Visible
symptoms: Find My shows an unexpected Bluetooth permission prompt, and Apple's Dock tile plugins appear as newly
added third-party background items.

## How it works

`src/identity.c` builds `/usr/lib/gg-code-identity.dylib`, an interposer loaded only by the native `tccd` and
`backgroundtaskmanagementd`. When a code-signing query names an executable whose full SHA-256 and path match the
fixed map built from the image's hao-runtime payload, it selects the preserved Apple-signed arm64e slice and asks
Security.framework to verify the original signature against `anchor apple`. Unlisted, modified or invalid code
uses the ordinary API. Callers' validity checks and requirements are unchanged, explicit architecture queries
pass through, and no TCC grants are added. Bundle aliases resolve only to the bundle's declared main executable.

`make` builds the dylib and the edited observers (`src/macho.py` keeps their original signed slices; the native
slice gains the dylib dependency). `src/kc-hook.sh` runs `src/install.py`, which installs them only over the exact
originals the artifact was built from. The observers' original arm64e slices stay byte-identical.

Limits: this covers static identity at these two consumers only. It does not address dynamic audit-token identity,
kernel platform flags or other signature checkers.

## Build

```sh
python3.14 patches/golden-gate-code-identity/make PAYLOAD ORIGINAL_SYSTEM /path/to/artifacts
```

`PAYLOAD` is the hao-runtime payload the image uses; `ORIGINAL_SYSTEM` is the pristine 26A428 System root. The
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

To check the Login Items framework itself on the target (never on the build host), build the read-only probe:

```sh
clang -arch x86_64 -fobjc-arc -framework Foundation tests/btm_probe.m -o btm-probe
codesign -f -s - btm-probe
```

Run it on the same application paths normally and then with `DYLD_INSERT_LIBRARIES` pointing at the identity
dylib. It calls `BTMCodeSignInfo` from the image's framework and prints the system-app classification and
attribution; it registers nothing and changes no permission decisions.

On a fresh Setup with a normal user: Find My runs without the Bluetooth prompt, unrelated apps still get ordinary
permission prompts, Login Items attributes App Store, Calendar, FaceTime, Phone and System Settings to Apple, and
both observers stay native and running. Existing records may need ordinary re-registration; never erase user
decisions to get a clean result.

## Validation

NEEDS VALIDATION. Host tests pass. On the target, the shim turns Find My's `anchor apple` result from failure to
success and makes `BTMCodeSignInfo` classify Calendar, FaceTime and Find My as system apps; correct Login Items
attribution after ordinary registration and a full image with this revision are not yet proven.

## Revisions
- r6 2026-11-11 Generate the identity map from the 26B131 System and matching hao payload. Boot validation pending.
- r5 2026-11-10 Initial public release.
