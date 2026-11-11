# golden-gate-user-template: what macOS makes in a new user's Library at the first login (app and widget containers
# with their containermanager xattrs and personality, chronod's widget descriptor cache, remindd's local stores), put
# into /Library/User Template/Non_localized, which macOS copies into every new home when the account is created
# (input user_template_golden_gate, cache/seeds/template from a first-login harvest of the same OS build; README).
# The artifact is already cleaned by the template rule (no symlinks, no app data, schema 0, no sandbox profile,
# personality flag set, PeopleWidget rows dropped); this extracts it, makes it root's like the stock template and
# re-checks the rule.
A="$P/inputs/user_template_golden_gate"
B=$(/usr/libexec/PlistBuddy -c 'Print :ProductBuildVersion' "$SYS/System/Library/CoreServices/SystemVersion.plist")
W=$(plutil -extract build raw -o - "$A/build.json")
[ "$W" = "$B" ] || { echo "golden-gate-user-template: template is for $W, System is $B" >&2; exit 1; }
NL="$DATA/Library/User Template/Non_localized"
[ -d "$NL/Library" ] || { echo "golden-gate-user-template: no stock User Template at $NL" >&2; exit 1; }
T="$NL/Library"
for r in Containers "Group Containers"; do
    [ ! -e "$T/$r" ] || { echo "golden-gate-user-template: $T/$r exists already (another recipe?)" >&2; exit 1; }
done
ditto -x "$A/user-template.cpio" "$DATA"
# root:wheel like the stock template, whoever made the archive; account creation gives the copy to the new user
chown -R 0:0 "$T/Containers" "$T/Group Containers" "$T/Preferences/com.apple.chronod.plist"
# the builder's extraction may tag files; the stock template carries no provenance
xattr -r -d com.apple.provenance "$NL" 2>/dev/null || true
xattr -r -d com.apple.quarantine "$NL" 2>/dev/null || true
n=$(find "$T/Containers" "$T/Group Containers" -type l | wc -l | tr -d ' ')
[ "$n" = 0 ] || { echo "golden-gate-user-template: $n symlinks in the template" >&2; exit 1; }
for m in "$T"/Containers/*/.com.apple.containermanagerd.metadata.plist; do
    [ "$(plutil -extract MCMMetadataSchemaVersion raw -o - "$m")" = 0 ] && \
    [ "$(plutil -extract MCMMetadataInfo.LSDataContainerPersonalityInfo raw -o - "$m")" = true ] && \
    ! plutil -extract MCMMetadataInfo.SandboxProfileData raw -o - "$m" >/dev/null 2>&1 || \
        { echo "golden-gate-user-template: $m does not follow the template rule" >&2; exit 1; }
done
echo "golden-gate-user-template: $B, $(ls "$T/Containers" | wc -l | tr -d ' ') containers, $(ls "$T/Group Containers" | wc -l | tr -d ' ') group containers, $(find "$T/Containers" "$T/Group Containers" -type f | wc -l | tr -d ' ') files"
