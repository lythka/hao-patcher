#!/bin/sh
# golden-gate-corewifi (kc-hook, after golden-gate-userspace): Tahoe 25G342's CoreWiFi as an on-disk override of the
# cached 27.x CoreWiFi, so Tahoe's airportd runs with the CoreWiFi it was built with and every client shares it, plus
# the compat library (installed as CoreWiFi, re-exporting CoreWiFiTahoe.dylib) for what 27.x clients need beyond it.
#   kc-hook.sh SYSTEM_MOUNT     env OCLP_PYTHON
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
"${OCLP_PYTHON:?}" -I "$H/install.py" "$S" "$H/inputs/corewifi_tahoe" "$H/inputs/corewifi_compat"
