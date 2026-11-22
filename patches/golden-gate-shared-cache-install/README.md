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

Laptop 2026-11-22, a MacBookPro9,2: the dev-beta image c3c0a5b (26B131, built from 7459d3e6 with r1; profile dev-beta-v0.0.1: the beta set plus dev-access) was pushed, booted to Setup and used (Maps, System Settings, Weather, Control Center). Its QEMU first boot passed in 490 s with the shared cache rebuilt in place and checked against the release (UUID 31DA2287, 36 files). Known issues, not this recipe's: first boot is slow (load above 400 for several minutes) and Setup's Transfer pane leaves its selection ring on "From a Mac".

## Revisions
- r2 2026-11-22 Validated r1 on hardware (Validation). Status product: it ships in the beta release. Nothing it installs changes.

- r1 2026-11-22 Build and verify the cache on the target before installing it and sealing System.
