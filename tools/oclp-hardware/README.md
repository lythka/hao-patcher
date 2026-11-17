# oclp-hardware

Builds the MacBookPro9,2 hardware overlay (input row `oclp_hardware`) from OCLP's
support files, and applies it to an offline Golden Gate System/Data pair. The system
stays Darwin 27; the overlay adds the legacy Intel graphics, wireless and audio
components that OCLP selects for this model.

## Inputs

- [OCLP 3.0.0-rc.5](https://github.com/dortania/OpenCore-Legacy-Patcher/releases/tag/3.0.0-rc.5),
  integrated by [`tools/oclp-integration`](../oclp-integration/README.md).
- [PatcherSupportPkg 2.0.5 Universal-Binaries.dmg](https://github.com/dortania/PatcherSupportPkg/releases/tag/2.0.5)
  (SHA256 `1c290781bc726759ff37a110ffcb7182a6c6fd3d8ebce112d134d482c8de52a3`), and
  its expanded root, read-only.
- OCLP's matching `payloads/Tools/RSRRepair`.

Universal-Binaries supplies hardware support, not all Tahoe donors; see
[Tahoe donors](../../docs/inputs.md#tahoe-donors).

## Build and apply

```sh
python3.14 -I tools/oclp-hardware/make build \
  --psp-root /path/to/expanded-support \
  --psp-archive /path/to/Universal-Binaries.dmg \
  --rsr-repair /path/to/RSRRepair --out /path/to/new-hardware-artifact
```

The artifact holds the selected files, an install map, its own installer and a
`build.json` with input and output hashes, modes and symlinks. The installer needs
only the artifact:

```sh
python3.14 -I /path/to/new-hardware-artifact/install-tools/apply apply \
  --artifact /path/to/new-hardware-artifact \
  --system-root /Volumes/OfflineSystem --data-root /Volumes/OfflineData \
  --report /path/to/new-application-report.json
```

Run it as root in a maintenance macOS, after the Tahoe framework bases and KDK
drivers are installed; it refuses the running root. Mounting, kernel collections
and snapshots are left to the image build.

## How it works

`profiles/mbp92.json` lists the nine OCLP patch sets selected for MacBookPro9,2 and
their file operations in order. `lib/` holds OCLP's own volume and file-operation
code, ported with local imports (`provenance.json` names the upstream files and
their hashes). Another model or support package needs its own reviewed profile.

`profile.py` makes a profile from OCLP's own patch-set detection on the target's
`oclp_hardware_profile` (`pipeline/targets/NAME.json`), run with the kit's Python and
site packages; nothing is typed by hand. It reproduces `profiles/mbp92.json` byte for
byte; `profiles/mbp121.json` (MacBookPro12,1) was made with it. `make build` still reads
`profiles/mbp92.json` only: a 12,1 overlay needs its own artifact and row.

```sh
python3.12 -I tools/oclp-hardware/profile.py --source OCLP_SOURCE --site SITE \
  --target pipeline/targets/mbp121.json --like tools/oclp-hardware/profiles/mbp92.json \
  tools/oclp-hardware/profiles/mbp121.json
```

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/oclp-hardware/tests
```

With `OCLP_SOURCE`, `OCLP_SITE` and `OCLP_PYTHON` set, `tests/test_profile.py` also
regenerates every profile from its target and compares the bytes.

The fixtures cover input hashes, file semantics, application order and use of the
standalone installer.

OCLP is BSD 4-Clause licensed; see [LICENSE.txt](LICENSE.txt).
