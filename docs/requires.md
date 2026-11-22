# `requires` and `proven_on`: a recipe's scope as data

Every recipe states where it is valid in `patch.json`, so a tool can answer "which recipes are valid on this Mac and
these builds" and a build stops instead of installing a recipe outside its scope. Comments in `order.txt` and row
folder names are not scope.

```json
"requires": {
  "os":       ["26B131"],
  "donor":    ["25G342"],
  "cpu":      "any",
  "gpu":      ["intel-ivb"],
  "firmware": "any"
},
"proven_on": [
  {"model": "MacBookPro9,2", "os": "26B131", "donor": "25G342", "revision": 7, "evidence": "laptop boot 2026-11-17, image 0d7818f9"}
]
```

## Keys

All five keys are present. Each is `"any"` or a non-empty list; `"any"` is a statement ("does not depend on it"),
not a default.

| Key | Values | Checked against | Met when |
|---|---|---|---|
| `os` | macOS 27 build ids (`26B131`) | the assembly's `golden_build` | the build is in the list |
| `donor` | Tahoe donor build ids (`25G342`) | the assembly's `donor_build` | the build is in the list |
| `gpu` | GPU families: `intel-snb`, `intel-ivb`, `intel-hsw`, `intel-bdw`, `intel-skl`, `intel-kbl`, `nvidia-tesla`, `nvidia-kepler`, `amd-terascale`, `amd-gcn`, `amd-polaris`, `amd-vega`, `amd-navi` | the target's `facts.gpu` | the Mac has at least one of them |
| `firmware` | the security chip: `none`, `t1`, `t2` | the target's `facts.firmware` | the Mac's is in the list |
| `cpu` | CPU features in lower case, as `machdep.cpu.features` and `leaf7_features` name them (`avx1.0`, `avx2`, `sse4.2`, `f16c`); `!NAME` for "must not have" | the target's OCLP hardware profile, `cpu.flags` and `cpu.leafs` | every feature listed is present, every `!NAME` absent |

- `donor` replaces `applies`: `applies.builds` becomes `requires.donor`, and a recipe has one or the other
  (`check` refuses both). `applies.models` is not carried over: model scope is the recipe's pack.
- A new GPU family or firmware value is added to `GPUS` / `FIRMWARE` in `pipeline/patches.py` with its first recipe
  or target.
- An unknown fact never meets a requirement: a target without `facts.gpu` fails every recipe that names a GPU.

## What enforces it

- `pipeline/patches.py check` (and so `pipeline/ci`): the keys and values above; `requires` and `applies` together;
  a recipe without `requires` that is not listed in `pipeline/requires-pending.txt`, and a listed one that has it;
  a build id in a recipe's `order.txt` comment that is not in its `requires.os` or `requires.donor`; `proven_on`
  entries (model, os, revision no newer than the recipe's, evidence; `donor` optional).
- `pipeline/patches.py plan SET TARGET OS/DONOR` (e.g. `26B131/25G342`) **stops** when a recipe that would run does
  not meet its `requires`, naming the recipe and each unmet key. golden-run (`stages/inputs.sh`) and `hao
  build-image` pass both builds. Given only `DONOR`, a recipe whose `os` is not `"any"` stops.
- `pipeline/patches.py requires` lists every recipe's `requires`, or `PENDING`.

## Matrix

`pipeline/patches.py matrix [--json]` plans, without building, every profile (`pipeline/profiles/*.json`) on every
target (`pipeline/targets/*.json`) with every (os, donor) pair an assembly names (`golden_build`, `donor_build`). One
line per cell: the recipes that run, those `requires` refuses (each with its unmet keys, indented below), and the
running ones without proof there: no `proven_on` entry for the target's model and the os build (and its donor, when the
entry names one), or only one for an older revision (`stale`). The build host's architecture is its own proof
column; `proven_on` does not record it yet, so it reads `unrecorded`. `[own]` marks a profile on its own target and
the pair of an assembly that builds it.

`--check` (run by `pipeline/ci`) exits 1 when a profile marked `"release": true` refuses a recipe in an `[own]` cell,
or no assembly builds it: a release profile must plan cleanly.

For a machine that is not a target yet, `pipeline/candidates` plans each release profile's set against the public
facts in `pipeline/candidates.json` (model, GPU families, firmware) and reports what it would refuse and what only
the machine's OCLP profile can settle ([candidates](candidates.md)).

## Filling it in (patcher and metal, for their own recipes)

1. Write `requires` from what the recipe actually depends on: the builds its rows and edits were made for (`os`,
   `donor`), the GPU whose driver it changes, CPU features its code needs or must avoid. Use `"any"` only when the
   recipe really does not depend on it (an SSH switch, a launchd plist).
2. Move `applies.builds` into `requires.donor` and drop `applies`.
3. A build named in the recipe's `order.txt` comment goes into `requires`, or out of the comment ("26B131 only" is
   `"os": ["26B131"]`).
4. `proven_on`: one entry per boot that proved a revision (model, os build, donor, revision, and the evidence in
   public words: the kind of boot, its date and the image's commit; the hygiene check refuses internal ids). It is what "works on" is generated from; leave it out when there is none.
5. Remove the recipe's line from `pipeline/requires-pending.txt`, bump `revision` with its README line, run
   `pipeline/patches.py tracker` and `check`. `requires` and `proven_on` are declarations: a recipe's
   `validated_digest` does not change.

When `pipeline/requires-pending.txt` is empty it is deleted and every recipe must have `requires`.
