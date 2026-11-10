# ssdpush

The receiving end of image transfers. `ssdpush-agent` runs on the target Mac (as
root for a raw disk), reads the frame stream that `ssdimg push` sends, checks each
chunk's BLAKE2b hash, writes it and reads it back. `ssdinstall` plays an image
bundle from a USB disk into the same agent, without a host Mac; the Recovery's
"Reinstall macOS" ([`patches/rec-reinstall`](../../patches/rec-reinstall)) uses it.

## Build

```sh
zsh tools/ssdpush/build.sh [ZSTD_SOURCE]
```

`ZSTD_SOURCE` is an unpacked [zstd](https://github.com/facebook/zstd) 1.5.x release
(default `store/cache/zstd/zstd-1.5.7`); only its decompressor is compiled in, under
zstd's BSD license. The script builds `ssdpush-agent-macos-x86_64` (SSSE3/AVX
BLAKE2b, for the target's macOS Recovery) and `ssdpush-agent-macos` (for local
tests) in this folder; they are not tracked. `ssdinstall` is built by
`patches/rec-reinstall/make.sh`.

## Use

See `tools/bin/ssdimg --help` and `tools/ssdimg/push.py`. A push overwrites the
selected disk: run it only against a target booted into a Recovery with SSH, after
confirming the disk. `ssdimg verify` re-reads a disk without writing.

The protocol hash-checks and reads back every chunk, so a completed transfer
proves the disk holds the image. It does not prove that the image boots.
