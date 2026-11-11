# golden-gate-corewifi

Gives the image Tahoe 25G342's x86_64 CoreWiFi in place of the cached 27.x CoreWiFi, for every process. The only
Intel airportd the image has is Tahoe's, and airportd is just the host of CoreWiFi's server: CWFXPCManager,
CWFXPCRequestProxy and CWFAutoJoinManager all live in CoreWiFi. With 27.x's CoreWiFi in airportd, three callout
reply blocks gained an NSInteger argument, so airportd crashed with `objc_retain(0x2)`. 213 of 302 XPC request types
were also renumbered, so 30 of the 48 types airportd handles reach the wrong handler or none: auto-join, passwords,
user settings and known-BSS edits among them. With Tahoe's CoreWiFi on both sides, airportd and its clients agree by
construction. A small compat library answers what 27.x clients need beyond Tahoe's CoreWiFi. airportd itself is not
edited.

The comparison behind this choice (option B) is in the 26B131 evidence: `corewifi/compare.md`.

## Inputs
- `corewifi_tahoe`: `CoreWiFiTahoe.dylib`, Tahoe 25G342's CoreWiFi cut from that build's
  `dyld_shared_cache_x86_64h` by [tools/cache-dylib](../../tools/cache-dylib/README.md). Its source cache is row
  `tahoe_os_cache_x86_64h`. The Intel installer's OS cryptex has no generic x86_64 cache. This image uses
  the generic code: no AVX2, BMI, FMA, MOVBE, LZCNT or TZCNT, which the Ivy Bridge CPU lacks.
- `corewifi_compat`: `CoreWiFi`, the compat library built from [build/corewifi_compat.m](build/corewifi_compat.m).
  It re-exports `CoreWiFiTahoe.dylib`.

Both rows come from one artifact made by [build/make](build/README.md), for 26B131 only: they live in
`pipeline/inputs/golden/26B131`. The 26A428 experiment assemblies that also select dev-bs-test find no row and stop,
because the override has not been coverage-checked against 26A428. Its `build.json` records the inputs, the
extraction, the verification and the coverage results.

## How it works
`src/kc-hook.sh` runs `src/install.py`, which writes two files into
`System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A`. The base layers already put that framework's
`Resources` there; the binary itself lives only in the cache. The two files are:
- `CoreWiFiTahoe.dylib`, install name `.../Versions/A/CoreWiFiTahoe.dylib`
- `CoreWiFi`, the compat library, install name `.../Versions/A/CoreWiFi`, re-exporting `CoreWiFiTahoe.dylib`

The install checks both install names and the re-export. It refuses to replace a different CoreWiFi binary, and it
edits no other file.

**Why dyld uses the files.** On macOS, dyld checks whether a file exists at a cached library's path when it loads
that library. If one does, dyld loads the file instead of the cached copy and repoints every cached client's bindings
to it through the cache's patch table. OCLP's IO80211 and WiFiPeerToPeer files already override their cached
libraries on this image this way (golden-gate-userspace r41/r43). On the 26B131 laptop (image bf10de4),
`lsof -p $(pgrep -x airportd)` shows the on-disk IO80211, IO80211OCLP.dylib, IO80211Old.dylib and the
WiFiPeerToPeer files mapped into airportd. Under Rosetta on a 27.2 host, the same files placed through
`DYLD_FRAMEWORK_PATH` load ahead of the host's cached CoreWiFi while 27.x's cached CoreWLAN is loaded too. No class is
implemented twice, and the CWF classes come from CoreWiFiTahoe.dylib (build/tests).

**The compat library.** Symbols two on-disk 27.x clients bind strongly, which Tahoe's CoreWiFi lacks:

| Symbol | Bound by | What the compat library provides |
|---|---|---|
| `CWFEventLinkQualityAssessmentKey` | SymptomEvaluator | 27.x's value. Tahoe's CoreWiFi never posts that event. |
| `CWFColocatedConsentResult` | iOSSupport SpeakerGroupSetupKit | 27.x's NSObject class. Tahoe's CoreWiFi never creates one. |

A table of 16 instance methods (`kMethods`, using 27.x's type encodings) covers what 27.x clients name on Tahoe's
classes. A constructor adds them with `class_addMethod`, which never replaces an existing method. All are no-ops:

| Class | Methods | Behaviour |
|---|---|---|
| CWFActivity | `processIdentifier` | returns 0 |
| CWFAutoJoinMetric | `duration`, `outcome`, `setOutcome:` | return 0; the setter ignores its value |
| CWFNetworkProfile | `carPlayCheckForInternetState`, `isConnectivityAssistEnabled`, `setConnectivityAssistEnabled:` | return 0 or NO; the setter ignores its value |
| CWFScanParameters | `setExcludeScanFlags:` | ignores its value |
| CWFScanResult, NSDictionary | `mrsnoConnection`, `rsnPriority` | return NO |
| CWFUIScanManager | `resetCache` | does nothing |
| CWFInterface (so also CWFWiFiUserAgent) | `setBTCoexActiveProfileName:error:` | returns NO with ENOTSUP |
| CWFInterface (so also CWFWiFiUserAgent) | `performColocatedNetworkScanWithConsentStatus:` | calls the handler with `(nil, ENOTSUP)` on a global queue, as 27.x does when it cannot scan |
| CWFConfigurationProfileManager | `activateWithCompletion:` | runs Tahoe's `-activate`, then the completion block, which takes no arguments |

**Coverage on 26B131** (`build/make`, recorded in `build.json`; [build/README.md](build/README.md) explains the check):
- Symbols. The cache's patch table has 206 CoreWiFi symbols. 32 are used outside CoreWiFi; the other 174 only by
  27.x CoreWiFi itself, which is no longer loaded.
- Clients. 18 on-disk 27.x programs link CoreWiFi. The image's three replacement programs are checked as well:
  Tahoe's airportd and WiFiMigratorPlugin, and OCLP's wifip2pd (13.7.2).
- Resolution. 125 symbols in all: 123 resolve to CoreWiFiTahoe.dylib and 2 to the compat library.
- Methods. Every selector the 51 clients name on any 27.x CoreWiFi class is answered, counting the cache's 27
  importers as one pooled client. Six 27.x-only classes have no instances under Tahoe:
  - CWFNetworkPathMonitor
  - _CWFNetworkWarningFlagsEntry
  - CWFLinkQualityAssessment
  - CWFAutoJoinUserActionMetric
  - CWFDirectToCloudClientStats
  - CWFTrialFactorProvider
- Environment. All 904 symbols CoreWiFiTahoe.dylib binds (5877 bind sites), across 25 libraries, exist in 26B131. That includes 19 IO80211
  symbols, which resolve through the image's IO80211 compat library and OCLP's wrapper and IO80211Old, and 2
  WiFiPeerToPeer symbols.
- Verification. Code uses 720 uniqued GOT slots, and all are bound. 3453 Swift relative pointers resolve.

## Risks
- **Every Wi-Fi client is affected.** The override covers about 130 CoreWiFi classes in every process that loads
  CoreWiFi. Before this, IO80211 and WiFiPeerToPeer were the only cached libraries overridden on this image, and
  both are small. A fault in the extracted library would show up in many processes at once. The checks above are
  static and Rosetta host runs; they say nothing about run-time behaviour on the image.
- **27.x-only features go dark.** These no longer work:
  - link-quality assessment
  - CarPlay internet checks and connectivity assist
  - Bluetooth coexistence profiles
  - colocated-network consent scans
  - direct-to-cloud statistics
  - NAN schedules
  - internet-sharing names
  - the 4 server handlers 27.x added (MDM, supervision, adaptive interface rank and EAP trust)
  - auto-join user-action metrics

  27.x clients get the no-ops above. A client that depends on such a result might log errors or retry; none was seen
  to require it statically.
- **Types and semantics drifted between the builds.** Selectors present in both builds can still differ. For example,
  `CWFScanParameters.scanFlags` is an `int` in Tahoe and a `uint64` in 27.x, and `CWFJoinStatus.endedAt` became
  read-only. Flags above bit 31 are lost.
- **Other images' categories.** 27.x categories that other cached images put on CWF classes, which the cache had
  preattached, rely on libobjc attaching them at run time to the overriding classes, as it must for any root.
  Tahoe images' categories (NPTKit) are dropped from the extracted class lists.
- **The extraction method.** tools/cache-dylib reverses the cache builder's optimizations itself. A bind that
  resolves through an addend is a heuristic. check.py found none unresolved.
- **Old dyld closures.** A process with a stale dyld closure could keep the cached copy until its closure is
  rebuilt.

## Verify
```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-corewifi/tests
python3.14 -I -B -m unittest discover -s patches/golden-gate-corewifi/build/tests
# with the artifact on a macOS 27 host: the real override under Rosetta, with 27.x's cached CoreWLAN loaded
COREWIFI_ARTIFACT=store/artifacts/corewifi-tahoe-... python3.14 -I -B -m unittest discover -s patches/golden-gate-corewifi/build/tests
python3.14 -I -B -m unittest discover -s tools/cache-dylib/tests
```
On the laptop (26B131, live, no reimage):
```sh
cd ~/hao-tools
A=/path/to/store/artifacts/corewifi-tahoe-25G342-26B131-...
F=/System/Library/PrivateFrameworks/CoreWiFi.framework/Versions/A
tools/laptop-swap put $A/CoreWiFiTahoe.dylib $F/CoreWiFiTahoe.dylib 755   # first: the compat library re-exports it
tools/laptop-swap put $A/CoreWiFi $F/CoreWiFi 755
tools/laptop-swap restart airportd; tools/laptop-swap restart WirelessRadioManagerd; tools/laptop-swap restart nearbyd
tools/laptop-swap restart WiFiAgent; tools/laptop-swap restart sharingd; tools/laptop-swap restart symptomsd
tools/laptop-ssh -t 60 'lsof -p $(pgrep -x airportd) | grep CoreWiFi'          # both files mapped
tools/laptop-ssh -t 60 'ls -t /Library/Logs/DiagnosticReports | head'          # no new airportd .ips
tools/laptop-ssh -t 90 'networksetup -listallhardwareports; networksetup -setairportnetwork en1 Skynet "$PW"; networksetup -getairportnetwork en1'
```
Then make the swap part of the system (`tools/laptop-swap persist --reboot`, for the holder of the laptop lock) or
build an image. Check that Skynet joins on its own after the reboot, and that no airportd, WiFiAgent or
WirelessRadioManagerd `.ips` appears.

## Validation
NEEDS VALIDATION: host checks only:
- The coverage check, the environment check and check.py pass against 26B131.
- Under Rosetta on a 27.2 (26B123) host, the override loads alongside 27.x's cached CoreWLAN. All 16 methods and both
  symbols answer, and a Swift conformance scan of the image succeeds.

Not yet tested:
- an image built with this recipe
- airportd running with it on the laptop, without a `.ips`
- a join through networksetup and through Setup/WiFiAgent
- auto-join of a remembered network after a reboot

## Revisions
- r1 2026-11-11 First version: Tahoe 25G342's CoreWiFi (tools/cache-dylib) installed as an on-disk override of the
  cached 27.x CoreWiFi, plus the compat library (2 symbols, 16 no-op methods). Coverage-checked against 26B131 and
  host-tested under Rosetta; image and laptop validation pending.
