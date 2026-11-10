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
