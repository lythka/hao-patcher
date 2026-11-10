# audiomxd-fix

Stops the audio retry loop that Golden Gate (26A428) runs on Intel while no user
is logged in. Before login, AudioAccessoryServices reports a false server death
to `audiomxd`, which retries its request and keeps asking `configd` for the
console user, so both services stay busy. The fix removes that one false
notification; real XPC-death handling and the original no-user error are kept.

## How it works

In AudioAccessoryServices (UUID `34D52126-6651-3DDE-BB42-6B17B8C547E1`),
`-[BTAudioRoutingRequest _ensureXPCStarted]` compares the console uid with zero.
Its zero branch calls `_handleServerDied` at `__TEXT+0x2456b`. The fix replaces
that six-byte indirect call with a six-byte NOP.

Before planning a write, the tool checks the image UUID, the conditional branch,
the receiver setup and the original bytes. Unknown code is refused, and an
already repaired cache plans no edits. The file offset is resolved through the
supplied cache's mappings (for the 26A428 cache it is
`dyld_shared_cache_x86_64.03+0x2192c56b`), not assumed.

## Usage

Requires Python 3.9 or later and the original 26A428 x86_64 dyld cache family
from the 27.0 RosettaOS. Run from this folder:

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

The repair matches only the 26A428 AudioAccessoryServices image. It has not yet
been confirmed on a freshly built image.

The cache mapping resolver and the 4 KiB signature writer are adapted from
`patches/golden-gate-userspace/src`. Licensing: see [NOTICE.md](NOTICE.md).
