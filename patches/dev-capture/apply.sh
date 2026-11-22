# dev-capture: what every dev run collects (owner 2026-11-18, "Always capture"). com.hao's log messages keep their
# private data and their info lines persist, so a refusal or crash reason reads in full from any boot's log. The folder
# /private/var/db/hao-capture is where the metadata recorder writes. Dev sets only.
install -d -o root -g wheel -m 755 "$DATA/Library/Preferences/Logging/Subsystems"
install -o root -g wheel -m 644 "$P/src/com.hao.plist" "$DATA/Library/Preferences/Logging/Subsystems/com.hao.plist"
plutil -lint "$DATA/Library/Preferences/Logging/Subsystems/com.hao.plist"
install -d -o root -g wheel -m 755 "$DATA/private/var/db/hao-capture"
install -d -o root -g wheel -m 755 "$DATA/usr/local/libexec/hao-capture" "$DATA/Library/LaunchDaemons"
install -o root -g wheel -m 755 "$P/inputs/hao_capture_job" "$DATA/usr/local/libexec/hao-capture/hao-capture"
install -o root -g wheel -m 755 "$P/inputs/hao_capture_inventory" "$DATA/usr/local/libexec/hao-capture/inventory.sh"
install -o root -g wheel -m 644 "$P/inputs/hao_capture_plist" "$DATA/Library/LaunchDaemons/local.hao.capture.plist"
plutil -lint "$DATA/Library/LaunchDaemons/local.hao.capture.plist"
# task-for-pid for every dev image including dev-lite (which has no dev-diagnostics): SIP bit 0x4 (the recorder's
# swift-inspect attaches to processes). No hao_crash=1 (r5): with it a dev image's first boot stalls before sandboxd.
PY=${OCLP_PYTHON:-/private/var/ssdshare/py/bin/python3.12}
"$PY" - "$ESP/EFI/OC/config.plist" <<'PYCONFIG'
import plistlib,sys
p=sys.argv[1]
with open(p,'rb') as f:d=plistlib.load(f)
n=d['NVRAM']['Add']['7C436110-AB2A-4BBB-A880-FE41995C9F82']
args=n.get('boot-args','').split()
n['boot-args']=' '.join(args)
c=int.from_bytes(n.get('csr-active-config',b'\x03\x08\x00\x00'),'little')|0x4
n['csr-active-config']=c.to_bytes(4,'little')
with open(p,'wb') as f:plistlib.dump(d,f)
PYCONFIG
plutil -lint "$ESP/EFI/OC/config.plist"
