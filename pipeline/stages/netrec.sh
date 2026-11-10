#!/bin/zsh
# netrec.sh TARGET_JSON BASESYSTEM_DMG OUT: the disk the NetBoot server sanboots for TARGET's model, on this Mac, no sudo.
# OUT/http/<model>/netrec.img = raw GPT disk, one FAT32 partition (Microsoft Basic Data):
#   \EFI\BOOT\  the target's OpenCore (the ESP of targets/<model>.json "esp", as OCLP installs it) with OpenCore.efi as
#               BOOTX64.EFI: iPXE's sanboot starts \EFI\BOOT\BOOTX64.EFI, and OpenCore takes that folder as its root
#   \com.apple.recovery.boot\BaseSystem.dmg   the Recovery (OpenCore reads it into RAM)
# plus OUT/http/<model>/netrec.md5 and boot.ipxe (iPXE script; @SERVER@ is filled in by netboot-server.py).
# OpenCore config changes for the NetBoot recovery (OCLP's config with these changes):
#   DmgLoading Any, BlessOverride \com.apple.recovery.boot\ only: OpenCore boots the unsigned BaseSystem.dmg
#   ScanPolicy 0x801 = FILE_SYSTEM_LOCK | FS_NTFS (Basic Data partitions), no DEVICE_LOCK: iPXE's SAN disk has a
#     MAC/URI device path that OpenCore's device policy classifies as nothing; the internal ESP/APFS stay invisible
#   RequestBootVarRouting off: the Recovery's bless --setBoot --nextonly and --netboot reach the firmware's own
#     variables (on, they land in OpenCore's OCBt* namespace and the firmware never sees them)
#   LauncherOption Disabled, HideAuxiliary off, AutoPkgInstaller.kext off, no sbvmm in RestrictEvents' revpatch;
#     boot-args + amfi=0x80 (the recovery tools are ad-hoc signed)
#   kernel patch: the boot progress bar advances evenly (vc_progressmeter_task increments; a kernel without the
#     16-byte table is left alone, Count 1)
#   no IAS install-phase or DefaultBackgroundColor NVRAM writes: the iPXE download screen writes the phases
set -eu
TJ=${1:?target json}; DMG=${2:?BaseSystem.dmg}; OUT=${3:?out folder}
P=${0:A:h:h}; L=(python3 -I $P/lock.py)
MODEL=$($L target $TJ model); ESPTAR=$($L path $TJ esp)
M=$OUT/http/$MODEL; mkdir -p $M
W=$(mktemp -d ${TMPDIR:-/tmp}/netrec.XXXXXX); DISK=""
trap '[[ -z $DISK ]] || hdiutil detach -quiet -force $DISK 2>/dev/null; rm -rf $W' EXIT
mkdir $W/esp $W/mnt; tar -xf $ESPTAR -C $W/esp EFI/OC
dsz=$(stat -f %z $DMG); osz=$(du -sk $W/esp/EFI/OC | awk '{print $1}')
mb=$(( dsz / 1048576 * 102 / 100 + osz / 1024 + 256 ))   # + FAT32 tables/clusters and GPT (2404 MB was too tight)
echo "netrec: $MODEL, BaseSystem $((dsz / 1048576)) MB, OpenCore $((osz / 1024)) MB -> ${mb} MB FAT32"
hdiutil create -quiet -size ${mb}m -fs "MS-DOS FAT32" -volname NETREC -layout GPTSPUD $W/netrec.dmg
DISK=$(hdiutil attach -nobrowse -noverify -nomount $W/netrec.dmg | awk '$2 == "GUID_partition_scheme" {print $1}')
# hdiutil adds an EFI system partition in front once the disk passes ~2 GB: removed, one Basic Data partition stays
for s in $(diskutil list $DISK | awk '$2 == "EFI" {print $NF}'); do diskutil eraseVolume "Free Space" %noformat% $s >/dev/null; done
DATA=$(diskutil list $DISK | awk '/Microsoft Basic Data/ {print $NF}')
diskutil mount -mountOptions nobrowse -mountPoint $W/mnt $DATA >/dev/null
mkdir -p $W/mnt/EFI $W/mnt/com.apple.recovery.boot
ditto --norsrc --noextattr --noacl $W/esp/EFI/OC $W/mnt/EFI/BOOT
mv $W/mnt/EFI/BOOT/OpenCore.efi $W/mnt/EFI/BOOT/BOOTX64.EFI; rm -f $W/mnt/EFI/BOOT/.content*   # OCLP's picker flags for its ESP
python3 -I - $W/mnt/EFI/BOOT/config.plist <<'EOF'
import plistlib, sys
p = sys.argv[1]; c = plistlib.load(open(p, 'rb'))
sec, boot = c['Misc']['Security'], c['Misc']['Boot']
sec['DmgLoading'] = 'Any'; sec['ScanPolicy'] = 0x00000801
c['Misc']['BlessOverride'] = ['\\com.apple.recovery.boot\\']
boot['LauncherOption'] = 'Disabled'; boot['HideAuxiliary'] = False
c['UEFI']['Quirks']['RequestBootVarRouting'] = False
for k in c['Kernel']['Add']:
    if k['BundlePath'] == 'AutoPkgInstaller.kext':
        k['Enabled'] = False
nv = c['NVRAM']['Add']['7C436110-AB2A-4BBB-A880-FE41995C9F82']
args = nv['boot-args'].split()
nv['boot-args'] = ' '.join(args + [a for a in ['amfi=0x80'] if a not in args])
rev = c['NVRAM']['Add'].get('4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102', {})
if 'revpatch' in rev:
    rev['revpatch'] = ','.join(x for x in rev['revpatch'].split(',') if x != 'sbvmm')
ias = '4D1EDE05-38C7-4A6A-9CC6-4BCCA8B38C14'
for k in ('IASInstallPhaseList', 'IASCurrentInstallPhase', 'DefaultBackgroundColor'):
    c['NVRAM']['Add'].get(ias, {}).pop(k, None)
    if k in c['NVRAM'].get('Delete', {}).get(ias, []) and k != 'DefaultBackgroundColor':
        c['NVRAM']['Delete'][ias].remove(k)
c['Kernel']['Patch'].append({
    'Arch': 'x86_64', 'Base': '', 'Comment': 'netrec: even boot progress bar (vc_progressmeter_task incr[])',
    'Count': 1, 'Enabled': True, 'Identifier': 'kernel', 'Limit': 0, 'Mask': b'', 'ReplaceMask': b'',
    'MinKernel': '25.0.0', 'MaxKernel': '25.99.99', 'Skip': 0,
    'Find': bytes.fromhex('10271027002000100008800180014000'), 'Replace': (65535).to_bytes(2, 'little') * 8})
plistlib.dump(c, open(p, 'wb'))
print('  OpenCore: boot-args %r, ScanPolicy 0x801, DmgLoading Any, RequestBootVarRouting off' % nv['boot-args'])
EOF
ditto --norsrc --noextattr --noacl $DMG $W/mnt/com.apple.recovery.boot/BaseSystem.dmg
find $W/mnt -name '._*' -delete
cmp -s $DMG $W/mnt/com.apple.recovery.boot/BaseSystem.dmg
diskutil unmount $W/mnt >/dev/null; hdiutil detach -quiet $DISK; DISK=""
hdiutil convert -quiet -format UDTO -o $W/netrec $W/netrec.dmg
mv $W/netrec.cdr $M/netrec.img; md5 -q $M/netrec.img > $M/netrec.md5
enc=${MODEL//,/%2C}
cat > $M/boot.ipxe <<EOF
#!ipxe
# Recovery for $MODEL, BaseSystem.dmg md5 $(md5 -q $DMG | cut -c1-8) (pipeline/stages/netrec.sh, $(date +%Y-%m-%d)).
# Download screen (the NetBoot server iPXE's ssdsplash: Apple logo + bar; never fatal): the download fills the bar to 48 %, the kernel
# and the Language Chooser the rest. \${ssdstyle} globe (ssd-ipxe-globe.efi): the Internet Recovery look instead.
iseq \${ssdstyle} globe && goto globe ||
ssdsplash $dsz 480 || echo netrec: no download screen
sanboot --no-describe http://@SERVER@/$enc/netrec.img || goto failed
:globe
ssdsplash $dsz 1000 globe || echo netrec: no download screen
sanboot --no-describe http://@SERVER@/$enc/netrec.img || goto failed
:failed
echo netrec: sanboot of $MODEL from @SERVER@ failed, returning to the firmware in 30 s
sleep 30
exit 1
EOF
ls -l $M
