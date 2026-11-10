#!/bin/sh
# Golden Gate first, in a disposable maintenance guest; no volume creation here.
# Phase 1, while System is the image's SOLE volume, role System, ungrouped:
#   golden-pristine.sh system --filesystem GOLDEN_FS_DMG --system SYSTEM_MOUNT \
#     --image OUTPUT_IMAGE --layout-helper golden-layout.py --python STAGED_PYTHON
# Then create ungrouped Data and invoke golden-layout.py group with Data UNMOUNTED.
# Mount Data only after grouping; add Preboot/Recovery/VM after grouping as needed.
# Phase 2:
#   golden-pristine.sh finalize --system SYSTEM_MOUNT --data DATA_MOUNT \
#     --fl ENTITLED_FL --kernel GOLDEN_X86_KERNEL --kdk EXPANDED_INTEL_KDK \
#     --kdk-preparer PINNED_OCLP_KDK_INTEL_PAYLOAD_PY \
#     --donor-cli TAHOE_DONORS_MAKE --extensions-artifact STOCK_TAHOE_SLE_ARTIFACT \
#     --python STAGED_PYTHON [--native-artifact TAHOE_NATIVE_ARTIFACT]
# Stock SLE installation restores its manifest metadata; KDK SLE then merges into
# it after the selected OCLP Intel-only preparer filters/thins a guest-local copy.
# Native install excludes kmutil;
# payload/build-tools/usr/bin/kmutil is a separately invoked maintenance linker.
# System/Library/Templates/Data and usr/share/firmlinks come only from Golden Gate.
# Phase 2 never recopies System/Cryptexes/OS: its protected altlink/inode survives.
# No Tahoe SystemVersion substitution, cryptex/KC/snapshot/EFI or boot-ready claim.
set -eu
umask 022
MODE=${1:---help}
if [ "$#" -gt 0 ]; then shift; fi
FILESYSTEM= SYSTEM= DATA= FL= KERNEL= KDK= KDK_PREPARER= DONOR_CLI= EXTENSIONS_ARTIFACT= NATIVE_ARTIFACT= PYTHON= IMAGE= LAYOUT=
WORK= MOUNTED=0
usage() { sed -n '2,18p' "$0" >&2; }
fail() { echo "golden-pristine: $*" >&2; exit 2; }
finish() {
    rc=$?
    trap - 0 1 2 15
    if [ "$MOUNTED" = 1 ]; then
        if hdiutil detach "$WORK/filesystem"; then MOUNTED=0
        else cleanup_rc=$?; [ "$rc" != 0 ] || rc=$cleanup_rc; fi
    fi
    if [ -n "$WORK" ] && [ "$MOUNTED" = 0 ]; then rm -rf "$WORK"; fi
    echo "RESULT rc=$rc"
    exit "$rc"
}
trap finish 0
trap 'exit 129' 1
trap 'exit 130' 2
trap 'exit 143' 15
case "$MODE" in system|finalize) ;; --help|-h) usage; exit 0 ;; *) usage; fail 'select system or finalize' ;; esac
while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || fail "missing value for $1"
    option=$1; value=$2; shift 2
    case "$option" in
        --filesystem) FILESYSTEM=$value ;; --system) SYSTEM=$value ;; --data) DATA=$value ;;
        --fl) FL=$value ;; --kernel) KERNEL=$value ;; --kdk) KDK=$value ;;
        --kdk-preparer) KDK_PREPARER=$value ;;
        --donor-cli) DONOR_CLI=$value ;; --extensions-artifact) EXTENSIONS_ARTIFACT=$value ;;
        --native-artifact) NATIVE_ARTIFACT=$value ;; --python) PYTHON=$value ;;
        --image) IMAGE=$value ;; --layout-helper) LAYOUT=$value ;;
        *) fail "unknown argument: $option" ;;
    esac
done
[ -n "$SYSTEM" ] && [ -n "$PYTHON" ] || fail '--system and --python are required'
SYSTEM=$(cd "$SYSTEM" && pwd -P)
case "$SYSTEM" in /|/System/Volumes/Data) fail 'System must be a separate offline output root' ;; esac
if [ "$MODE" = system ]; then
    [ -n "$FILESYSTEM" ] && [ -n "$IMAGE" ] && [ -n "$LAYOUT" ] ||
        fail 'system needs --filesystem, --image and --layout-helper'
    "$PYTHON" -I "$LAYOUT" check-system --image "$IMAGE" --system "$SYSTEM"
    WORK=$(mktemp -d "${TMPDIR:-/private/var/tmp}/golden-pristine.XXXXXX")
    install -d -m 755 "$WORK/filesystem"
    hdiutil attach -readonly -nobrowse -owners on -mountpoint "$WORK/filesystem" "$FILESYSTEM"
    MOUNTED=1
    diskutil enableOwnership "$SYSTEM"
    echo 'STEP golden-system'
    # Firmlink anchors cannot be copied as ordinary BSD flags/xattrs. Preserve
    # their other metadata and defer only those APFS objects to finalize/fl.
    "$PYTHON" -I "$LAYOUT" copy-system --image "$IMAGE" --system "$SYSTEM" --source "$WORK/filesystem"
    "$PYTHON" -I "$LAYOUT" copy-altlink --image "$IMAGE" --system "$SYSTEM" \
        --source "$WORK/filesystem/System/Cryptexes/OS"
    echo 'Golden System copied; create/group Data before finalize.'
else
    [ -n "$DATA" ] && [ -n "$FL" ] && [ -n "$KERNEL" ] && [ -n "$KDK" ] &&
        [ -n "$KDK_PREPARER" ] && [ -n "$DONOR_CLI" ] && [ -n "$EXTENSIONS_ARTIFACT" ] || fail 'finalize input missing'
    DATA=$(cd "$DATA" && pwd -P)
    case "$DATA" in /|/System/Volumes/Data) fail 'Data must be a separate offline output root' ;; esac
    case "$SYSTEM/" in "$DATA/"*) fail 'System/Data roots overlap' ;; esac
    case "$DATA/" in "$SYSTEM/"*) fail 'System/Data roots overlap' ;; esac
    diskutil enableOwnership "$DATA"
    echo 'STEP golden-data'
    ditto --rsrc --extattr --acl "$SYSTEM/System/Library/Templates/Data" "$DATA"
    echo 'STEP golden-firmlinks'
    TAB=$(printf '\t')
    while IFS="$TAB" read -r path target; do
        case "$path" in ''|\#*) continue ;; esac
        case "$path" in /*) ;; *) fail "firmlink source is not absolute: $path" ;; esac
        case "$path" in /|*/../*|*/..|*/./*|*/.|*//*) fail "invalid firmlink source: $path" ;; esac
        case "$target" in ''|/*|..|../*|*/../*|*/..|./*|*/./*|*/.|*//*) fail "invalid firmlink target: $target" ;; esac
        # Apple's list contains optional absent anchors (e.g. AppleInternal).
        if [ ! -e "$SYSTEM$path" ] && [ ! -L "$SYSTEM$path" ]; then continue; fi
        if [ ! -d "$DATA/$target" ]; then install -d -m 755 "$DATA/$target"; fi
        "$FL" set "$SYSTEM$path" "$target"
    done < "$SYSTEM/usr/share/firmlinks"
    echo 'STEP tahoe-extensions'
    "$PYTHON" -I "$DONOR_CLI" install-extensions --artifact "$EXTENSIONS_ARTIFACT" --system "$SYSTEM"
    echo 'STEP intel-kdk'
    WORK=$(mktemp -d "${TMPDIR:-/private/var/tmp}/golden-pristine.XXXXXX")
    # The pinned preparer excludes debug symbols before copying. A raw ditto
    # of the shared KDK fails on their symlink attributes under virtiofs.
    echo "KDK preparer: $KDK_PREPARER"
    shasum -a 256 "$KDK_PREPARER"
    "$PYTHON" -I -c 'import json,runpy,sys; print(json.dumps(runpy.run_path(sys.argv[1])["prepare"](sys.argv[2],sys.argv[3]),indent=2))' \
        "$KDK_PREPARER" "$KDK/System/Library/Extensions" "$WORK/kdk-intel"
    # Normalize only the new local output; never chown the shared input.
    chown -R root:wheel "$WORK/kdk-intel"
    rsync -r -i -a --checksum "$WORK/kdk-intel/" "$SYSTEM/System/Library/Extensions/"
    echo 'STEP darwin27-kernel'
    install -d -m 755 "$SYSTEM/System/Library/Kernels"
    if [ -e "$SYSTEM/System/Library/Kernels/kernel" ] || [ -L "$SYSTEM/System/Library/Kernels/kernel" ]; then
        chflags -h noschg,nouchg,norestricted "$SYSTEM/System/Library/Kernels/kernel"
    fi
    install -o root -g wheel -m 644 "$KERNEL" "$SYSTEM/System/Library/Kernels/kernel"
    if [ -n "$NATIVE_ARTIFACT" ]; then
        echo 'STEP tahoe-native'
        "$PYTHON" -I "$DONOR_CLI" install-native --artifact "$NATIVE_ARTIFACT" --system "$SYSTEM"
    fi
    sync
    echo 'Golden Gate roots prepared; Preboot, cryptex/KC, snapshots, EFI and boot validation remain separate.'
fi
