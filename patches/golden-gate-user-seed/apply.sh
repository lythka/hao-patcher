# golden-gate-user-seed: the per-user seed runtime (mac-image user-seed, seed.zsh seed7, unchanged). launchd runs
# src/seed.zsh as root at boot and whenever the local users folder changes; for each new account (uid >= 501, created
# < 600 s ago) it copies what PerUserSeed holds into the account's cache folders before its apps start: is/ (icon
# index, golden-gate-icon-caches), metal/ (shader trees, golden-gate-shader-caches), fonts/ (font registry,
# golden-gate-font-registry). It places nothing when PerUserSeed/build is not the running OS build.
SEED="$DATA/Library/Application Support/PerUserSeed"
B=$(/usr/libexec/PlistBuddy -c 'Print :ProductBuildVersion' "$SYS/System/Library/CoreServices/SystemVersion.plist")
install -d -o root -g wheel -m 700 "$SEED"
printf %s "$B" > "$SEED/build"
chown root:wheel "$SEED/build"; chmod 644 "$SEED/build"
L="$DATA/Library/LaunchDaemons/local.peruser-seed.plist"
install -d -o root -g wheel -m 755 "$DATA/Library/LaunchDaemons"
sh "$P/src/plist.sh" "$P/src/seed.zsh" > "$L"
chown root:wheel "$L"; chmod 644 "$L"
plutil -lint "$L"
echo "golden-gate-user-seed: seed build $B"
