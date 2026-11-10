# rec-startup-guard: bootguard (local.bootguard) keeps the Recovery's Startup Disk from pointing the firmware at the
# internal macOS's boot.efi, which this Mac cannot start without OpenCore: it points it at the internal ESP's OpenCore.
install -d -o root -g wheel -m 755 $BS/usr/local $BS/usr/local/ssdtools
install -o root -g wheel -m 755 $P/build/bootguard $P/src/bootguard.sh $P/src/internal-esp $BS/usr/local/ssdtools/
install -o root -g wheel -m 644 $P/src/local.bootguard.plist $BS/System/Library/LaunchDaemons/local.bootguard.plist
