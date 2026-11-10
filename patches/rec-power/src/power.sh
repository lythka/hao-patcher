#!/bin/sh
# power.sh (local.power, once at boot): recovery's sleep policy (recovery keeps pmset settings in RAM, so every boot).
# On power: never sleep, display off after 10 min. On battery: sleep after 20 min idle, display off after 5 min.
# Work that must not be cut off holds its own assertion (Apple's installer; our tools run under caffeinate).
pmset -c sleep 0 disksleep 0 displaysleep 10
pmset -b sleep 20 disksleep 10 displaysleep 5
pmset -g custom
