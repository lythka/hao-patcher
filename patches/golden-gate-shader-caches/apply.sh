# golden-gate-shader-caches: this build's HD 4000 Metal shader caches onto Data (input shader_caches_golden_gate, made
# by cache/seeds/make from a laptop harvest of the same OS build). data.tar is Data-rooted, owners and modes as harvested:
#   private/var/folders/zz/.../C/<bundle>/com.apple.metal{,fe}   system accounts (root, _windowserver, _mbsetupuser,
#                                                               _securityagent, ...), with their ancestor folders
#   Library/Application Support/PerUserSeed/metal/<bundle>/     per-user trees; golden-gate-user-seed copies them
#                                                               into each new account (list this after it)
A="$P/inputs/shader_caches_golden_gate"
B=$(/usr/libexec/PlistBuddy -c 'Print :ProductBuildVersion' "$SYS/System/Library/CoreServices/SystemVersion.plist")
W=$(plutil -extract build raw -o - "$A/build.json")
[ "$W" = "$B" ] || { echo "golden-gate-shader-caches: pack is for $W, System is $B" >&2; exit 1; }
[ -d "$DATA/Library/Application Support/PerUserSeed" ] || { echo "golden-gate-shader-caches: list it after golden-gate-user-seed" >&2; exit 1; }
tar -xpf "$A/data.tar" --numeric-owner -C "$DATA"
echo "golden-gate-shader-caches: $(tar -tf "$A/data.tar" | grep -c -v '/$') files for $B"
