# darwin27-sandbox-disk-space

Lets sandboxed programs on macOS 27.0 (26A428) read exact free disk space without a Sandbox violation report on
every query. Only 26A428 needs it: 27.2 (26B123, and 26B131) ships the rule, so the `golden-gate` set (26A428) keeps
this entry and the 26B131 sets (`dev-bs-test`) leave it out. Without it, tens of thousands of `deny(1) system-info vfs.disk-space` reports accumulate per hour and
`sandboxd` spends significant time handling them.

## Root cause
- The 26A428 x86_64 kernel calls `mac_system_check_info(cred, "vfs.disk-space")` on every `statfs`, `fstatfs`,
  volume-space `getattrlist` and quota call. A denial coarsens the reported free space; the call still succeeds.
  Tahoe's kernel has no such check.
- 26A428's `/System/Library/Sandbox/Profiles/system.sb` (like Tahoe's) has no rule for it, so every
  `(deny default)` profile that imports `system.sb` (tccd, WindowServer, mds, logd, `application.sb` and many
  more) denies and reports each call.
- macOS 27.2 (26B123) fixes this in userspace: its `system.sb` is 26A428's plus one block after the libmalloc block,
  `(allow system-info (info-type "vfs.disk-space"))`.
- Tahoe's Sandbox kext, which serves 27.0 userspace here, evaluates this rule the same way 27.0's does, so the
  userspace fix applies unchanged.

## How it works
`src/disk_space_rule.py` inserts 27.2's block after the libmalloc block of `system.sb` (the libmalloc block must occur
once; a file that already allows the info type is left as it is). `src/kc-hook.sh` installs the result as
root:wheel 0644 with the `restricted` flag, like Apple's file, and does not touch a `system.sb` that already has the
rule. Applied to 26A428's file, the output is byte-identical to 26B123's. It runs in set order after
[golden-gate-userspace](../golden-gate-userspace/README.md); nothing earlier writes `system.sb`. No input row.

## Verify
- `python3.14 -I -m unittest discover -s patches/darwin27-sandbox-disk-space/tests`
- The hook log prints the new `system.sb` SHA-256 (0b038968... for Golden Gate, equal to 26B123's).
- On the image, `grep -c vfs.disk-space /System/Library/Sandbox/Profiles/system.sb` prints 1, and the kernel log has
  no `deny(1) system-info vfs.disk-space` from processes whose profile imports `system.sb`.

## Validation
NEEDS VALIDATION: the recipe reproduces 26B123's file and a statfs test under Tahoe's adapted Sandbox kext returns
exact counts with the rule; the report count on a booted image with this patch has not been measured. Profiles that
do not import `system.sb` keep reporting.

## Revisions
- r3 2026-11-12 Declare the system.sb rule in `edits/system-sb.json` (edit-engine text.insert, unless already present); the hook is unchanged.
- r2 2026-11-11 Leaves a `system.sb` that already has the rule untouched instead of reinstalling it; removed from the 26B131 set `dev-bs-test` (26B131's file has the rule; the build log said "rule already present"). Kept in `golden-gate` (26A428). README: 26A428 only. Build validation pending.
- r1 2026-11-10 Initial public release.
