# golden-gate-font-registry: fontd's registries of this build (input font_registry_golden_gate, cache/seeds/make from a
# laptop harvest of the same OS build): the Setup account's C/com.apple.FontRegistry in place (setup.tar, owner 248),
# and the account-neutral user registry into PerUserSeed/fonts/, which golden-gate-user-seed's seed.zsh places into
# each new account with that account's ~/Library/Fonts written into it (list this after golden-gate-user-seed).
A="$P/inputs/font_registry_golden_gate"
B=$(/usr/libexec/PlistBuddy -c 'Print :ProductBuildVersion' "$SYS/System/Library/CoreServices/SystemVersion.plist")
W=$(plutil -extract build raw -o - "$A/build.json")
[ "$W" = "$B" ] || { echo "golden-gate-font-registry: registries are for $W, System is $B" >&2; exit 1; }
SEED="$DATA/Library/Application Support/PerUserSeed"
[ -d "$SEED" ] || { echo "golden-gate-font-registry: list it after golden-gate-user-seed" >&2; exit 1; }
tar -xpf "$A/setup.tar" --numeric-owner -C "$DATA"
install -d -o root -g wheel -m 755 "$SEED/fonts"
install -o root -g wheel -m 644 "$A/user/fontregistry.user" "$SEED/fonts/fontregistry.user"
echo "golden-gate-font-registry: Setup registry and user seed for $B"
