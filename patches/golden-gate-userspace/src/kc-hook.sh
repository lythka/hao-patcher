#!/bin/sh
# After darwin27-kernel rebuilt the native collections; install Golden Gate userspace.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
"$OCLP_PYTHON" -I "$H/install.py" "$1" "$H/inputs/golden_gate_userspace" "$H/inputs/display_state_shim" "$H/inputs/io80211_compat" \
    "$H/inputs/wifip2p_compat"
