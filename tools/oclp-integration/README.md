# oclp-integration

Reconstructs the OpenCore Legacy Patcher source tree that the image builder runs.
`target.patch` extends upstream OCLP with offline target-volume patching from an
Apple silicon host, recorded hardware profiles and Intel KDK thinning. Builds name
the resulting commit as their OCLP revision (`oclp` in the
[assembly recipe](../../pipeline/assemblies/mbp92-golden-first.json)).

## Usage

```sh
python3 tools/oclp-integration/make NEW_ARTIFACT [--upstream EXISTING_OCLP_CLONE]
```

The tool checks out upstream commit `e2107e05d9ce574e9ac3fd8b223e20a2edb7ba53`,
applies `target.patch`, and requires the resulting Git tree to equal
`77b788410c4d5d73029d1c3845305eecd8d169eb` (content, executable modes and
symlinks). It then commits the tree and prints `OCLP_REPO=` and `OCLP_COMMIT=` for
the build. The output folder holds the `source/` checkout and a `build.json`.

The tree is exact; the commit hash differs from the reference commit `fd83bda09`
because author and time differ. OCLP's support-package downloads are separate
build inputs (see [Downloads](../../docs/inputs.md#downloads)); the patch embeds no
binaries.

## Verify

Run `make` into a new folder; it fails if the tree differs. From the output, run
the standalone Intel KDK tests:

```sh
python3 source/ssd-target/tests/test_kdk_intel_payload.py
```

OCLP's other test scripts need OCLP's Python dependencies.

## License

OpenCore Legacy Patcher is developed by Dortania and the OpenCore Legacy Patcher
contributors under the BSD 3-Clause license ([LICENSE.txt](LICENSE.txt)), which also
covers `target.patch`.
