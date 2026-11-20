#!/bin/zsh
# start.sh VM SHARE [ssdvm run options, e.g. --usb IMG]: boot the builder VM headless into its full macOS (SIP off,
# amfi_get_out_of_my_way=1), start the host side of its root agent (vmctl serve) and wait until the agent answers.
# VM = a builder clone made and provisioned once with pipeline/vm/clones.py (clone, then first start); its
# agent polls the host on the clone's own port, so several clones can run at once. PIDs and logs go to VM.run/.
#   Env: VMBIN (folder with ssdvm), VMCTL (vmctl), WAIT (seconds, default 300)
set -u
VM=${1:A}; SHARE=${2:A}; shift 2; R=$VM.run; mkdir -p $R
# The clone's own disk grows by what the guest writes outside the share (logs, caches: a few GB a session); a build's
# data is in its workspace image, which golden-run counts before it starts the VM.
python3 -I ${0:A:h}/../diskguard.py check --need ${VM_DISK_NEED_GB:-20} --who ssdvm --what ${VM:t} --path ${VM:h} || exit 3
# SSDVM_MAC: a base VM (no clone marker) being set up by clean-vm make-base.
MAC=${SSDVM_MAC:-$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["mac"])' $VM/.ssdvm-clone.json)}
# vmctl serve and exec read the VM's share from the vnc.json ssdvm writes once up; one from an earlier run must not count
rm -f $VM/vnc.json
nohup $VMBIN/ssdvm run $VM --mac $MAC --share $SHARE "$@" > $R/ssdvm.log 2>&1 &
echo $! > $R/ssdvm.pid
t0=$(date +%s); serve=""
while (( $(date +%s) - t0 < ${WAIT:-300} )); do
    ps -p $(cat $R/ssdvm.pid) >/dev/null || { echo "start.sh: VM exited"; cat $R/ssdvm.log; exit 1; }
    if [[ -z $serve ]] || ! ps -p $serve >/dev/null; then       # serve exits while the bridge is not up yet
        nohup $VMCTL $VM serve > $R/serve.log 2>&1 &
        serve=$!; echo $serve > $R/serve.pid
    fi
    out=$(perl -e 'alarm 15; exec @ARGV' $VMCTL $VM exec 'echo AGENT-UP $(sw_vers -buildVersion)' 2>/dev/null)
    [[ $out == AGENT-UP* ]] && { echo "start.sh: agent up after $(( $(date +%s) - t0 )) s (guest ${out#AGENT-UP })"; exit 0; }
    sleep 5
done
echo "start.sh: no agent after ${WAIT:-300} s"; exit 1
