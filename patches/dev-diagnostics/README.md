# dev-diagnostics

The measurement settings of a bench image, in one place and only in dev sets (pack `dev`, never detected). An
end-user image is built without this recipe and carries none of them.

## What it changes (EFI partition, `EFI/OC/config.plist`, after hao-opencore)

| Key | Value | Why |
|---|---|---|
| boot-args | adds `hao_nx_stats=1 hao_bridge_stats=1 hao_diag_time=1` before `auxKCAtBoot=false` (no `hao_crash=1` since r11) | hao-runtime's crash lines on the console (dev images only, owner 2026-11-17), and its NX fault tables, bridge statistics, phase lines and usage counters (boot-gantt reads them), which cost time on every translated native call. |
| `Misc.Debug.Target` | 0x41 | OpenCore's log goes to a file on the EFI partition, nothing on screen, for collection from Recovery. hao-opencore writes 0 (no log). |
| `Misc.Debug.DisplayLevel` | adds 0x40 | the file log includes OpenCore's info lines (each kernel patch result). |
| `NVRAM.Add` `csr-active-config` | 0x827 (the image's 0x803 plus 0x4 and 0x20) | DTrace can attach to a process (`dtrace -p PID`, the pid provider, user stacks): task-for-pid and unrestricted DTrace. hao-opencore's end-user value stays 0x803. Written by `apply.sh` only: a data value cannot be declared with `plist.set` yet. |

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

NEEDS VALIDATION: r4's SIP value has not been built or booted; what should be shown on a dev image: `csrutil status` lists DTrace Restrictions and Task for PID as disabled, `dtrace -p PID -n 'syscall:::entry /pid == $target/ { @[probefunc] = count(); } tick-2s { exit(0); }'` prints counts, and an end-user image's config still has 0x803. Earlier revisions: not yet built into a bench image or booted. Host evidence: hao-opencore r50's declared values, then
golden-gate-live-text-service's and this recipe's, applied to the base config of the bench image built 2026-11-13
(run golden-26B131-20261113-091741) give exactly that image's config.plist apart from Kernel.Add/Patch (hao-opencore's
`tests/test_edits.py` with `HAO_OPENCORE_INPUT` and `HAO_OPENCORE_IMAGE_CONFIG`): bench images keep the same config.

## Revisions
- r11 2026-11-22 hao_crash=1 left out: with it a dev image's first boot stalls before sandboxd (QEMU, ab855d51; without it the same image boots in 537 s); hao-runtime's fix is parked until after beta.1.
- r10 2026-11-17 The dev boot-args follow hao-opencore r80: no `-nokcmismatchpanic`, no `-pmap_pcid_disable`.
- r9 2026-11-17 Declares its scope (`requires`, docs/requires.md): boot-args, OpenCore log settings and SIP flags: no build or hardware dependence. Nothing it installs changes.
- r8 2026-11-17 Its declared boot-args carry hao-opencore r72's `amfi_force_policy=8`; nothing this recipe adds changes.
- r7 2026-11-17 Adds `hao_crash=1`, which hao-opencore r71 no longer writes (owner 2026-11-17: dev builds only); dev images keep the same boot-args.
- r6 2026-11-17 `diagnostic-boot-args` names a reason for each boot argument (`arg_reasons`), as edit specs now require. Declaration only: nothing it installs changes.
- r5 2026-11-16 Declares its pack (dev): recipe layout format pass; nothing it installs changes.

- r4 2026-11-14 Dev sets boot with `csr-active-config` 0x827: the image's 0x803 plus task-for-pid and unrestricted DTrace, so DTrace can attach to processes on a dev image. End-user images are unchanged (hao-opencore still writes 0x803).
- r3 2026-11-13 `apply.sh` runs Python as `$OCLP_PYTHON` (the kit's CPython for the Mac the assembly runs on, arm64 or x86_64), falling back to the builder VM's share copy; same Python version and bytes written.
- r2 2026-11-13 The declared boot-args follow hao-opencore r52 (no ic_csr_internal=1); script unchanged.

- r1 2026-11-13 The measurement boot arguments and the OpenCore file log, moved out of hao-opencore (r50) into this dev-only recipe.
