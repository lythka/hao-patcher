# Patches

A patch is one ordered change to the image or its Recovery. Each folder here holds one patch: its recipe, the
source it builds, and its tests. A set in `pipeline/sets/` lists the patches an image uses, in order;
[TRACKER.md](TRACKER.md) lists every patch with its revision, stage, sets and validation state.

```sh
python3.14 -I pipeline/patches.py plan pipeline/sets/golden-gate-26B131.txt pipeline/targets/mbp92.json 25G342 \
  --locks pipeline/inputs/golden/26B131:pipeline/inputs/25G342:pipeline/inputs/common
python3.14 -I pipeline/patches.py path golden-gate-corespotlight-compat
python3.14 -I pipeline/patches.py check
```

## Patch format

Each patch has a `patch.json`, an implementation and a `README.md`.

```json
{
  "revision": 3,
  "stage": "oclp-hook",
  "applies": {"models": ["MacBookPro9,2"], "builds": ["25G332"]},
  "inputs": ["darwin27_kernel"],
  "status": "experiment",
  "validated": false
}
```

| Key | Meaning |
|---|---|
| `revision` | Counts up with every change to the patch or its input rows. |
| `stage` | When it runs (see below). |
| `applies` | `"any"`, or the models and macOS builds it supports; a key left out matches every value. |
| `inputs` | Input row names, resolved from `pipeline/inputs/BUILD/`, then `pipeline/inputs/common/`. The patch sees each as `inputs/NAME`. |
| `status` | `product`, `experiment`, `dev`, `diagnostic`, `unfinished` or `deprecated`. It says what the patch is for, not whether it works. |
| `validated` | `true` only when a boot test has proven the current revision. |

| Stage | Implementation | Runs |
|---|---|---|
| `oclp-hook` | `src/hook.sh`, `src/kc-hook.sh`, or both | `hook.sh` inside OCLP's root patch; each `kc-hook.sh` after it, in set order, on the live System. |
| `post` | `apply.sh` | After userspace installation, on the finished volumes. |
| `recovery` | `apply.sh`, optional `make.sh` and `kexts.txt` | In the Recovery build ([pipeline/recovery](../pipeline/recovery)). |

The README says what the patch does and why, how it works and what it needs, and ends with these sections in this
order:

- `## Verify`: how to check the result on a built image.
- `## Validation`: what has been tested. When `validated` is false it starts with `NEEDS VALIDATION`.
- `## Revisions`: the last section, newest line first, `- rN DATE change`, where N equals `revision`.

`python3.14 -I pipeline/patches.py check` enforces these rules, and the pipeline tests fail when
[TRACKER.md](TRACKER.md) is stale. After changing a patch:

```sh
python3.14 -I pipeline/patches.py tracker
python3.14 -I pipeline/patches.py check
```

## Sets

`golden-gate` holds the compatibility patches of the default image; development agents, captured account state and
warm-up caches are not part of it. `recovery` holds the Recovery patches (`rec-*`). Patches that modify disks or the
operating system run only in the builder VM, never on the host.
