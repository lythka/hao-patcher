# Golden Gate assembly recipe

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

A build is a dev build when its set includes a recipe with status `dev` (`dev-access`);
golden-run then sets `IMAGE_DEV_BUILD=1` for the recipes (otherwise `0`), and dev-only diagnostics
follow it.

The runner creates its own builder clone and takes the builder lock. The guest
verifies that the attached workspace is the intended external USB device before
formatting it. It never formats the host or the builder's boot disk.

Inputs are staged once. The guest applies donors and repairs in order, records
commands and errors, then detaches the completed target. A separate completion
file controls export; incidental success messages from nested hooks cannot
complete the build. A failed build retains its logs and does not publish an image. See
[Building an image](../../docs/building.md) for preparation and output locations.
