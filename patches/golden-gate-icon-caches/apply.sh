# golden-gate-icon-caches: IconServices' caches of this build (input icon_caches_golden_gate, cache/seeds/make from a
# laptop harvest of the same OS build): the shared store Library/Caches/com.apple.iconservices.store (_iconservices)
# and the system accounts' C/com.apple.iconservices indexes in place (icon-caches.tar, owners and modes as harvested),
# and the user's index into PerUserSeed/is/, which golden-gate-user-seed copies into each new account (list this
# after it).
A="$P/inputs/icon_caches_golden_gate"
B=$(/usr/libexec/PlistBuddy -c 'Print :ProductBuildVersion' "$SYS/System/Library/CoreServices/SystemVersion.plist")
W=$(plutil -extract build raw -o - "$A/build.json")
[ "$W" = "$B" ] || { echo "golden-gate-icon-caches: caches are for $W, System is $B" >&2; exit 1; }
SEED="$DATA/Library/Application Support/PerUserSeed"
[ -d "$SEED" ] || { echo "golden-gate-icon-caches: list it after golden-gate-user-seed" >&2; exit 1; }
tar -xpf "$A/icon-caches.tar" --numeric-owner -C "$DATA"
install -d -o root -g wheel -m 755 "$SEED/is"
for f in "$A"/user/*; do install -o root -g wheel -m 644 "$f" "$SEED/is/"; done
echo "golden-gate-icon-caches: $(ls "$DATA/Library/Caches/com.apple.iconservices.store" | grep -c isdata) store units; user index $(stat -f %z "$SEED/is/store.index") bytes"
