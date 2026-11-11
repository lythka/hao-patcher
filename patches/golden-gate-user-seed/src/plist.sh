#!/bin/sh
# plist.sh SEED_ZSH: the local.peruser-seed launchd plist on stdout. seed.zsh is the `zsh -c` argument, XML-escaped
# (& < >) with newlines and tabs as character references, so the daemon is one file and cannot drift from the script.
set -eu
printf %s '<plist version="1.0"><dict><key>Label</key><string>local.peruser-seed</string><key>ProgramArguments</key><array><string>/bin/zsh</string><string>-c</string><string>'
awk 'BEGIN { ORS = "" } { gsub(/&/, "\\&amp;"); gsub(/</, "\\&lt;"); gsub(/>/, "\\&gt;"); gsub(/\t/, "\\&#9;"); print $0 "&#10;" }' "$1"
printf %s '</string></array><key>WatchPaths</key><array><string>/private/var/db/dslocal/nodes/Default/users</string></array><key>RunAtLoad</key><true/><key>StandardOutPath</key><string>/private/var/log/peruser-seed.log</string><key>StandardErrorPath</key><string>/private/var/log/peruser-seed.log</string></dict></plist>'
