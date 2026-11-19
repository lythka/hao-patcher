# golden-gate-user-seed

Gives every new account the seeded caches before its apps first need them: a launchd daemon runs
`src/seed.zsh` (the earlier Tahoe image project's seed7) as root at boot and whenever the local users folder changes, and for each account
created less than 600 s ago copies what `PerUserSeed` holds into that account's cache folders. On its own it places
nothing; the payloads come from the patches listed after it.

## What it installs (Data volume)
| Path | Content |
|---|---|
| `Library/LaunchDaemons/local.peruser-seed.plist` (root 644) | `/bin/zsh -c <seed.zsh>` (`src/plist.sh` escapes the script into the plist), WatchPaths the dslocal users folder, RunAtLoad, log `/private/var/log/peruser-seed.log` |
| `Library/Application Support/PerUserSeed/` (root 755) | the seed folder (readable: the account's own copy reads it) |
| `.../PerUserSeed/build` | the System's ProductBuildVersion: seed.zsh places nothing on another build |

## Who writes
The daemon runs as root, but it never writes into an account's folders as root. Those folders belong to the account,
so a link the account plants there could otherwise redirect a root write, chown or rename. Every step that writes
there is a `*_as` function that `asuser` runs as the account (`sudo -u #uid`, uid 501 and up):
- the LaunchServices store;
- the icon index;
- the font registry, including its sqlite3 update;
- each Metal chunk;
- the aerial still.

The passd poke runs in the account's session as the account. Root still writes only its own folders: its records in
`/private/var/db/peruser-seed`, and the wallpaper under `/private/var/db/Wallpapers` (root:wheel). It also still reads
account folders and stops the account's `lsd`. `tests/test_as_account.py` runs the account path through a `sudo`
stand-in and refuses a root-side function that writes.

## What seed.zsh places, from which patch
| Payload | Into the new account | From |
|---|---|---|
| `is/` | `DARWIN_USER_CACHE_DIR/com.apple.iconservices` (711/600) | golden-gate-icon-caches |
| `metal/<bundle>/` | `DARWIN_USER_CACHE_DIR/<bundle>/`, never over a tree the app already made, without any `com.apple.metalfe` (path-bound module caches) | golden-gate-shader-caches |
| `fonts/fontregistry.user` | `DARWIN_USER_CACHE_DIR/com.apple.FontRegistry/`, with `<home>/Library/Fonts` written into it | golden-gate-font-registry |
| `wallpaper/{Metadata.plist,Wallpaper.png}` | root's `/private/var/db/Wallpapers/<GeneratedUID>/` (root:wheel, metadata first), never over a folder WallpaperAgent made | golden-gate-wallpaper-export |
| `ls/com.apple.LaunchServices-<n>-v2.csstore` (and its `SystemDataOnly-` twin) | `DARWIN_USER_DIR/com.apple.LaunchServices.dv/` | none yet: `cache/seeds/make launchservices` cuts it from a clean harvest |
The account's own Library (containers, chronod, Reminders) is not seeded here: macOS copies it from the User
Template at account creation (`golden-gate-user-template`), the other half of the same method.
The LaunchServices store (`ls/`), the wallpaper, the passd kick and the efilogin re-enable are seed.zsh steps that
are skipped when their payload or flag is absent; this patch ships none of them. The store's name is taken from the
payload: the one `ls/com.apple.LaunchServices-<n>-v2.csstore` (lsd's store number differs per OS: Tahoe 20971545,
26B131 35651614); with none or more than one, the step is skipped. No store ships yet: the one harvested from a test
account names the dev pack's launchd plists, so a clean one has to come from the first login of an image without the
dev pack (`cache/seeds/README.md`). The passd kick needs a reason on this OS first, and the efilogin deferral is a
separate decision.

## History
r1-r3 (2026-11-05 to 11-09, 26A428) shipped the same runtime plus the font registry and Tahoe's efilogin-helper
deferral; a54ec244 removed it with the reduced Golden Gate profile. r4 brings back the runtime only: fonts are their
own patch now, so each seed can be dropped alone, and the efilogin deferral (untested on 27) stays out.

## Declared files
`patch.json` `files` lists the two files apply.sh installs: the launchd plist (made by `src/plist.sh` from `src/seed.zsh`)
and `PerUserSeed/build`, both generated at install (origin `hao`, no row, no edit specs). Not declared: what seed.zsh
writes into each new account at run time (icon index, Metal trees, font registry), since those paths hold the account's
uid and cache-folder hash. The payload files it reads are declared by the three cache patches.
`tests/test_edits.py` runs apply.sh on a fixture and checks the installed files equal the declared ones.

## Verify
Declaration: `python3.14 -I -B -m unittest discover -s patches/golden-gate-user-seed/tests`.
Host: `python3.14 -I -B -m unittest discover -s patches/golden-gate-user-seed/tests` (the five seed patches as a chain
on a fixture Data volume, the plist carries seed.zsh byte for byte, `zsh -n`). On the laptop after Setup made an
account: `/private/var/log/peruser-seed.log` shows the icon index, the Metal trees and the font registry placed for
the new uid before its desktop started.

## Validation

Laptop 2026-11-19, a MacBookPro9,2 with image 8305fdd (26B131, built from fb6542df with r15): for the new account, /private/var/log/peruser-seed.log shows the icon index, the font registry and 16 Metal bundle trees placed and "seeded", and "no LS store in the payload" (no LaunchServices store ships yet).

## Revisions
- r16 2026-11-19 Validated r15 on hardware (Validation). Nothing it installs changes.
- r15 2026-11-18 seed.zsh takes the LaunchServices store's name from the payload instead of Tahoe's hard-coded `com.apple.LaunchServices-20971545-v2.csstore`, so a 26B131 store (`-35651614-`) is placed. No store ships yet, so a new account gets the same files as with r14. `tests/test_as_account.py` checks the name.
- r14 2026-11-17 Declares its scope (`requires`, docs/requires.md): the seed runtime and the payload layout of 26B131's caches. Nothing it installs changes.
- r13 2026-11-17 Status product (was experiment): it ships in the release profiles. Declaration only; `validated` stays false until a boot proves the re-cut. Nothing it installs changes.
- r12 2026-11-17 seed.zsh writes into an account's folders only as that account (`asuser`: `sudo -u #uid`), never as root; PerUserSeed is 755 so the account can read it. `tests/test_as_account.py` runs that path and refuses root-side writes.
- r11 2026-11-17 Comments, README and tests name no private project or account; nothing it installs changes.
- r10 2026-11-16 Declares its pack (base): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.
- r9 2026-11-13 Documentation only: wording. The seed and what it installs are unchanged.
- r8 2026-11-13 Tests and README only: the seed chain includes golden-gate-user-template; README names it. seed.zsh unchanged.
- r7 2026-11-12 Declare the installed files in patch.json `files` (generated at install); the hook is unchanged.
- r6 2026-11-12 seed.zsh never places `com.apple.metalfe`: an older seed's clang module cache (Preview's `.pcm`) records the harvest account's cache folder, so in a new account the Metal front end refused it ("PCH was compiled with module cache path ...") and SceneKit in Preview drew magenta. Test runs `place_chunk` itself.
- r5 2026-11-11 Optional (group caches) with needs_any caches: skipped when no cache patch runs, since the copier would have nothing to place. apply.sh unchanged.
- r4 2026-11-11 Restored for 26B131 without payloads of its own: runtime and build gate only; fonts moved to golden-gate-font-registry, efilogin deferral dropped. Root cause: a54ec244 removed every seed, so each new account compiles and renders everything at first login (bf10de4: the test account's iconservicesagent 2:13 CPU).
- r3 2026-11-09 Port Tahoe's scoped Setup Preboot deferral together with the seed7 re-enable marker.
- r2 2026-11-06 Ship 27.0 font registries (Setup account and per-user seed).
- r1 2026-11-05 From user-seed r7 without the wallpaper payload and the efilogin flag.
