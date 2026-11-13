# golden-gate-root-launchservices: root's LaunchServices store and authd's auth.db, as the image's own lsd and authd
# wrote them in a builder boot of this image (input root_launchservices_golden_gate, made by
# tools/first-boot-seeds/rootls collect; no laptop, no account). Without them lsd builds root's store at the first
# boot (26B131 on the MacBookPro9,2: lsd 38.2 -> 122.6 s, loginwindow blocked until 94.4 s, fskitd until 122.6 s)
# and authd imports 204 rules. The store holds the image's file and volume IDs: it fits only the image that booted.
A="$P/inputs/root_launchservices_golden_gate"
B=$(/usr/libexec/PlistBuddy -c 'Print :ProductBuildVersion' "$SYS/System/Library/CoreServices/SystemVersion.plist")
W=$(plutil -extract build raw -o - "$A/build.json")
[ "$W" = "$B" ] || { echo "golden-gate-root-launchservices: store is for $W, System is $B" >&2; exit 1; }
(cd "$A" && shasum -a 256 -s -c SHA256SUMS) || { echo "golden-gate-root-launchservices: $A fails its SHA256SUMS" >&2; exit 1; }
[ ! -e "$DATA/private/var/db/auth.db" ] || { echo "golden-gate-root-launchservices: Data already has an auth.db" >&2; exit 1; }
install -d -o root -g wheel -m 755 "$DATA/private/var/db"
install -o root -g wheel -m 600 "$A/auth.db" "$DATA/private/var/db/auth.db"
# root's DARWIN_USER_DIR (uid 0's fixed UUID: the same path on every Mac); modes as lsd and dirhelper leave them on a
# booted Mac (zz, zyx..., 0: 755; com.apple.LaunchServices.dv: 744; stores 644)
R="$DATA/private/var/folders/zz/zyxvpxvq6csfxvn_n0000000000000"
install -d -o root -g wheel -m 755 "$DATA/private/var/folders/zz" "$R" "$R/0"
install -d -o root -g wheel -m 744 "$R/0/com.apple.LaunchServices.dv"
for f in "$A/ls"/*.csstore; do
    install -o root -g wheel -m 644 "$f" "$R/0/com.apple.LaunchServices.dv/${f##*/}"
done
echo "golden-gate-root-launchservices: root store (schema $(plutil -extract schema raw -o - "$A/build.json")) and auth.db for $B"
