# golden-gate-metal-gva

OCLP's AppleGVA (the "Big Sur GVA" patchset, macOS 11.7.10) reports Intel hardware JPEG decode as available on the
MacBookPro9,2 (its board-id is under "System Support" in the AppleGVA Info.plist the image has). On the HD 4000 that
path hands ImageIO's clients surfaces before they are complete: in Photos, zooming a JPEG from the grid shows a strip
of stale content at another scale for about a second (diag#0025). PNG, which never uses GVA, is clean.

This recipe edits one function of OCLP's AppleGVA so it answers "unavailable" for JPEG; JPEG then decodes in
software. H.264 and the other GVA paths are untouched.

| File | Change |
|---|---|
| `System/Library/PrivateFrameworks/AppleGVA.framework/Versions/A/AppleGVA` | `_AVFJPG_GetJPEGDecodeAvailability`: `push rbp; mov rbp,rsp` -> `xor eax,eax; ret` (0 is the function's own "unsupported" answer); ad hoc signed with its identifier and entitlements kept |

Declared in [`edits/applegva.json`](edits/applegva.json); [`src/edit-gva.py`](src/edit-gva.py) makes the same edit
and [`src/kc-hook.sh`](src/kc-hook.sh) installs it after the OCLP hardware overlay, in set order after
`golden-gate-hd4000-planes`. Input: OCLP's file `2a4fb182...`;
output after signing `4b0977c7...` (the file tested on the laptop).

## Overlaps

- `golden-gate-hd4000-planes` (product, in the 26B131 sets) answers the other half of diag#0025: the colour bands of
  plane textures with the wrong row pitch. Whether the strip this recipe removes is still there with only that fix
  is the open check below.
- The shared-cache fold (`golden-gate-shared-cache`, `-catalog`) folds OCLP's AppleGVA into the rebuilt cache and
  refuses a provider whose bytes differ from its artifact's. This recipe is for unfolded images until a rebuilt
  cache is made from the edited AppleGVA.

Pack `metal-3802-extras` (opt-in): no profile takes it yet.

## Verify
```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-metal-gva/tests   # GVA_OCLP_APPLEGVA=PATH adds OCLP's AppleGVA
```
On the Mac: import a JPEG into Photos and double-click its thumbnail: the zoom shows no strip; a H.264 video still
plays with hardware decode. The hook log prints the installed file's SHA-256.

## Validation
NEEDS VALIDATION: the same bytes ran on the test laptop on 2026-11-14, before golden-gate-hd4000-planes landed; no image built with this recipe. The check: on an image with hd4000-planes, zoom a JPEG in Photos. A strip
means this recipe goes into the 26B131 sets; none means it is dropped.

## Revisions
- r2 2026-11-20 Current recipe format: kc-hook.sh, pack metal-3802-extras (opt-in), 26B131 requires; overlaps noted.
- r1 2026-11-14 New: OCLP's AppleGVA reports Intel hardware JPEG decode unavailable (Photos zoom strip, diag#0025).
