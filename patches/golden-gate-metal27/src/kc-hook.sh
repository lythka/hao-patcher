#!/bin/sh
# golden-gate-metal27 (kc-hook, dev only, in no default set): macOS 27.2.1's own x86_64 Metal for every process on
# the HD 4000, over OCLP's wrapper (tools/metal-adapter make.py installed, inputs/metal27_installed). Runs after
# golden-gate-hello (whose wrapper edits it replaces) and before golden-gate-native-identities. install.py refuses a
# System it was not made for and writes the stand-ins, the shim, then Metal.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
"${OCLP_PYTHON:?}" -I "$H/install.py" "${1:?System mount}" "$H/inputs/metal27_installed"
