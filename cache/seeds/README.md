# cache/seeds: shader, icon and font seeds from a laptop harvest

A booted image compiles Metal shaders, renders icons and builds fontd's registry for every account it runs, and on
the MacBookPro9,2 that is minutes of CPU at first boot and first login (bf10de4, 26B131: MTLCompilerService 4:21,
iconservicesagent 1:24 at Setup + 9 min, load 476). These two tools take what one running image made and turn it into
the inputs the seed patches install, so the next image starts with them:

| Artifact (row in `pipeline/inputs/golden/<build>/`) | Installed by |
|---|---|
| `shader-caches-<build>-*` (`shader_caches_golden_gate`) | `patches/golden-gate-shader-caches` |
| `icon-caches-<build>-*` (`icon_caches_golden_gate`) | `patches/golden-gate-icon-caches` |
| `font-registry-<build>-*` (`font_registry_golden_gate`) | `patches/golden-gate-font-registry` |
| `user-template-<build>-*` (`user_template_golden_gate`), from `collect-template.sh` + `template` | `patches/golden-gate-user-template` |

The per-user parts go to `Library/Application Support/PerUserSeed/`, which `patches/golden-gate-user-seed`'s
seed.zsh copies into each new account.

```sh
# 1. On the laptop (read-only, root SSH; best when idle, after Setup and a desktop session):
ssh root@LAPTOP 'sh -s' < cache/seeds/collect.sh > store/artifacts/harvest-<build>-<image>-<stamp>.tar
# 2. On the host: the three artifacts next to the harvest, and their rows
python3.14 -I cache/seeds/make store/artifacts/harvest-<build>-<image>-<stamp>.tar [--uid 501]
# 3. Closed loop, after a boot of an image with the pack: what that boot still compiled
python3.14 -I cache/seeds/make compare store/artifacts/shader-caches-<build>-*/data.tar NEW_HARVEST.tar
```

## What a harvest holds (`collect.sh`)
Paths relative to `/` (Data-rooted): every account's `com.apple.metal` and `com.apple.metalfe` trees under
`private/var/folders/*/*/C/`, the shared icon store `Library/Caches/com.apple.iconservices.store`, every account's
`C/com.apple.iconservices` and `C/com.apple.FontRegistry` folders, and `diag/`: `cache-dirs.tsv` (uid and cache
folder of every account), `folders.txt` (mode, uid, gid, flags of every ancestor folder, so `make` can recreate them
with their owners), `metal-files.txt` (every Metal cache file with size and mtime), `icons.txt` (store unit count,
each index header and `__cache_info__`), `hao.txt` (`/private/var/db/hao`), `ps.txt`, `log-counts.txt`,
`xattrs.txt`, `system.txt`. `SEED_ROOT` points it at a fixture tree (tests), `SEED_NO_LOG=1` skips `log show`.

## What `make` writes
- shader-caches `data.tar`: the system accounts' (uid < 501) trees at their paths with their owners and modes, each
  ancestor folder from `folders.txt` before them (an image has no `C/<bundle>` folder for a system account until
  that process runs, and tar would make a missing one root-owned); the user's `com.apple.metal` trees under
  `PerUserSeed/metal/<bundle path>/` (root, 755/644). The user's `com.apple.metalfe` trees are left out (counted as
  `user_metalfe_skipped`): they are clang module caches whose `.pcm` records its own absolute cache folder, which a
  new account's folder never matches, so the Metal front end refuses them and run-time shader compiles fail
  (Preview's SceneKit drew magenta). A system tree's `.pcm` must record the folder it is installed at (system
  accounts' folders are the same on every machine), or `make` stops.
- icon-caches: `icon-caches.tar` (the shared store, root's and the other system accounts' index folders with their
  ancestors) and `user/` (the user's `store.index`, `__cache_info__`). Refuses an index that is not version 11 or
  whose heap is not a whole number of records, and any `__cache_info__` whose ProductBuildVersion is not the harvest's
  build (IconServices resets such an index at start).
- font-registry: `setup.tar` (Setup's (248) `com.apple.FontRegistry`, WAL folded in) and `user/fontregistry.user`
  (the user's, with its user-font-folder row set to fontd's "not seen" state under `/var/empty/Library/Fonts`, as
  the earlier Tahoe image project's font-registry recipe r2; seed.zsh writes the new account's home into it). Refuses a seed that still
  names the harvest user's home.
Each gets `build.json` (inputs, counts, index geometry, registry versions, source commit) and `SHA256SUMS`; the folder
name ends in the first 12 hex of SHA256SUMS' hash. An existing folder is never overwritten.

## User Template (`collect-template.sh`, `template`)
The account's own Library at its first login (containers, chronod's widget descriptor cache, remindd's stores) is not
a cache folder seed.zsh can fill: macOS copies `/Library/User Template/Non_localized` into the new home when the
account is created, so it goes there (`patches/golden-gate-user-template`, mac-image's `user-template` with the
archive BETA7 rule). Harvest it from an account whose first login has settled (Setup done, desktop up ~15 min);
apps opened in it add containers (metadata only), so harvest before using it where possible.

```sh
# 1. On the laptop (read-only except a temp folder; root SSH): SEED_USER is the account
ssh root@LAPTOP 'SEED_USER=benchtest sh -s' < cache/seeds/collect-template.sh \
    > store/artifacts/template-harvest-<build>-<image>-<stamp>.cpio
# 2. On the host: the artifact next to the harvest, and its row (user_template_golden_gate)
python3.14 -I cache/seeds/template store/artifacts/template-harvest-<build>-<image>-<stamp>.cpio
```
- `collect-template.sh` writes a ditto (cpio) archive, not a tar: the containers' xattrs (containermanager identity,
  data-container personality, up to ~2 MB each) must survive. It copies `Library/Containers`, the chronod and
  Reminders group containers and `com.apple.chronod.plist`, then drops every file but the containermanagerd metadata
  plists and widget placeholders (no app data leaves the laptop), and copies the databases with `sqlite3 .backup`.
  `diag/` holds sw_vers, the account (user, uid, home, real name, creation time), each container's birth time and
  xattr names, and the symlink counts.
- `template` applies the rule (patches/golden-gate-user-template README, "What ships"): no symlinks, metadata schema
  0, the harvest user's compiled sandbox profiles removed, the personality flag on every container, chrono.sql
  reduced to ExtensionMetadata and Descriptors without PeopleViewService rows, chronod.plist without its session
  keys, Reminders' `*.sqlite` only. It refuses a result that still contains the account's home path, `/<user>/` or
  its real name. Entries keep the harvest's mtimes. The cpio is not byte-reproducible (ditto writes inode numbers),
  so each run makes a new folder. `build.json` counts what the rule did.

## Facts this is built on
- Metal keys follow each function's bitcode hash, and Apple rebuilds every metallib per build: 26A428 -> 26B131
  (metalfe 32023.921 -> .922) kept 0 of 3,437 function hashes in the six libraries `cache/metallib` repairs. A pack
  serves only the build it was harvested or generated from.
- On bf10de4 the caches persist across boots: Setup's iconservicesagent tree was last written on boot 1 (07:55) and
  not touched on boot 2, while Setup Assistant, Control Center and the video decoder added records on boot 2.
- IconServices 27 (26B123 host, 26B131 laptop): store-info version 4 as in Tahoe; index version 11 with
  **172-byte records** (Tahoe: 152). Harvested indexes are shipped as they are; the earlier Tahoe image project's generator (universe,
  render, append) needs the 27 record layout before it can add records.
- An icon index record's digest of a bundle includes the bundle folder's mtime; a harvested index serves the next
  image only for bundles whose folder mtimes the build reproduces. The closed loop shows any that do not.

## Verify
`python3.14 -I -B -m unittest discover -s cache/seeds/tests`: collect.sh on a fixture tree; make on a synthetic
harvest (owners, ancestors, user-tree normalisation, index checks, the font placeholder, compare);
collect-template.sh on a fixture home and template on its harvest (the rule, the account-trace refusal).
