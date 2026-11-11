# display-state-shim

Builds `dss_shim.dylib`, the display-state adapter that
[`patches/golden-gate-userspace`](../../patches/golden-gate-userspace) loads into
WindowServer on Golden Gate Intel images (input row `display_state_shim`). Without it,
every Golden Gate client fails with "Error deserializing display system state" and
sees no displays.

## How it works

Golden Gate's x86_64 CoreDisplay encodes the display system state without the
one-byte "sync-session UUID present" flag that every Golden Gate SkyLight client
reads at offset `0x20`. Both layouts start with the same 32-byte header; the client then
expects the flag before the `u32` display count, while the x86_64 encoder writes
the count directly. Everything after that is identical.

The library interposes `_CGXGetDisplaySystemState`, the function SkyLight's MIG
server calls, and inserts a zero flag byte (no sync-session UUID). It acts only in
the window-server process with a SkyLight and a CoreDisplay whose UUIDs are listed in
`src/dss_builds.h`; everything else passes through, and the unified log
(`com.hao.dss-shim`) says once why: `dss-shim inert: MISSING: x86_64 SkyLight image`,
`dss-shim inert: unsupported: no entry for CoreDisplay UUID ...`, and so on. A buffer is
converted only if its count is at most 64 and its first display record has version 3
(the x86_64 encoder's), so a buffer already in the client layout is never converted
twice.

## Builds

| Build | SkyLight | CoreDisplay |
|---|---|---|
| 26A428 | `02A9A202-2F39-3204-86DE-A09EA6AE8DEE` (953.5.5) | `61C82C50-C91D-3028-933E-FC20D2AB40FA` |
| 26B131 | `21ACABFA-B6CC-3EC9-A3CF-6D4D6B637F5A` (957.8.8) | `8B08B249-430F-3565-A375-86272774C777` |

An entry is added after comparing the build's x86_64 libraries (extracted from its
dyld shared cache) with a build already listed. For 26B131: CoreDisplay's disassembly equals
26A428's apart from addresses, so the encoder is the same; SkyLight's MIG server
`_XGetDisplaySystemState` is the same; and the client decoder
`get_current_display_system_state` reads the same field sequence (header, the flag
at `0x20`, the optional UUID, the count, then per display the same fields up to
version 9). The only decoder change is one more `u8` read for display version 10 or
later, which version-3 records never reach.

## Build

Requires macOS, Python 3.12 or later, the Xcode command-line tools, and Rosetta
on Apple silicon for the x86_64 tests.

```sh
python3 -I tools/display-state-shim/display-state-shim --out NEW_ARTIFACT
```

The destination must not exist. The inputs are the C sources and the handwritten
CoreDisplay link stub in this folder; no Apple binary is needed. The output is
`dss_shim.dylib` (x86_64, ad hoc signed) and a `build.json` with the source commit,
source hashes, compiler, SDK, command, the supported builds and UUIDs, and output
hash. The install name `/usr/local/lib/dss_shim.dylib` is kept for compatibility; the
image patch installs the library at `/usr/lib/dss_shim.dylib`.

The output bytes depend on the compiler, SDK and linker. To use a new build, put
its hash and path in the `display_state_shim` row.

## Verify

```sh
python3 -I -m unittest discover -s tools/display-state-shim/tests
```

The test runs 14 converter checks on synthetic buffers with the x86_64 layout,
checks the build table and the process gate (without CoreDisplay), builds the
library, checks its architecture, signature and manifest, and loads it in a signed
command-line process to confirm the CoreDisplay import binds. It
launches no GUI application. On a booted image, WindowServer logs the first
rewritten state and the displays come online.

## Revisions
- r4 2026-11-11 Build table `src/dss_builds.h` replaces the pinned 26A428 UUIDs and adds 26B131 (same encoder and
  wire format); the inert log names the missing image or the unlisted UUID.
