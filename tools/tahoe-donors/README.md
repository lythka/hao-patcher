# tahoe-donors

Extracts the Tahoe (25G332) files a Golden Gate image needs on Intel: native
userspace files, stock kernel-extension resources, DriverKit, boot assets and
Recovery. Each role becomes one input row (`tahoe_native_donors`,
`tahoe_extensions_donors`, `tahoe_driverkit_donors`, `tahoe_boot_donors`,
`tahoe_recovery_donors`). Tahoe is not installed as the base system.

## Usage

```sh
python3.14 -I tools/tahoe-donors/make --help
```

Use `native --help`, `extensions --help`, `driverkit --help`, `boot --help`
and `recovery --help` for each role's input paths. The source is the Tahoe
25G332 installer unpacked by [`pipeline/unpack`](../../pipeline/unpack/README.md),
with its installer metadata. DriverKit also needs the generic x86_64 cache from
the SystemOS image. The
recipe is [recipes/25G332.json](recipes/25G332.json).

Outputs are new artifact directories with selected payload files and manifests
recording content hashes, modes, ownership, symlink targets and source identity.
No Apple payload is included in Git. The original inputs are read-only.

## Libraries from the donor's shared cache

Tahoe ships some libraries only inside its OS shared cache. The recipe's
`cache_images` names them (today CoreWiFi, from the x86_64h OS cache: Tahoe's
airportd needs the CoreWiFi it was built with). `native --cache DIR` cuts each
one out of the cache family in `DIR` (donor-inputs' `out/os-cache/`) with
[`tools/cache-dylib`](../cache-dylib/README.md), keeping its install name, signs it
ad hoc, verifies it with cache-dylib's `check.py` (bound GOT slots, resolvable
Swift references, no instruction Ivy Bridge lacks) and adds it to the `system`
role at its own path, with the recipe's owner, mode and flags. The extraction
records (every symbol bound per dependency, the Objective-C repairs) go to
`cache-images.json` beside the manifest.

Apple's extractor cannot do this. `dsc_extractor` (and `dyld_shared_cache_util
-extract`, which calls it), built from dyld-1378 source, writes files for
debuggers: it zeroes the rebase, bind and lazy-bind information and the export
trie, and keeps the cache's segment order. `ipsw dyld extract` (with or without
`--slide`/`--objc`) likewise writes no fixups. dyld refuses all of these
(`__DATA_CONST segment missing SG_READ_ONLY flag`, then `segment '__DATA_DIRTY'
vm address out of order`), and even past that nothing would rebind their
uniqued GOT slots.

The file keeps the original install name and path, so it can serve as a
library provider for a rebuilt shared cache as well as an on-disk file.

## Composition rules

- Copy Golden Gate System/Data first, then apply these donors.
- Overlay the KDK's Intel extensions onto the stock extension resource tree.
- Install complete Tahoe framework bases before OCLP's selected replacements.
- Supply generic x86_64 DriverKit; x86_64h alone does not support Ivy Bridge.
- Rebuild collections around Darwin 27; omit Tahoe's original kernel and KCs.
- Use Tahoe `kmutil` only as a maintenance build tool.

[Tahoe donors](../../docs/inputs.md#tahoe-donors) explains why the KDK and
Universal-Binaries cannot replace every installer input.

## Verification

```sh
python3.14 -I -B -m unittest discover -s tools/tahoe-donors/tests
```

Tests use authored filesystem fixtures for extraction, metadata, hardlinks,
exclusions and installer ordering. They do not mount or modify an OS image.

## Selection before extraction

`selection.py` checks the target's native code and cache image list before Tahoe
payload extraction. Target roots with Intel code are omitted from the extraction
list. Mixed-architecture, missing and resource-only roots remain candidates;
`keep_donor_roots` always takes precedence. The disk-image services remain paired
with Tahoe's IOHDIXController, as established by the 27-first laptop test.

## Revisions
- r11 2026-11-11 `native --cache`: the recipe's `cache_images` (Tahoe CoreWiFi from the x86_64h OS cache) are cut out with tools/cache-dylib, verified and added to the system role at their own path; extraction records in `cache-images.json`.
- r10 2026-11-11 Select positively identified native target roots before extracting the Tahoe payload; support fat64 Mach-O headers. DriverKit fixture metadata now follows the actual optional metadata argument.
