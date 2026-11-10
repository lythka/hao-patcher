# dev-access

Root SSH into a bench image on the test laptop, so a test boot can be checked and the laptop sent back to NetBoot
with no one at the keyboard. Dev set only (`pipeline/sets/dev.txt`): it puts the test host's private key into the
image, which AGENTS.md allows only for dev images.

## What it installs (Data volume)
| Path | From |
|---|---|
| `private/var/root/.testssh/` (700) | |
| `.../ssh_host_ed25519_key` (600) | inputs row `test_host_key`: the test host key, `SHA256:a/MmAw/0nhj9lw+qzgSpisYAvFoPkXyINJyNASHtDi4` (the key the host's known_hosts already has for the laptop) |
| `.../authorized_keys` (600) | inputs row `test_authorized_key`: the host's `~/.ssh/mbp92_test.pub` |
| `.../sshd_config` | `src/sshd_config`: root by key only, no passwords, `UsePAM no` |
| `Library/LaunchDaemons/local.testssh.plist` | `src/local.testssh.plist`: launchd runs `sshd -i` per connection on port 22 |

Apple's `ssh.plist` and the Remote Login setting are not touched.

## Origin
The root-ssh-key part of `~/ssd-tools-archive/ssd-tools/patchset/data/data.dev-access` (staged by
`handoff/testssh/stage-testssh.sh`, 161bb103). The archive's testbench recorder, TCC rows and early capture are
the `testbench` patch; its IconServices logging profile is `dev-telemetry`.

## Verify
After a test boot: `ssh -i ~/.ssh/mbp92_test root@<laptop> sw_vers` answers with the image's build.


## Validation
In use (FACT, from the build manifests in store/runs): r3 is in 12 image builds, first mbp92-25G332-dev-cache-layout-20261101-020101, latest mbp92-25G332-dev-next-20261102-115128,
including the benched dev images (store/runs/bench-mbp92-25G332-dev-*) and the owner's beta. No open item.

## Revisions
Newest first: revision, date, change (commit). `revision` in patch.json is the newest; bump it
and add a line here with every change to this patch or its input rows.
- r3 2026-10-28 Lock rows one file each; working files in store/tmp; store-gc; STATUS edited only by the merging session (5a7d438)
- r2 2026-10-28 PLAN 3c dev patches: dev-telemetry, testbench; boot-kc-trim experiment (dev-kctrim set) (259a8f5)
- r1 2026-10-28 Patch mechanism (PLAN step 2) and the dev-access patch (badb51b)
