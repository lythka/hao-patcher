#!/bin/zsh
# A0 marker (tools/gg-recovery): proves a shell runs in the Golden Gate BaseSystem. Its lines go to /dev/console,
# which serial=3 copies to the serial port. launchd runs it at load and every 60 s.
p() { print -r -- "GG-A0 $*" }
p "zsh $ZSH_VERSION pid $$ ppid $PPID uid $UID arch $(/usr/bin/uname -m) darwin $(/usr/bin/uname -r)"
p "build $(/usr/bin/sw_vers -productVersion 2>&1) ($(/usr/bin/sw_vers -buildVersion 2>&1)) basesystem $([[ -d /System/Library/BaseSystem ]] && print yes || print no)"
p "pid1 $(/bin/ps -o comm= -p 1 2>&1) processes $(/bin/ps -ax -o pid= 2>/dev/null | /usr/bin/wc -l | /usr/bin/tr -d ' ')"
p "root $(/sbin/mount 2>&1 | /usr/bin/head -1)"
p "en0 $(/sbin/ifconfig en0 2>&1 | /usr/bin/grep -E 'inet |status' | /usr/bin/tr -s ' \t' ' ' | /usr/bin/tr '\n' ';')"
p "uptime $(/usr/bin/uptime 2>&1)"
