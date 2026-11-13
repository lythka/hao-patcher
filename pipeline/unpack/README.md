# pipeline/unpack

Unpacks a macOS installer into the pristine System tree, both cryptexes and the
Recovery `BaseSystem.dmg`, on the host as a normal user: no root, no VM and no Intel
CPU. It then checks the result against Apple's own manifests. The image build uses
it, through [`pipeline/stages/unpack.sh`](../stages/unpack.sh), for the Tahoe
installer's `unpack`, `tree` and `lists` input rows.

## Usage

```sh
pipeline/unpack/unpack-installer --dry-run INPUT NEW_OUTDIR
pipeline/unpack/unpack-installer [--keep-intermediates] INPUT NEW_OUTDIR
pipeline/unpack/unpack-installer --verify-only OUTDIR --manifest FILE --scratch NEW_DIR
```

`INPUT` is an `Install macOS <name>.app`, an `InstallAssistant.pkg` or a
`SharedSupport.dmg`. `OUTDIR` must be new or empty; nothing is overwritten.
`--dry-run` prints the plan with real member names, sizes and free space.
`--verify-only` re-runs the checks on an existing folder without writing into it.
Exit status: 0 all checks pass, 1 a check failed, 2 usage or refused, 3 a step
failed.

A 25G325 unpack peaks near 60 GB of free space; a `.pkg` input adds an 18 GB copy
of `SharedSupport.dmg` until cleanup.

## How it works

1. Take `SharedSupport.dmg` from the app, or from the flat `.pkg` with `xar`
   (its size and SHA-1 must match the package's table of contents).
2. Attach it read-only and unzip only the members needed: the payload chunks and
   their metadata, the image patches, the x86_64 BaseSystem patch, the BOMs,
   `AssetData/boot` (without Apple silicon kernelcaches and other Macs' firmware),
   `Restore/` and `usr/`.
3. `pbzx.py` decodes the chunks into one Apple Archive, `payload.yaa`.
4. `aa extract` writes `tree/` without owners and flags (those need root).
5. `links.py` makes the hard links listed in `links.txt`.
6. `ripatch` rebuilds the cryptex images from Apple's full-replacement RIDIFF10
   patches through the host's `RawImagePatch`, which verifies Apple's digest.
   `bx50full.py` rebuilds `BaseSystem-x86_64.dmg` from its BXDIFF50 patch.
7. Check every file against `post.bom` (`bomcheck.py`), BaseSystem against its
   chunklist (`chunklist.py`), and the patch logs for a digest match.
8. Remove the intermediates (unless `--keep-intermediates`) and write
   `unpack-manifest.json` with every output's hash, every step and every check.

As a normal user, `aa` cannot enter a directory whose archive mode lacks the owner
`x` bit (`Templates/Data/.../dslocal/nodes/Default` is 0600). The driver accepts
that error only, and then applies those entries' mode and mtime itself.

`tree/` has Apple's content but not all its metadata: `aa` does not apply the mode
or mtime of entries flagged `SF_RESTRICTED` (almost all of System), so they keep
default modes. The real owners, modes, flags and mtimes are in `payload-list.json`
and `fixup-list.json`. `pristine-tools/mklist.py` turns them into the `lists`
(`sys.tsv`, `data.tsv`, `data-symlinks.tsv`) that the builder VM's `hmeta` applies;
never copy `tree/` as it is.

## Files

| File | Purpose |
|---|---|
| `unpack-installer` | The driver above |
| `pbzx.py`, `yaa.py`, `links.py` | pbzx decoder, Apple Archive reader (for xattrs), hard links |
| `ripatch.c`, `Makefile` | RIDIFF10 patcher; `make` builds `build/ripatch`, the driver builds its own copy. It loads `libParallelCompression` with `dlopen` and links only libSystem |
| `bx50full.py`, `chunklist.py`, `bomcheck.py` | BaseSystem rebuild and the checks |
| `walk.py`, `compare.py`, `datatemplate.py`, `fillin.py`, `tccheck.py` | Single-step helpers: tree listing, payload vs pristine System, the Data template model, root-only directories from a noowners mount (edits its TSV in place), trust-cache coverage |
| `pristine-tools/` | `mklist.py` (the lists) and the builder-VM tools `autool`, `hmeta` and `fl` (input rows in `pipeline/inputs/common/`); `build-fl NEW_FOLDER` builds `fl` (universal, macOS 27.0, signed with `ent.plist`) with a `build.json` naming the commit |

The installer is only read; it is attached `-readonly -nobrowse` and detached
again. None of Apple's code is included here.

## Minimal Intel donor extraction

`donor-inputs INSTALLER OUT --requirements tools/tahoe-donors/recipes/intel-donors.json
--kdk TAHOE_KDK --target-system GOLDEN_SYSTEM --target-cache-images IMAGE_LIST`
reads the package in place and extracts only required donor roots. Native target
roots are removed from the payload selection before extraction. The generic
x86_64 DriverKit cache is required; the Haswell-only cache is not selected for
MacBookPro9,2. The requirement's `os_cache` families (Tahoe's x86_64h OS cache) are copied to
`out/os-cache/`, the source of the recipe's `cache_images` (CoreWiFi, which Tahoe ships only in its cache) that
`tools/tahoe-donors native --cache` cuts out. Cryptex reconstruction stops once the requested cache families exist.

Measured with Tahoe 25G342 and Golden Gate 26B131: 8 roots stay native and 42
come from Tahoe. The payload archive shrank from 1,942,322,740 to 1,476,002,843
bytes. DriverKit cache bytes shrank from 59,511,923 to 29,755,960, with one
cryptex reconstruction instead of two. Native and extension manifests match the
previous extraction exactly; boot and Recovery file contents match (extraction
timestamps differ). The disk-image services stay pinned to Tahoe after the earlier
27-first laptop test found they must match IOHDIXController.
