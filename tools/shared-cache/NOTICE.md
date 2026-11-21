# Notices

The code in this folder is covered by the repository's [MIT license](../../LICENSE), except the third-party source
listed here, which keeps its own license. It contains no Apple binaries; the dyld cache and libraries it reads and the
family it writes are the user's own files and keep Apple's terms.

- `vendor/frida-gum/gumx86relocator.h`: [frida-gum](https://github.com/frida/frida-gum) 17.25.0, wxWindows Library
  Licence 3.1 (`vendor/frida-gum/COPYING`; provenance in `source.json`).
- `vendor/swift-remote-mirror/`: headers from [Swift](https://github.com/swiftlang/swift) at caab90f, Apache License
  2.0 with Runtime Library Exception (`vendor/swift-remote-mirror/LICENSE.txt`; provenance in `source.json`).
- `toolchain/added-source/include/` and `toolchain/source.patch`: headers from Apple's open-source releases (libc,
  xnu, libplatform, libpthread, CommonCrypto; sources in `toolchain/inherited-header-provenance.json`) and a patch
  against Apple's published dyld source
  ([dyld-1378](https://github.com/apple-oss-distributions/dyld/tree/dyld-1378)), under the Apple Public Source
  License 2.0 (`APPLE_LICENSE_HEADER` and xnu's `APPLE_OSREFERENCE_LICENSE_HEADER`) or the Apache License 2.0 as
  stated in each file.
- `toolchain/added-source/include/System/atexit.h`: from Apple's Libc (FreeBSD `stdlib/atexit.h`), Copyright (c)
  1990, 1993 The Regents of the University of California, under the BSD license stated in the file.
- Not stored here, downloaded when the toolchain is built: Apple's dyld source (tag dyld-1378, given by the user to
  `toolchain/make`) and Apple's
  [AvailabilityVersions](https://github.com/apple-oss-distributions/AvailabilityVersions) (tag
  AvailabilityVersions-157.2, cloned by `toolchain/make`, which generates dyld's `VersionMap.h` from it).
- `llvm-disasm/make` downloads [llvm-project 21.1.8](https://github.com/llvm/llvm-project/releases/tag/llvmorg-21.1.8)
  to build the X86 decoder, under Apache License 2.0 with LLVM Exceptions. The artifact includes LLVM's
  `LICENSE.TXT`; the source archive URL and SHA256 are pinned in the producer.

- Input rows `shared_cache_frida_arm64_devkit` and `shared_cache_frida_x86_devkit`: Frida Gum 17.25.0
  [release devkits](https://github.com/frida/frida/releases/tag/17.25.0), wxWindows Library Licence 3.1.
  `runtime-helpers/make` links both architectures into the offline relocator; `consumer-helpers/make` links the
  target-only fixtures. Frida bytes are not copied into the rebuilt cache.
- `shared_cache_swift_demangle`: Swift demangling sources at caab90f7f930e77ac7e460151754f73cb3a848bc,
  Apache License 2.0 with Runtime Library Exception, linked with LLVM 21.1.8 under Apache License 2.0 with LLVM
  Exceptions. `swift-demangle/make` builds the pinned universal helper; the artifact includes Swift's LICENSE.txt.
- `shared_cache_generator`, `shared_cache_objc_writer`, `shared_cache_swift_table_writer`, and
  `shared_cache_swift_conformance_writer`: source-built dyld-1378 serializers under the licenses listed above;
  `helpers/make` links them into universal executables.

- `shared_cache_builder`: the offline bundle combines this producer with the pinned dyld-based helper
  executables, Frida relocator/runtime object and LLVM decoder. `package-user-inputs` includes dyld's
  `APPLE_LICENSE`, this notice, and the LLVM/Frida licence files. The Swift metadata catalog is a separate
  gzip asset; neither bundle contains the user's Apple executable files or rebuilt shared cache.
