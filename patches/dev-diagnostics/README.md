# dev-diagnostics

The measurement settings of a bench image, in one place and only in dev sets (pack `dev`, never detected). An
end-user image is built without this recipe and carries none of them.

## What it changes (EFI partition, `EFI/OC/config.plist`, after hao-opencore)

| Key | Value | Why |
|---|---|---|
| boot-args | adds `hao_nx_stats=1 hao_bridge_stats=1 hao_diag_time=1` after `hao_crash=1` | hao-runtime's NX fault tables, bridge statistics, phase lines and usage counters (boot-gantt reads them). They cost time on every translated native call. |
| `Misc.Debug.Target` | 0x41 | OpenCore's log goes to a file on the EFI partition, nothing on screen, for collection from Recovery. hao-opencore writes 0 (no log). |
| `Misc.Debug.DisplayLevel` | adds 0x40 | the file log includes OpenCore's info lines (each kernel patch result). |

Declared in `edits/config.json` (`plist.set`); `tests/test_edits.py` proves the spec and `apply.sh` write the same
config, starting from hao-opencore's declared values, and that nothing else changes.

Until this recipe, hao-opencore added the three boot arguments itself when golden-run set `IMAGE_DEV_BUILD=1` (a set
with a status `dev` recipe), and wrote the file log in every build. Both now live here.

## Verify

```sh
python3.14 -I -B -m unittest discover -s patches/dev-diagnostics/tests
```

In a bench image's `EFI/OC/config.plist`: the boot-args carry the three switches and `Misc.Debug.Target` is 65. On
the laptop: `sysctl kern.bootargs` lists them, and `EFI/OC/opencore-*.txt` appears on the EFI partition after a boot.
In an end-user image (`mbp92-26B131`): none of them.

## Validation

NEEDS VALIDATION: not yet built into a bench image or booted. Host evidence: hao-opencore r50's declared values, then
golden-gate-live-text-service's and this recipe's, applied to the base config of the bench image built 2026-11-13
(run golden-26B131-20261113-091741) give exactly that image's config.plist apart from Kernel.Add/Patch (hao-opencore's
`tests/test_edits.py` with `HAO_OPENCORE_INPUT` and `HAO_OPENCORE_IMAGE_CONFIG`): bench images keep the same config.

## Revisions

- r1 2026-11-13 The measurement boot arguments and the OpenCore file log, moved out of hao-opencore (r50) into this dev-only recipe.
