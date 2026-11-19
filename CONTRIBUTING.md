# Contributing to hao-patcher

Bug reports and pull requests are welcome. Please describe the macOS build, the
hardware and the exact image recipe you used, because most problems depend on all
three.

## Development setup

Maintainers work on an Apple silicon Mac with the Xcode command-line tools, Python 3.14 and QEMU (for boot
tests); building images there uses the builder VM and the inputs in [docs/inputs.md](docs/inputs.md), and building
our own assets from source needs Xcode 27. Users need none of this: a release builds its USB installer with `hao`,
without Apple silicon or a virtual machine ([the builder path](docs/builder-path.md)).

## Tests

Host tests use fixtures and never start a VM or touch a disk:

```sh
python3.14 -I -B -m unittest discover -s pipeline/tests
python3.14 -I pipeline/patches.py check
```

Each tool and patch with a `tests/` folder (or a `test.sh`) documents its own
command in its README; run the suites for the code you change. Then build an image
and boot it in [QEMU](tools/qemu-vm/README.md) before you test it on hardware.

## Changing a fix

A fix is a recipe in `patches/` plus the source that builds its inputs.
[patches/README.md](patches/README.md) describes the format. In short:

- Bump `revision` in the patch's `patch.json` and add a line to its README's
  `## Revisions` section.
- Set `validated` to `false` until a boot test proves the new revision, and start
  the README's `## Validation` with `NEEDS VALIDATION`.
- Regenerate the three generated files (below). The tests compare each one byte for byte.
- Keep version-specific data (OS builds, models, hashes) in input rows, targets and
  `patch.json`, not in pipeline code.

## Worked example: one recipe change, clone to pull request

This replays commit `f2e7a11e`, which added one launchd service to
[golden-gate-unsupported-daemons](patches/golden-gate-unsupported-daemons/README.md) (r2 to r3).
On Macs without a Secure Enclave, `com.apple.prngseedd` exits at once and launchd restarts it about every 10 s.
None of these steps need the store, a VM or a Mac to test on.

**1. Edit the recipe** (three files in `patches/golden-gate-unsupported-daemons/`):

| File | Change |
|---|---|
| `src/disabled-daemons.txt` | One line: the label and its reason, with the evidence |
| `patch.json` | `revision` 2 to 3; `validated` true to false; remove `validated_revision` and `validated_digest` |
| `README.md` | Why the label is there; `NEEDS VALIDATION (r3: ...)` at the top of `## Validation`; an `r3` line under `## Revisions` |

**2. Run the recipe's own suite and the format check:**

```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-unsupported-daemons/tests
python3.14 -I pipeline/patches.py check
```

Both pass, but `pipeline/tests` now fails: `test_tracker_is_current`, `test_catalog_is_current` and
`test_manifest_is_current`. They fail because the revision appears in three generated files.

**3. Regenerate them:**

```sh
python3.14 -I pipeline/patches.py tracker     # patches/TRACKER.md
python3.14 -I pipeline/patches.py catalog     # docs/PATCHES.md
python3.14 -I pipeline/patches.py manifest    # docs/MANIFEST.md
```

If you add a recipe to a profile (`pipeline/profiles/NAME.json`) or remove one from it, also regenerate that profile's set:
`python3.14 -I pipeline/patches.py set pipeline/profiles/NAME.json pipeline/sets/NAME.txt`.

**4. Check the diff.** Expect the three recipe files plus one changed row or line in each generated file:

```text
 docs/MANIFEST.md                                                 | 4 ++--
 docs/PATCHES.md                                                  | 2 +-
 patches/TRACKER.md                                               | 2 +-
 patches/golden-gate-unsupported-daemons/README.md                | 8 ++++++++
 patches/golden-gate-unsupported-daemons/patch.json               | 8 +++-----
 patches/golden-gate-unsupported-daemons/src/disabled-daemons.txt | 1 +
```

In `TRACKER.md` the recipe's row goes from `r2 ... ok (r1)` to `r3 ... **needs**`. A change to any file you did not edit
means your clone was stale or you changed something else.

**5. Commit, then run CI.** `pipeline/ci` runs every check a push must pass: the format, sets, edit specs, the release
matrix, hygiene and every test suite. It checks out the committed tree in a temporary worktree, so commit first:

```sh
git commit -am "golden-gate-unsupported-daemons r3: disable prngseedd (no SEP)

Tested: unsupported-daemons tests; patches.py check; pipeline/ci"
python3.14 -I pipeline/ci                  # about 80 s; ends "ci: COMMIT PASS: N checks, 0 failed"
python3.14 -I pipeline/ci --list           # the checks it runs
python3.14 -I pipeline/ci --only layout    # one check while you iterate (--here: uncommitted tree)
```

Maintainers run the same command on your pull request.

**6. Open the pull request.** Say which suites and CI you ran and what still needs a boot. A boot-only change
(here: the service stays off and nothing else respawns) stays `validated: false`. A maintainer sets it after a
boot test of the new revision on the model and macOS build it targets.

## Rules

- Never commit Apple binaries, IPSW or installer contents, kernels, caches or files
  larger than 1 MB. Store them under `store/` and record their SHA256 in an input row.
- Never run APFS formatting or mutation tools (`newfs_apfs`, `apfs_*`) on the host;
  they run only inside the builder VM.
- Ad-hoc sign every x86_64 binary you build before running it (`codesign -s - FILE`).
- Never launch GUI applications built for the target on your host.
- Keep third-party source with its license and list it in that tool's `NOTICE.md`.
- The repository is public: `pipeline/tests/test_private_references.py` fails on paths into private repositories or home folders.

## Commit messages

Say what changed and why, and end with a `Tested:` line that says what you
actually ran.
