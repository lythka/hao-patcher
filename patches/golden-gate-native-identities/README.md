# golden-gate-native-identities

Lists the native programs this image re-signed ad hoc next to the Apple-signed originals they were made from, so the
code identity carrier in the image's Security.framework ([golden-gate-userspace](../golden-gate-userspace/README.md),
"Code identity") can answer a requirement check such as `anchor apple` for them as Apple would for the original.

Several recipes edit Apple's native programs and re-sign them ad hoc: Tahoe's WindowServer takes 27.x's Info.plist
and entitlements (golden-gate-userspace), `corespotlightd` and `managedcorespotlightd` load the CoreSpotlight adapter
(golden-gate-corespotlight-compat). An ad hoc signature has no Apple anchor, so any daemon that checks such a program
against `anchor apple` refuses it. Programs that hao-runtime runs through a stub are listed by hao itself
(`/usr/lib/hao/stub-identities`); this recipe lists only native programs.

## How it works

`src/kc-hook.sh` runs last among the oclp-hook recipes, after every recipe that re-signs. It mounts the build's
pristine 27.x System (`golden_gate_userspace`'s `filesystem.dmg`, read-only) and runs `src/native_identities.py
install` with two source roots in order: that System and the Tahoe native donors (`TAHOE_NATIVE_ARTIFACT/payload/
system`, set by `pipeline/stages/golden-image.py`).

`native_identities.py` walks the System for executables (MH_EXECUTE) whose x86_64 slice is signed ad hoc. For each it
takes the first source root that has, at the same path, the program's original:

- a signed x86_64 slice that is not ad hoc, with the same signing identifier;
- the same program: at least 90% of the original's signed bytes after the load commands are unchanged at the same
  offsets (compared in 64-byte pieces), which holds for an inserted load command or a replaced Info.plist section but
  not for a different program installed at that path (OCLP's own programs, for example);
- copied to `/usr/lib/hao/originals/<path>` (root:wheel, 0644, directories 0755), the copy passes
  `codesign --verify --arch x86_64 -R='anchor apple'`.

It then writes `/usr/lib/hao/native-identities` (root:wheel 0644) in hao-runtime's format
(`include/hao/idlist.h`): `<observed cdhash>  <reference cdhash>  x86_64  usr/lib/hao/originals/<path>`. The
observed cdhash is that of the re-signed slice (what the kernel reports for the running program); the reference is
the original slice's. Programs in `stub-identities` and programs without such an original get no row; the record
`System/Library/CoreServices/golden-gate-native-identities.json` names each with the reason. A program that is still
Apple-signed is not touched and needs no row.

The walk reads only what a decision needs: for every executable of at least 4 KiB, the fat header, the x86_64 slice's
Mach-O header and load commands and its code signature blob (the CodeDirectory gives the cdhash, the identifier, the
flags and the CMS blob); the whole slice of the observed program and of its original is read only for the 90%
comparison, which only programs with a matching Apple-signed original reach. On the 26B131 image that is 172 MB
instead of 7.4 GB (6,500 executables, 4 compared), with the same rows and the same skips as reading every file whole.

On the 26B131 inputs the expected rows are WindowServer (original: Tahoe's, from the native donors), `corespotlightd`
and `managedcorespotlightd` (originals: 27.2.1's universal programs). A host simulation of the WindowServer and
`corespotlightd` edits against those inputs gave both rows (307/317 and 1501/1501 pieces unchanged).

Trust: the list sits on the sealed System volume; hao-runtime reads it only when root owns it and every directory up
to `/` and none is writable by group or others. The carrier gives a listed program exactly the answers its Apple
original gets; the program itself is the edited one (security trade-off: hao-runtime `docs/security-model.md`,
"Apple identity for listed programs").

Declared files: `patch.json` `files` lists `usr/lib/hao/native-identities`, the record, and the three originals the README expects (WindowServer from Tahoe, `corespotlightd` and `managedcorespotlightd` from 27.2.1). They are generated or copied whole, so no edit spec is needed; `tests/test_edits.py` checks the paths against the script. Each says `"after": ["overlays"]`: the list is keyed on the CDHashes of the re-signed programs as installed (an overlay signed on another machine has other CDHashes, if only from signature padding), so it is generated on the target after every overlay file is in place; `pipeline/materialize.py` defers it and lists it last in `deferred.json`.

## Verify

```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-native-identities/tests
python3.14 -I patches/golden-gate-native-identities/src/native_identities.py scan SYSTEM --source ROOT ...
```

The tests build fixtures from this Mac's Apple-signed `/usr/bin/true` and `/usr/bin/false`: a re-signed copy gets
a row whose cdhashes match `codesign`; a changed identifier, a different program at the original's path, a program
without an original and a listed hao stub get none; the installed original and list have the stated modes; a second
run gives the same list.

On a built image: `/usr/lib/hao/native-identities` lists WindowServer, `corespotlightd` and `managedcorespotlightd`;
each `usr/lib/hao/originals/...` copy passes `codesign --verify --arch x86_64 -R='anchor apple'`; and
`codesign --verify -R='anchor apple' /System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/Resources/WindowServer`
passes on the image (codesign checks through the image's Security.framework, so through the carrier), where the
same file fails on any other Mac.

The header-only read is checked against a whole-file read on fixtures (thin, fat, ad hoc, truncated at every
header boundary) and, with `NATIVE_IDENTITIES_SYSTEM=ROOT NATIVE_IDENTITIES_SOURCES=ROOT:ROOT` (a built image's System
mounted read-only and its record's two sources), on a real System:
`python3.14 -I -B -m unittest discover -s patches/golden-gate-native-identities/tests -k test_real_system`.

## Validation
NEEDS VALIDATION: r4 reads headers and signatures instead of whole files. On the exported 26B131 image (System
read-only, the record's two sources) its scan equals r3's byte for byte (rows and skips), the list it gives equals the
image's `/usr/lib/hao/native-identities`, and all 11,517 Mach-O files of that System and the pristine 27.2.1 System
give the same signature and slice bytes; the hook is otherwise unchanged. VM build 2026-11-13 (dev-26B131): same 3
rows and 936 skips; the step took 220 s (232 s before), so in the builder reading the files was not the main cost
(the walk of the System's ~340k entries is the likely one). The hook's log now gives the walk's seconds, entries and
executables read, to confirm that on the next build.
r1: laptop 2026-11-11, image 4d1c48c (26B131): Setup's automatic login after account creation is authorized in 16 s (system.login.console with the auto-login mechanism).
Earlier: Host tests pass and a host simulation of the 26B131 WindowServer and `corespotlightd` edits gives
their rows. No image has been built with this recipe.

## Revisions
- r5 2026-11-13 Input rows record their content sha256 (folder rows sealed with `lock.py seal`); the same artifacts at the same paths, so what this recipe installs is unchanged.
- r4 2026-11-13 `native_identities.py` reads each executable's headers and code signature only, and a slice's bytes only for the 90% comparison (172 MB instead of 7.4 GB on 26B131); same rows, skips and list. Logs the walk's time and counts.
- r3 2026-11-12 Declare that its files are made on the target after every overlay file is installed (`"after": ["overlays"]`): the list is keyed on the CDHashes of the files as installed; the hook is unchanged. Keeps r1's validation: what it installs is unchanged (validated_digest).
- r2 2026-11-12 Declare the files it writes (the list, the record and the three expected originals) in patch.json `files`; the hook is unchanged.
- r1 2026-11-11 Initial: Apple originals of re-signed native programs under /usr/lib/hao/originals and the native-identities list, replacing golden-gate-code-identity's map and per-daemon interposer.
