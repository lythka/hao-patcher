# shared-cache

Rebuilds the x86_64 dyld shared cache of macOS 27 with libraries from the disk folded in as cache images
("providers"), with the ObjC tables, Swift prespecialized metadata, patch table and code signatures regenerated for
the new image set. On a hao image, OCLP and hao replace some of the cache's libraries on disk (Metal, Wi-Fi, video
decode, multitouch, Live Text). dyld then treats those cache images as overridden: it loads the disk copies, turns off
Swift prespecialization for the processes that load them and cannot use conformance caching. Folding the replacements
into the cache removes those overrides.

The tool reads Apple's cache and libraries from the user's own image and writes a new cache family next to it. It
ships as code only: no Apple binary, cache or library is part of this repository, and the family it writes is an
Apple-derived file that stays on the user's Mac.

Whether a rebuilt family works is shown only by booting an image that installs it; a passing build and readback do not
show it.

## Requirements

- A Mac with Apple silicon or an Intel Mac. Built and checked on Apple silicon with macOS 27.2 and Xcode 27.2.
- Xcode (the full app: `xcodebuild` builds Apple's cache builder) and its command-line tools.
- Python 3.14 (`python3.14` on the `PATH`).
- Apple's published dyld source, tag `dyld-1378`, from
  [apple-oss-distributions/dyld](https://github.com/apple-oss-distributions/dyld/tree/dyld-1378).
- Network access while the toolchain is built: `toolchain/make` clones Apple's
  [AvailabilityVersions](https://github.com/apple-oss-distributions/AvailabilityVersions) at tag
  AvailabilityVersions-157.2 to generate dyld's `VersionMap.h` (`--availability DIR` uses a copy instead).
- The macOS 27 26B131 system to rebuild, mounted read-only: the System volume (or its snapshot), the OS and App
  cryptexes and the Data volume. A hao image built with `tools/hao` has all four.
- About 30 GB free: the work folder holds about 21 GB during a build (removed at the end), the family about 7 GB.

## Build

```sh
git clone --depth 1 --branch dyld-1378 https://github.com/apple-oss-distributions/dyld.git DYLD_SOURCE

# Apple's cache builder and the host adapters, from DYLD_SOURCE plus toolchain/source.patch and
# toolchain/added-source; built for this Mac's architecture (--arch arm64|x86_64 to choose).
python3.14 -I tools/shared-cache/toolchain/make DYLD_SOURCE TOOLCHAIN

# The fold-in family: the 26 providers of features/fold-in-26B131.json, Apple's Swift entries kept,
# no prebuilt loader sets (dyld uses its just-in-time loaders).
python3.14 -I -B tools/shared-cache/make \
  --system SYSTEM --os OS_CRYPTEX --app APP_CRYPTEX --data DATA \
  --features fold-in-26B131 --no-swift-catalog --no-prebuilt-loaders \
  --toolchain TOOLCHAIN --work NEW_WORK --output NEW_FAMILY
```

`SYSTEM`, `OS_CRYPTEX`, `APP_CRYPTEX` and `DATA` are the mount points of the image's System, OS cryptex, App
cryptex and Data volume. Instead of the four, `make IMAGE` mounts a disk image (`IMAGE` a `.img`, or a folder with
`target.img`) read-only itself; an image repository (a folder with `image.json`) is exported first with
`tools/bin/ssdimg` (`SSDIMG` names another copy). `NEW_WORK` and `NEW_FAMILY` must not exist.

Before writing, the build checks that the volumes of `NEW_WORK` and `NEW_FAMILY` have about 30 GB free (plus the
image's size when an image repository is exported into `NEW_WORK`) and stops otherwise; `SHARED_CACHE_DISK_CHECK=COMMAND` adds a site check, run as `COMMAND check --need GB --who shared-cache
--what TEXT`, whose non-zero exit stops the build too. When the family is written, `NEW_WORK/build` (the
intermediates, about 21 GB) and an exported `source.img` are removed; `--keep-work` keeps them.

A provider's bytes come from the System given, at the path its feature names. A feature file may instead name a
file outside the System for a provider (`"sources": {"NAME": {"path": FILE, "sha256": HASH}}`), for example a
library built after the image; the hash is checked before the build.

`--swift-catalog CATALOG` adds Swift prespecialized metadata from a catalog that `make swift-capture` made for the
same Mach-O build (it is captured on the target, a 26B131 x86_64 system); its entries go into libswiftCore's
pointer-keyed table next to Apple's. Without it (`--no-swift-catalog`) Apple's entries are kept as they are.

## Outputs

`NEW_FAMILY` holds:

- `dyld_shared_cache_x86_64` and its subcaches (`.01` ... `.33`), `.atlas` and `.map`: the family, signed.
- `retired-roots.txt`: the providers' install paths. The image that installs the family removes these files from
  the disk (or keeps them; then dyld still loads the disk copies).
- `provider-provenance.json`: each provider's source (system or file), install path and sha256.
- `optimizations.json`: what the readback of the final family found: images, ObjC roots and header-infos, Swift
  keys (retained and added) and relocated pointers.
- `build.json`: the generator's revision (the newest line of Revisions below) and git commit, the date, and the
  path and sha256 of every input. It names the build machine's paths; it stays with the family.

The build refuses, rather than guesses, when an input does not fit: an import no image defines, a provider outside
the given System without a pinned source, an interpose section left in the family, a table that does not read back.
The refusal names the image and symbol.

## How it works

1. **Providers.** Each provider is read from the System (or its pinned source) and laid out like a cache image.
   Every image that depended on the cache image it replaces is bound to the provider's exports; a symbol the
   provider lacks gets dyld's missing-symbol marker (a non-weak import), null (a weak import) or keeps its old
   target (an ObjC class reference whose old class bytes stay in the cache), and anything else stops the build.
2. **Interposers.** dyld ignores `__interpose` in cache images, so each interpose tuple of a provider becomes cache
   patches: every use of the original that the cache's patch table lists is retargeted to the replacement, while the
   interposer's own calls through keep the original. The section is zeroed and renamed `__ipose_applied`.
3. **Cache.** Apple's cache builder (built from dyld-1378 by `toolchain/make`) lays out the new family. The ObjC
   selector, class and protocol tables, header-infos and image indexes are rebuilt for the new image set; Swift's
   prespecialized tables are rebuilt with Apple's entries kept and any catalog entries added.
4. **Readback.** The signed family is read back independently of the writer: image list, loader fields, ObjC roots
   in both dyld's and libobjc's views, every Swift key and every relocated pointer. `optimizations.json` records it.

`tools/live-text/interpose-check FAMILY RUNTIME` checks step 2 on a family through `ipsw`, independently of this
tool.

## Other commands

`make` takes a subcommand instead of an image as its first argument for the Swift metadata work: `swift-capture`
(prepare a capture bundle for the target, or normalize a capture into a catalog), `inventory` and `swift-select`
(list and subset a catalog), `prespecialization-overrides` (edit libswiftCore and dyld so prespecialization stays on
with overridden images, the alternative to folding them in), `fastpaths`, `metadata-expand`, `swift-coverage` and
the probes listed in `make`. Each prints its usage with `--help`.

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/shared-cache/tests
```

The host suite (about 470 tests, a few seconds) uses synthetic Mach-O and cache fixtures; it needs no Apple data.
Tests that need a real cache, a built toolchain or a target runtime skip unless their variables are set:
`CACHE_TOOLCHAIN` (a `toolchain/make` output), `SHARED_CACHE_STORE` (a store with saved catalogs),
`SWIFT_WITNESS_RUNTIME_TOOLCHAIN`, `SWIFT_WITNESS_CONSUMER_BUNDLE`.

The fold-in build is reproducible: built from a fresh clone, with toolchains for arm64 and for x86_64, it gives the
same family byte for byte apart from `build.json` (date, commit, revision, paths, and the toolchain's compiled
objects, which embed their build folder).

## Validation

NEEDS VALIDATION: no family built by this revision has been booted yet. A boot of an image that installs it (the
`dev-bs-test-fold` set) is the check; a build and its readback are not.

## Licences

See [NOTICE.md](NOTICE.md): the vendored frida-gum and Swift headers and Apple's open-source headers and the patch
against dyld keep their own licences. The rest is covered by the repository's MIT license.

## Rebuild Swift conformance lookups

Cache builders can regenerate type, metadata and foreign-name lookups from the
final image sections and ObjC identities. The normal build does this before
loader finalization. Each key is checked with Apple's runtime table reader;
Python separately checks all values and duplicate chains.

```sh
# Inspect a family and emit regenerated table blobs (no cache writes).
python3.14 -I tools/shared-cache/make swift-conformance --cache CACHE --output NEW_DIRECTORY
# Emit a signed family from a pinned bootstrap manifest.
python3.14 -I tools/shared-cache/make swift-conformance --manifest MANIFEST --output NEW_DIRECTORY
```

Set `CACHE_TOOLCHAIN` to a `toolchain/make` output. `--writer` reuses a compiled
adapter. `--replay-existing` with `--cache` calibrates serialization against the
existing records instead of scanning sections.

The signed stage preserves table addresses and refuses tables larger than their
original reservations. It updates subcache UUIDs, the main UUID directory and
signatures; the normal producer subsequently rebuilds external sidecars. A stage
output alone is not a deployable artifact. Existing runtime override gates remain
in force; host lookup checks do not establish boot success or a speedup.

### Standalone x86_64 decoder

Build the decoder with `python3.14 -I tools/shared-cache/llvm-disasm/make OUTPUT` (CMake, make and a macOS
compiler/SDK). The output contains the signed library, LLVM license and `build.json`; `work/` is disposable.
The library itself needs only macOS system libraries. Run the cache tool with x86_64 Python when using it
(on Apple silicon: `SHARED_CACHE_DISASM=OUTPUT/libhao-x86-disasm.dylib arch -x86_64 python3.14 -I tools/shared-cache/make ...`).
An arm64 Python process uses Xcode's libLTO, since it cannot load the x86_64 decoder.

## Revisions
- r95 2026-11-20 The decoder follows the process architecture: x86_64 Python (Intel or Rosetta) uses the
  llvm_disasm row, arm64 Python uses Xcode's libLTO. r94 loaded the x86_64 row on arm64 and failed 6 host tests.
- r94 2026-11-20 Build a pinned, redistributable x86_64 LLVM decoder with llvm-disasm/make. Decoding uses
  SHARED_CACHE_DISASM, then the verified llvm_disasm input row when present, then Xcode's libLTO.
- r93 2026-11-20 toolchain/make applies source.patch with --forward --batch: a DYLD_SOURCE already patched in place
  stops the build instead of being silently un-patched (patch took such hunks as reversed).
- r92 2026-11-20 tools/prespecialization-adoption.py: on a running target, checks that the Swift runtime reads the
  family's generated prespecialization map and finds entries in it.
- r91 2026-11-20 macOS 27: the witness workers (swift-witness-graph, swift-dependency-replay, swift-witness-consumer) hook through tools/inline-hook.h (frida-gum's x86 relocator and writer, a trampoline written RW then made RX) instead of GumInterceptor, whose unwind broker recursed until the stack overflowed before the first record. normalize_graph finds owned rows through an index, not a scan per reference (a 155k-constructor witness capture took hours, now minutes). dependency-replay REPLAY_CONTINUE=1 (diagnostic) lists every record whose initialization changes identity in one run; the verdict still fails on any.
- r90 2026-11-18 Folded providers' small method lists are written in the stock in-cache form (0xe000000f): types are offsets from the selector base, and their strings are copied into the selector pool, which lies in range of it. They were entry-relative (0xc000000f), and 27's libobjc reads every in-cache small list base-relative, so 13,676 methods returned other strings as their types (sharingd and WiFiAgent aborted). The assembly now reads every rewritten list back that way and refuses a method whose types differ from its source.
- r89 2026-11-18 The program loader pool reserves twice the source pool plus 4 KiB per provider class plan (was 64 KiB). The 64 KiB term reserved 196 MB for a 66,118-record Swift catalog, against a final pool of 17.9 MB. The fresh metadata region then fit no free gap within legacy_objc_root's +-2 GiB, and the build refused. A pool past its reserve is still refused (build_pool).
- r88 2026-11-18 Prebuilt loaders in 27's layout (26B131): a TLV pre-keyed flag at +0x2c bit 4, an 11-bit regionsCount, +0x26 left 0, versionHash 0x9593b355 (source.patch, read_set). The generator maps 27's 568-byte cache header. Main executables no longer get the treat-as-weak operator binds (27 resolves them at launch), and a Catalyst program takes iOSSupport twins first. A cached dylib that links nothing but names an umbrella gets that umbrella as its dependent. Regenerated from stock 262FA161: the cached set and 2375 of 2376 program sets match byte for byte (open: one weak link satisfied only by a cache alias).
- r87 2026-11-18 Regenerate Swift conformance lookups from final image sections before loader finalization; verify keys with Apple’s reader and update signatures and UUID references.
- r86 2026-11-18 No prebuilt loaders: the dylibs loader-set address is the cache base, not 0. 26B131's dyld calls validHeader on it without a null check, and 0 faulted launchd at boot; at the base it reads the header magic and falls back to JIT loaders.
- r85 2026-11-15 Disk guard: a build checks free space on its work and output volumes (and an optional site check
  command) before writing, and removes its intermediates when the family is written (`--keep-work` keeps them).
- r84 2026-11-15 First public revision: folds the on-disk replacements of a 26B131 x86_64 cache into a new signed
  family (providers, interposers as cache patches, ObjC and Swift tables, readback); the toolchain builds on Apple
  silicon and Intel from Apple's dyld-1378 and AvailabilityVersions-157.2.
