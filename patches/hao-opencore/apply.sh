#!/bin/sh
# The ESP's OpenCore binaries are already the OpenCore 1.0.5 release golden-run chose (RELEASE by default, DEBUG when
# the assembly says "opencore": "debug"): pipeline/stages/build-efi.py replaces every *.efi of OCLP's generated EFI
# from that archive. This recipe adds only the image's kernel
# repairs, kexts and configuration.
set -eu
# The kit's Python for the Mac the assembly runs on (golden-image.py sets OCLP_PYTHON); the builder VM's share copy
# for the legacy runner.
PY=${OCLP_PYTHON:-/private/var/ssdshare/py/bin/python3.12}
# kmutil embeds its build directory and a fresh enclosing UUID. The recorded repairs bind unchanged
# only after verifying the complete referenced kernel/kext segment closure (repair_binding.py, the reference).
REPAIR_WORK=$(mktemp -d /private/var/tmp/kernel-repairs.XXXXXX)
trap 'rm -rf "$REPAIR_WORK"' EXIT
ROW_BOUND=
if "$PY" -I "$P/src/repair_binding.py" bind \
    --profile "$P/inputs/kernel_repair_binding/profile.json" \
    --artifact "$P/inputs/kernel_repair_efi" \
    --boot-kc "$SYS/System/Library/KernelCollections/BootKernelExtensions.kc" \
    --output "$REPAIR_WORK/artifact"; then
    ROW_BOUND=$REPAIR_WORK/artifact
    # Preserve the per-image binding record with the build's patch log, before cleanup.
    cp "$REPAIR_WORK/artifact/build.json" "${PATCH_OUTPUT_DIR:-$P}/kernel-repair-binding.json"
else
    echo "hao-opencore: the recorded repairs do not bind to this BootKC; kc-binder binds it on its own"
fi
# kc-binder (staged by make.sh) computes the repairs from this BootKC. It refuses unless they equal the recorded
# repairs bound above, or, when those refused, unless IntelCommpage's compiled addresses hold for this BootKC.
"$PY" -I "$P/src/kc_bind.py" \
    --binder "$P/build" \
    --boot-kc "$SYS/System/Library/KernelCollections/BootKernelExtensions.kc" \
    --row "$P/inputs/kernel_repair_efi" \
    --profile "$P/inputs/kernel_repair_binding/profile.json" \
    --abi-header "$P/inputs/intel_commpage_abi" \
    --row-bound "$ROW_BOUND" \
    --out "$REPAIR_WORK/bound" \
    --record "${PATCH_OUTPUT_DIR:-$P}/kc-binder.json"
ditto "$REPAIR_WORK/bound/EFI/OC/Kexts/IntelCommpage.kext" "$ESP/EFI/OC/Kexts/IntelCommpage.kext"
ditto "$P/inputs/hao_runtime/HaoExec.kext" "$ESP/EFI/OC/Kexts/HaoExec.kext"
# Bind HaoExec to the kernel collection this image boots (it stays inactive on any other kernel).
"$PY" -I "$P/inputs/hao_runtime/stamp_identity.py" \
    "$SYS/System/Library/KernelCollections/BootKernelExtensions.kc" "$ESP/EFI/OC/Kexts/HaoExec.kext"
"$PY" -I "$P/src/repairs.py" \
    "$ESP/EFI/OC/config.plist" "$REPAIR_WORK/bound" \
    "$SYS/System/Library/KernelCollections/BootKernelExtensions.kc"
"$PY" -I "$P/src/early_kexts.py" \
    "$SYS" "$ESP" "$P/src/early-kexts.txt"
# The image's dyld counts as an internal install by itself (golden-gate-dyld-internal: internalInstall() answers true),
# so it loads OCLP's on-disk Metal roots without the ic_csr_internal boot-arg, which made every process see an
# Apple-internal build (mobileassetd then asked Apple's internal asset server: no Spotlight, Siri or language assets).
# Without that dyld, dropping the boot-arg would leave 27's Metal in place of OCLP's: refuse.
"$PY" -I -c 'import sys; sys.exit(0 if bytes.fromhex("554889e5bf1000000031c00f1f0085c00f94c05dc3") in open(sys.argv[1], "rb").read() else 1)' "$SYS/usr/lib/dyld" || {
    echo "hao-opencore: $SYS/usr/lib/dyld lacks the internalInstall edit (golden-gate-dyld-internal missing from the set): OCLP's Metal roots would not load without ic_csr_internal" >&2; exit 1; }
"$PY" - "$ESP/EFI/OC/config.plist" <<'PYCONFIG'
import os,plistlib,sys
p=sys.argv[1]
with open(p,'rb') as f:d=plistlib.load(f)
d['Kernel']['Add']=[x for x in d['Kernel']['Add'] if x.get('BundlePath')!='HaoExec.kext']
d['Kernel']['Add'].append(dict(Arch='x86_64',BundlePath='HaoExec.kext',Comment='hao: route unprepared arm64-only executables, UUID-bound',Enabled=True,ExecutablePath='Contents/MacOS/HaoExec',PlistPath='Contents/Info.plist',MinKernel='27.0.0',MaxKernel='27.99.99'))
n=d['NVRAM']['Add']['7C436110-AB2A-4BBB-A880-FE41995C9F82']
# Firmware boot.efi logging overrides are independent of kernel boot-args.
# A one-shot override may already be consumed by the time macOS can inspect it.
# Clear only these diagnostic overrides at the next OpenCore boot.
remove=d['NVRAM'].setdefault('Delete',{}).setdefault('7C436110-AB2A-4BBB-A880-FE41995C9F82',[])
for key in ('bootercfg', 'bootercfg-once'):
    n.pop(key,None)
    if key not in remove:remove.append(key)
# debug=0x100 waits for a debugger after a panic; the machine then needs a person to power-cycle it.
# Without it a panic reboots into the NetBoot default and the panic text stays in NVRAM (aapl,panic-info).
# -v and startup_debug=1 (verbose text on screen, extra kernel startup logging) are off: the early-boot
# evidence is the unified log (and, in bench images, OpenCore's file log from dev-diagnostics).
args=[a for a in n.get('boot-args','').split() if a not in ('debug=0x100', '-v', 'startup_debug=1', 'ic_csr_internal=1')]
# amfi_get_out_of_my_way=1: hao gives each arm64-only program an ad-hoc signed x86_64 slice carrying the
# program's own (Apple-private) entitlements; AMFIPass alone kills such a PID 1 (experimental sets only).
# hao_crash: hao's crash lines (cheap, every build). The measurement switches (hao_nx_stats, hao_bridge_stats,
# hao_diag_time) are dev-diagnostics' (pack dev): bench images only.
for arg in ('-lilubetaall', '-pmap_pcid_disable', 'amfi_get_out_of_my_way=1',
            'hao_crash=1',
            # kernelmanagerd skips the boot-time Aux KC build. 27's kmutil cannot build an x86_64 collection, so the
            # build (of the codeless AppleMobileDevice) failed at every boot and held the System KC back ~18 s.
            'auxKCAtBoot=false'):
    if arg not in args:args.append(arg)
n['boot-args']=' '.join(args)
# No OpenCore log in an end-user image (Target 0: no logging, no file writes at boot, nothing on screen). Bench
# images get the file log on the ESP from dev-diagnostics (pack dev).
d['Misc']['Debug']['Target'] = 0
# A normal boot shows nothing but the Apple logo (owner 2026-11-13): no boot.efi/apfs.efi debug lines.
d['Misc']['Debug']['AppleDebug'] = False
with open(p,'wb') as f:plistlib.dump(d,f)
PYCONFIG
plutil -lint "$ESP/EFI/OC/config.plist"
