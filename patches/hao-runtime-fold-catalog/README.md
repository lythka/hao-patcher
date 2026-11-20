# hao-runtime-fold-catalog

[hao-runtime-fold](../hao-runtime-fold/README.md)'s installation for the B+catalog image of the shared-cache fold-in
(`dev-bs-test-fold-catalog`), in hao-runtime-fold's place. The B+catalog family has its own cache UUID, and hao's
generated data names the UUID it was made for, so this image takes a payload built for that family. A set picks
recipes and a recipe names its rows, so the two families' payloads need two recipes.

## Input

`hao_runtime_fold_catalog` (`pipeline/inputs/golden/26B131/`): hao-runtime-26B131-20261118-3a6a5d9-catalog-d3bdb76e,
a payload folder as `hao_runtime` (payload-manifest sha256 checked by the row), from hao-runtime 3a6a5d9 for the
B+catalog family (cache D3BDB76E, row `rebuilt_cache_catalog`).

## How it works

`src/kc-hook.sh` runs the payload's own `hao-install.py` exactly as hao-runtime's hook does, on
`inputs/hao_runtime_fold_catalog`. Variant `cache-catalog` replaces hao-runtime-fold, which replaces hao-runtime.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/hao-runtime-fold-catalog/tests`: the hook is hao-runtime's on this
  row, and only the B+catalog set uses it, in place of both hao-runtime and hao-runtime-fold.
- On the image: `usr/lib/hao/install.json` and the payload's `build.json` name cache UUID D3BDB76E.

## Validation

NEEDS VALIDATION: not built by golden-run. A QEMU boot of an aae0ab3 image with the B+catalog family D3BDB76E and this
payload swapped in reached sshd in 559 s, with no panic and no launchd refusal.

## Revisions
- r5 2026-11-20 Row hao_runtime_fold_catalog is hao-runtime 90c2b9e built for the witness family 31DA2287 (1892 of 1892 units match); libhao 076cc54f.
- r4 2026-11-20 Row hao_runtime_fold_catalog is hao-runtime b05855f built for the witness family 31DA2287 (row rebuilt_cache_catalog r8): 1892 of 1892 units name its images; libhao 9b40371a.
- r3 2026-11-20 Row hao_runtime_fold_catalog is hao-runtime b05855f built for the 66k family 322743F4 (the runtime of row hao_runtime r93; libhao 9b40371a).
- r2 2026-11-20 Row `hao_runtime_fold_catalog`: hao-runtime aba5ce7 (the RC runtime with the JIT arena and the Weather fix) for the 66k-record catalog family 322743F4, libhao db500576: the runtime of the v0.0.1 rebuilt-cache image.
- r1 2026-11-18 New: the B+catalog set's runtime payload (row `hao_runtime_fold_catalog`, hao-runtime 3a6a5d9 for
  D3BDB76E), in hao-runtime-fold's place.
