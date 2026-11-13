# Image pipeline

Use [`golden-run`](golden-run) with the [assembly recipe](assemblies/mbp92-golden-first.json)
to build Golden Gate first and add explicit Intel donors. [Building an image](../docs/building.md)
covers prerequisites, commands, output and validation.

| Path | Role |
|---|---|
| `assemblies/` | OS/donor versions, image sizes and selected patch set |
| `golden-run` | Host staging, isolated VM execution and image capture |
| `stages/golden-*.py`, `stages/golden-pristine.sh` | Guest filesystem and image assembly |
| `patches.py` | Ordered patch plan and recipe validation |
| `materialize.py`, `overlays.py` | Declared files made into pack overlays; with `golden-run --from-overlays`, which recipes install from them instead of their hooks, and the image tree comparison |
| `inputs/`, `targets/`, `sets/` | Input rows, hardware profile and patch sets |
| `lock.py` | Reads input rows and target files; cache keys for `run` |
| `vm/` | Maintenance VM lifecycle |
| [`unpack/`](unpack/README.md) | Installer extraction for donor preparation |
| `recovery` | Offline Recovery build |
| `run` | Legacy Tahoe-first stock-image builder |

## Patches and inputs

```sh
python3.14 -I pipeline/patches.py plan pipeline/sets/golden-gate-26B131.txt \
  pipeline/targets/mbp92.json 25G342 \
  --locks pipeline/inputs/golden/26B131:pipeline/inputs/25G342:pipeline/inputs/common
python3.14 -I pipeline/patches.py check
```

Each patch is `patches/NAME`. Each input row is one file holding optional `#`
comment lines and `SHA256-or-- PATH`; a relative `PATH` is taken from the
repository root (normally `store/...`). Build-specific rows take precedence over
`common/`. See [Input rows](../docs/inputs.md#input-rows).

`golden-gate-26B131` is the 27.2.1 image's set; `dev-bs-test` adds dev-only recipes to it;
`golden-gate` is the older 26A428 set. `recovery` supplies the
offline installation recipes; `stock` is the patch set of the older Tahoe-first
builder.

## Tests

```sh
python3.14 -I -B -m unittest discover -s pipeline/tests
```

The fixtures check input rows, cache keys, input staging, patch order, source
selection, guest workspace isolation and failure reporting without formatting
disks or starting a VM. A successful image build and boot are separate checks.

## Live swaps (`swaps.py`, `live-swaps.tsv`)

hao-tools' `laptop-swap put` records every live swap on this Mac (ledger and bytes under
`~/mac-image/store/laptop-swaps/`, or `HAO_SWAP_LEDGER`); the laptop's own log is lost at each push. `pipeline/build` and
`hao build-image` run `swaps.py check` first and stop while a swapped path's newest swap has no resolution: a line in
`live-swaps.tsv` (`SHA256<TAB>PATH<TAB>row NAME | recipe NAME | dropped: WHY`), or, under `/usr/lib/hao/`, the same bytes
in the hao_runtime row's payload. `swaps.py status` lists them.
