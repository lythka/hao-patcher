# first-boot-seeds

Measure what a first boot builds that later boots reuse, and make root's LaunchServices store and `auth.db` from a
builder boot of the image instead of a laptop harvest. Host tools: they read logs and a read-only Data volume and
write a new artifact folder; they never touch a disk or the running system.

| Tool | Does |
|---|---|
| `bootcost LOG [--json OUT]` | For every boot in a `.logarchive` (or a `log show --style ndjson` export): first-boot milestones and spans (lsd building root's store and its three phases, clients blocked on it, fskitd's wait, authd's import, the kept-alive Preboot update, the Setup RAM disk, the first screen), counts (plugins, apps, blocked clients, widget kills) and fontworker bookmarks per account. Times are seconds after the boot's mach time zero (2-3 s before the kernel's first line, which the bench's `seconds_after_kernel` uses). Two boots of one image side by side are the closed-loop measure of a seed or warm-up. |
| `rootls collect DATA_ROOT --build B --schema N` | From the Data volume of an image that booted once in the builder (no account yet): root's `com.apple.LaunchServices.dv/*.csstore` and `private/var/db/auth.db` (WAL folded in), into `root-launchservices-B-DATE-HASH12/` with `build.json` and `SHA256SUMS`; runs `check`; prints the row. Reads nothing else of that boot (its system keychain, Local KDC, logs and hardware state must not ship). |
| `rootls check ARTIFACT --build B [--schema N] [--deny S]... [--dump]` | Refuses stores without lsd's `bdsl` magic, of another or mixed schema, without the main store, or naming an account home, a `/Volumes/` mount outside `/System/Volumes`, the dev test harness or a `--deny` string; `auth.db` must pass `integrity_check` with config version 2 and rules. `--dump` has this Mac's `lsregister -dump` read the store and requires its header to name build B (lsregister dumps the running system's own database when it cannot read the file's schema). |

`patches/golden-gate-root-launchservices` installs the artifact.

## Making the artifact (builder VM and QEMU; not run yet)
1. Build the image as usual (`pipeline/golden-run`).
2. Boot it once: `tools/qemu-vm start --image IMAGE --esp ESP`; wait for lsd's "set seeding complete" (and
   "_LSSessionSave: saved database") in the guest log; stop before Setup makes an account. The schema N is lsd's
   "lsd starting with schema version N" (`bootcost` prints it).
3. Read-only attach of the run's Data volume (as `qemu-vm logs` does), then
   `rootls collect DATA --build 26B131 --schema 35651614 --source RUN --out store/artifacts`.
4. Point the `root_launchservices_golden_gate` row (`pipeline/inputs/golden/26B131/`) at the folder and run the
   posts of the same image again with the patch in the set. The store holds that image's file and volume IDs, so it
   is valid for that image only; any rebuild needs a new boot.

## Target warm-up (releases)
A release installs file by file on the target (new file IDs) and ships no Apple-derived data, so lsd builds root's
store at the first boot there. A first-boot warm-up daemon (runs only while root's store is missing) can only order
that boot, and each step needs a laptop boot to time (`bootcost` on that boot against the second boot):
1. At launchd start, ask lsd to seed (`lsregister -seed` as root) instead of waiting ~10 s for its first client.
2. Until lsd posts `com.apple.LaunchServices.DatabaseSeedingComplete`: start nothing of ours; optionally move
   first-boot jobs nothing waits on to background (`taskpolicy -b`: mobileassetd/assetsubscriptiond, analyticsd,
   triald, configureLocalKDC/certtool, softwareupdated), never lsd's own helpers (trustd) or its clients.
3. After it, with Setup Assistant on screen: the listed icons, then the listed shaders, at background QoS, paused
   while an account logs in.
4. Font registries are built by each account's fontd at that account's first login; nothing can run them earlier.

## Verify
```sh
python3.14 -I -B -m unittest discover -s tools/first-boot-seeds/tests
python3.14 -I tools/first-boot-seeds/bootcost BENCH_RUN/boot.logarchive
```
