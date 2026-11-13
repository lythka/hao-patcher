#!/bin/sh
# Stage tools/kc-binder for the builder VM: apply.sh binds the kernel repairs and checks IntelCommpage against the
# BootKC this image just linked (src/kc_bind.py). Repository source, not a per-OS binary input.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 -I "$HERE/../../tools/kc-binder/src/stage.py" "${1:?output}"
