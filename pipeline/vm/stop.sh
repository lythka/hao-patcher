#!/bin/zsh
# stop.sh VM: unmount the guest's external disks, shut the builder VM down cleanly, stop vmctl serve. Env: VMCTL.
set -u
VM=${1:A}; R=$VM.run
if [[ -f $R/ssdvm.pid ]] && ps -p $(cat $R/ssdvm.pid) >/dev/null; then
    # the guest only hears us through vmctl serve: start it if it is not running (a kill alone risks the target disk)
    if [[ ! -f $R/serve.pid ]] || ! ps -p $(cat $R/serve.pid) >/dev/null; then
        nohup $VMCTL $VM serve > $R/serve.log 2>&1 &
        echo $! > $R/serve.pid
    fi
    for i in $(seq 1 12); do
        perl -e 'alarm 15; exec @ARGV' $VMCTL $VM exec \
            'for d in $(diskutil list external physical | awk "/^\/dev\//{print \$1}"); do diskutil unmountDisk $d >/dev/null 2>&1; done; sync; (sleep 2; shutdown -h now) >/dev/null 2>&1 & echo stopping' 2>/dev/null | grep -q stopping && break
        sleep 5
    done
    for i in $(seq 1 60); do ps -p $(cat $R/ssdvm.pid) >/dev/null || break; sleep 2; done
    ps -p $(cat $R/ssdvm.pid) >/dev/null && { echo "stop.sh: VM still running after 120 s, killing"; kill $(cat $R/ssdvm.pid); }
fi
[[ -f $R/serve.pid ]] && kill $(cat $R/serve.pid) 2>/dev/null
echo "stop.sh: stopped"
