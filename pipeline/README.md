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
| `candidates`, `candidates.json` | The machines people ask about but that are not targets, and what each release profile would refuse on them ([candidates](../docs/candidates.md)) |
| `lock.py` | Reads input rows and target files; cache keys for `run` |
| `python-site` | Row `python_site` (the site-packages OCLP imports in the builder) made from the pinned PyPI wheel rows `python_site_*`: `make OUT`, `check SITE` |
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

## CI (`pipeline/ci`)

```sh
python3.14 -I pipeline/ci [COMMIT] [--log FILE]   # every check a push must pass; exit 1 if any fails
python3.14 -I pipeline/ci --here --only suite:tools/hao   # this working tree, one check
```

It checks the commit out in a temporary worktree (no `store/`, nothing uncommitted) with proxies closed. First it runs
`tools/hao/hao doctor --for ci --log json` there (the pinned Python, Xcode, host macOS and script shebangs; no disk or
URL checks): if doctor fails, no other check runs and the run exits 1 with the one failed check `doctor`, its lines in
the `--log` JSON (PROTOCOL S14; `hao create-installer` runs the same doctor before its first question). Then it runs
`patches.py check`, every profile's set against its file, the edit specs and signing lint, the hygiene check of the
tree and the commit message, and every test suite (`pipeline/tests`, `tools/*/tests`, `patches/*/tests`,
`patches/*/build/tests`, `cache/*/tests`, `cache/metallib/lib`). One line per check, then the failing ones' output.
No push, merge or tag without a green run on that commit (PROTOCOL S8); a test that needs Apple files skips when
they are absent. A new check is a function added to `CHECKS`.

Scripts run on the build Mac (`pipeline/`, the host tools) name `#!/usr/bin/env python3.14` and are run as
`python3.14 -I SCRIPT`; scripts that run in the builder VM (`pipeline/stages/`, `pipeline/kit.py`), on the target or
in the Recovery keep the interpreter they run with there.

## Live swaps (`swaps.py`, `live-swaps.tsv`)

The laptop swap tool records every file it puts on the laptop, on this Mac (a ledger with the bytes beside it,
named by `HAO_SWAP_LEDGER` or the first line of `~/.config/hao/swap-ledger`); the laptop's own log is lost at each
push. `pipeline/build` and `hao build-image` run `swaps.py check` first and stop while a swapped path's newest swap has no resolution: a line in
`live-swaps.tsv` (`SHA256<TAB>PATH<TAB>row NAME | recipe NAME | dropped: WHY`), or, under `/usr/lib/hao/`, the same bytes
in the hao_runtime row's payload. `swaps.py status` lists them.
