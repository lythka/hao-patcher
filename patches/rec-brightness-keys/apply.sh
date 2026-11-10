# rec-brightness-keys: brightkeys (LaunchAgent local.brightkeys) drives the display and keyboard backlight, the volume
# keys and the bezel in the Recovery.
install -d -o root -g wheel -m 755 $BS/usr/local $BS/usr/local/ssdtools
install -o root -g wheel -m 755 $P/build/brightkeys $BS/usr/local/ssdtools/brightkeys
install -o root -g wheel -m 644 $P/src/local.brightkeys.plist $BS/System/Library/LaunchAgents/local.brightkeys.plist
