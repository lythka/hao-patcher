# rec-bluetooth-input: external keyboard/mouse/trackpad drivers (USB and Bluetooth) from the root-patched System and
# btpower (local.btpower), which turns the Bluetooth controller on at boot; kexts.txt adds the drivers to the KC.
E=System/Library/Extensions
for t in $E/AppleHIDKeyboard.kext $E/AppleHIDMouse.kext $E/IOBluetoothHIDDriver.kext $E/AppleBluetoothMultitouch.kext; do
    rm -rf "$BS/$t"; ditto "$SYS/$t" "$BS/$t"
done
install -d -o root -g wheel -m 755 $BS/usr/local $BS/usr/local/ssdtools
install -o root -g wheel -m 755 $P/build/btpower $BS/usr/local/ssdtools/btpower
install -o root -g wheel -m 644 $P/src/local.btpower.plist $BS/System/Library/LaunchDaemons/local.btpower.plist
