# rec-progress-phase: local.iasphase moves Apple's install phase from "Boot 1" to "Language Chooser" before the
# Recovery's progress window reads it, so a NetBoot's one boot bar does not jump back (any other boot: no change).
install -d -o root -g wheel -m 755 $BS/usr/local $BS/usr/local/ssdtools
install -o root -g wheel -m 755 $P/src/iasphase.sh $BS/usr/local/ssdtools/iasphase.sh
install -o root -g wheel -m 644 $P/src/local.iasphase.plist $BS/System/Library/LaunchDaemons/local.iasphase.plist
