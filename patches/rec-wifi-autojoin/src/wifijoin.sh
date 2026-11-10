#!/bin/sh
# wifijoin.sh (local.wifijoin, once at boot): (re)starts Apple's airportd once with wifijoin.dylib inserted.
# launchd drops DYLD_* from a job's EnvironmentVariables, but `launchctl debug --environment` applies it to the next
# launch. A copy of airportd under another path crashes, so a wrapper with airportd.real is not an option. If airportd restarts later (KeepAlive after a crash) it runs stock.
L=/usr/local/ssdtools/wifijoin.dylib
PATH=/usr/bin:/bin:/usr/sbin:/sbin
[ -f $L ] || { echo "wifijoin: no $L"; exit 0; }
launchctl debug system/com.apple.airportd --environment DYLD_INSERT_LIBRARIES=$L >/dev/null &&
    launchctl kickstart -k system/com.apple.airportd && echo "wifijoin: $(date +%T) airportd restarted with $L" ||
    echo "wifijoin: $(date +%T) launchctl failed: airportd runs stock"
