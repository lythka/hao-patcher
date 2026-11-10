# rec-install-volume: the image's own Recovery volume carries our end-user Recovery instead of Apple's, so Cmd-R and
# OpenCore's Recovery entry start it. Input row enduser_recovery = the volume/ folder of a pipeline/recovery output
# (BaseSystem.dmg, its unsigned BaseSystem.chunklist, patches.txt). The pair replaces Apple's in <Recovery>/<volume
# group>/, in place: no second *.dmg in that folder (OpenCore boots the first one it finds).
R=$P/inputs/enduser_recovery
set -- "$REC"/*/BaseSystem.dmg
[ $# = 1 ] && [ -f "$1" ] || { echo "rec-install-volume: want one <group>/BaseSystem.dmg on the Recovery volume: $*"; exit 1; }
D=${1%/*}
cp "$R/BaseSystem.dmg" "$D/BaseSystem.dmg"
cp "$R/BaseSystem.chunklist" "$D/BaseSystem.chunklist"
echo "rec-install-volume: recovery patches $(cut -d' ' -f2 "$R/patches.txt" | tr '\n' ' ')"
echo "rec-install-volume: $D: BaseSystem.dmg $(stat -f %z "$D/BaseSystem.dmg") bytes, chunklist sha256 $(shasum -a 256 "$D/BaseSystem.chunklist" | cut -c1-64)"
df -h "$REC" | tail -1
