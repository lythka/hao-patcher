#!/bin/sh
# Runs ON THE LAPTOP in the NetBoot Recovery, as root.
#   sh spike.sh T NAME [ARGS]
# T is the spike's RAM disk (common.sh setup). Checks every uploaded input against T/inputs/manifest.tsv (the
# host's sha256), unpacks the bundled Python and the code tar onto T, checks that Python starts, then runs
# T/code/tools/recovery-spikes/remote/NAME_spike.py T ARGS with it (python -I -B).
set -u
T=${1:?T}; name=${2:?NAME}; shift 2
cd "$T" || { echo "END $name rc=1"; exit 1; }
tab=$(printf '\t'); bad=0; n=0
while IFS=$tab read -r file sha size; do
    n=$((n + 1))
    have=$(shasum -a 256 "inputs/$file" 2>/dev/null | cut -d' ' -f1)
    [ "$have" = "$sha" ] || { echo "CHECK upload FAIL inputs/$file is '$have', want $sha"; bad=1; }
done < inputs/manifest.tsv
[ $bad = 0 ] || { echo "END $name rc=1"; exit 1; }
echo "CHECK upload PASS $n files match the host's sha256"
t0=$(date +%s)
if ! { tar -xzf inputs/python.tar.gz -C "$T" && mkdir -p code && tar -xf inputs/code.tar -C code; }; then
    echo "CHECK unpack FAIL python or code archive"; echo "END $name rc=1"; exit 1
fi
PY=$T/python/bin/python3
echo "TIME unpack $(( $(date +%s) - t0 ))"
t0=$(date +%s)
v=$("$PY" -I -c 'import platform, sys; print(sys.version.split()[0], platform.machine(), sys.platform)' 2>&1); rc=$?
if [ $rc = 0 ] && [ "$v" = "3.12.14 x86_64 darwin" ]; then
    echo "CHECK python-starts PASS $v"
else
    echo "CHECK python-starts FAIL rc=$rc: $v"; echo "END $name rc=1"; exit 1
fi
echo "TIME python-start $(( $(date +%s) - t0 ))"
exec "$PY" -I -B "$T/code/tools/recovery-spikes/remote/${name}_spike.py" "$T" "$@"
