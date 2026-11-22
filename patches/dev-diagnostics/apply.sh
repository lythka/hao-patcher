#!/bin/sh
# dev-diagnostics (post, after hao-opencore; dev sets only, pack dev): the measurement settings of a bench image, kept
# out of every end-user image. hao-runtime's diagnostic boot arguments (cost time on every translated native call)
# and OpenCore's log file on the EFI partition (early-boot evidence collected from Recovery), and the two System
# Integrity Protection flags DTrace needs to attach to a process.
set -eu
# The kit's Python for the Mac the assembly runs on (golden-image.py sets OCLP_PYTHON); the builder VM's share copy
# for the legacy runner.
PY=${OCLP_PYTHON:-/private/var/ssdshare/py/bin/python3.12}
"$PY" - "$ESP/EFI/OC/config.plist" <<'PYCONFIG'
import plistlib,sys
p=sys.argv[1]
with open(p,'rb') as f:d=plistlib.load(f)
n=d['NVRAM']['Add']['7C436110-AB2A-4BBB-A880-FE41995C9F82']
args=n.get('boot-args','').split()
# No hao_crash=1 (r11): with it a dev image's first boot stalls before sandboxd.
# hao_nx_stats/hao_bridge_stats: NX fault tables and bridge statistics; hao_diag_time: mach-clock stamps and phase
# lines, PID 1's pre-Data lines kept and flushed by the bridge_stats report (boot-gantt reads them). Before
# auxKCAtBoot=false, in this order.
at=args.index('auxKCAtBoot=false') if 'auxKCAtBoot=false' in args else len(args)
for arg in reversed(('hao_nx_stats=1','hao_bridge_stats=1','hao_diag_time=1')):
    if arg not in args:args.insert(at,arg)
n['boot-args']=' '.join(args)
# OpenCore's log: 0x01 logging + 0x40 file on the EFI partition, nothing on screen (0x02 off); info lines (0x40).
d['Misc']['Debug']['Target']=0x41
d['Misc']['Debug']['DisplayLevel']|=0x40
# System Integrity Protection, dev sets only: the image's value (0x803: unsigned kexts, unrestricted filesystem,
# unauthenticated root) plus task-for-pid (0x4) and unrestricted DTrace (0x20), so dtrace can attach to a process.
# Never 0x10 (Apple internal). OpenCore deletes the stored variable first (NVRAM.Delete, from hao-opencore).
c=int.from_bytes(n.get('csr-active-config',b'\x03\x08\x00\x00'),'little')|0x24
n['csr-active-config']=c.to_bytes(4,'little')
with open(p,'wb') as f:plistlib.dump(d,f)
PYCONFIG
plutil -lint "$ESP/EFI/OC/config.plist"
