#!/bin/zsh
# Laptop run of the Metal27 adapter test (MacBookPro9,2, Golden Gate 26B131 image). As the logged-in user, no root.
# r3: compute (as r2) plus offscreen render: a constant-colour triangle, a textured triangle and a source-compiled
# triangle, drawn into a 64x64 texture and read back pixel by pixel (src/test.m, run_render).
# r4: 3802's reflection in 27's layout (the stand-in cuts the input-stage table 27's reader no longer has). r3's render
# runs all aborted there: the image's libmetal-framebuffer.dylib, loaded by the HD 4000 driver, asks every render
# pipeline for reflection. New: reflection cases (HAO_REFLECTION=1, argument reflection printed and compared with the
# control's) and an A/B without the cut.
# Changes nothing on the system: every test process runs under sandbox-exec with all file writes denied except into
# this run's results folder; nothing is copied, swapped or installed; the OCLP files are only read.
# Usage: ./run.sh            -> results/<timestamp>/ (summary.txt first)
set -u
B=${0:A:h}
R=$B/results/$(date +%Y%m%d-%H%M%S)
mkdir -p $R
R=${R:A}
T=$B/bin/metal-adapter-test
say() { print -r -- "$*" | tee -a $R/run.log; }

# ---- 0. bundle integrity: refuse a partial or altered copy
cd $B
if ! /usr/bin/shasum -a 256 -c MANIFEST.sha256 > $R/manifest-check.txt 2>&1; then
  say "REFUSE: bundle files do not match MANIFEST.sha256 (see $R/manifest-check.txt)"; exit 2
fi
if [[ ! -x /usr/bin/sandbox-exec ]]; then say "REFUSE: /usr/bin/sandbox-exec missing; not running unsandboxed"; exit 2; fi
say "bundle OK; results in $R"

# ---- 1. what this system has (read only)
SL=/System/Library
OCLP=(
  $SL/Frameworks/Metal.framework/Versions/A/Metal
  $SL/Frameworks/Metal.framework/Versions/A/MetalOld.dylib
  $SL/Frameworks/Metal.framework/Versions/A/XPCServices/MTLCompilerService.xpc/Contents/MacOS/MTLCompilerService
  $SL/PrivateFrameworks/MTLCompiler.framework/Versions/3802/MTLCompiler
  $SL/PrivateFrameworks/MTLCompiler.framework/Versions/31001/MTLCompiler
  $SL/PrivateFrameworks/GPUCompiler.framework/Versions/3802/Libraries/libLLVM.dylib
  $SL/PrivateFrameworks/GPUCompiler.framework/Versions/31001/Libraries/libLLVM.dylib
  $SL/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver
  $SL/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/libMTLIntelCompilerPlugin.dylib
  $SL/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/libigdmd.dylib
  /usr/lib/libmetal-framebuffer.dylib
  /usr/lib/libmetalold-selectors.dylib
)
{
  print "== $(date)"; sw_vers; uname -a; sysctl -n hw.model machdep.cpu.brand_string; id -un
  print "\n== OCLP / Metal files used (sha256, type)"
  for f in $OCLP; do
    if [[ -e $f ]]; then print "$(/usr/bin/shasum -a 256 $f | cut -c1-64)  $f"; print "    $(/usr/bin/file -b $f)"
    else print "MISSING  $f"; fi
  done
  print "\n== MTLCompilerService version"
  /usr/bin/plutil -p $SL/Frameworks/Metal.framework/Versions/A/XPCServices/MTLCompilerService.xpc/Contents/version.plist 2>&1
  print "\n== bundle symlinks resolve to"
  ls -la $B/fw/Metal.framework/Versions/A/ $B/fw/Metal.framework/Versions/A/XPCServices/ 2>&1
  print "\n== the HD 4000 driver's load commands (weak: the image's framebuffer and selector dylibs)"
  /usr/bin/otool -L $SL/Extensions/AppleIntelHD4000GraphicsMTLDriver.bundle/Contents/MacOS/AppleIntelHD4000GraphicsMTLDriver 2>&1
  print "\n== IOAccelerator Metal plugin"
  /usr/sbin/ioreg -r -c IOAccelerator -d 1 -l 2>/dev/null | grep -E '"(MetalPluginName|MetalPluginClassName|IOClass|model)"'
} > $R/system.txt 2>&1
say "system info: $R/system.txt"
missing=0
for f in $OCLP[4,10]; do [[ -e $f ]] || { say "MISSING $f"; missing=1; }; done

CACHE=$(getconf DARWIN_USER_CACHE_DIR)
ls -la ${CACHE}com.apple.metal/ > $R/metal-cache-before.txt 2>&1
touch $R/.start

# ---- 2. one sandboxed process per case
PROFILE="(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath \"$R\") (literal \"/dev/null\") (literal \"/dev/tty\") (regex #\"^/dev/fd/\") (literal \"/dev/dtracehelper\"))"
COMMON=(HOME=$HOME PATH=/usr/bin:/bin HAO_TIMEOUT=120 DYLD_INSERT_LIBRARIES=$B/bin/q2log.dylib)
ADAPTER=(HAO_EXPECT=adapter DYLD_FRAMEWORK_PATH=$B/fw HAO_DOWNGRADE_TEMPLATE=$B/lib/downgrade-template.req7)
FLIP=(HAO_METAL27_INTERNAL=1 MTL_MONOLITHIC_COMPILER=1)
timeouts=0
run_case() {  # NAME KERNEL METALLIB ENV...
  local name=$1 k=$2 lib=$3; shift 3
  local d=$R/$name; mkdir -p $d/tmp $d/dump
  if (( timeouts >= 2 )); then say "$name: SKIPPED (two earlier timeouts: the GPU may be hung)"; return; fi
  # DYLD_* go through env's argv: a SIP-protected binary (sandbox-exec, env) drops them from its own environment
  # HAO_FE_MODULE_CACHE: the stand-in moves 31001's frontend module cache (27 points it at the user cache dir, which
  # the sandbox denies) into this run's folder; only the adapter-source run uses it
  /usr/bin/sandbox-exec -p "$PROFILE" /usr/bin/env -i $COMMON TMPDIR=$d/tmp/ HAO_STANDIN_DUMP=$d/dump \
    HAO_FE_MODULE_CACHE=$d/tmp/metalfe "$@" \
    $T $k $B/lib/$lib > $d/log.txt 2>&1
  local rc=$?
  local res=$(grep -m1 '^RESULT' $d/log.txt || print "RESULT kernel=$k status=NO-RESULT exit=$rc")
  local td=$(grep -m1 '^TEARDOWN' $d/log.txt || print "TEARDOWN -")
  [[ $res == *status=TIMEOUT* ]] && (( timeouts++ ))
  say "$(printf '%-26s exit=%-3s %s | %s' $name $rc "$res" "$td")"
}

say "---- control: the system's Metal (OCLP wrapper + MetalOld, XPC MTLCompilerService)"
run_case control-add1            add1            kernels-27.metallib HAO_EXPECT=control
run_case control-saxpy           saxpy_long_name kernels-27.metallib HAO_EXPECT=control
run_case control-source          source          kernels-27.metallib HAO_EXPECT=control
run_case control-11-add1         add1            kernels-11.metallib HAO_EXPECT=control
run_case control-11-saxpy        saxpy_long_name kernels-11.metallib HAO_EXPECT=control
run_case control-render-color    render-color    render-27.metallib  HAO_EXPECT=control
run_case control-render-texture  render-texture  render-27.metallib  HAO_EXPECT=control
run_case control-render-source   render-source   render-27.metallib  HAO_EXPECT=control
run_case control-reflect-add1    add1            kernels-27.metallib HAO_EXPECT=control HAO_REFLECTION=1
run_case control-reflect-render-texture render-texture render-27.metallib HAO_EXPECT=control HAO_REFLECTION=1
if (( missing )) && [[ -z ${HAO_RUN_ALL:-} ]]; then  # HAO_RUN_ALL=1: host plumbing test only
  say "adapter runs SKIPPED: an OCLP file they use is missing (see above)"
else
  say "---- adapter: 27.2.1's own Metal (bundle) + shim, in-process compile through OCLP 31001 + 3802"
  run_case adapter-add1          add1            kernels-27.metallib $ADAPTER $FLIP
  run_case adapter-saxpy         saxpy_long_name kernels-27.metallib $ADAPTER $FLIP
  run_case adapter-source        source          kernels-27.metallib $ADAPTER $FLIP
  run_case adapter-11-add1       add1            kernels-11.metallib $ADAPTER $FLIP
  run_case adapter-11-saxpy      saxpy_long_name kernels-11.metallib $ADAPTER $FLIP
  say "---- adapter, render: offscreen draws read back (the shim hands the driver MetalOld-layout descriptor structs)"
  run_case adapter-render-color      render-color   render-27.metallib $ADAPTER $FLIP
  run_case adapter-render-texture    render-texture render-27.metallib $ADAPTER $FLIP
  run_case adapter-render-source     render-source  render-27.metallib $ADAPTER $FLIP
  run_case adapter-11-render-color   render-color   render-11.metallib $ADAPTER $FLIP
  run_case adapter-11-render-texture render-texture render-11.metallib $ADAPTER $FLIP
  say "---- adapter, reflection asked for (MTLPipelineOptionArgumentInfo): compared with control-reflect-*"
  run_case adapter-reflect-add1           add1           kernels-27.metallib $ADAPTER $FLIP HAO_REFLECTION=1
  run_case adapter-reflect-render-texture render-texture render-27.metallib  $ADAPTER $FLIP HAO_REFLECTION=1
  say "---- A/B: render without the descriptor translation (expected: the driver misreads 27's structs, CRASH)"
  run_case adapter-nolayouts-render-color render-color render-27.metallib $ADAPTER $FLIP HAO_METAL27_LAYOUTS=0
  say "---- A/B: 3802's reflection unchanged (expected: CRASH in 27's reflection reader, as every r3 render run)"
  run_case adapter-noreflection-render-color render-color render-27.metallib $ADAPTER $FLIP HAO_STANDIN_REFLECTION=0
  say "---- adapter, device created by hand (driver bundle + -initWithAcceleratorPort:)"
  run_case adapter-manual-add1   add1            kernels-27.metallib $ADAPTER $FLIP HAO_DEVICE=manual
  run_case adapter-manual-saxpy  saxpy_long_name kernels-27.metallib $ADAPTER $FLIP HAO_DEVICE=manual
  say "---- variant: 27's Metal through the system's x86_64 MTLCompilerService (no isInternalBuild flip)"
  run_case xpc-add1              add1            kernels-27.metallib $ADAPTER
  run_case xpc-saxpy             saxpy_long_name kernels-27.metallib $ADAPTER
fi

# ---- 3. evidence that nothing outside the bundle was written by the tests
ls -la ${CACHE}com.apple.metal/ > $R/metal-cache-after.txt 2>&1
{
  print "Metal cache dir ${CACHE}com.apple.metal/ before/after:"
  diff $R/metal-cache-before.txt $R/metal-cache-after.txt && print "  unchanged"
  print "\nMetal-related files newer than the run start under the user cache and temp dirs (expected: none):"
  find $CACHE $(getconf DARWIN_USER_TEMP_DIR) -newer $R/.start -type f 2>/dev/null | grep -i -e metal -e mtl | head -50
  print "\nNew crash reports (written by the system, e.g. for MTLCompilerService):"
  find $HOME/Library/Logs/DiagnosticReports -newer $R/.start -type f 2>/dev/null
} > $R/writes-check.txt 2>&1
for f in $(find $HOME/Library/Logs/DiagnosticReports -newer $R/.start -type f 2>/dev/null); do cp $f $R/; done
/usr/bin/timeout 180 /usr/bin/log show --style compact --start "$(date -r $(stat -f %m $R/.start) '+%Y-%m-%d %H:%M:%S')" \
  --predicate 'process == "metal-adapter-test" OR process == "MTLCompilerService" OR (process == "kernel" AND eventMessage CONTAINS "metal-adapter-test")' \
  > $R/unified-log.txt 2>&1

# ---- 4. summary
{
  print "Metal27 adapter laptop test, $(date)"; print "results: $R"; print
  grep -E '^[a-z0-9-]+ +exit=' $R/run.log
  print
  pass() { grep -q "status=PASS" $R/$1/log.txt 2>/dev/null; }
  if pass control-add1 && pass control-saxpy; then ctl=PASS; else ctl=FAIL; fi
  if pass adapter-add1 && pass adapter-saxpy; then ad=PASS
  elif pass adapter-manual-add1 && pass adapter-manual-saxpy; then ad=PASS-MANUAL; else ad=FAIL; fi
  if pass xpc-add1 && pass xpc-saxpy; then xpc=PASS; else xpc=FAIL; fi
  if pass control-render-color && pass control-render-texture; then rctl=PASS; else rctl=FAIL; fi
  if pass adapter-render-color && pass adapter-render-texture; then rad=PASS; else rad=FAIL; fi
  refl() { grep '^\[test\] reflection' $R/$1/log.txt 2>/dev/null; }
  same_refl() { pass control-reflect-$1 && pass adapter-reflect-$1 && [[ -n "$(refl control-reflect-$1)" ]] &&
                [[ "$(refl control-reflect-$1)" == "$(refl adapter-reflect-$1)" ]]; }
  if same_refl add1 && same_refl render-texture; then rfl=PASS; else rfl=FAIL; fi
  print "control (system Metal = OCLP wrapper + MetalOld): $ctl"
  print "adapter (27.2.1 Metal, in-process OCLP 31001 + 3802): $ad"
  print "xpc variant (27.2.1 Metal, system MTLCompilerService, no flip): $xpc"
  print "render control (system Metal): $rctl"
  print "render adapter (27.2.1 Metal): $rad"
  print "reflection (adapter PASS, argument reflection identical to the control's): $rfl"
  case "$ctl/$ad" in
    PASS/PASS) print "=> YES: macOS 27's own Metal ran both kernels on the HD 4000 with correct results (compute).";;
    PASS/PASS-MANUAL) print "=> YES with a hand-made device: compile and execution work; MTLCreateSystemDefaultDevice did not give the device (see adapter-add1).";;
    PASS/FAIL) print "=> NOT YET: the system's own path works here, the adapter does not; read adapter-*/log.txt.";;
    *) print "=> INCONCLUSIVE: the control failed, so this image's Metal does not run these kernels either.";;
  esac
  case "$rctl/$rad" in
    PASS/PASS) print "=> RENDER YES: macOS 27's own Metal drew a solid and a textured triangle on the HD 4000; every pixel read back matched.";;
    PASS/FAIL) print "=> RENDER NOT YET: the system's own path draws here, the adapter does not; read adapter-render-*/log.txt.";;
    *) print "=> RENDER INCONCLUSIVE: the control failed, so this image's Metal does not draw these either.";;
  esac
  print "\nnothing-written check: $R/writes-check.txt"
} > $R/summary.txt
rm -f $R/.start
cat $R/summary.txt
