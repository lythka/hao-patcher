# Packs

A pack groups the recipes one kind of hardware needs, so an installer (or `hao patch`) applies only what the
detected Mac uses. Each `NAME.json` has:

| Key | Meaning |
|---|---|
| `detect` | `{"any_intel": true}`, `{"oclp_patchsets": [...]}` (selected when OCLP's detection picks one of these patch sets for the Mac) or `{"board_ids": [...]}` |
| `oclp_patchsets` | the OCLP patch sets whose files belong to this pack |
| `phase` | `installer` (touches kexts, the cryptex or the EFI partition: only from the installer or the on-disk Recovery) or `live-capable` (`hao patch` may apply it to the running system) |
| `proven_on` | models a boot test proved it on |
| `recipes` | member recipes; a recipe is in at most one pack, and a recipe in no pack applies as before |
| `opt_in` | optional, `true`: never selected by detection (`--packs auto`), only by name; for dev-only trials (`metal27-trial`) |

```sh
python3.14 -I pipeline/patches.py packs pipeline/targets/mbp92.json                        # detected packs
python3.14 -I pipeline/patches.py plan SET TARGET BUILD --packs auto                       # plan with detection
python3.14 -I pipeline/patches.py plan SET TARGET BUILD --packs base,metal-3802            # chosen packs
```

Pack `dev` (opt-in) holds what only bench images carry: `dev-access` (root SSH) and `dev-diagnostics` (hao-runtime's
measurement boot-args, the OpenCore file log). `patches.py check` refuses a status-`dev` recipe outside an opt-in pack,
a dev recipe in a set not named `dev-*`, and any other recipe reading a build-wide dev flag; a test keeps each bench
set equal to its end-user set plus pack `dev`.

`patches.py check` validates every pack. A test keeps the MacBookPro9,2 plan unchanged under `--packs auto`. Only
the recipes the workflow plan names are assigned so far. `input-usb-multitouch` waits for a detection rule: OCLP has
no patch set for it.
