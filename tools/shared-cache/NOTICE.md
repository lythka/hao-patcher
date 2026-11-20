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
