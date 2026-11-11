# Golden Gate assembly recipe

`mbp92-golden-first.json` selects macOS 27.0/26A428 for MacBookPro9,2 and names
Tahoe 25G332 as its donor source. Golden Gate System/Data are installed first.

```sh
python3.14 -I pipeline/golden-run pipeline/assemblies/mbp92-golden-first.json \
  --repo /path/to/new-image-repository
```

| Field | Meaning |
|---|---|
| `target` | Hardware description under `pipeline/targets/` |
| `golden_build`, `donor_build` | Installed OS and separately selected donor build |
| `patch_set` | Ordered recipes, run in file order (`pipeline/sets/`): `golden-gate` (26A428), `dev-bs-test` (bench), `dev-golden-bootstrap` |
| `inputs` | Native, extensions, boot, DriverKit, Recovery and hardware artifacts |
| `size_bytes` | Target raw-image capacity |
| `workspace_size_bytes` | Separate sparse maintenance-workspace capacity |
| `oclp` | Selected OCLP integration revision |
| `skip_optional` | Optional, e.g. `["caches"]`: optional recipe groups to leave out ([optional cache recipes](../../docs/building.md#optional-cache-recipes)) |

The runner creates its own builder clone and takes the builder lock. The guest
verifies that the attached workspace is the intended external USB device before
formatting it. It never formats the host or the builder's boot disk.

Inputs are staged once. The guest applies donors and repairs in order, records
commands and errors, then detaches the completed target. A separate completion
file controls export; incidental success messages from nested hooks cannot
complete the build. A failed build retains its logs and does not publish an image.

The recipe and the `golden-gate` set still need a fresh boot test. See
[Building an image](../../docs/building.md) for preparation and output locations.
