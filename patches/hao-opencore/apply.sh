#!/bin/sh
# The ESP's OpenCore binaries are already the OpenCore 1.0.5 DEBUG release: pipeline/stages/build-efi.py replaces
# every *.efi of OCLP's generated EFI from the opencore_debug archive. This recipe adds only the image's kernel
# repairs, kexts and configuration.
set -eu
# kmutil embeds its build directory and a fresh enclosing UUID. Bind the unchanged
# repairs only after verifying the complete referenced kernel/kext segment closure.
REPAIR_WORK=$(mktemp -d /private/var/tmp/kernel-repairs.XXXXXX)
trap 'rm -rf "$REPAIR_WORK"' EXIT
/private/var/ssdshare/py/bin/python3.12 -I "$P/src/repair_binding.py" bind \
    --profile "$P/inputs/kernel_repair_binding/profile.json" \
    --artifact "$P/inputs/kernel_repair_efi" \
    --boot-kc "$SYS/System/Library/KernelCollections/BootKernelExtensions.kc" \
    --output "$REPAIR_WORK/artifact"
# Preserve the per-image binding record with the build's patch log, before cleanup.
cp "$REPAIR_WORK/artifact/build.json" "${PATCH_OUTPUT_DIR:-$P}/kernel-repair-binding.json"
ditto "$REPAIR_WORK/artifact/EFI/OC/Kexts/IntelCommpage.kext" "$ESP/EFI/OC/Kexts/IntelCommpage.kext"
ditto "$P/inputs/hao_runtime/HaoExec.kext" "$ESP/EFI/OC/Kexts/HaoExec.kext"
# Bind HaoExec to the kernel collection this image boots (it stays inactive on any other kernel).
/private/var/ssdshare/py/bin/python3.12 -I "$P/inputs/hao_runtime/stamp_identity.py" \
    "$SYS/System/Library/KernelCollections/BootKernelExtensions.kc" "$ESP/EFI/OC/Kexts/HaoExec.kext"
/private/var/ssdshare/py/bin/python3.12 -I "$P/src/repairs.py" \
    "$ESP/EFI/OC/config.plist" "$REPAIR_WORK/artifact" \
    "$SYS/System/Library/KernelCollections/BootKernelExtensions.kc"
/private/var/ssdshare/py/bin/python3.12 -I "$P/src/early_kexts.py" \
    "$SYS" "$ESP" "$P/src/early-kexts.txt"
/private/var/ssdshare/py/bin/python3.12 - "$ESP/EFI/OC/config.plist" <<'PYCONFIG'
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
# evidence is OpenCore's file log on the ESP (below) and the unified log.
args=[a for a in n.get('boot-args','').split() if a not in ('debug=0x100', '-v', 'startup_debug=1')]
# amfi_get_out_of_my_way=1: hao gives each arm64-only program an ad-hoc signed x86_64 slice carrying the
# program's own (Apple-private) entitlements; AMFIPass alone kills such a PID 1 (experimental sets only).
# hao_crash: hao's crash lines (cheap, every build). hao_nx_stats/hao_bridge_stats: NX fault tables and bridge
# statistics, the diagnostics the QEMU runs use; they cost time on every translated native call, so only dev builds
# (golden-run sets IMAGE_DEV_BUILD=1 when the set has a status-dev recipe) get them.
dev=os.environ.get('IMAGE_DEV_BUILD')=='1'
for arg in ('-lilubetaall', '-pmap_pcid_disable', 'amfi_get_out_of_my_way=1',
            'hao_crash=1') + (('hao_nx_stats=1', 'hao_bridge_stats=1') if dev else ()) + (
            # IntelCommpage reports CSR_ALLOW_APPLE_INTERNAL so 27.0's dyld honours OCLP's on-disk Metal roots.
            'ic_csr_internal=1',):
    if arg not in args:args.append(arg)
n['boot-args']=' '.join(args)
# Keep boot.efi/OpenCore evidence on the ESP for collection from Recovery.
# Target: 0x01 logging, 0x02 on screen, 0x40 file. The log goes to the ESP file only; the screen shows no OpenCore
# text (OCLP's base config sets 0x02 with its debug log).
d['Misc']['Debug']['Target'] = (d['Misc']['Debug']['Target'] | 0x41) & ~0x02
d['Misc']['Debug']['DisplayLevel'] |= 0x40
with open(p,'wb') as f:plistlib.dump(d,f)
PYCONFIG
plutil -lint "$ESP/EFI/OC/config.plist"
