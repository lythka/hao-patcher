# golden-gate-userspace build

Extracts the filesystem, SystemOS, AppOS and RosettaOS images of one Apple silicon restore identity from a stored
IPSW. The result is the `golden_gate_userspace` input of [golden-gate-userspace](../README.md).

## Usage
```sh
python3.14 -I patches/golden-gate-userspace/build/make \
  --ipsw /path/to/UniversalMac_26A428_Restore.ipsw --board j293ap \
  --out store/artifacts/golden-gate-userspace-26A428
```
The output folder must be new. The MacBookPro9,2 image uses the `j293ap` erase identity of the 26A428 restore
archive.

## How it works
`make` selects exactly one customer erase identity from the BuildManifest, extracts its four userspace images and
decodes the two encrypted ones with `ipsw fw aea`. APFS clones share the bytes between the extraction and the payload.
It does not mount the images or run their programs; the patch later mounts them read-only in the builder VM.

Outputs: `payload/{filesystem,os,app,rosetta}.dmg`, `identity.plist`, `userspace.json`, the original components in
`sources/`, and `build.json` with the source commit, archive hash, component hashes and dependency versions. The image
bytes are deterministic for a given archive; dates, paths and logs vary.

Requirements: macOS with APFS, Python 3.12 or later, and [ipsw](https://github.com/blacktop/ipsw) 3.1.737
(65ceb7620436150e232970ebd0875cfe8062b63c).

## Verify
`python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/build/tests`. On a real archive, check the
component roles and hashes in `build.json`, then the installed SystemVersion and both cache architectures in the built
image.

## Provenance
Apple's BuildManifest supplies the component mapping. The source is the UniversalMac 27.0 26A428 restore archive,
SHA-256 `2a5d3c695d501022b7fad9adaffcf2627bcb867d993fb5662dcd41bac99a2836`.

## Revisions
- r2 2026-11-10 Initial public release.
