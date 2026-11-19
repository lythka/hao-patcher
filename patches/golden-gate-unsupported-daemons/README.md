# golden-gate-unsupported-daemons

Apple services that cannot work on an Intel Mac do not start. Each is disabled with launchd's own override (what
`launchctl disable system/LABEL` writes), so `launchctl enable` turns it back on.

## What it changes (Data volume)

- `private/var/db/com.apple.xpc.launchd/disabled.plist`: each label in `src/disabled-daemons.txt` is set to true.
  Apple's own entries and any others are kept.

## Why (S18)

Each label's reason is in `src/disabled-daemons.txt`. The first two are the Apple Intelligence daemons, which need
Apple silicon:
- `com.apple.modelmanagerd` respawns every 1-3 s on an Intel image (diag#19). Each instance exits after a
  memory-limit warning, and the next call from RunningBoard or gamepolicyd starts it again. In the first-login tail
  that was 26 instances in 44 s and 62% of the CPU (measured 2026-11-18).
- `com.apple.modelcatalogd` downloads assets for it.

`com.apple.prngseedd` needs a Secure Enclave. It runs `seputil --daemonize-reseed`, which reseeds the kernel PRNG from
the SEP's random-number generator. On an Intel Mac without a SEP, seputil exits at once without finishing its XPC
activity, and launchd starts it again about every 10 s: 595 exits in one boot (laptop, 2026-11-18). Tahoe ships an
arm64e-only seputil, so on Intel Tahoe this job never runs.

A label is added here only with evidence that the service is inert or failing on this hardware.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/golden-gate-unsupported-daemons/tests`: the plist gets every
  label, keeps other entries, and is root 644.
- On the Mac: `launchctl print-disabled system` lists the labels as disabled; no modelmanagerd runs after login; its
  callers (runningboardd, gamepolicyd) log no retry loop; first login settles sooner.

## Validation

Laptop 2026-11-19, a MacBookPro9,2 with image 8305fdd (26B131, built from fb6542df with r3): `launchctl print-disabled system` lists modelmanagerd, modelcatalogd and prngseedd as disabled, and launchd started none of them after boot. r1, 2026-11-18, on a fresh install of a v0.0.1 candidate: first login settled in 885 s, against 928 s on stock.

## Revisions
- r4 2026-11-19 Validated r3 on hardware (Validation). Nothing it installs changes.
- r3 2026-11-18 com.apple.prngseedd disabled: it needs a Secure Enclave and respawned every ~10 s (CB1).
- r2 2026-11-18 Validated r1 on hardware (Validation); nothing it installs changes.
- r1 2026-11-18 New: modelmanagerd and modelcatalogd disabled (diag#19; owner: disable what this hardware cannot run).
