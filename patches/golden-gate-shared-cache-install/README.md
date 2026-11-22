# Build the shared cache during installation

This recipe gives installer users the verified rebuilt cache before their first boot.

The assembler runs this recipe in two phases after the provider recipes. The build phase reads the target System,
Data and read-only OS/App cryptex views, checks the source inventory, and builds from pinned offline tools and the
Swift catalog. It refuses any output other than the expected UUID and all 36 hashes. A verified family survives a
build retry on target Data. The install phase uses the existing fold recipe before the final System snapshot.

Stock profiles continue to use the stock cache. This recipe does not enable live cache activation.

## Verify

Run the recipe tests and the pipeline assembler resume tests. Image, Recovery and hardware proofs are separate.

## Validation

NEEDS VALIDATION: the install path requires an image build, QEMU first boot and hardware installation.

## Revisions

- r1 2026-11-22 Build and verify the cache on the target before installing it and sealing System.
