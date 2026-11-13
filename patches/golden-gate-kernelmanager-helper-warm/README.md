# golden-gate-kernelmanager-helper-warm

Starts Apple's `kernelmanager_helper` with launchd, at Interactive priority, instead of on demand at background
priority: `RunAtLoad=true` and `POSIXSpawnType=Interactive` for the job `com.apple.kernelmanager_helper`, in its plist
and in its entry of launchd's job cache. Everything else in the job stays Apple's (`EnablePressuredExit`,
`EnableTransactions`, the Mach service, `_PanicOnCrash`). The Golden Gate port of mac27's Tahoe recipe
`kernelmanager-helper-warm`; it pairs with [hao-opencore](../hao-opencore/README.md) r44's `auxKCAtBoot=false`.

## Root cause

FACT (bench of image 8f5ddce, 26B131, log `machTimestamp` s after the kernel; 27.2's kernelmanagerd 514.40.8):
- For every kext the kernel loads that is in none of the collections it knows, kernelmanagerd asks
  kernelmanager_helper for a load report, on the thread that serves the kernel's requests. Those are the 9 kexts
  OpenCore injects (Lilu, RestrictEvents, BlueToolFixup, CryptexFixup, KDKlessWorkaround, RSRHelper,
  AirportBrcmFixup, AMFIPass); the helper answers "Did not find identifier" for each, every boot.
- In 8f5ddce the helper was already running then, because kernelmanagerd had spawned it 20 s earlier to build an
  Aux KC (which fails on this OS): the 9 answers take 0.04 s (68.89-68.93) and IOKit quiet follows at 69.46.
- With hao-opencore r44 that build is skipped, so the first load report (Lilu) would spawn the helper. Its start
  took 10.1 s in 8f5ddce (launchd spawn 48.20, "Starting userland kernel management helper" 50.24, listening 58.34),
  under hao and at the job's background priority (`POSIXSpawnType Adaptive`; a kernel upcall donates no importance).
  mac27 measured the same on Tahoe (5.3 s queue wait; X86PlatformPlugin, AppleHDA and autofs wait behind it, then
  IOKit quiet, WindowServer and the Language Chooser).

26B131's job plist is byte-identical to Tahoe 25G332's (sha256 2de9e533...): same label, path and keys.

## How it works

`src/kc-hook.sh` runs `src/warm.py` on the job plist and on launchd's job cache `System/Library/xpc/launchd.plist`
(binary; the job's entry is keyed by `/System/Library/LaunchDaemons/com.apple.kernelmanager_helper.plist` and carries
the same keys), then puts both back root:wheel 0644 `restricted`, as [golden-gate-userspace](../golden-gate-userspace/README.md)
does for its disabled hybridsearchd job. It must run after golden-gate-userspace, which also writes the job cache. The
edits are declared as `plist.set` in [edits/job.json](edits/job.json) and
[edits/launchd-job-cache.json](edits/launchd-job-cache.json); `tests/test_edits.py` proves the engine writes the
script's bytes. No input row.

- `EnablePressuredExit` stays: launchd may stop the helper under memory pressure when it holds no transaction, and
  relaunches it on the next message, as today.
- Cost: the helper's start moves to launchd start, next to kernelmanagerd's own start (38.9-48.0 s in 8f5ddce), on 4
  logical CPUs. mac27 measured kernelmanagerd's Aux activation 0.8 s later on Tahoe for this reason.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/golden-gate-kernelmanager-helper-warm/tests`; with
  `KMHELPER_WARM_JOB` and `KMHELPER_WARM_CACHE` set to 26B131's pristine files, the real-input tests check the
  recorded outputs (job 9c54029c..., cache after golden-gate-userspace 5a45e45b...).
- The build log prints `golden-gate-kernelmanager-helper-warm: HASH PATH` for both files.
- On the laptop: `plutil -p /System/Library/LaunchDaemons/com.apple.kernelmanager_helper.plist` shows both keys;
  `launchctl print system/com.apple.kernelmanager_helper` shows the job running after boot. In the boot log launchd
  spawns kernelmanager_helper near kernelmanagerd's start (not "because ipc" at the first load report), the helper is
  listening before kernelmanagerd's "Received kext load notification: as.vit9696.Lilu", and the 9 "Did not find
  identifier" answers follow their notifications within milliseconds.

## Validation

NEEDS VALIDATION: not built or booted. Host tests pass with the real 26B131 files (extracted read-only from the
26B131 IPSW).

## Revisions
- r1 2026-11-12 New: kernelmanager_helper RunAtLoad, Interactive (job plist and launchd job cache), so the helper is ready for the OpenCore kexts' load reports once hao-opencore r44 skips the boot-time Aux KC build that used to start it.
