# kc-binder

Computes the Darwin 27 early-VM kernel repairs (OpenCore `Kernel.Patch` entries) and IntelCommpage's collection
address table for a given Boot kernel collection. It needs only Python's standard library: no compiler,
disassembler or Apple tool. That way an installer can bind against the collection it has just linked instead of
copying repairs captured from a bootstrap image (workflow plan step 10). The image build binds with it:
`patches/hao-opencore` stages it (`make.sh`, `kc-binder stage`) and its `src/kc_bind.py` runs it in the builder VM on
the BootKC the build just linked. There it must equal the recorded `kernel_repair_efi` that `src/repair_binding.py`
binds to the same collection; on a collection those refuse, it binds alone when IntelCommpage's compiled addresses
still hold (see [hao-opencore](../../patches/hao-opencore/README.md)).

## What it computes, and from what

| Input | Where | Gives |
|---|---|---|
| `kc-recipe.json` | `tools/kernel-fixes/profiles/BUILD/` (read only) | every repair point as (kernel segment, offset); the early-VM template (reservations, register roles, overwrite size, cave capacity) |
| binder recipe | `recipes/BUILD.json` | per code point: OpenCore-style mask, sha256 of the masked bytes, and the (segment, offset) each masked RIP-relative or rel32 field must reach; the IntelCommpage symbol, hook and IOSurface points |
| PV blob | `blobs/pv-alias.json` | `tools/kernel-foundation/src/bootstrap_pv_alias.S` assembled once, with each rel32 field zeroed and listed as a fixup (offset, symbol): `int32 = symbol - (origin + offset + 4)` |
| Boot KC | the collection being bound | the bytes, and where kmutil placed each fileset member's segments |

The binder recipe pins `kc-recipe.json` and the blob by sha256. It holds masks, hashes and positions only. It
holds no Apple bytes.

For a collection, `src/kcbind.py`:

1. reads the fileset entries and selects the recipe whose `kc-recipe.json` names the kernel member's UUID;
   it reads the Darwin version from the kernel member and requires the recipe's version;
2. resolves every point through the kernel member's own segment placement;
3. checks each code point (initializer site, kmem_init range call, PV registration call, the 32 bytes after the
   PV padding): the masked bytes must hash to the recipe, and every masked field must reach its recorded target in
   this collection; the 1024 PV padding bytes must be zero;
4. emits the four patches as `tools/kernel-foundation/early_vm.py` and `pv_bootstrap.py` do: the
   initializer code, the ascending-bounds register swap, the PV code (blob linked at the padding address) and the
   `call` to it;
5. requires each `Find` to occur exactly once in the whole collection, at its resolved point;
6. builds the IntelCommpage table: the 28 kernel symbols, alias and map slots, the eight hook sites with their
   first 14 bytes (masked check as in step 3), the IOSurface header, entry and UUID, the kernel header and UUID,
   and the `user_trap` and `csr_get_active_config` reads that `commpage_build.py` turns into the trampoline prefix
   and boot-args offsets.

`kc-binder compare` prints these results next to a recorded `kernel_repair_efi` artifact, the
`kernel_repair_binding` profile and the `commpage_abi.hpp` IntelCommpage was compiled with, one row per field.

## Usage

```sh
python3.14 -I tools/kc-binder/kc-binder bind --boot-kc KC --out NEW_DIR
python3.14 -I tools/kc-binder/kc-binder compare --boot-kc KC --repair-efi EFI_ARTIFACT \
  [--binding BINDING_ARTIFACT] [--abi-header commpage_abi.hpp]
```

```sh
python3.14 -I tools/kc-binder/kc-binder stage NEW_DIR
```

`stage` copies what `bind` reads at run time (`kc-binder`, `src/kcbind.py`, `src/compare.py`, every recipe and the
files it pins, `tools/kernel-foundation/commpage_build.py`) into NEW_DIR at the same repository-relative paths, with
`stage.json` (each file's SHA-256; `src/stage.py` `verify` checks it). `src/stage.py` runs on Python 3.9, so the
host's `/usr/bin/python3` can stage; the binder itself runs on the builder VM's Python 3.12.

`bind` writes `EFI/OC/config.plist` (the same layout `kernel-fixes build-repairs` writes: the four patches and
the IntelCommpage `Kernel.Add` entry), `capture.json` (the early-VM capture, as `port_profile.finish_early_vm`
writes it), `commpage-table.json` and `bind.json` (inputs, resolved points, output hashes). Any failed check
stops with exit status 1 and a message that names the point. Nothing is written in that case.

Release-time producers (they need clang, llvm-objdump through `tools/kernel-fixes/port_profile.py`, and the
standalone kernel; the binder itself needs none of these):

```sh
python3.14 -I tools/kc-binder/kc-binder make-blob --kc-recipe tools/kernel-fixes/profiles/26B131/kc-recipe.json \
  --out tools/kc-binder/blobs/pv-alias.json
python3.14 -I tools/kc-binder/kc-binder make-recipe --kc-recipe tools/kernel-fixes/profiles/26B131/kc-recipe.json \
  --profile tools/kernel-fixes/profiles/26B131 --blob tools/kc-binder/blobs/pv-alias.json \
  --kernel KERNEL --boot-kc REFERENCE_KC --iosurface-layout REFERENCE_KC_IOSURFACE_LAYOUT \
  --out tools/kc-binder/recipes/26B131.json
```

`make-blob` checks that the template, linked at arbitrary values, equals a fresh assembly at those values.
`make-recipe` binds the reference KC with the new recipe before it returns.

## Not done yet

- `repair_binding.py` and the recorded `kernel_repair_efi` stay the reference the image build checks the binder
  against. Its pinned SHA256 and `tools/kernel-fixes` are unchanged.
- IntelCommpage still compiles the addresses into the kext (`commpage_abi.hpp`). The table computed here is what
  a load-time version would read. The kext change (read (member, segment, offset) at load and add the member's
  segment base) and the way the table reaches it (for example an OpenCore-injected property) are open, and need
  a boot test.
- HaoExec identity stamping is separate (`hao_runtime`'s `stamp_identity.py`).
- Only 26B131 has a binder recipe. A new kernel needs `kernel-fixes port-profile` / `complete-profile`
  first, then `make-recipe`.

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/kc-binder/tests
```

Synthetic tests always run. They build Boot KCs with movable segments and the code shapes the binder reads, bind
them with one recipe after kmutil-like moves, check every refusal (changed byte, misdirected field, non-zero
padding, duplicate Find, already-ascending bounds, other kernel or version, changed hook, broken pin), check the
IntelCommpage table and header lines, and run the command line. With `xcrun clang` they also compare all four
patches with today's producers (`early_vm.py` + `pv_bootstrap.py`) on the synthetic collections and reassemble the
PV blob. The checked-in recipe and blob are checked against their pins.

The recorded 26B131 artifacts run when these are set (the folders are the `kernel_repair_efi` and
`kernel_repair_binding` rows, `python3.14 -I pipeline/lock.py path pipeline/inputs/golden/26B131 ROW`):

| Variable | Value |
|---|---|
| `KC_BINDER_BOOT_KC` | one or more Boot KCs, `:`-separated (the `darwin27_boot_kc` row; a recorded run's `guest-output/guest/BootKernelExtensions.kc`) |
| `KC_BINDER_REPAIR_EFI` | the `kernel_repair_efi` folder: `config.plist` byte-identical, every patch field, the capture |
| `KC_BINDER_REPAIR_BINDING` | the `kernel_repair_binding` folder: patch sites and all `abi_bindings` |
| `KC_BINDER_COMMPAGE_ABI` | `build/commpage_abi.hpp` of the commpage artifact the binding profile pins |
| `KC_BINDER_KERNEL`, `KC_BINDER_IOSURFACE_LAYOUT` | `darwin27_kernel` and the reference KC's `iosurface-layout.json`: `make-recipe` reproduces `recipes/26B131.json` (needs llvm-objdump) |

Host tests prove the computation only. A boot of the bound collection (QEMU, then the MacBookPro9,2) is a
separate check, and the binder has not been through one.
