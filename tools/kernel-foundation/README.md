# kernel-foundation

Kernel-side build inputs for the Golden Gate image: it packages the unchanged Darwin 27 Intel
kernel, assembles the OpenCore EFI tree that loads IntelCommpage and the kernel repairs, and holds
the IntelCommpage source and generators that [`tools/kernel-fixes`](../kernel-fixes/README.md)
builds from.

## Layout

- `make`: the command line (below).
- `src/`: IntelCommpage kext source (adapted from XNU, APSL 2.0; [`licenses/`](licenses)).
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

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/kernel-foundation/tests
```

The tests check kernel packaging, command dispatch, EFI contents and repair binding on small
fixtures, and compile and run host harnesses for the Sandbox, IOSurface and SSE adapters. They
do not load a kext or boot a VM.

`tests/bootstrap_pv_vm.py`, `tests/startup_trace_vm.py` and `tests/ssealign_oracle.py` are not
part of unit discovery: they drive a paused QEMU guest over GDB, or build a test binary to run
inside a guest, and need a VM you supply.
