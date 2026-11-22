# Candidates: hardware that is asked about, but is not a target

A **target** (`pipeline/targets/NAME.json`) is a machine this project can build for. It carries the machine's board
id, its facts (GPU family, firmware), the SHA256 of its ESP as OCLP installed it, and an OCLP hardware profile with
the PCI list, the CPU flags and the patch sets OCLP selects there. A **candidate** is a machine people have asked
about, or a near neighbour of the proven MacBookPro9,2, that is not a target yet.

The registry [`pipeline/candidates.json`](../pipeline/candidates.json) records only what can be known without the
machine:

- the model identifier, or `null` for a generation-level request;
- the GPU families from `patches.py`'s `GPUS`;
- the firmware (`none`, `t1`, `t2`);
- why it is here, and where the public facts come from.

It deliberately holds none of a target's measured fields (`board_id`, `cpu`, `pci`, `ioreg`, `esp`,
`oclp_hardware_profile`). A row that tries to carry one fails `pipeline/tests/test_candidates.py`. A candidate is a
question, not a claim about the machine.

## What the report says

```sh
python3.14 -I pipeline/candidates                 # every release profile, every candidate
python3.14 -I pipeline/candidates --profile alpha-v0.1.0
python3.14 -I pipeline/candidates --json
```

For each candidate and each release profile, the tool plans the profile's own set on the profile's build pair and
classifies every recipe:

| Bucket | Meaning |
|---|---|
| `run` | every requirement the tool can decide is met |
| `refused` | a known fact (OS, donor, GPU, firmware) does not meet `requires`; the reason is printed |
| `unknown` | only the CPU is undecided: no OCLP hardware profile exists for the candidate yet |
| `skipped` | `applies` excludes the candidate's model and build |

`refused` is the actionable list: those recipes would have to grow the candidate's GPU family, or be replaced,
before the machine could use this release. `unknown` is the list a target's OCLP profile settles: its CPU flags
turn each entry into `run` or `refused`.

The report does not invent a target. It reads the release sets as they are, so it shows the current surface, not
the surface after a port.

## The candidates

<!-- candidates-table:start -->
| Model | Machine | GPU | Firmware | Status | Issue |
|---|---|---|---|---|---|
| MacBookPro10,1 | MacBook Pro (Retina, 15-inch, Early 2013) | intel-ivb, nvidia-kepler | none | near-term | — |
| MacBookPro11,2 | MacBook Pro (Retina, 15-inch, Late 2013) | intel-hsw | none | near-term | — |
| MacBookPro16,1 | MacBook Pro (16-inch, 2019) | amd-navi | t2 | requested | [#80](https://github.com/lythka/hao-patcher/issues/80) |
| — | Haswell and Broadwell integrated graphics (generation request) | intel-hsw, intel-bdw | none | requested | [#2](https://github.com/lythka/hao-patcher/issues/2) |
| — | HP EliteBook 840 G4 (Kaby Lake, Intel HD 620) | intel-kbl | none | requested | [#5](https://github.com/lythka/hao-patcher/issues/5) |
| — | Skylake integrated graphics (generation request) | intel-skl | none | requested | [#7](https://github.com/lythka/hao-patcher/issues/7) |
| — | HP Pavilion dv6-7070ex (Ivy Bridge, Intel HD 4000) | intel-ivb | none | requested | [#4](https://github.com/lythka/hao-patcher/issues/4) |
| — | VMware SVGA 3D (virtual machine, software graphics) | none | none | software | [#93](https://github.com/lythka/hao-patcher/issues/93) |
<!-- candidates-table:end -->

`near-term` is the project's own sequencing: a machine close enough to the proven MacBookPro9,2 that its target is
the next plausible one. `requested` comes from a GitHub issue. `software` is the generic, software-graphics path
(`pipeline/profiles/generic.json`), not a platform GPU.

## From candidate to target

A candidate becomes a target only with the machine in hand. The four things a target needs, and where they come
from:

1. **The machine's IODeviceTree and CPU.** `ioreg -p IODeviceTree -a` (sanitised) gives the board id, the PCI
   devices, the firmware vendor and the CPU flags and leafs. They become the target's `oclp_hardware_profile`;
   [`pipeline/targets/mbp92.json`](../pipeline/targets/mbp92.json) is the worked example.
2. **The ESP as OCLP installed it.** A tarball under `store/` (never committed) and its SHA256 in `esp.sha256`.
   Without it the target has no boot layout of its own.
3. **The OCLP hardware profile.** `tools/oclp-hardware/profiles/NAME.json` with its `patchset_order`, so
   `patches.py packs TARGET` selects the platform packs instead of stopping.
4. **A boot on the machine.** `proven_on` entries for the recipes that ran, and `validated: true` once a boot proves
   the revision. Until then a target can exist, but its recipes stay unproven.

The generic target ([`pipeline/targets/generic.json`](../pipeline/targets/generic.json)) shows the shape without
1–3: it names no board id, no GPU and no ESP, and its OCLP profile has no patch sets. That is the software path,
not a model port.

## Adding a candidate

Add one object to `pipeline/candidates.json`:

```json
{
 "id": "macbookpro11-2",
 "name": "MacBook Pro (Retina, 15-inch, Late 2013)",
 "model": "MacBookPro11,2",
 "gpu": ["intel-hsw"],
 "firmware": "none",
 "status": "near-term",
 "issue": null,
 "why": "Haswell with Iris Pro 5200: the first machine that would exercise an intel-hsw recipe.",
 "source": "Apple's MacBook Pro (Retina, 15-inch, Late 2013) specifications: Intel Iris Pro Graphics."
}
```

Then regenerate the table above:

```sh
python3.14 -I pipeline/candidates --markdown
```

and replace the text between the two `candidates-table` markers. The test fails when the table is stale, when a GPU
family is not in `GPUS`, or when a row carries a target's measured field.

See [`requires.md`](requires.md) for what a recipe's `requires` means, [`layout.md`](layout.md) for the pack and
profile model, and [`building.md`](building.md) for how a profile and a target become an image.
