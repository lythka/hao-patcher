#!/bin/zsh
# inputs.sh SHARE TARGET LOCK COMMON OCLP SET: assemble the guest inputs into SHARE, the folder the builder VM sees over
# virtiofs (use a new SHARE per run: the guest's virtiofs view caches files that change in place).
#   SHARE/pristine      payload.yaa, links.txt, firmlinks_payload (unpack), sys/data lists, firmlinks + root_uuid metadata, tools
#   SHARE/oclp    OCLP source at commit OCLP (git archive of $OCLP_REPO) + the Universal-Binaries.dmg it expects
#   SHARE/kdk     the build's KDK, expanded (System/Library/Extensions + SystemVersion.plist), cached in $CACHE
#   SHARE/py SHARE/py-x86_64 SHARE/site   arm64 and x86_64 CPython + the site-packages OCLP imports (universal)
#   SHARE/patches.txt   the set's image patches that apply to this target and build (pipeline/patches.py plan)
#   SHARE/optional.json the set's optional patches that ran or were skipped, with the reason
#   SHARE/hooks/NN-<name>  the set's oclp-hook patches (patches/<name>/src, OCLP runs its hook.sh SYS DATA) and
#   SHARE/post/NN-<name>   post patches (patches/<name>), NN = set order; each with inputs/<row> for its lock rows and,
#                          when the patch has make.sh, build/ from `sh make.sh build` on the host (as pipeline/recovery)
#   SHARE/profile.json    the target's OCLP hardware profile
set -eu
SHARE=${1:?share}; TARGET=${2:?target json}; LOCK=${3:?inputs lock}; COMMON=${4:?common lock}; OCLP=${5:?OCLP commit}; SET=${6:?set}
HERE=${0:A:h}; TOP=${HERE:h:h}; L=(python3 -I $TOP/pipeline/lock.py)
CACHE=${CACHE:-$TOP/store/cache}; OCLP_REPO=${OCLP_REPO:-$TOP/store/oclp}
in() { $L path $LOCK $1; }; common() { $L path $COMMON $1; }

FOUNDATION=${7:-legacy}
case $FOUNDATION in legacy|golden-first) ;; *) print -u2 "unknown foundation: $FOUNDATION"; exit 2 ;; esac
KDKDMG=$(in kdk)
mkdir -p $SHARE/pristine $SHARE/oclp $SHARE/kdk $SHARE/hooks $SHARE/post
cp -p $HERE/copy-hooks.py $SHARE/copy-hooks.py
if [[ $FOUNDATION == legacy ]]; then
    UNPACK=$(in unpack); LISTS=$(in lists)
    cp -c $UNPACK/payload.yaa $UNPACK/zip/AssetData/payloadv2/{links.txt,firmlinks_payload} $SHARE/pristine/
    cp -c $LISTS/{sys,data,data-symlinks}.tsv $SHARE/pristine/
    cp $HERE/firmlinks.tsv $SHARE/pristine/meta17.tsv; cp $HERE/rootuuid.tsv $SHARE/pristine/
    for t in autool hmeta fl fl-noent; do cp -c $(common $t) $SHARE/pristine/; done
else
    # Golden-first assembly reads its filesystem and donor artifacts explicitly.
    # Only the generic entitled firmlink tool is shared with pristine assembly.
    cp -c $(common fl) $SHARE/pristine/fl
fi

git -C $OCLP_REPO archive $OCLP | tar -x -C $SHARE/oclp
cp $(common psp_$($L psp $SHARE/oclp)) $SHARE/oclp/Universal-Binaries.dmg

KDK=$($L kdk-name $KDKDMG); K=$CACHE/kdk-${${KDK:r}##*_}
if [[ ! -f $K/System/Library/CoreServices/SystemVersion.plist ]]; then
    W=$(mktemp -d); mkdir $W/m
    hdiutil attach -readonly -nobrowse -mountpoint $W/m $KDKDMG >/dev/null
    pkgutil --expand-full $W/m/KernelDebugKit.pkg $W/x >/dev/null; hdiutil detach $W/m >/dev/null
    mkdir -p $K/System/Library/CoreServices
    ditto $W/x/KDK.pkg/Payload/System/Library/Extensions $K/System/Library/Extensions
    cp $W/x/KDK.pkg/Payload/System/Library/CoreServices/SystemVersion.plist $K/System/Library/CoreServices/
    rm -rf $W
fi
ditto $K $SHARE/kdk/$KDK

# Both CPythons: SHARE/py (arm64) and SHARE/py-x86_64; the assembly copies the one for the Mac it runs on to its py/.
for arch dir in arm64 py x86_64 py-x86_64; do
    PY=$CACHE/python/$arch
    [[ -x $PY/python/bin/python3.12 ]] || { rm -rf $PY; mkdir -p $PY; tar -xzf $(common python_$arch) -C $PY; }
    ditto $PY/python $SHARE/$dir
done
ditto $(common python_site) $SHARE/site

# A patch input: the Golden Gate build's row (GOLDEN_LOCK, golden-first only), else the donor build's, else common's.
# Optional patches whose rows are not there, or whose group is in SKIP_OPTIONAL (space-separated), are left out of the
# plan; SHARE/optional.json says which ran and which were skipped, and why.
GOLDEN_LOCK=${GOLDEN_LOCK:-}
skip=(); for g in ${=SKIP_OPTIONAL:-}; do skip+=(--skip-optional $g); done
python3 -I $TOP/pipeline/patches.py plan $SET $TARGET ${LOCK:t:r} --locks ${GOLDEN_LOCK:+$GOLDEN_LOCK:}$LOCK:$COMMON \
    $skip --record $SHARE/optional.json > $SHARE/plan.txt
awk '$1 != "recovery"' $SHARE/plan.txt > $SHARE/patches.txt; rm $SHARE/plan.txt   # recovery patches: pipeline/recovery
row() { if [[ -n $GOLDEN_LOCK && -f $GOLDEN_LOCK/$1 ]]; then $L path $GOLDEN_LOCK $1; elif [[ -f $LOCK/$1 ]]; then in $1; else common $1; fi; }
i=0
while read -r stage name ins; do
    i=$((i + 1))
    recipe=$(python3 -I $TOP/pipeline/patches.py path $name)
    if [[ $stage == oclp-hook ]]; then d=$SHARE/hooks/$(printf %02d $i)-$name; ditto $recipe/src $d
    else d=$SHARE/post/$(printf %02d $i)-$name; ditto $recipe $d; fi
    [[ ! -f $recipe/make.sh ]] || sh $recipe/make.sh $d/build   # host build of the patch's binaries, as pipeline/recovery
    mkdir -p $d/inputs
    # Bundle mtimes participate in IconServices identities. Preserve the artifact's
    # metadata when cloning it into the share, so its generated indexes stay valid.
    for n in ${=ins}; do cp -Rpc $(row $n) $d/inputs/$n; done
done < $SHARE/patches.txt
$L target $TARGET oclp_hardware_profile > $SHARE/profile.json
{
    echo "oclp $(git -C $OCLP_REPO rev-parse $OCLP) ($OCLP), PatcherSupportPkg $($L psp $SHARE/oclp)"
    echo "kdk $KDK ($(shasum -a 256 $KDKDMG | cut -c1-16))"
    echo "set ${SET:t:r}: $(python3 -I $TOP/pipeline/patches.py revisions $(cut -d' ' -f2 $SHARE/patches.txt))"
    echo "foundation $FOUNDATION"
} > $SHARE/MANIFEST.txt
cat $SHARE/MANIFEST.txt
