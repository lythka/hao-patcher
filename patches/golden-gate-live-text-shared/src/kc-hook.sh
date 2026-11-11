#!/bin/sh
# golden-gate-live-text-shared (kc-hook, after golden-gate-hello and metal-compiler-compat): the Live Text runtime
# in System at its install root (/usr/lib/hao-live-text), attached to every Metal client through libhelloblur.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
"${OCLP_PYTHON:?}" -I "$H/install.py" "${1:?System mount}" "$H/inputs/live_text_framework"
