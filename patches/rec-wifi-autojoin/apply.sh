# rec-wifi-autojoin: local.wifijoin restarts airportd once at boot with wifijoin.dylib inserted, so the Recovery joins
# the Wi-Fi network saved in NVRAM by itself.
install -d -o root -g wheel -m 755 $BS/usr/local $BS/usr/local/ssdtools
install -o root -g wheel -m 755 $P/build/wifijoin.dylib $P/src/wifijoin.sh $BS/usr/local/ssdtools/
install -o root -g wheel -m 644 $P/src/local.wifijoin.plist $BS/System/Library/LaunchDaemons/local.wifijoin.plist
