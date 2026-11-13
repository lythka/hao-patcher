#!/bin/sh
# dev-diagnostics (post, after hao-opencore; dev sets only, pack dev): the measurement settings of a bench image, kept
# out of every end-user image. hao-runtime's diagnostic boot arguments (cost time on every translated native call)
# and OpenCore's log file on the EFI partition (early-boot evidence collected from Recovery).
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
# hao_nx_stats/hao_bridge_stats: NX fault tables and bridge statistics; hao_diag_time: mach-clock stamps and phase
# lines, PID 1's pre-Data lines kept and flushed by the bridge_stats report (boot-gantt reads them). After hao_crash=1.
at=args.index('hao_crash=1')+1 if 'hao_crash=1' in args else len(args)
for arg in reversed(('hao_nx_stats=1','hao_bridge_stats=1','hao_diag_time=1')):
    if arg not in args:args.insert(at,arg)
n['boot-args']=' '.join(args)
# OpenCore's log: 0x01 logging + 0x40 file on the EFI partition, nothing on screen (0x02 off); info lines (0x40).
d['Misc']['Debug']['Target']=0x41
d['Misc']['Debug']['DisplayLevel']|=0x40
with open(p,'wb') as f:plistlib.dump(d,f)
PYCONFIG
plutil -lint "$ESP/EFI/OC/config.plist"
