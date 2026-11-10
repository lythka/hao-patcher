# corespotlight-fix

Standalone build and offline installer for [golden-gate-corespotlight-compat](../README.md), for use outside the
image pipeline. See the patch README for the root cause and validation state. Run the commands from this folder.

## Build

Requirements: macOS with Xcode command-line tools/SDK, Python 3 and codesign. The SDK provides Apple's link stubs;
no Apple binaries are distributed here.

```sh
python3 patch build --output /absolute/path/to/artifacts
```

Prints a new artifact directory containing the signed `corespotlight_compat.dylib` and `build.json`.

## Install

Supply a writable, offline Golden Gate 26A428 System volume containing Apple's `corespotlightd` and
`managedcorespotlightd`, then run as root:

```sh
sudo python3 patch install --system /Volumes/TargetSystem \
  --artifact /absolute/path/to/artifacts/corespotlight-compat-26A428-DATE-HASH
```

This patches and signs both daemons and installs the adapter, using the patch's own `src/kc-hook.sh`. Mounting,
snapshot creation and boot selection stay with the caller. The command refuses `/`; it does not format disks or
modify accounts.

## Verify

Run the patch tests as described in the [patch README](../README.md#verify).

## License

Original project code is under the repository's [MIT license](../../../LICENSE). Third-party code and Apple
components keep their own terms; see [NOTICE.md](NOTICE.md).

## Revisions
- r1 2026-11-10 Initial public release.
