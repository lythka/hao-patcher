# golden-gate-root-launchservices

Root's LaunchServices store and authd's `auth.db`, as this image's own lsd and authd wrote them in one builder boot
of the image, so the first boot on the Mac does not build them. Post patch, MacBookPro9,2 / 26B131, optional (group
`boot-seeds`: skipped while the row is missing). In no set yet: it waits for the first artifact (below).

| From the artifact (`root_launchservices_golden_gate`) | Installed at (Data) |
|---|---|
| `auth.db` | `private/var/db/auth.db` (root:wheel 600) |
| `ls/com.apple.LaunchServices-35651614-v2.csstore`, `ls/SystemDataOnly-...` | root's `DARWIN_USER_DIR` `0/com.apple.LaunchServices.dv/` (folders 755, store folder 744, stores 644) |

apply.sh refuses an artifact for another build, one that fails its `SHA256SUMS`, and a Data volume that already has
an `auth.db`.

## Why
The 26B131 image 8f5ddce's true first boot (bench `bench-mbp92-26B131-tested-20261112-204015`, boot EA2861D2; times
after mach zero, `tools/first-boot-seeds/bootcost`): lsd finds no store at 38.2 s and builds it until 122.6 s (CoreTypes
and extension points 15.8 s, 486 system plugins 39.2 s, 218 apps 29.1 s). loginwindow, tccd, secinitd and
backgroundtaskmanagementd block on it until 94.4 s; fskitd waits for "seeding complete" until 122.6 s, and the Setup
RAM disk (mbsystemadministration, 106.6 s) is not attached until 132.1 s, after fskit's modules reach
diskarbitrationd. authd imports 204 rules 38.6-42.0 s. Setup Assistant appears at 155.7 s; the same image's second
boot shows its first screen at 81.4 s.

## Where the input comes from
Generated, not harvested: a builder boot of the built image (`tools/qemu-vm start`), stopped after lsd logs "set
seeding complete" and before any account exists, then `tools/first-boot-seeds/rootls collect` on the read-only Data
volume of that run (`tools/first-boot-seeds/README.md`). Nothing else of that boot is taken (its system keychain,
Local KDC, logs and hardware state stay with it). The store holds the image's file and volume IDs, so it fits only
the image that booted: the row must be made from the same image this patch is applied to (a second pass over the
posts of that image), and a rebuilt image needs a new boot. A release that installs file by file on the target
gets new file IDs, and ships no Apple-derived data, so it cannot use this: there lsd builds the store at the first
boot, and only the order of that boot can be helped (`tools/first-boot-seeds/README.md`, Target warm-up).

## Declared files
`patch.json` `files` lists the three files apply.sh installs, whole files from the row. The store names carry 26B131's
schema (35651614); another build's lsd writes another name. `tests/test_apply.py` makes an artifact with `rootls
collect` from a fixture Data volume and runs apply.sh on a fixture.

## Verify
Host: `python3.14 -I -B -m unittest discover -s patches/golden-gate-root-launchservices/tests` and
`python3.14 -I -B -m unittest discover -s tools/first-boot-seeds/tests`. The artifact:
`python3.14 -I tools/first-boot-seeds/rootls check ARTIFACT --build 26B131 --schema 35651614`.
On the laptop, first boot of an image with it (`bootcost` on the boot log): lsd logs no `_CSStoreCreateWithURL failed`
and no "Seeding database with UID: 0", no "Database was seeding", authd logs "Database check OK" and no import,
fskitd does not wait, and Setup Assistant appears near the second boot's 81 s.

## Validation
NEEDS VALIDATION: no artifact exists yet (it needs the builder boot) and no image with it has booted. Open: whether
lsd keeps a store that a QEMU boot of the same image wrote when the laptop numbers its disks differently (lsd
re-reads each volume's ID at start: "Updating state of volume ... to mounted with volume ID ...").

## Revisions
- r1 2026-11-12 New: install root's LaunchServices store and auth.db from a builder boot of the same image (row root_launchservices_golden_gate, tools/first-boot-seeds/rootls).
