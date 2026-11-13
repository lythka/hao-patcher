# Declaring a recipe's edits

How to write down what a recipe changes, as edit specs and `patch.json` `files`, without changing what it does.
Follow this exactly. The worked example is `patches/golden-gate-hello` r4: `edits/metal.json`,
`patch.json` `files`, `tests/test_edits.py`.

A declaration is accepted only when a test proves that the spec, applied by `tools/edit-engine`, writes **the same
bytes** as the recipe's existing code: on a synthetic file every time the tests run, and on the real input file
from the store when its environment variable is set. Never make a spec "close enough".

## 1. Read the recipe

Read `patches/NAME/patch.json`, `README.md`, every file in `src/`, and the producer of any input row it installs
(the README's table says where the source lives: `tools/…`, `cache/…` or the patch's `build/`). List every file the
recipe adds or changes on the installed system: the install path, how the hook gets it, and who made the bytes.

## 2. Decide each file's entry

| The hook… | `files` entry |
|---|---|
| installs a whole file it does not edit (an input row, a built artifact) | `{"path": P, "origin": {"kind": "hao", "row": ROW, "note": …}}`. `row` must be one of the patch's `inputs` |
| edits a file someone else made (Apple, Tahoe donor, OCLP, or another recipe's output) | `{"path": P, "edits": "edits/SHORT.json"}` plus that spec |

Origins: `apple` = Golden Gate's own file, `tahoe` = a Tahoe donor, `oclp` = OCLP's payload, `hao` = ours.

Install attributes: give every entry the hook's `install`: `{"mode", "owner", "flags"}` exactly as the hook leaves
the file (`install -o root -g wheel -m 755` → `"mode": "0755", "owner": "root:wheel"`; `chflags restricted`, or a
copy of the flags of the file it replaces when that file has them → `"flags": ["restricted"]`). Without it the
materialiser writes root:wheel, 0755 for a Mach-O and 0644 otherwise, no flags. Files from a tar member keep the
member's mode and numeric owner (as `tar -xp --numeric-owner`), so they need no `install` unless the hook changes
them. A file made on the target that must wait for others there says so: `"after": [PATH | "overlays"]`
(`overlays`: after every overlay file and every other target step, e.g. anything keyed on installed CDHashes).

## 3. Write the spec

Copy the shape of `patches/golden-gate-hello/edits/metal.json`. The format is in this folder's README.

- **Bytes checked and written at fixed places** → `literal`. Anchor on a defined symbol (`base` + `limit` ≤ 256):
  run `nm -m` on a *copy* of the real input to find one. Make `find` long enough to match exactly once
  (`count` 1); include the unchanged bytes around the change. Use `section` when no symbol is near, and `at` only
  when neither works. Fill `asm` before/after from a disassembly of the copy (`otool -tV` or
  `llvm-objdump -d --start-address`), not from memory.
- **Sites computed by code** (plans of `(offset, before, after, reason)`) → `matcher`. If the code exposes a
  function that takes the file's bytes, name it in `fn`. Always add `resolved` sites for the real input's sha256,
  copied from that code's own output.
- **Load command added** → `macho.add-dylib`, with exactly the command type (`weak`), `timestamp` and versions the
  existing code writes. Read the `struct.pack` call.
- **Plist key set** → `plist.set`.
- **Metallib rewrite, kernel-collection link, cryptex build, hao stub embedding, HaoExec's identity stamp, kernel
  repairs bound to the BootKC** → the external op (`metallib.rewrite`, `kc.link`, `cryptex.build`, `hao.embed-stub`,
  `hao.stamp`, `kc.bind`) with `args` naming its profile or row (`hao.stamp`, `kc.bind`: `kernel_collection`, the
  installed file it reads). That is a declaration only;
  no byte test is possible for it, so say so in the report. A file inside a tree the same recipe declares whole (a
  kext folder) can have its own entry like this: the materialiser makes it from that entry, never from the tree.
- Every edit gets a `reason` in plain words (what the user gets, not how), taken from the recipe's README.
- `sign`: `{"mode": "adhoc", "identifier": …}` when the hook runs `codesign -f -s -` (copy its `-i`), else `null`.
  Copy the rest of the call too: `--preserve-metadata=A,B` → `"preserve": [A, B]` (and `identifier` when the hook
  reads `-i` from the file itself); `--entitlements FILE` where FILE comes from another program
  (`codesign -d --entitlements - --xml OTHER`) → `"entitlements_from": {"kind", "path"}` naming OTHER. Equal bytes
  outside the signature are not enough: `src/signlint.py` refuses a hook call no spec accounts for, and
  `verify-declaration --reference` compares identifier, flags and entitlements with what the hook installed.
- `expect`: `{real input sha256: output sha256}`, from running the existing code on a copy (step 4).

If a change cannot be expressed with these kinds, **stop for that file**: declare the others, and say in your
report exactly what is missing. Do not change `tools/edit-engine`.

## 4. Find the real input and the reference output

Real inputs are under `store/` (a symlink in the main checkout `~/hao-patcher`; a worktree has none, so use the
main checkout's `store/` by absolute path, read-only). OCLP's payload is in
`store/artifacts/oclp-hardware-mbp92-*/payload/`. Golden Gate and Tahoe files are in the artifacts the input rows
name: `python3.14 -I pipeline/lock.py path pipeline/inputs/BUILD ROW` resolves a row. Copy the file to your scratch
folder and work only on the copy. Run the recipe's existing pure-Python edit code on the copy to get the reference
output. Never run hooks, `install`, `codesign` on system files, or anything that mounts, formats or writes disks.

## 5. Write the test

`patches/NAME/tests/test_edits.py`, modelled on golden-gate-hello's:

1. `test_spec_follows_the_format`: `spec.validate` is empty, and `patch.json` `files` lists the spec.
2. `test_same_bytes_…_synthetic`: build a synthetic input with `tools/edit-engine/tests/fixture.py` (or a few
   lines of `struct`), at the same offsets and with the same bytes the existing code expects. Run the existing code
   (subprocess `sys.executable -I`, or import its pure function) and the engine, and assert the bytes are equal.
3. `test_same_bytes_…_real`: skipped unless `NAME_IN_UPPER_SNAKE_INPUT` (one variable per real file) points at
   the real input. Assert equality with the existing code and `record['expected'] == 'match'`.

When the existing code cannot run on the host (it needs root, a mount, or `xcrun`), test the spec against its
recorded reference output instead, and explain why in the test docstring and the report.

## 6. Bump and regenerate

- `patch.json`: `revision` + 1, add `files`. Leave `validated`, `status`, `inputs` and `applies` alone.
- `README.md`: a sentence where the README describes the edit, pointing to `edits/…json` and the test; the test
  command in `## Verify`; a newest `## Revisions` line `- rN 2026-11-12 Declare … in edits/…; the hook is
  unchanged.`
- `python3.14 -I pipeline/patches.py tracker && python3.14 -I pipeline/patches.py catalog`
- These must all pass:
  ```sh
  python3.14 -I pipeline/patches.py check
  python3.14 -I -B -m unittest discover -s pipeline/tests
  python3.14 -I -B -m unittest discover -s tools/edit-engine/tests
  python3.14 -I -B -m unittest discover -s patches/NAME/tests          # with and without the real-input variables
  ```

## Rules

- Change only `patches/NAME/{patch.json,README.md,edits/,tests/}`, `patches/TRACKER.md` and `docs/PATCHES.md`.
  Never change a hook, `src/`, `build/`, an input row, `tools/edit-engine`, another patch, or any pipeline code.
- Never edit pinned producers: `patches/hao-opencore/src/repair_binding.py`, `tools/kernel-fixes/*`,
  `darwin27-kernel/src/kc-hook.sh`.
- Never commit Apple binaries, files over 1 MB, or private paths (`/Users/…`, evidence folders). Real inputs
  reach tests only through environment variables.
- Work in your own worktree and branch from `builder/edit-engine`. Commit there with a message that says what
  changed and why and ends with a `Tested:` line naming the exact commands and which real-input tests ran. Do not
  push or merge.

## Acceptance

The maintainer runs, on your branch, with each real input you name:

```sh
python3.14 -I tools/edit-engine/verify-declaration declare/YOURS --env NAME=PATH ...
python3.14 -I tools/edit-engine/verify-declaration declare/YOURS --system ROOT --reference PATH=FILE ...
```

It refuses changes outside the allowed paths, a revision that does not go up by exactly 1, any other
`patch.json` change, private paths, a hook signing call no spec accounts for (signlint), and any failing suite; a
real-input test without its file is UNVERIFIED. With `--reference` (a file the recipe's hook installed, e.g. from a
built image), the branch's materialiser makes that path and it must have the same code outside the signature and
the same signing identifier, flags and entitlements; a different CDHash from signature padding is only a note.

## Report

For each recipe: the files declared (path, origin or spec, edit kinds); anything not declared and why; each real
input used (store path and sha256) and the reference output sha256; the test commands and their results
(including which tests were skipped); your branch and commit.
