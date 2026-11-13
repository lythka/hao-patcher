#!/bin/sh
# golden-gate-live-text-service (post, after hao-opencore): RestrictEvents stops blocking mediaanalysisd at the
# next OpenCore boot, only in an image that carries the Live Text runtime (golden-gate-live-text-shared).
set -eu
# The kit's Python for the Mac the assembly runs on (golden-image.py sets OCLP_PYTHON); the builder VM's share copy
# for the legacy runner.
PY=${OCLP_PYTHON:-/private/var/ssdshare/py/bin/python3.12}
"$PY" -I "$P/src/config.py" "$ESP/EFI/OC/config.plist" "$SYS"
plutil -lint "$ESP/EFI/OC/config.plist"
