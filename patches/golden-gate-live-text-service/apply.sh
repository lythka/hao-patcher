#!/bin/sh
# golden-gate-live-text-service (post, after hao-opencore): RestrictEvents stops blocking mediaanalysisd at the
# next OpenCore boot, only in an image that carries the Live Text runtime (golden-gate-live-text-shared).
set -eu
/private/var/ssdshare/py/bin/python3.12 -I "$P/src/config.py" "$ESP/EFI/OC/config.plist" "$SYS"
plutil -lint "$ESP/EFI/OC/config.plist"
