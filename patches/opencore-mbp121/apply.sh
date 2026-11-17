#!/bin/sh
# opencore-mbp121 (post, right before hao-opencore; pack mbp121): the MacBookPro12,1's part of OpenCore beyond what
# OCLP's generator gives it. OCLP leaves CryptexFixup out on AVX2 CPUs; the image's modified OS cryptex needs its APFS
# root-hash patch, so it is added from OCLP's pinned payload. hao-opencore then adds the Darwin 27 parts and checks
# every element's reason (this model's reasons and kext provenance are the target's platform_data).
set -eu
# The kit's Python for the Mac the assembly runs on (golden-image.py sets OCLP_PYTHON); the builder VM's share copy
# for the legacy runner.
PY=${OCLP_PYTHON:-/private/var/ssdshare/py/bin/python3.12}
"$PY" -I "$P/src/model.py" "$ESP" "$P/inputs/oclp_source"
plutil -lint "$ESP/EFI/OC/config.plist"
