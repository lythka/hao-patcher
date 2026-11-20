# golden-gate-shared-cache-catalog

The B+catalog image of the shared-cache fold-in: [golden-gate-shared-cache](../golden-gate-shared-cache/README.md)'s
fold, unchanged, with the family built with runtime-perf's catalog of 320 Swift records instead of family B. Only
`pipeline/sets/dev-bs-test-fold-catalog.txt` has it, in the place golden-gate-shared-cache has in dev-bs-test-fold.
It exists because a set picks recipes, and a recipe names its rows: the two families need two rows.

## Input

`rebuilt_cache_catalog` (`pipeline/inputs/25G342/`): rebuilt-cache-fold-in-catalog-26B131-20261118-5aacad92 (cache
UUID D3BDB76E), from the same generator commit (5aacad92), System view, roots and provider bytes as family B.

## How it works

`make.sh` stages golden-gate-shared-cache's `src/fold.py` and `src/providers.txt` as `build/fold`, and runs that
recipe's own `make.sh` into `build/fold/build` (golden-gate-userspace's code). `src/kc-hook.sh` runs
`build/fold/fold.py install` on `inputs/rebuilt_cache_catalog`. Every step, gate and record is golden-gate-shared-cache's.
A change to that recipe's code reaches this one at the next build without a revision here.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/golden-gate-shared-cache-catalog/tests`: the hook and its row; the
  staged fold is golden-gate-shared-cache's byte for byte and finds its staged userspace.
- The base recipe's suite reads this row's artifact too.
- Build log and image: as golden-gate-shared-cache, with this row's artifact.
- On the target, the Swift runtime reads this family's generated table: `tools/shared-cache/tools/prespecialization-adoption.py
  expect inputs/rebuilt_cache_catalog/dyld_shared_cache_x86_64 > expect.json` on the host; on the target
  `SWIFT_DEBUG_ENABLE_LIB_PRESPECIALIZED_LOGGING=1 /usr/bin/swcutil --help 2>&1 | grep Prespecializations > adopt.log`; then
  `prespecialization-adoption.py judge expect.json adopt.log` must say PASS (the logged map, less the slide, is the
  build's map, and a lookup finds an entry). The build's audit proves the table's contents, not that it is read.

## Validation

r8 on hardware: image a0bae56 (MacBookPro9,2, 2026-11-20) booted to the desktop; all 36 installed cache files equal
the row's artifact (UUID 31DA2287); `prespecialization-adoption.py judge` PASS (map 0x7ff87e92d000 after the slide
0xdfda000, lookups found entries in programs run natively and under hao); in ControlCenter, Optional<URL>, Optional<Data>
and Optional<UUID> resolve to the generated metadata.

## Revisions
- r9 2026-11-20 Verify adds the on-target adoption check (tools/shared-cache prespecialization-adoption.py); r8's
  family validated on hardware (a0bae56). Nothing it installs changes.
- r8 2026-11-20 Row rebuilt_cache_catalog is the witness family 31DA2287: 99,062 prespecialized metadata records and 25,671 protocol witness tables from a native capture, verified in isolation; prebuilt loaders and conformance tables as before.
- r7 2026-11-20 Row `rebuilt_cache_catalog`: rebuilt-cache-fold-in-catalog-66k-26B131-20261118-851adc54-fe70eccb (cache 322743F4, the 66k-record Swift catalog family with the ObjC method lists fixed), the cache image 3957131 booted to the desktop with sharingd and WiFiAgent up.
- r6 2026-11-19 Stages golden-gate-shared-cache r11's fold.py: the folded OS cryptex is compacted to what it holds
  before it is sealed (about 14.6 GB instead of 26 GB on 26B131).
- r5 2026-11-18 Row `rebuilt_cache_catalog`: rebuilt-cache-fold-in-catalog-26B131-20261118-5aacad92 (cache UUID
  D3BDB76E), family B r86 with the 320-record catalog; the set's runtime is now hao-runtime-fold-catalog.
- r4 2026-11-17 Declares its scope (`requires`, docs/requires.md): the rebuilt 26B131 catalog family. Nothing it installs changes.
- r3 2026-11-16 Declares its pack (base): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r2 2026-11-15 Declares its installed files (patch.json files). Installed bytes unchanged.
- r1 2026-11-15 New: golden-gate-shared-cache's fold on the B+catalog family (row rebuilt_cache_catalog), for set dev-bs-test-fold-catalog.
