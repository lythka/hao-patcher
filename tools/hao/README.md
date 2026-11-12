# hao

The user commands of the hao builder (workflow plan, step 9a on). Today: `hao patch` (live updates and revert),
`hao explain` and `hao inspect`.

## hao patch

Applies pack overlays (one pack's final files + `manifest.json` with each file's sha256, mode, owner and flags) to
a system root:

- writes only files whose full sha256 differs from the overlay's, and checks each before (unchanged since
  planning) and after (the written file has the manifest's sha256)
- backs up every replaced file and records every new one, so `--revert` restores replaced files and removes
  files the patch added (a plain backup of existing files misses those)
- takes a lock of its own (`STATE/lock`)
- live, it does not apply `installer`-phase packs (kexts, the cryptex, the EFI partition) and lists them as
  needing Recovery; `--installer` applies them (installer or on-disk Recovery)
- the same detail levels as the edit engine: `--quiet`, default, `--show`, `--log json`

```sh
python3.14 -I tools/hao/hao patch OVERLAY... --root ROOT --dry-run
python3.14 -I tools/hao/hao patch OVERLAY... --root ROOT
python3.14 -I tools/hao/hao patch --list --root ROOT
python3.14 -I tools/hao/hao patch --revert --root ROOT [--id hao-YYYYMMDD-HHMMSS]
```

State (records and backups) defaults to `ROOT/private/var/db/hao`.

Not done yet:

- **Live snapshots.** The live macOS backend (mount the system volume read-write, `bless --create-snapshot`
  with a named snapshot, revert to it) refuses to run until the step 9a laptop run proves it. That includes
  whether the previous snapshot survives. Until then ROOT is a folder: a fixture, or a volume the operator
  mounted.
- **Overlays.** They are produced by hand or by tests. The host materialiser that writes them from edit specs
  and the artifact store comes with the release tooling (plan step 13).
- **Signing on end users' Macs.** Replaced files load on the dev image because it boots with
  `amfi_get_out_of_my_way=1`. That is open for the owner.

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/hao/tests
```
