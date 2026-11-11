# golden-gate-corewifi build

`make` produces one artifact holding both rows of [golden-gate-corewifi](../README.md):
- `CoreWiFiTahoe.dylib` (row `corewifi_tahoe`)
- `CoreWiFi`, the compat library (row `corewifi_compat`)

It also writes `build.json`. The artifact is for one 27.x build, because the coverage depends on that build's
clients.

```sh
python3.14 -I patches/golden-gate-corewifi/build/make store/artifacts \
  --tahoe-cache store/inputs/25G342/os-cryptex-x86_64h \
  --system /path/to/26B131-SystemOS-root \
  --cache /path/to/26B131-RosettaOS/System/Library/dyld/dyld_shared_cache_x86_64 \
  --donors store/artifacts/minimal-native-25G342-26B131/payload/system \
  --oclp store/artifacts/oclp-hardware-mbp92-.../payload/13.7.2-25 \
  --io80211-compat store/artifacts/io80211-compat-26B131-.../io80211_compat.dylib \
  --wifip2p-compat store/artifacts/wifip2p-compat-26B131-.../wifip2p_compat.dylib
```

## Steps

1. **Extract.** [tools/cache-dylib](../../../tools/cache-dylib/README.md) cuts CoreWiFi out of the Tahoe
   `dyld_shared_cache_x86_64h` as `CoreWiFiTahoe.dylib`, and the file is signed ad hoc. Its `check.py` must pass:
   - every GOT slot code uses is bound
   - no code reference falls outside the dylib into the uniqued GOT region
   - the Swift metadata references resolve
2. **Environment.** Every symbol the extracted library binds must exist in the 27.x build. For most libraries that
   means its x86_64 cache. For IO80211 and WiFiPeerToPeer it means the files the image installs, each resolved
   through re-exports:
   - golden-gate-userspace's compat library
   - OCLP's wrapper, renamed `...OCLP.dylib`
   - `...Old.dylib`

   Missing binds to weakly linked libraries are recorded; they bind to NULL.
3. **Compat.** `corewifi_compat.m` is compiled as `CoreWiFi`, re-exporting `CoreWiFiTahoe.dylib`, and signed ad hoc.
4. **Coverage.** `corewifi_coverage.py` checks the build's clients against the result.
   - Clients:
     - the x86_64 cache's patch table. Symbols only 27.x CoreWiFi itself uses are left out; shared GOT uses count as
       foreign.
     - every on-disk Mach-O of the build that links CoreWiFi, except the ones the image replaces
     - the replacements themselves: programs in the donor and OCLP folders that link CoreWiFi
   - Symbols must resolve to the compat library or to Tahoe's CoreWiFi. A symbol that is code in 27.x must not
     resolve to data.
   - Methods. Every selector a client names that some 27.x CoreWiFi class implements must be answered by Tahoe's class
     chain, by the compat table (`kMethods`) or by NSObject. Programs built against Tahoe's CoreWiFi are left out of
     this pass.
   - A compat table entry that Tahoe already implements is an error.

   The pass reuses golden-gate-userspace's `src/io80211/coverage.py` and `methods.py` (ipsw, dyld_info).

Any error stops `make` with `MISSING/unsupported`, `MISSING method` or `SHADOWS Tahoe`, and no artifact is written.
`build.json` records:
- the sources' hashes
- the inputs: the Tahoe cache's main file, the build's cache and the IO80211/WiFiPeerToPeer files
- the extraction report: the binds per library and the statistics
- check.py's record
- the environment and coverage records

## Verify
```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-corewifi/build/tests
```
- `test_coverage.py` runs the check on authored fixtures.
- `test_compat.py` runs the compat library under Rosetta against an authored stand-in for Tahoe's CoreWiFi.
- With `COREWIFI_ARTIFACT` set on a 27.x host, `test_compat.py` also runs the real override, with 27.x's cached
  CoreWLAN loaded and a Swift conformance scan.
