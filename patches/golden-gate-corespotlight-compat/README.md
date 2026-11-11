# golden-gate-corespotlight-compat

Adds `-[CSSearchQueryContext compiledRankingFactsConfiguration]` to Darwin 27.0's x86_64 CoreSpotlight, loaded into
the native `corespotlightd` and `managedcorespotlightd`, which call it for every Spotlight query. Without it both
daemons crash on every query.

## Root cause

`corespotlightd` and `managedcorespotlightd` are universal and run natively. SpotlightDaemon's
`-[SPCoreSpotlightTask _makeSIQueryWithQueryString:queryContext:]` sends `compiledRankingFactsConfiguration`, but
26A428's x86_64 CoreSpotlight (the Rosetta slice) lacks that method; only the arm64e build has it. The query raises
"unrecognized selector", and libdispatch's callout unwind information then makes libunwind fault instead of
aborting cleanly. Each crash also starts a ReportCrashService that costs about 20 s of CPU, so the daemon's restart
loop keeps the machine busy. Both slices have the `_compiledRankingFactsConfiguration` ivar and
`-rankingFactsConfiguration`; x86_64 `_CSRankingModelConfiguration` has no `-compile`.

26B131 (27.2.1) is the same: its x86_64 CoreSpotlight 2465.1.9 (UUID `534B9EB3-36F4-3D46-9640-3E5181279147`) still
lacks the getter and has the `NSDictionary` ivar `_compiledRankingFactsConfiguration`, `_CSRankingModelConfiguration`
still has no `-compile`, and the x86_64 SpotlightDaemon still references the selector. The library looks the class
and ivar up by name, so the same artifact serves both builds.

## How it works

`src/corespotlight_compat.m` builds `corespotlight_compat.dylib` (x86_64, links Foundation and CoreSpotlight). At
load it adds the getter only when the class lacks it, mirroring the arm64e code: with a ranking configuration it
returns `[configuration compile]` (nil where x86_64 has no `-compile`, which selects default ranking); otherwise the
ivar. If Apple ships the method, the library does nothing.

`src/kc-hook.sh` (run after `golden-gate-userspace`) uses `src/add_load_dylib.py` to append
`LC_LOAD_DYLIB /usr/lib/corespotlight_compat.dylib` to the x86_64 slice of both daemons (other slices unchanged),
re-signs each ad hoc with its identifier, entitlements and flags (no hardened runtime, so no library validation),
and installs the dylib.

## Build

```sh
python3.14 patches/golden-gate-corespotlight-compat/make /path/to/artifacts
```

Needs Xcode clang/SDK and codesign. Produces `corespotlight-compat-26A428-DATE-HASH12/` with the ad-hoc-signed
dylib and `build.json`; point input row `corespotlight_compat_shim` at the dylib. [build/](build/README.md) wraps
the same build and an offline installer for use outside the image pipeline.

## Verify

```sh
CORESPOTLIGHT_SYSTEM=/path/to/26A428/SystemRoot \
  python3.14 -I -m unittest discover -s patches/golden-gate-corespotlight-compat/tests
```

The getter test runs the dylib under Rosetta on an Apple silicon Mac (whose x86_64 CoreSpotlight lacks the getter
too): the method is absent before loading and present after, returns nil for a fresh context and the ivar's value
when set. The load-command test edits the 26A428 daemons from `CORESPOTLIGHT_SYSTEM` (skipped when unset) and checks
that only the x86_64 slice changes. On the target, the unified log has `corespotlight-compat: added ...` and both
daemons keep their PIDs with no new crash reports.

## Validation
Laptop 2026-11-11, image 4d1c48c (26B131): Spotlight queries run through corespotlightd and mds and return results.
Earlier: The host getter and donor Mach-O tests pass and the dylib builds with `-Werror`; installation in
a built image and stable daemons on the MacBookPro9,2 are not yet proven.

## Revisions
- r4 2026-11-11 Checked against 26B131: the getter is still missing and the ivar unchanged; the 26A428 artifact serves it unchanged.
- r3 2026-11-10 Initial public release.
