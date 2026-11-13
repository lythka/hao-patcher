# kernel-foundation

Kernel-side build inputs for the Golden Gate image: it packages the unchanged Darwin 27 Intel
kernel, assembles the OpenCore EFI tree that loads IntelCommpage and the kernel repairs, and holds
the IntelCommpage source and generators that [`tools/kernel-fixes`](../kernel-fixes/README.md)
builds from.

## Layout

- `make`: the command line (below).
- `src/`: IntelCommpage kext source (adapted from XNU, APSL 2.0; [`licenses/`](licenses)).
  `src/commpage_table.h` is its collection address table (below).
- `commpage_build.py`, `sandbox_layout.py`, `iosurface_layout.py`, `sse_codegen.py`: IntelCommpage
  build and its layout generators.
- `early_vm.py`, `pv_bootstrap.py`: the early-VM OpenCore kernel patches.
- `vm_esp.py`: OpenCore EFI assembly.
- `tests/`: host tests and harnesses.

## Usage

```sh
python3.14 -I tools/kernel-foundation/make kernel \
  --kernel /path/to/kernel --out /path/to/new-kernel-artifact

python3.14 -I tools/kernel-foundation/make vm-esp \
  --reference /path/to/reference-efi --donor-esp /path/to/esp.tar \
  --darwin-major 27 --commpage-kext /path/to/IntelCommpage.kext \
  --kernel-repair-artifact /path/to/repairs --boot-kc /path/to/BootKernelExtensions.kc \
  --kext /path/to/HaoExec.kext --out /path/to/new-efi
```

`kernel` checks that the input is a thin x86_64 Mach-O, copies it unchanged to `payload/kernel`
and records its UUID, version and hash; the result is the `darwin27_kernel` input.
`vm-esp` copies the reference EFI tree, adds IntelCommpage, any `--kext` bundles and the kernel
repairs matching `--boot-kc` to its OpenCore config, and writes `build.json`; its results are the
`kernel_repair_efi` and `qemu_esp_hao` inputs ([docs/inputs.md](../../docs/inputs.md#input-rows)).

`commpage-kext`, `sandbox-layout`, `iosurface-layout`, `build-repairs` and `install-kernel` are
passed through to `tools/kernel-fixes/kernel-fixes`. Every command takes `--help`.

## IntelCommpage's address table

Since r42 IntelCommpage reads every kernel collection address it uses from one table in its own
binary, the `__DATA,__ic_table` section, instead of having them compiled into its code. The table
holds the 28 kernel functions and globals, the commpage alias and map slots, the eight hook sites
with their first 14 bytes, the IOSurface member's header and entry, and the kernel header.
`commpage-kext` writes the reference collection's values into it (`build/commpage-table.bin`,
`build/commpage-table-layout.json`). [`tools/kc-binder`](../kc-binder/README.md) writes another
collection's values into the same bytes, so a collection kmutil laid out differently for the same
kernel needs no rebuilt kext.

Before reading any entry the kext checks the table's magic (`ICTB`), version (1), size, slot counts,
layout (the first 16 bytes of the SHA-256 of the slot names, in order) and kernel UUID. It then
checks the kernel header's UUID at the table's address, the 14 bytes at every hook site, and the
full prologue each trampoline copies. If any check fails it logs the reason and does not start.

`commpage_abi.hpp` keeps every r41 line with its value. The table entries' lines now sit in
`namespace ic_reference`, so the code cannot use them. `IC_TABLE_REFERENCE` is the table's initial
contents. The other lines derived from the collection (`CODE_LINES` in kc-binder: the user_trap and
IOSurface prologues, the boot-args offsets, the UUIDs) describe the kernel's code, not its
placement, and stay compiled in. `commpage_build.py` holds the table layout for Python
(`table_bytes`, `read_table`, `find_table`, `stamp_table`); `src/commpage_table.h` holds it for C.
`tests/test_commpage_table.py` compiles the C side and compares the bytes.

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/kernel-foundation/tests
```

The tests check kernel packaging, command dispatch, EFI contents and repair binding on small
fixtures, and compile and run host harnesses for the Sandbox, IOSurface and SSE adapters. They
also compile the address table (C) and compare it with the Python layout. With
`KERNEL_FOUNDATION_COMMPAGE_ARTIFACT` set to a built commpage artifact, they also check that the
kext's table is `build/commpage-table.bin` and that no table address appears anywhere else in the
kext. They do not load a kext or boot a VM.

`tests/bootstrap_pv_vm.py`, `tests/startup_trace_vm.py` and `tests/ssealign_oracle.py` are not
part of unit discovery: they drive a paused QEMU guest over GDB, or build a test binary to run
inside a guest, and need a VM you supply.

## Revisions
- r42 2026-11-12 IntelCommpage reads its collection addresses from `__DATA,__ic_table` at load (checked: magic, version, size, layout, kernel UUID), so tools/kc-binder can bind it to a moved layout without a rebuild. The full trampoline prologues are checked at load. The reference table equals r41's compiled values on every recorded 26B131 collection. The kext's bytes change, so it needs a boot.
- r41 2026-11-11 Remove unsupported filter nodes only when they are isolated roots of operations absent from the donor, with no retained references or auxiliary root tables. Fixes the 26B131 apsd profile refusing conversion (filter 110) and blocking Setup Assistant. Host decision-equivalence tests pass; the laptop accepts the converted profile in a separate probe. A full boot with the new kext remains open.
- r40 2026-11-11 Bind the Sandbox operation layout to the supplied userspace cache UUID. Use vendored commpage source without requiring an unused older kernel binary. Kernel collection and hardware validation remain separate from host tests.
