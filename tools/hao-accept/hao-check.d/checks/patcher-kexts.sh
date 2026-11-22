#!/bin/sh
# hao-check plug-in, patcher: the OpenCore kexts load or stay out as the image's config intends (PA1).
# Run as root over SSH on the laptop's dev image (POSIX sh), with OUT, CUSER, CUID set (see checks/README). Also runs in
# QEMU (the generic image's config). It mounts the boot disk's ESP read-only when it is not mounted and unmounts it
# after; it changes nothing else and writes only under $OUT.
# Checks: kexts-esp (the config is found), kexts-loaded (every enabled Kernel.Add entry with an executable whose
# MinKernel/MaxKernel cover this kernel is loaded with the ESP bundle's version), kexts-not-loaded (no Enabled-false
# entry's ESP copy is loaded), kexts-blocked (each enabled Kernel.Block identifier is either not loaded or loaded with
# the version of the ESP's replacement).
PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/libexec
export PATH
LC_ALL=C
export LC_ALL
NAME=patcher-kexts
if [ -z "${OUT:-}" ]; then echo "HAOCHECK CHECK env FAIL OUT is not set"; echo "HAOCHECK END $NAME"; exit 2; fi
if [ -z "${CUSER:-}" ]; then CUSER=$(stat -f %Su /dev/console 2> /dev/null); fi
if [ -z "${CUID:-}" ] && [ -n "$CUSER" ]; then CUID=$(id -u "$CUSER" 2> /dev/null); fi
E="$OUT/$NAME"
mkdir -p "$E"

oneline() { tr '\n\t' '  ' | tr -s ' ' | sed 's/^ //; s/ $//' | cut -c1-400; }
result() {   # result CHECK STATUS DETAIL [ISSUE]
  _d=$(printf '%s' "$3" | oneline)
  if [ -n "${4:-}" ]; then printf 'HAOCHECK CHECK %s %s %s issue=%s\n' "$1" "$2" "$_d" "$4"
  else printf 'HAOCHECK CHECK %s %s %s\n' "$1" "$2" "$_d"; fi
}
# bounded SECS CMD...: run CMD, kill it after SECS seconds; returns its status, or 124 on timeout.
bounded() {
  _s=$1; shift
  "$@" &
  _p=$!
  ( sleep "$_s"; kill -KILL "$_p" 2> /dev/null ) > /dev/null 2>&1 < /dev/null &
  _w=$!
  wait "$_p"; _rc=$?
  kill "$_w" 2> /dev/null; wait "$_w" 2> /dev/null
  [ $_rc -eq 137 ] && return 124
  return $_rc
}
as_user() { launchctl asuser "$CUID" sudo -u "$CUSER" "$@"; }
# crashes_since_boot REGEX: crash and panic report files written since boot whose process name matches REGEX.
crashes_since_boot() {
  _b=$(sysctl -n kern.boottime 2> /dev/null | sed -E 's/.*sec = ([0-9]+).*/\1/')
  [ -n "$_b" ] || return 0
  touch -t "$(date -r "$_b" +%Y%m%d%H%M.%S)" "$E/.boot.ref"
  for _d in /Library/Logs/DiagnosticReports "/Users/$CUSER/Library/Logs/DiagnosticReports"; do
    [ -d "$_d" ] && find "$_d" -maxdepth 2 -type f \( -name '*.ips' -o -name '*.crash' -o -name '*.panic' \) -newer "$E/.boot.ref" 2> /dev/null
  done | while read -r _f; do
    _n=$(basename "$_f")
    printf '%s\n' "$_n" | grep -E -q "^($1)[-_.]" && printf '%s\n' "$_n"
  done
}
# loaded_kexts FILE: "bundle-id (version)" for every loaded kext.
loaded_kexts() {
  { bounded 60 kmutil showloaded --list-only 2> /dev/null || bounded 60 kextstat -l 2> /dev/null; } |
    awk '{for (i = 1; i < NF; i++) if ($(i+1) ~ /^\([0-9]/) {v = $(i+1); gsub(/[()]/, "", v); print $i, v}}' | sort -u > "$1"
}


MOUNTED=
cleanup() { [ -n "$MOUNTED" ] && diskutil unmount "$MOUNTED" > /dev/null 2>&1; }
trap cleanup EXIT

store=$(diskutil info / 2> /dev/null | awk -F': *' '/APFS Physical Store/ {print $2; exit}')
disk=$(printf '%s' "$store" | sed -E 's/s[0-9]+$//')
esp=$(diskutil list "$disk" 2> /dev/null | awk '$2 == "EFI" {print $NF; exit}')
mp=$(diskutil info "$esp" 2> /dev/null | awk -F': *' '/Mount Point/ {print $2; exit}')
if [ -n "$esp" ] && [ -z "$mp" ]; then
  mkdir -p "$E/esp"
  if bounded 60 diskutil mount readOnly -mountPoint "$E/esp" "$esp" > "$E/mount.txt" 2>&1; then mp="$E/esp"; MOUNTED="$E/esp"; fi
fi
CFG="$mp/EFI/OC/config.plist"
K="$mp/EFI/OC/Kexts"
if [ -z "$mp" ] || [ ! -f "$CFG" ]; then
  result kexts-esp FAIL "no OpenCore config on the boot disk's ESP (store ${store:-?}, ESP ${esp:-?}, mount ${mp:-none})"
  echo "HAOCHECK END $NAME"; exit 0
fi
cp "$CFG" "$E/config.plist"
result kexts-esp PASS "$esp: $CFG"

kv() { printf '%s' "$1" | awk -F. '{printf "%d", $1 * 10000 + $2 * 100 + $3}'; }
KERN=$(kv "$(uname -r)")
loaded_kexts "$E/loaded.txt"
ver_loaded() { awk -v id="$1" '$1 == id {print $2; exit}' "$E/loaded.txt"; }
px() { plutil -extract "$1" raw -o - "$2" 2> /dev/null; }

missing= ; wrongver= ; strays= ; nexp=0 ; ncodeless=0 ; nout=0 ; ndis=0
i=0
: > "$E/esp-kexts.txt"
while b=$(px "Kernel.Add.$i.BundlePath" "$CFG"); do
  en=$(px "Kernel.Add.$i.Enabled" "$CFG"); ex=$(px "Kernel.Add.$i.ExecutablePath" "$CFG")
  pp=$(px "Kernel.Add.$i.PlistPath" "$CFG"); mn=$(px "Kernel.Add.$i.MinKernel" "$CFG"); mx=$(px "Kernel.Add.$i.MaxKernel" "$CFG")
  i=$((i + 1))
  id=$(px CFBundleIdentifier "$K/$b/$pp"); v=$(px CFBundleVersion "$K/$b/$pp")
  printf '%s\t%s\t%s\t%s\t%s-%s\t%s\n' "$b" "$en" "$id" "$v" "$mn" "$mx" "$ex" >> "$E/esp-kexts.txt"
  if [ "$en" != true ]; then
    ndis=$((ndis + 1))
    lv=$(ver_loaded "$id")
    [ -n "$id" ] && [ -n "$lv" ] && [ "$lv" = "$v" ] && strays="$strays ${b##*/}($lv)"
    continue
  fi
  if [ -z "$ex" ]; then ncodeless=$((ncodeless + 1)); continue; fi
  lo=0; hi=999999
  [ -n "$mn" ] && lo=$(kv "$mn"); [ -n "$mx" ] && hi=$(kv "$mx")
  if [ "$KERN" -lt "$lo" ] || [ "$KERN" -gt "$hi" ]; then nout=$((nout + 1)); continue; fi
  nexp=$((nexp + 1))
  lv=$(ver_loaded "$id")
  if [ -z "$id" ]; then missing="$missing ${b##*/}(no-Info.plist)"
  elif [ -z "$lv" ]; then missing="$missing ${b##*/}"
  elif [ "$lv" != "$v" ]; then wrongver="$wrongver ${b##*/}(loaded:$lv,esp:$v)"
  fi
done
d="$nexp expected loaded ($ncodeless codeless and $nout outside this kernel's range not counted)"
if [ -z "$missing$wrongver" ]; then result kexts-loaded PASS "$d"
else result kexts-loaded FAIL "$d; not loaded:${missing:- none}; another version loaded:${wrongver:- none}"; fi
if [ -z "$strays" ]; then result kexts-not-loaded PASS "$ndis disabled entries, none of their ESP copies loaded"
else result kexts-not-loaded FAIL "disabled in the config but loaded:$strays"; fi

blocked= ; bad= ; j=0
while bid=$(px "Kernel.Block.$j.Identifier" "$CFG"); do
  ben=$(px "Kernel.Block.$j.Enabled" "$CFG"); j=$((j + 1))
  [ "$ben" = true ] || continue
  blocked="$blocked $bid"
  lv=$(ver_loaded "$bid"); [ -z "$lv" ] && continue
  rv=$(awk -F'\t' -v id="$bid" '$2 == "true" && $3 == id {print $4; exit}' "$E/esp-kexts.txt")
  [ "$lv" = "$rv" ] || bad="$bad $bid(loaded:$lv,esp:${rv:-none})"
done
if [ -z "$blocked" ]; then result kexts-blocked SKIP "no enabled Kernel.Block entry"
elif [ -z "$bad" ]; then result kexts-blocked PASS "blocked:$blocked; only the ESP's replacement (or nothing) loaded"
else result kexts-blocked FAIL "the OS's copy loaded:$bad"; fi

echo "HAOCHECK END patcher-kexts"
