#!/bin/sh
# Link the Golden Gate kernel with the selected Intel driver tree after hardware
# overlays. Tahoe kmutil is an explicit build input, independent of the target OS.
# Preserve the collection path: boot repairs are bound to the resulting bytes.
set -eu
SYS=$1; H=$(cd "$(dirname "$0")" && pwd); K=$H/inputs/darwin27_kernel
cp "$K" "$SYS/System/Library/Kernels/kernel"
chmod 644 "$SYS/System/Library/Kernels/kernel"
# Paths inside the target must differ from the live builder's paths for kmutil.
KC=System/Library/KernelCollections; B=/.darwin27-kc-build   # the build path is linked into the BootKC: changing it changes the bytes the repairs are bound to
W=$(mktemp -d)
cp "$H/inputs/tahoe_kmutil" "$W/kmutil"; chmod 755 "$W/kmutil"; codesign -s - -f "$W/kmutil"
mkdir "$SYS$B"
set -- -a x86_64 --allow-missing-kdk -R "$SYS" -k "$SYS/System/Library/Kernels/kernel" -V release -B "$B/BootKernelExtensions.kc"
"$W/kmutil" create -n boot "$@"
"$OCLP_PYTHON" -I "$H/members.py" "$SYS$B/BootKernelExtensions.kc" | grep -vx com.apple.kernel > "$SYS$B/BootKernelExtensions.kc.elides"
"$W/kmutil" create -n sys "$@" -S "$B/SystemKernelExtensions.kc" $(sed 's/^/--elide-identifier /' "$SYS$B/BootKernelExtensions.kc.elides")
for f in BootKernelExtensions.kc BootKernelExtensions.kc.elides SystemKernelExtensions.kc; do
    install -o root -g wheel -m 644 "$SYS$B/$f" "$SYS/$KC/$f"
done
info() { diskutil info -plist "$1" | plutil -extract "$2" raw -o - - 2>/dev/null; }
PBD=$(info "${PBU:?PBU}" DeviceIdentifier); GROUP=${GRP:?GRP}; OURS=
if [ -z "$(info "$PBD" MountPoint)" ]; then
    OURS=$(mktemp -d)
    diskutil mount -mountOptions nobrowse -mountPoint "$OURS" "$PBD" >/dev/null
fi
diskutil enableOwnership "$PBD" >/dev/null
PREBOOT=$(info "$PBD" MountPoint)/$GROUP/boot/$KC
for f in BootKernelExtensions.kc BootKernelExtensions.kc.elides; do
    install -o root -g wheel -m 644 "$SYS$B/$f" "$PREBOOT/$f"
done
if [ -n "$OURS" ]; then diskutil unmount "$PBD" >/dev/null; rmdir "$OURS"; fi
rm -rf "$SYS$B" "$W"
echo "Installed the Darwin 27.0 kernel; built Boot $(shasum -a 256 "$SYS/$KC/BootKernelExtensions.kc" | cut -c1-16) and System collections"
