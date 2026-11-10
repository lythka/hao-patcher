# rec-reinstall: Apple's Reinstall macOS entry keeps its name, icon and place but installs the image from a USB disk.
# The Recovery's Springboard starts the Utilities.plist button marked IsInstallAssistant by its Path; that one file is
# replaced (cat: Apple's owner and mode kept). ssdinstall, startup-internal and ssdpush-agent go to /usr/local/ssdtools,
# next to rec-startup-guard's internal-esp and bootguard, which startup-internal uses.
U="$BS/System/Installation/CDIS/Recovery Springboard.app/Contents/Resources/Utilities.plist"
i=0; X=""
while plutil -extract Buttons.$i xml1 -o /dev/null "$U" 2>/dev/null; do
    [ "$(plutil -extract Buttons.$i.IsInstallAssistant raw -o - "$U" 2>/dev/null)" = true ] && X=$(plutil -extract Buttons.$i.Path raw -o - "$U")
    i=$((i + 1))
done
[ -n "$X" ] && [ -f "$BS$X" ] || { echo "rec-reinstall: no installer button (IsInstallAssistant) in $U"; exit 1; }
cat "$P/build/InstallAssistant" > "$BS$X"
install -d -o root -g wheel -m 755 "$BS/usr/local" "$BS/usr/local/ssdtools" "$BS/usr/local/ssdtools/bin"
install -o root -g wheel -m 755 "$P/build/ssdinstall" "$P/src/startup-internal" "$BS/usr/local/ssdtools/"
install -o root -g wheel -m 755 "$P/build/ssdpush-agent" "$BS/usr/local/ssdtools/bin/"
echo "rec-reinstall: $X replaced"
