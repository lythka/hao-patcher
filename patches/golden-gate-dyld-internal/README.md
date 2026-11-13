# golden-gate-dyld-internal

Makes dyld, and only dyld, count as an Apple-internal install, so the `ic_csr_internal=1` boot-arg can go.

## Why
macOS 27's x86_64 dyld loads on-disk roots of shared-cache images (OCLP's Metal wrapper and MetalOld, which replace
27's Metal on the HD 4000) only when `dyld4::SyscallDelegate::internalInstall()` is true, i.e. when
`csr_check(CSR_ALLOW_APPLE_INTERNAL)` succeeds. Until hao-opencore r52 the `ic_csr_internal=1` boot-arg made
IntelCommpage report that bit to every process. os_variant then made the whole system an Apple-internal build:
mobileassetd asked Apple's internal asset server, so Spotlight's ranking models, Siri and language assets never
downloaded (Spotlight listed apps below web results).

## What it changes
`usr/lib/dyld` (hao-runtime's replace/dyld, installed by hao-runtime earlier in the set), x86_64 slice only:

| Where | Before | After |
|---|---|---|
| `dyld4::SyscallDelegate::internalInstall() const` + 9 | `call _csr_check` (E8 rel32) | `xor eax,eax; nop dword [rax]` (31 C0 0F 1F 00) |

`edits/dyld.json` declares it (literal, anchored at the symbol, the call's offset masked); `src/edit_dyld.py` does it in
the builder (one match in the x86_64 slice, or it refuses), then the hook re-signs ad hoc with hao-runtime's identifier
`com.apple.darwin.ignition` and installs it root:wheel 0755 `restricted`. hao-opencore r52 refuses an image whose dyld
lacks the edit, and `hao patch` treats an edited dyld as loading on-disk roots (`tools/hao/src/livepatch.py`).

## Verify
```sh
python3.14 -I -B -m unittest discover -s patches/golden-gate-dyld-internal/tests   # HAO_DYLD=PATH: a real dyld
```
On the laptop: `sysctl kern.bootargs` has no `ic_csr_internal`; OCLP's Metal still loads (`vmmap` of WindowServer shows
`MetalOld.dylib`); `log show --predicate 'process == "mobileassetd"'` names the public asset server; Spotlight finds
Terminal as its top hit.

## Validation
NEEDS VALIDATION: host tests (script and spec agree on hao-runtime's dyld); not built into an image or booted.

## Revisions
- r1 2026-11-13 internalInstall() answers true in dyld only (owner chose this over the boot-arg, 2026-11-13).
