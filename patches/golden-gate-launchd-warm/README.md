# golden-gate-launchd-warm

Starts Apple LaunchDaemons that the boot waits on with launchd, at Interactive priority, instead of on demand at
background priority, by editing each job's plist and its entry in launchd's job cache. One recipe owns these launchd
edits; the jobs are listed in `JOBS` in [src/warm.py](src/warm.py):

| Job | Keys set | Who waits for it |
|---|---|---|
| `com.apple.kernelmanager_helper` | `RunAtLoad=true`, `POSIXSpawnType=Interactive` | kernelmanagerd, for the load report of each kext OpenCore injects |
| `com.apple.mbsystemadministration` | `KeepAlive={PathState: {/private/var/db/.AppleSetupDone: false}}`, `POSIXSpawnType=Interactive` | loginwindow, before Setup Assistant's first pane |

Every other key stays Apple's. Formerly `golden-gate-kernelmanager-helper-warm` (r1); the kernelmanager_helper part
is the Golden Gate port of mac27's Tahoe recipe `kernelmanager-helper-warm` and pairs with
[hao-opencore](../hao-opencore/README.md) r44's `auxKCAtBoot=false`.

## Root cause

### kernelmanager_helper

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

### mbsystemadministration

FACT (mac27's boot Gantt of image a007b23, 26B131): loginwindow asks Setup Assistant's root service
`/System/Library/CoreServices/Setup Assistant.app/Contents/Resources/mbsystemadministration` (Mach service
`com.apple.mbsystemadministration`) before it shows Setup. launchd spawns it then, "because ipc" at 66.90; it is
listening at 71.09 and the Language Chooser appears at 71.74. That is 4.4 s of the second boot and 4.7 s of the first
in which the screen waits for one daemon's start. The program is arm64e only, so it runs under hao.

FACT (26B131's job, sha256 34d524fb...; its arm64e code read with `ipsw`): no `RunAtLoad`, no `KeepAlive`,
`POSIXSpawnType Adaptive`, `EnableTransactions false`, `EnablePressuredExit false`, so launchd never stops it.
`-[MBSAServer init]` sets `shouldTerminateWhenIdle` to YES; the program exits ("No more connections,
mbsystemadministration will exit...") only when its last client connection goes away. `-[MBSAServer startServer]`
logs "Starting MBSystemAdministration...", gets an Admin framework authorization and resumes its two listeners; the
account, session and reboot work happens only on a client's request.

Who needs it: Setup Assistant, which runs while `/private/var/db/.AppleSetupDone` is missing (every image is
generalized without it, see `tools/ssdimg/generalize.py`; Setup writes it when it creates the account). On a set-up
system nothing asks for it at boot. Started unconditionally (`RunAtLoad`), it would start on every boot and, with no
client ever connecting, stay resident until shutdown. So the job gets launchd's own condition instead:
`KeepAlive` `PathState` `/private/var/db/.AppleSetupDone` = false starts it at load while the file is missing and does
not start it once it exists. Apple uses the same shape for 26B131's cryptexd (`/private/var/run/com.apple.security.cryptexd/init`
false, with Mach services) and configureLocalKDC (`/var/db/.configureLocalKDC` false). After Setup it is started on
demand as before (per-user Setup after the first login still reaches it through its Mach service).

## How it works

`src/kc-hook.sh` runs `src/warm.py` on the System mount: it writes each job plist and launchd's job cache
`System/Library/xpc/launchd.plist` (binary; each job's entry is keyed by its plist path and carries the same keys) to
a work folder, and the hook puts each back root:wheel 0644 `restricted`, as
[golden-gate-userspace](../golden-gate-userspace/README.md) does for its disabled hybridsearchd job. It must run after
golden-gate-userspace, which also writes the job cache (the ledger chain apple -> golden-gate-userspace -> this
recipe). The edits are declared as `plist.set` in [edits/kernelmanager-helper-job.json](edits/kernelmanager-helper-job.json),
[edits/mbsystemadministration-job.json](edits/mbsystemadministration-job.json) and
[edits/launchd-job-cache.json](edits/launchd-job-cache.json); `tests/test_edits.py` proves the engine writes the
script's bytes and that the hook installs every file the script writes. No input row. To add a job: append it to
`JOBS`, add its plist to the hook's list, `patch.json` `files` and a spec, and its edits to the cache spec.

- kernelmanager_helper keeps `EnablePressuredExit`: launchd may stop it under memory pressure when it holds no
  transaction, and relaunches it on the next message, as today.
- mbsystemadministration under `KeepAlive`: if it exits while Setup is not done (its last client went away), launchd
  starts it again (after its 10 s throttle) instead of on the next request; with no client it then stays up, so this
  does not loop. Once `.AppleSetupDone` exists an exit is final until the next request.
- Cost: both starts move to launchd start, into the busiest part of the boot, on 4 logical CPUs. mac27 measured
  kernelmanagerd's Aux activation 0.8 s later on Tahoe for the helper. mbsystemadministration's start (4.2 s under
  hao at 66.90) then overlaps the rest of the boot instead of the idle screen before Setup; on a set-up system it
  costs nothing.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/golden-gate-launchd-warm/tests`; with `LAUNCHD_WARM_SYSTEM`
  set to a read-only 26B131 System volume, the real-input test checks the recorded outputs (kernelmanager_helper job
  9c54029c..., mbsystemadministration job 1e78a418..., cache after golden-gate-userspace e4433c0a...).
- The build log prints `golden-gate-launchd-warm: HASH PATH` for the three files.
- On the laptop: `plutil -p` of each job plist shows its keys. In the boot log launchd spawns kernelmanager_helper
  near kernelmanagerd's start (not "because ipc" at the first load report); the helper is listening before
  kernelmanagerd's "Received kext load notification: as.vit9696.Lilu", and the 9 "Did not find identifier" answers
  follow their notifications within milliseconds.
- Before Setup (first boot, generalized image): launchd spawns mbsystemadministration at its bootstrap, not "because
  ipc", "Starting MBSA listener" precedes loginwindow's first connection, and the Language Chooser follows
  loginwindow's request without the 4 s gap; `launchctl print system/com.apple.mbsystemadministration` lists the
  path-state condition.
- After Setup (`/private/var/db/.AppleSetupDone` present): no mbsystemadministration process after boot
  (`pgrep -fl mbsystemadministration` empty) until something asks for it.

## Validation

NEEDS VALIDATION: not built or booted. Host tests pass with the real 26B131 files (read-only System volume of the
26B131 IPSW).

## Revisions
- r2 2026-11-12 Renamed from golden-gate-kernelmanager-helper-warm and made a job list; adds com.apple.mbsystemadministration (KeepAlive PathState while .AppleSetupDone is missing, Interactive), which loginwindow waits for before Setup (4.4-4.7 s in a007b23).
- r1 2026-11-12 New: kernelmanager_helper RunAtLoad, Interactive (job plist and launchd job cache), so the helper is ready for the OpenCore kexts' load reports once hao-opencore r44 skips the boot-time Aux KC build that used to start it.
