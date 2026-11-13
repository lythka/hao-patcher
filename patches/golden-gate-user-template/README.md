# golden-gate-user-template

Puts what macOS makes in a new user's Library during the first login into `/Library/User Template/Non_localized`,
which macOS copies into every new home when the account is created: every app and widget container (folders with
their containermanager xattrs and data-container personality, metadata plist, widget placeholders), chronod's widget
descriptor cache and plist, and remindd's local stores. They exist from the account's first second, so the first
login does not create, sandbox-profile, personalize and fill them while the desktop starts. This is mac-image's
user-template (r5) for Golden Gate, with the archive BETA7 rule (`data.user-seed` C1-C3) applied in full.

The per-user caches that cannot live in the template (they sit in the account's `DARWIN_USER_CACHE_DIR`, whose name
is per account) stay with `golden-gate-user-seed`'s seed.zsh: icon index, Metal trees, font registry. The two
mechanisms are mac-image's pair (`user-template` + `user-seed`).

## Why (the first-login storm, 26B131)
Image 4d1c48c (hao-patcher adfd15a0, golden-gate-user-seed r4 with icon, Metal and font seeds), owner's Setup,
account `benchtest` created 10:37:31, auto-login 10:37:46: load 1005 at 10:41, SSH unanswered ~3 min (the maintainers' laptop evidence
for image 4d1c48c). FACT, read on the laptop afterwards:
- seed.zsh did its part: account seen 2.3 s after creation, icon index, font registry and 17 Metal trees placed by
  10:37:37, before the login (`/private/var/log/peruser-seed.log`).
- The stock User Template has no `Containers`; the first login made 328 containers (147 by 10:39, 167 more by 10:49)
  and 73 group containers.
- secinitd (benchtest's, arm64e translated by hao) 1:58 CPU, the largest user-session process. 10:37:40-10:48:00:
  194 sandbox initializations and 158 `Failed to set LS data container personality info`. Each initialization queries
  the container (~0.25 s), compiles the app's sandbox profile, then tries to synthesize the container's personality
  (connects to iconservices, TCC, WindowServer, which a background secinitd may not reach) and fails to store it, so
  the next launch of that container does it again. Only 27 of 328 containers ended with the personality.
- chronod: `Descriptors loaded from cache (0)` at 10:39:37, then 454 `getAllDescriptors` and 236 watchdog lines:
  every widget and control extension launched, most two to four times (Notes, WindowManager, Wallpaper, Shortcuts,
  Screenshot controls 4 each), each launch another sandbox initialization. The cache it built holds 21 extensions /
  25 descriptors, `ingestedByVersion` 26B131.
This is the Tahoe first login before BETA7 (archive `handoff/patch-catalog/NOTES-C.md` C1: "secinitd synthesizes a
personality icon set (28 sizes) per container unless the flag is true. At first login that was 2,184 renders ahead of
the Dock"; NOTES-widgets-containers: chronod cache empty at every first login, 44-48 extension launches, 130-174
CPU-s for uid 501). BETA7's template fixed it on 25G241: secinitd icon requests 2,184 -> 0, Dock to desktop.ready
159 s -> 1.2 s (07f vs 07g), "Descriptors loaded from cache (32)" (08-all).

## What ships
`cache/seeds/template` makes the artifact from a harvest (`cache/seeds/README.md`, "User Template"); the rule:
| Item | Kept | Changed |
|---|---|---|
| `Containers/<id>/` | folders and their xattrs, `.com.apple.containermanagerd.metadata.plist`, `Data/SystemData/com.apple.chrono/placeholders/**` | no symlinks; metadata `MCMMetadataSchemaVersion` 0, `MCMMetadataInfo.LSDataContainerPersonalityInfo` true, `SandboxProfileData` and its validation info removed |
| `Group Containers/group.com.apple.chronod/` | metadata plist, `chronod/chrono.sql` | schema 0; only `ExtensionMetadata` and `Descriptors` rows, PeopleViewService rows removed; WAL folded in, replicator dropped |
| `Group Containers/group.com.apple.reminders/` | metadata plist, `Container_v1/Stores/*.sqlite` | schema 0 |
| `Preferences/com.apple.chronod.plist` | `migrationState` and the rest | `lastKnownTimes`, `NetworkEnabledAfterBootNotification.lastLaunchBootSessionUUID`, `extensionsPendingDescriptorRefetch` removed |
Why each change (mac-image user-template README, archive NOTES-C):
- **No symlinks.** Account creation ditto-copies the template, then `touch`es every path; a link whose target does
  not exist yet (`~/Library/Keychains`, Mail, Calendars) becomes an empty file and contactsd loops ("Not a
  directory"; Tahoe 07g hung Setup).
- **Schema 0.** containermanagerd re-runs its schema on first use and makes the links itself, owned by the user
  (Tahoe: "updated schema from (0) -> (44)"); without it the links are never made.
- **No sandbox profile.** 26B131's compiled `SandboxProfileData` embeds the harvest user's name and cache folder
  (FACT, 81 of 81 profiles in the 26A428 capture); its validation info names `_HOME`, `_USER`, `_UID`, so it never
  matched another account anyway. secinitd compiles each profile at the container's first launch, as without the
  template.
- **Personality flag.** BETA7 C1's final form: the flag on every container ("flag ADDED, since a fresh harvest lacks
  it" for its widgets3 containers). With it secinitd skips the personality; the personality xattr ships where the
  harvest has it (27 of 328 on 4d1c48c, 56 of 276 on 26A428).
- **chronod.** The descriptor rows hold no uid, GUID, home or time (BETA7, two users compared) except PeopleWidget,
  whose default intent is the user's own contact card ("Bench Test" in the 26A428 capture: `template` refuses any
  output that still names the harvest account). `HostConfigs` (per-user widget UUIDs) and the rest are not shipped.
  chronod checks each extension's version and `ingestedByVersion` and each descriptor's locale token; a mismatch is a
  refetch, as without the template. en_US only benefits fully.
- **Reminders.** remindd creates three Core Data stores with fixed account and list IDs at its first launch inside
  UserAccountUpdater (Tahoe: ~16 s of Create Account); with them it loads.

## Input
`user_template_golden_gate` (`pipeline/inputs/golden/<build>/`): `cache/seeds/template`'s artifact folder,
`user-template.cpio` (ditto archive, Data-rooted `Library/User Template/Non_localized/Library/...`), `build.json`
(build, counts, harvest hash), `SHA256SUMS`. apply.sh refuses an artifact for another build (identity xattr format,
chronod rows and placeholders are per build: 26A428's containers carry `schema-version`/`uuid` xattrs, 26B131's a
single `com.apple.containermanager.identity`), a Data volume whose template already has `Containers`, and any
container metadata that does not follow the rule. OPEN: no 26B131 artifact yet; the capture is
`cache/seeds/collect-template.sh` on the laptop (cache/seeds README). Until that row exists the patch is
`unfinished` and in no set. Then: the row `pipeline/inputs/golden/26B131/user_template_golden_gate` as
`cache/seeds/template` prints it, `status` `experiment`, the next revision, and the line
`golden-gate-user-template` after `golden-gate-icon-caches` in `pipeline/sets/dev-bs-test.txt`.

## Limits
- Every account made from the template starts with the harvest's container UUIDs and Reminders store UUIDs (Tahoe
  shipped the same; containermanagerd rewrote the identity to the new uid). Local, non-iCloud stores.
- Containers for apps the harvest account opened are shipped too (metadata only); each costs a copy and a touch at
  account creation.
- Personality is skipped, not made, for containers whose harvest lacks the xattr: anything that shows a container's
  icon (the "access data from other apps" prompt) falls back to its default.
- secinitd still compiles one sandbox profile per container at its first launch.

## Verify
Host: `python3.14 -I -B -m unittest discover -s cache/seeds/tests` (collect-template.sh on a fixture home, the rule,
refusals) and `python3.14 -I -B -m unittest discover -s patches/golden-gate-user-seed/tests` (this patch in the seed
chain). On the laptop after Setup made an account (read-only):
- `ls ~/Library/Containers | wc -l` right after Create Account is the template's count; the folders' birth times are
  the account's creation.
- `find ~/Library/Containers/com.apple.weather/Data -maxdepth 2 -type l | wc -l` > 0 after the first launch
  (containermanagerd relinked; schema 44 in the metadata plist again), no "Not a directory" from contactsd.
- secinitd: `log show --start <login> --end <login+10 min> --predicate 'process == "secinitd" AND eventMessage
  CONTAINS "personality"'` near 0 (4d1c48c: 158); secinitd CPU (4d1c48c: 1:58).
- chronod: `Descriptors loaded from cache (N)` with N > 0, few watchdog lines (4d1c48c: 0 and 236).
- load average peak and SSH reachability across the login (4d1c48c: 1005, ~3 min unanswered).

## Validation
NEEDS VALIDATION: r3 has its 26B131 input (the 2026-11-13 harvest of benchtest's first login on 3b2291a) and is in
the 26B131 sets; not built into an image or booted yet. Before that: the rule is tested on host fixtures and was run on the 26A428 capture
(276 containers, 9,318 symlinks and 81 sandbox profiles removed, 220 flags added, 2 PeopleWidget rows removed; 76 MB
cpio). Not built into an image or booted.

## Revisions
- r4 2026-11-13 Input rows record their content sha256 (folder rows sealed with `lock.py seal`); the same artifacts at the same paths, so what this recipe installs is unchanged.
- r3 2026-11-13 26B131 input row user_template_golden_gate (harvest of benchtest's first login on image 3b2291a: 336 containers, 31 chronod descriptors, 3 Reminders stores) and status experiment; in golden-gate-26B131, dev-bs-test and dev-metal27 after golden-gate-user-seed. apply.sh unchanged.
- r2 2026-11-11 Back for 26B131 with BETA7's full rule (personality flag on every container, no sandbox profile cache, chronod reduced to its descriptor cache without PeopleWidget), made on the host by cache/seeds/template from a collect-template.sh harvest and checked by apply.sh. Root cause of the 4d1c48c login storm: a54ec244 removed the template, so the first login made 328 containers and secinitd retried each personality (this commit).
- r1 2026-11-06 From user-template r5's rule without its container list, for a 27.0 first-login harvest (mac27 dea52005; removed by a54ec244).
