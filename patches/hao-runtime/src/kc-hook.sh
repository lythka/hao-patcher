#!/bin/sh
# After golden-gate-userspace installed the 27.0 userspace: install hao and give
# every arm64-only 27.0 executable an x86_64 slice that runs it through hao.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
"$OCLP_PYTHON" -I "$H/inputs/hao_runtime/hao-install.py" "$1" "$H/inputs/hao_runtime"
