# golden-gate-user-seed

Gives every new account the seeded caches before its apps first need them: a launchd daemon runs mac-image's
`src/seed.zsh` (seed7, unchanged) as root at boot and whenever the local users folder changes, and for each account
created less than 600 s ago copies what `PerUserSeed` holds into that account's cache folders. On its own it places
nothing; the payloads come from the patches listed after it.

## What it installs (Data volume)
| Path | Content |
|---|---|
| `Library/LaunchDaemons/local.peruser-seed.plist` (root 644) | `/bin/zsh -c <seed.zsh>` (`src/plist.sh` escapes the script into the plist), WatchPaths the dslocal users folder, RunAtLoad, log `/private/var/log/peruser-seed.log` |
| `Library/Application Support/PerUserSeed/` (root 700) | the seed folder |
| `.../PerUserSeed/build` | the System's ProductBuildVersion: seed.zsh places nothing on another build |

## What seed.zsh places, from which patch
| Payload | Into the new account | From |
|---|---|---|
| `is/` | `DARWIN_USER_CACHE_DIR/com.apple.iconservices` (711/600) | golden-gate-icon-caches |
| `metal/<bundle>/` | `DARWIN_USER_CACHE_DIR/<bundle>/`, never over a tree the app already made | golden-gate-shader-caches |
| `fonts/fontregistry.user` | `DARWIN_USER_CACHE_DIR/com.apple.FontRegistry/`, with `<home>/Library/Fonts` written into it | golden-gate-font-registry |
The LaunchServices store (`ls/`), the wallpaper, the passd kick and the efilogin re-enable are seed.zsh steps that
are skipped when their payload or flag is absent; this patch ships none of them (the LaunchServices store names the
harvest user's home; the wallpaper was Tahoe's; the efilogin deferral is a separate decision).

## History
r1-r3 (2026-11-05 to 11-09, 26A428) shipped the same runtime plus the font registry and Tahoe's efilogin-helper
deferral; a54ec244 removed it with the reduced Golden Gate profile. r4 brings back the runtime only: fonts are their
own patch now, so each seed can be dropped alone, and the efilogin deferral (untested on 27) stays out.

## Verify
Host: `python3.14 -I -B -m unittest discover -s patches/golden-gate-user-seed/tests` (the four seed patches as a chain
on a fixture Data volume, the plist carries seed.zsh byte for byte, `zsh -n`). On the laptop after Setup made an
account: `/private/var/log/peruser-seed.log` shows the icon index, the Metal trees and the font registry placed for
the new uid before its desktop started.

## Validation
NEEDS VALIDATION: r4 is built only on the host fixture; no image with it has booted. On 26A428 images r2/r3's runtime
placed trees (owner sessions 2026-11-06), not re-checked on 26B131.

## Revisions
- r4 2026-11-11 Restored for 26B131 without payloads of its own: runtime and build gate only; fonts moved to golden-gate-font-registry, efilogin deferral dropped. Root cause: a54ec244 removed every seed, so each new account compiles and renders everything at first login (bf10de4: benchtest iconservicesagent 2:13 CPU) (this commit).
- r3 2026-11-09 Port Tahoe's scoped Setup Preboot deferral together with the seed7 re-enable marker.
- r2 2026-11-06 Ship 27.0 font registries (Setup account and per-user seed).
- r1 2026-11-05 From user-seed r7 without the wallpaper payload and the efilogin flag.
