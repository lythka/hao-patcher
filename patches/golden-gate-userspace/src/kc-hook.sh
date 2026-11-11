#!/bin/sh
# golden-gate-userspace (kc-hook, second entry of every golden set, after darwin27-kernel linked the collections).
# The base layers already installed Golden Gate's System/Data, the Tahoe donors and OCLP's hardware overlay; this
# recipe adds what makes that userspace run natively on Intel: the combined OS cryptex (27.x SystemOS + RosettaOS
# x86_64 dyld cache, the Tahoe x86_64 DriverKit cache, the cache repairs), OCLP compat libraries for IO80211 and
# WiFiPeerToPeer, the display-state shim, and the disabled Apple-silicon-only jobs. Every later recipe edits this.
#   kc-hook.sh SYSTEM_MOUNT     env OCLP_PYTHON GRP PBU, and from golden-image.py (runner-only facts):
#     TAHOE_DRIVERKIT_ARTIFACT  the assembly's tahoe_driverkit_donors artifact
#     TAHOE_DONOR_TOOL          tools/tahoe-donors/make, which installs it into the cryptex
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
"${OCLP_PYTHON:?}" -I "$H/install.py" "$S" "$H/inputs/golden_gate_userspace" "$H/inputs/display_state_shim" \
    "$H/inputs/io80211_compat" "$H/inputs/wifip2p_compat" \
    --driverkit-artifact "${TAHOE_DRIVERKIT_ARTIFACT:?set by golden-image.py}" \
    --donor-tool "${TAHOE_DONOR_TOOL:?set by golden-image.py}"
