# display-state-shim

Builds `dss_shim.dylib`, the display-state adapter that
[`patches/golden-gate-userspace`](../../patches/golden-gate-userspace) loads into
WindowServer on 26A428 Intel images (input row `display_state_shim`). Without it,
every Golden Gate client fails with "Error deserializing display system state" and
sees no displays.

## How it works

Golden Gate's x86_64 CoreDisplay encodes the display system state without the
one-byte "sync-session UUID present" flag that every 27.0 SkyLight client reads at
offset `0x20`. Both layouts start with the same 32-byte header; the client then
expects the flag before the `u32` display count, while the x86_64 encoder writes
the count directly. Everything after that is identical.

The library interposes `_CGXGetDisplaySystemState`, the function SkyLight's MIG
server calls, and inserts a zero flag byte (no sync-session UUID). It acts only in
the window-server process with SkyLight UUID `02A9A202-2F39-3204-86DE-A09EA6AE8DEE`
and CoreDisplay UUID `61C82C50-C91D-3028-933E-FC20D2AB40FA`; everything else passes
through. A buffer is converted only if its count is at most 64 and its first
display record has version 3 (the 26A428 x86_64 encoder's), so a buffer already in
the client layout is never converted twice.

## Build

Requires macOS, Python 3.12 or later, the Xcode command-line tools, and Rosetta
on Apple silicon for the x86_64 tests.

```sh
python3 -I tools/display-state-shim/display-state-shim --out NEW_ARTIFACT
```

The destination must not exist. The inputs are the C sources and the handwritten
CoreDisplay link stub in this folder; no Apple binary is needed. The output is
`dss_shim.dylib` (x86_64, ad hoc signed) and a `build.json` with the source commit,
source hashes, compiler, SDK, command and output hash. The install name
`/usr/local/lib/dss_shim.dylib` is kept for compatibility; the image patch installs
the library at `/usr/lib/dss_shim.dylib`.

The output bytes depend on the compiler, SDK and linker. Apple clang 21.0.0 with
the 27.2 SDK reproduces the dylib selected by the `display_state_shim` row
(SHA256 `b5067e614e765427c1524f02dfa397472891e274d06a36cd779506a20649b2d1`). To use
a new build, put its hash and path in that row.

## Verify

```sh
python3 -I -m unittest discover -s tools/display-state-shim/tests
```

The test runs 14 converter checks on synthetic buffers with the 26A428 layout,
builds the library, checks its architecture, signature and manifest, and loads it
in a signed command-line process to confirm the CoreDisplay import binds. It
launches no GUI application. On a booted image, WindowServer logs the first
rewritten state and the displays come online.
