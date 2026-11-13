#!/bin/sh
# golden-assemble.sh KIT LOCAL [--owner USER:GROUP] [--disk diskN --confirm-disk diskN]
#
# Assemble the Golden Gate image from a staged kit (pipeline/kit.py) on the Mac this runs on: the arm64 builder VM
# (golden-run's guest job), an Intel Mac building onto an external SSD (hao build-disk) or the target's Recovery
# (hao install). KIT is read only. LOCAL (new or empty, on a disk the assembly does not erase) gets local copies of
# the assembly source, this Mac's CPython (KIT/py or KIT/py-x86_64, by uname -m), the site packages, hooks and posts,
# the rendered configuration (LOCAL/config.json) and the work folder; the target is LOCAL/target.img, or the whole
# disk with --disk (golden-image.py's DiskTarget refuses a disk the installer's checks refuse).
# Exit status: golden-image.py's, or the first failing copy's.
set -u
set -o pipefail
[ $# -ge 2 ] || { echo 'usage: golden-assemble.sh KIT LOCAL [--owner U:G] [--disk diskN --confirm-disk diskN]' >&2; exit 2; }
G=$1; W=$2; shift 2
OWNER=root:wheel; DISK=; CONFIRM=
while [ $# -gt 0 ]; do
    case $1 in
        --owner) OWNER=$2; shift 2 ;;
        --disk) DISK=$2; shift 2 ;;
        --confirm-disk) CONFIRM=$2; shift 2 ;;
        *) echo "golden-assemble.sh: unknown option $1" >&2; exit 2 ;;
    esac
done
case $(uname -m) in
    arm64) PYDIR=py ;;
    x86_64) PYDIR=py-x86_64 ;;
    *) echo "golden-assemble.sh: no kit Python for $(uname -m)" >&2; exit 2 ;;
esac
[ -f "$G/kit.json" ] || { echo "golden-assemble.sh: $G has no kit.json" >&2; exit 2; }
[ -x "$G/$PYDIR/bin/python3.12" ] || { echo "golden-assemble.sh: the kit has no $PYDIR/ CPython" >&2; exit 2; }
install -d -m 755 "$W/source" "$W/hooks" "$W/post" "$W/py" "$W/site" "$W/tmp" || exit $?
export TMPDIR="$W/tmp"
bootstrap() {
    (cd "$G/assembly-source" && tar -cf - .) | (cd "$W/source" && tar -xf -) || return $?
    (cd "$G/$PYDIR" && tar -cf - .) | (cd "$W/py" && tar -xf -) || return $?
    (cd "$G/site" && tar -cf - .) | (cd "$W/site" && tar -xf -) || return $?
    "$W/py/bin/python3.12" -I "$W/source/copy-hooks.py" "$G/hooks" "$W/hooks" --owner "$OWNER" || return $?
    (cd "$G/post" && tar -cf - .) | (cd "$W/post" && tar -xf -) || return $?
    chown -R "$OWNER" "$W/source" "$W/py" "$W/site" "$W/post" || return $?
}
bootstrap || exit $?
echo "Assembly storage after local source/hook copies:"
df -k "$W"
set --
[ -z "$DISK" ] || set -- --disk "$DISK" --confirm-disk "$CONFIRM"
"$W/py/bin/python3.12" -I "$W/source/kit.py" render "$G" "$W" --out "$W/config.json" "$@" || exit $?
PYTHONPATH="$W/site" "$W/py/bin/python3.12" -I "$W/source/golden-image.py" "$W/config.json"
