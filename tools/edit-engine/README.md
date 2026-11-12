# edit-engine

Applies declared file edits. A recipe that changes a file someone else made (Apple, a Tahoe donor, OCLP) writes the
change down as an **edit spec**, `patches/NAME/edits/FILE.json`: where the change goes, the bytes it expects, the
bytes it writes, and why. This tool applies a spec to a file, checks every write before and after, and explains
specs in plain text. `pipeline/patches.py check` validates every spec, and `patches.py explain` and
[docs/PATCHES.md](../../docs/PATCHES.md) render them.

Each file is described as one origin, then ordered edits, then one signature:

```
final file = sign( edit_n( … edit_1( origin ) ) )
```

## Usage

Python 3.9 or later, standard library only. From the repository root:

```sh
python3.14 -I tools/edit-engine/edit-engine apply SPEC IN OUT                 # one line per edit
python3.14 -I tools/edit-engine/edit-engine apply --show-edits SPEC IN OUT    # + bytes, instructions, reasons
python3.14 -I tools/edit-engine/edit-engine apply --quiet SPEC IN OUT         # failures only
python3.14 -I tools/edit-engine/edit-engine apply --log json SPEC IN OUT      # one JSON event per line
python3.14 -I tools/edit-engine/edit-engine apply --sign --record R.json SPEC IN OUT
python3.14 -I tools/edit-engine/edit-engine apply --store STORE SPEC IN OUT   # keep, and reuse when hashes match
python3.14 -I tools/edit-engine/edit-engine inspect STORE PATH                # original and patched side by side
python3.14 -I tools/edit-engine/edit-engine check SPEC ...
python3.14 -I tools/edit-engine/edit-engine explain SPEC ...
```

`apply` never changes IN. It writes OUT only when every edit applied and every check passed. Otherwise it exits 1
and the message names the edit. `--sign` re-signs OUT ad hoc as the spec's `sign` says (macOS `codesign`).
`--record` writes the input and output SHA-256, each edit's sites (offset, bytes before and after) and whether the
output matches the spec's recorded one.

## Artifact store

With `--store STORE`, every apply keeps its original, its result and its record, and is inspectable afterwards:

```
STORE/objects/<sha256>                    content-addressed bytes (originals and results)
STORE/results/<key>.json                  one apply: input/output sha256 and the full record
STORE/view/<path>/original, patched       links to the two objects, side by side
STORE/view/<path>/record.json, explain.txt
```

`key` is the sha256 of the engine version, the canonical spec, the input's sha256 and the source of every matcher
function the spec names. When a stored result has the same key and its output object still hashes to the
recorded sha256, the build reuses it and does not re-run the edits (`reused` in the output, `file.reused` in the
JSON log). A damaged object, or a changed spec, input, matcher or engine version, means the edits run again.
`inspect` prints where both files are, their hashes, the changed byte ranges and the explanation.

## Spec format 1

```json
{
 "format": 1,
 "path": "System/Library/Frameworks/Metal.framework/Versions/A/Metal",
 "arch": "x86_64",
 "origin": {"kind": "oclp", "name": "Metal 3802 Common"},
 "applied_by": "installer",
 "edits": [
  {"id": "hello-not-skipped", "kind": "literal",
   "base": "_fake_LCAUtilities_shouldSkipHello", "limit": 64,
   "find": "B0 01 0F BE C0 48 83 C4 10 5D C3", "replace": "B0 00 0F BE C0 48 83 C4 10 5D C3",
   "asm": {"before": "mov al,1", "after": "mov al,0"},
   "reason": "Language Chooser plays the hello animation"}
 ],
 "sign": {"mode": "adhoc", "identifier": "Metal"},
 "expect": {"INPUT_SHA256": "OUTPUT_SHA256"}
}
```

| Key | Meaning |
|---|---|
| `path` | the file on the installed system, relative to its root |
| `arch` | `x86_64`: the file is a Mach-O (thin, or universal with one x86_64 slice) and must still parse after the edits |
| `origin` | who made the bytes this spec edits: `kind` `apple` (Golden Gate), `tahoe`, `oclp` or `hao`, with optional `name`, `row` (input row) and `note` |
| `applied_by` | `installer` (default), `opencore` or `kext:IntelCommpage` |
| `edits` | applied in order; each sees the result of the ones before |
| `sign` | `null` or `{"mode": "adhoc", "identifier": …}`: signed once, after the last edit |
| `expect` | input SHA-256 → output SHA-256 recorded for that input. A different output is reported, not refused |

Every edit has `id` (lower-case letters, digits, `-`), `kind`, `reason`, and optionally `evidence` and `asm`
(`before`/`after` text).

**literal**: OpenCore-style find and replace. `find` and `replace` are hex bytes of the same length. `mask` selects
which bits of `find` must match, and `replace_mask` selects which bits `replace` writes. `count` (default 1) is how
many matches there must be, no more and no fewer. Give exactly one anchor:

- `base` + `limit`: search the `limit` bytes from a defined symbol (preferred: survives relinks)
- `section`: search one section, `"__TEXT,__text"`
- `at`: one file offset (`"0x…"`)

If `find` is not there but `replace` is, the error says the file was edited before.

**matcher**: sites that code has to compute. `resolved` maps an input SHA-256 to its checked-in sites
(`at`, `before`, `after`), so the exact bytes can be read without running anything. For an input with no resolved
sites, `fn` (`"relative/path.py:function"`, called with the file's bytes, returning `(offset, before, after, …)`
tuples) computes them. Every site's `before` bytes must be there.

**op**: structural changes.

| op | args |
|---|---|
| `macho.add-dylib` | `path` (absolute), `weak`, `timestamp` (default 2, as ld64 writes), `current_version`, `compat_version`. Uses the zero padding after the load commands and leaves the signature for `sign` |
| `plist.set` | `key` (list of dictionary keys and array indexes), `value`. Keeps XML or binary format. Not mixed with byte edits in one spec |
| `metallib.rewrite`, `kc.link`, `cryptex.build`, `hao.embed-stub` | declared for `explain`; applied by their own tools, so `apply` refuses them |

Checks: every literal and matcher site is checked before it is written. Two edits that touch the same bytes (or
the same plist key) are refused. After the last edit, every written byte must still be what its edit wrote, and an
`arch` file must still parse.

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/edit-engine/tests
python3.14 -I -B -m unittest discover -s patches/golden-gate-hello/tests   # HELLO_OCLP_METAL=PATH: OCLP's wrapper too
```

The tests build synthetic x86_64 Mach-O files (thin and universal), so they need no Apple binaries. They cover
symbol, section and offset anchors, masks, counts, already-applied files, overlaps, matchers, dylib insertion and its
refusals, plist edits, refused external ops, the `expect` note, the result-parses check, and each command-line detail
level, plus the store: reuse, the side-by-side view, re-applying after damage or any change to the key's parts,
and `inspect`. `golden-gate-hello`'s test proves its spec writes the same bytes as its `edit-metal.py`.

## Status

The first piece of the declared-edit catalog (workflow plan step 4). Recipe hooks still run their own scripts, and
specs are proven equal to them by tests. Hooks move onto the engine one recipe at a time.
