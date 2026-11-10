#!/bin/sh
set -eu
H=$(cd "$(dirname "$0")" && pwd)
"${OCLP_PYTHON:?}" -I "$H/install.py" "${1:?System mount}" "$H/inputs/code_identity"
