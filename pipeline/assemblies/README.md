# Assemblies

An assembly is one buildable image: target model, OS build, donors, OCLP revision, patch set and image repository.
Switch OS or model by building another assembly:

```sh
python3.14 -I pipeline/build dev-26B131 --latest     # macOS 27.2.1 + hao, newest finished hao payload
python3.14 -I pipeline/build tahoe-mbp92-25G342      # macOS 26 Tahoe 25G342 + OCLP, no hao
python3.14 -I pipeline/build NAME --dry-run          # print the command only
```

| Assembly | Runner | Image |
|---|---|---|
| `dev-26B131.json` | golden | 27.2.1 (26B131) for MacBookPro9,2, dev set (root SSH, hao statistics); the laptop images |
| `mbp92-26B131.json` | golden | the same without dev recipes (not yet built) |
| `dev-26B131-metal27.json` | golden | `dev-26B131` with set `dev-metal27`: every process on 27.2.1's own Metal ([golden-gate-metal27](../../patches/golden-gate-metal27/README.md)); dev trial, own image repository |
| `tahoe-mbp92-25G342.json` | tahoe | Tahoe 25G342 for MacBookPro9,2 with stock OCLP and root SSH (set `tahoe-dev`), inputs `pipeline/inputs/25G342-tahoe` (full installer unpack) |

`"runner": "golden"` (the default) runs `pipeline/golden-run`; `"runner": "tahoe"` runs the Tahoe-first
`pipeline/run TARGET OS_BUILD OCLP PATCH_SET`. `"repo"` is the image repository, relative to the checkout
(`--repo` overrides it). `--latest` first runs `pipeline/latest` (below) and stops if it moved the row, so the change
is committed before the image is built.

## Golden Gate assembly recipe

`mbp92-26B131.json` selects macOS 27.2.1/26B131 for MacBookPro9,2 with Tahoe 25G342 as
its donor source and the `golden-gate-26B131` set. `dev-26B131.json` is the same build
with `dev-bs-test` (adds root SSH and hao's diagnostic statistics). Golden Gate System/Data are installed first.
The laptop tests so far used `dev-26B131.json`; `mbp92-26B131.json` has not been built yet.

`mbp92-golden-first.json` (27.0/26A428, donor 25G332, set `golden-gate`) and the
`exp-*.json` experiments are older and no longer maintained.

```sh
python3.14 -I pipeline/golden-run pipeline/assemblies/mbp92-26B131.json \
  --repo /path/to/new-image-repository
```

Before a build, take the newest finished hao payload (clean `build.json`, `build.log` ending `rc=0`):

```sh
python3.14 -I pipeline/latest hao_runtime 26B131 --dry-run   # what it would pick
python3.14 -I pipeline/latest hao_runtime 26B131             # repoint the row, bump its patches, regenerate TRACKER
```

It changes nothing when the row already names the newest payload; otherwise check, test and commit as usual
(CLAUDE.md) before running golden-run.

| Field | Meaning |
|---|---|
| `target` | Hardware description under `pipeline/targets/` |
| `golden_build`, `donor_build` | Installed OS and separately selected donor build |
| `patch_set` | Ordered recipes, run in file order (`pipeline/sets/`): `golden-gate-26B131`, `dev-bs-test` (26B131 with dev recipes), `golden-gate` (26A428, older), `dev-golden-bootstrap` |
| `inputs` | Native, extensions, boot, DriverKit, Recovery and hardware artifacts |
| `size_bytes` | Target raw-image capacity |
| `workspace_size_bytes` | Separate sparse maintenance-workspace capacity |
| `oclp` | Selected OCLP integration revision |
| `skip_optional` | Optional, e.g. `["caches"]`: optional recipe groups to leave out ([optional cache recipes](../../docs/building.md#optional-cache-recipes)) |

A dev (bench) image differs from an end-user image only by the recipes of pack `dev` (`packs/dev.json`: `dev-access`,
`dev-diagnostics`), which only sets named `dev-*` hold; `patches.py check` refuses a status-`dev` recipe outside an
opt-in pack or in any other set. There is no build-wide dev switch.

The runner creates its own builder clone and takes the builder lock. The guest
verifies that the attached workspace is the intended external USB device before
formatting it. It never formats the host or the builder's boot disk.

Inputs are staged once. The guest applies donors and repairs in order, records
commands and errors, then detaches the completed target. A separate completion
file controls export; incidental success messages from nested hooks cannot
complete the build. A failed build retains its logs and does not publish an image. See
[Building an image](../../docs/building.md) for preparation and output locations.
