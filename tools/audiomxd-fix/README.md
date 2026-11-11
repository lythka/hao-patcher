# audiomxd-fix

Stops the audio retry loop that Golden Gate runs on Intel while no user
is logged in. Before login, AudioAccessoryServices reports a false server death
to `audiomxd`, which retries its request and keeps asking `configd` for the
console user, so both services stay busy. The fix removes that one false
notification; real XPC-death handling and the original no-user error are kept.

## How it works

In AudioAccessoryServices, `-[BTAudioRoutingRequest _ensureXPCStarted]` compares
the console uid with zero. Its zero branch sends `_handleServerDied`. The fix
replaces that six-byte indirect call with a six-byte NOP.

The tool finds the call by its shape, not by offset: in `__TEXT,__text` it looks
for `movq SELREF(%rip),%rsi; movq %reg,%rdi; callq *GOT(%rip)` (or the repaired
NOP), resolves each selector reference through the cache's slide info, and keeps
the one `_handleServerDied` send that a `cmpl $0,disp8(%rbp); je` branch reaches.
It must find exactly one; otherwise it stops with `MISSING/unsupported` and the
image UUID. An already repaired cache plans no edits. On 27.0 (26A428) the site
is `dyld_shared_cache_x86_64.03+0x2192c56b`; on 27.2.1 (26B131) it is
`dyld_shared_cache_x86_64.01+0x2217f56b`.

## Usage

Requires Python 3.9 or later and the original x86_64 dyld cache family from the
Golden Gate RosettaOS. Run from this folder:

```sh
python3 -I audiomxd-fix plan --cache CACHE_DIRECTORY
python3 -I audiomxd-fix build --cache CACHE_DIRECTORY --output NEW_ARTIFACT
sudo python3 -I audiomxd-fix install --cache OFFLINE_CRYPTEX_CACHE_DIRECTORY --artifact ARTIFACT
```

- `plan` prints the file/offset edits as JSON. The image build uses this:
  [`patches/golden-gate-userspace`](../../patches/golden-gate-userspace) applies
  the edits before it re-signs the cache (input row `audio_fix_source`).
- `build` copies only the affected subcache, applies the edit, re-signs it with
  4 KiB pages and writes the input and output hashes to `build.json`.
- `install` checks those hashes and keeps ownership, modes and flags. Run it
  only in an offline maintenance environment. It does not mount, seal, bless
  or replace a running cryptex.

## Verify

```sh
python3 -I -m unittest discover -s tests
```

The tests cover the no-console call, an already repaired cache, a wrong
architecture or UUID, and a changed branch or call. On a booted image, leave
Language Chooser idle for 30 seconds and check that `audiomxd` and `configd`
are idle, then check sound playback, the Control Center audio controls, and
logging out and back in.

## Limitations

The planner finds the site in 26A428 and 26B131; a later build is accepted only
if the same code shape and selector are present. Not yet confirmed on a freshly
built 26B131 image.

The cache mapping resolver and the 4 KiB signature writer are adapted from
`patches/golden-gate-userspace/src`. Licensing: see [NOTICE.md](NOTICE.md).

## Revisions

- r2 2026-11-11 Locate the no-console call by code shape and selector instead of UUID and offset (26A428 and 26B131).
