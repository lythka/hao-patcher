# Contributing to hao-patcher

Bug reports and pull requests are welcome. Please describe the macOS build, the
hardware and the exact image recipe you used, because most problems depend on all
three.

## Development setup

You need an Apple silicon Mac with the Xcode command-line tools, Python 3.14 and
QEMU (for boot tests). Building images also needs the builder VM and the inputs in
[docs/inputs.md](docs/inputs.md).

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
- Regenerate the tracker with `python3.14 -I pipeline/patches.py tracker`.
- Keep version-specific data (OS builds, models, hashes) in input rows, targets and
  `patch.json`, not in pipeline code.

## Rules

- Never commit Apple binaries, IPSW or installer contents, kernels, caches or files
  larger than 1 MB. Store them under `store/` and record their SHA256 in an input row.
- Never run APFS formatting or mutation tools (`newfs_apfs`, `apfs_*`) on the host;
  they run only inside the builder VM.
- Ad-hoc sign every x86_64 binary you build before running it (`codesign -s - FILE`).
- Never launch GUI applications built for the target on your host.
- Keep third-party source with its license and list it in that tool's `NOTICE.md`.

## Commit messages

Say what changed and why, and end with a `Tested:` line that says what you
actually ran.
