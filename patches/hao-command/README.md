# hao-command

Installs the `hao` command on the Mac: `/usr/local/bin/hao`, which runs the release's `tools/hao` with a bundled
x86_64 CPython 3.12. macOS 27 has no working `python3`, so `hao` brings its own (owner, 2026-11-16: "Bundle it.").

## What it installs (Data volume)

| Path | What |
|---|---|
| `usr/local/bin/hao` | A two-line launcher: `exec /usr/local/lib/hao/python/bin/python3.12 -I /usr/local/lib/hao/hao/tools/hao/hao "$@"` |
| `usr/local/lib/hao/python/` | Input row `python_x86_64` (python-build-standalone install_only), unpacked |
| `usr/local/lib/hao/hao/` | `tools/hao` of this release without its tests, the pinned-asset downloader `tools/acquire/acquire`, with `tools/hao/vendor` (Rich, MIT) and the notices; `make.sh` stages it |

Everything is on the Data volume, root:wheel, not writable by others, so `hao update` can replace it without
touching the sealed System.

## Verify

On the installed Mac: `hao`, `hao status`, `hao --help`.

## Validation

NEEDS VALIDATION: r14 adds the pinned-asset downloader for `hao cache build`.

Laptop 2026-11-19, a MacBookPro9,2 with image 8305fdd (26B131, built from fb6542df with r12): `/usr/local/bin/hao verify` runs and reports against 38 pack manifests, and the listing of /private/var/log/hao collected from that boot shows `drwxr-xr-x root wheel`.

## Revisions
- r15 2026-11-22 Show target cache rebuild requirements and permit resume at its build checkpoint.
- r14 2026-11-21 Stage the downloader used by the installed cache rebuild command.
- r13 2026-11-19 Validated r12 on hardware (Validation). Nothing it installs changes.
- r12 2026-11-18 Makes hao's log folder /private/var/log/hao root:wheel 755 on Data. launchd created it 744 for the AOT job's log, so per-user hao logs were lost.
- r11 2026-11-18 SHA256SUMS staged with release.json (an unsigned release, v0.0.1, is checked against it)
- r10 2026-11-17 a candidate build's tools/hao/data/candidate-keys.pub is staged with hao (never tracked; release-cut --candidate writes it)
- r9 2026-11-17 Status product (was experiment): it ships in the release profiles. Declaration only; `validated` stays false until a boot proves the re-cut. Nothing it installs changes.
- r8 2026-11-17 the new launcher is made by mktemp; every folder from usr to bin must be root's and loses its ACLs; a link at its name (to a file or a folder) is removed, a folder refused; release.json.sig staged with release.json
- r7 2026-11-17 the launcher is written to a new file and renamed into place; symlinked folders refused; make.sh stages committed bytes (git archive) into a fresh folder
- r6 2026-11-17 Declares its scope (`requires`, docs/requires.md): bundled CPython and tools/hao: no build or hardware dependence. Nothing it installs changes.
- r5 2026-11-17 Declares its pack (base): recipe layout format pass; its pack decides the machines (applies.models dropped); nothing it installs changes.

- r4 2026-11-16 no du in apply.sh (the Recovery has none); in the dev, release and metal27 sets too
- r3 2026-11-16 make.sh stages from a release archive too (no .git): files on disk, VERSION from pipeline/SOURCE_COMMIT
- r2 2026-11-16 make.sh writes tools/hao/VERSION (the staged commit) for hao's version on a dev image
- r1 2026-11-16 first recipe: CPython row and tools/hao under /usr/local/lib/hao, launcher at /usr/local/bin/hao
