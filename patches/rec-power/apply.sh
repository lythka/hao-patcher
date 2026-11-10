# rec-power: the Recovery's sleep policy, set at every boot by local.power: on AC never sleep, on battery 20 min.
install -d -o root -g wheel -m 755 $BS/usr/local $BS/usr/local/ssdtools
install -o root -g wheel -m 755 $P/src/power.sh $BS/usr/local/ssdtools/power.sh
install -o root -g wheel -m 644 $P/src/local.power.plist $BS/System/Library/LaunchDaemons/local.power.plist
