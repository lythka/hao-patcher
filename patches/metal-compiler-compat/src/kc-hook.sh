#!/bin/sh
# metal-compiler-compat (kc-hook, after the OCLP overlay installed the legacy 3802 Metal stack): OCLP's
# MTLCompilerService loads cache/metallib's compat.dylib at startup (inputs/metal_compiler_compat, tools/live-text).
set -eu
H=$(cd "$(dirname "$0")" && pwd)
"${OCLP_PYTHON:?}" -I "$H/install.py" "${1:?System mount}" "$H/inputs/metal_compiler_compat"
