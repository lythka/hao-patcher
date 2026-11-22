#!/bin/sh
set -eu
S=${1:?System mount}; H=$(cd "$(dirname "$0")" && pwd)
"${OCLP_PYTHON:?}" -I "$H/rebuild.py" "$S" "${HAO_CACHE_PHASE:?assembler must select build or install}"
