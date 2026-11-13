"""Bind the kernel repairs to the BootKC this image just linked, with tools/kc-binder, and check them against the row.

  kc_bind.py --binder STAGE --boot-kc KC --row ROW --profile PROFILE --abi-header HEADER
             [--row-bound DIR] --out DIR --record FILE

STAGE is tools/kc-binder as make.sh staged it (stage.json). ROW is the kernel_repair_efi input, PROFILE the
kernel_repair_binding profile.json, HEADER the commpage_abi.hpp IntelCommpage.kext was compiled with
(intel_commpage_abi). DIR (--row-bound) is src/repair_binding.py's output when the recorded repairs bound to KC;
leave it empty when repair_binding.py refused KC.

OUT is an artifact in the layout src/repairs.py installs from (build.json, EFI/OC/config.plist and
EFI/OC/Kexts/IntelCommpage.kext), plus capture.json and commpage-table.json. Which repairs it holds:

- binder: kc-binder computed them from KC, and they equal the row's bound artifact byte for byte (config.plist, every
  patch field and the capture) and the profile's patch sites and IntelCommpage bindings. Any difference refuses.
  IntelCommpage's address table, written for KC, must equal the one the kext already holds. This is every image
  built today: the installed files are the row's.
- binder-only: repair_binding.py refused KC (a collection the row was not recorded for), and kc-binder bound it.
  An IntelCommpage.kext with the load-time table (__DATA,__ic_table) gets KC's table written into its binary; the
  lines of HEADER that are code facts rather than table entries (kcbind.CODE_LINES) must still equal what kc-binder
  computes for KC. A kext from before the table has the collection addresses compiled in, so every
  collection-dependent line of HEADER must equal KC's. Otherwise this refuses and names the lines.
- row: no kc-binder recipe names KC's kernel (e.g. 26A428); the row's bound artifact is used as before.

In every mode the row's IntelCommpage.kext files must be the ones the profile lists; only binder-only can change the
installed binary, and only inside its table (the record's commpage_table says whether it did). Exit status 1 with a
message naming the check on any refusal; OUT is written only when every check passes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import plistlib
import runpy
import shutil
import sys

KEXT = 'EFI/OC/Kexts/IntelCommpage.kext'
BINARY = 'Contents/MacOS/IntelCommpage'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Refused(ValueError):
    pass


def binder_modules(stage):
    """(kcbind, compare, commpage_build namespace, stage record) from a staged kc-binder, after checking the stage."""
    stage = Path(stage).resolve()
    src = stage/'tools/kc-binder/src'
    record = json.loads((stage/'stage.json').read_text())
    for relative, digest in sorted(record['files'].items()):
        if sha((stage/relative).read_bytes()) != digest:
            raise Refused('staged kc-binder file %s differs from stage.json' % relative)
    sys.path.insert(0, str(src))
    import kcbind
    import compare
    if Path(kcbind.__file__).resolve().parent != src or Path(compare.__file__).resolve().parent != src:
        raise Refused('kcbind/compare did not load from the staged binder')
    return kcbind, compare, runpy.run_path(str(stage/'tools/kernel-foundation/commpage_build.py')), record


def kext_files(artifact):
    root = Path(artifact)
    return {str(p.relative_to(root)): sha(p.read_bytes()) for p in sorted((root/KEXT).rglob('*')) if p.is_file()}


def check_kext(row, profile):
    expected = {k: v for k, v in profile['payload_files'].items() if k.startswith(KEXT+'/')}
    if not expected or kext_files(row) != expected:
        raise Refused('IntelCommpage.kext in the row differs from the files the binding profile lists')


def bad_rows(rows):
    return ['%s: %s' % (what, detail) for what, same, detail in rows if not same]


def bind(stage, collection, row, profile_path, header_path, row_bound, out):
    """The record of the binding; OUT written as described above."""
    row, out, stage = Path(row), Path(out), Path(stage).resolve()
    profile = json.loads(Path(profile_path).read_text())
    header = Path(header_path).read_bytes()
    if sha(header) != profile['abi_header_sha256']:
        raise Refused('the commpage_abi.hpp given is not the one the binding profile (and IntelCommpage.kext) pins')
    check_kext(row, profile)
    kcbind, compare, foundation, staged = binder_modules(stage)
    data = Path(collection).read_bytes()
    kc = kcbind.Collection(data)
    recipes = [kcbind.Recipe(p, stage) for p in sorted((stage/'tools/kc-binder/recipes').glob('*.json'))]
    record = dict(unit='patches/hao-opencore', operation='kc-bind', date=datetime.now(timezone.utc).isoformat(),
                  boot_kc_sha256=sha(data), kernel_uuid=kc.kernel.uuid.hex() if kc.kernel.uuid else None,
                  row_record_sha256=sha((row/'build.json').read_bytes()), profile_sha256=sha(Path(profile_path).read_bytes()),
                  abi_header_sha256=sha(header), binder=staged['files'], row_bound=bool(row_bound))
    try:
        recipe = kcbind.select(recipes, kc)
    except kcbind.BindError as error:
        if not str(error).startswith('MISSING') or not row_bound:
            raise Refused('kc-binder: %s%s' % (error, '' if row_bound else
                                               '; and the recorded repairs did not bind to this BootKC either'))
        shutil.copytree(row_bound, out)
        record.update(mode='row', reason=str(error), files=files(out))
        return record
    patches, capture, report = kcbind.bind_repairs(recipe, kc)
    table = kcbind.commpage_table(recipe, kc)
    config = kcbind.config_plist(patches)
    reference = plistlib.loads((row/'EFI/OC/config.plist').read_bytes())
    if [(p['Comment'], p['Identifier']) for p in patches] != [(p['Comment'], p['Identifier'])
                                                              for p in reference['Kernel']['Patch']]:
        raise Refused('kc-binder computed other repairs than the row holds')
    if plistlib.loads(config)['Kernel']['Add'] != reference['Kernel']['Add']:
        raise Refused('kc-binder writes another IntelCommpage Kernel.Add entry than the row')
    header_rows = compare.abi_header(table, header.decode(), foundation)
    header_bad = bad_rows(header_rows)
    binary = (row/KEXT/BINARY).read_bytes()
    load_time = foundation['find_table'](binary) is not None
    if load_time:
        # IntelCommpage reads its collection addresses from __DATA,__ic_table: write this BootKC's.
        try:
            stamped, table_data = kcbind.stamp(recipe, table, foundation, binary)
        except kcbind.BindError as error:
            raise Refused('IntelCommpage.kext table: %s' % error)
        code_bad = bad_rows(row for row in header_rows if row[0].split()[-1] in kcbind.CODE_LINES)
    else:
        stamped, table_data, code_bad = binary, None, header_bad
    if row_bound:
        bound = Path(row_bound)
        if config != (bound/'EFI/OC/config.plist').read_bytes():
            raise Refused('kc-binder config.plist differs from the recorded repairs bound to this BootKC')
        differ = bad_rows(compare.repairs(patches, capture, kc, bound))
        differ += bad_rows(compare.binding(patches, table, kc, profile))
        differ += header_bad
        if stamped != binary:
            differ.append('IntelCommpage table: this BootKC gives another table than the kext holds')
        if differ:
            raise Refused('kc-binder and the recorded repairs differ for this BootKC: ' + '; '.join(differ))
        if kext_files(bound) != kext_files(row):
            raise Refused('the bound IntelCommpage.kext differs from the row')
        mode = 'binder'
    else:
        if code_bad:
            raise Refused('IntelCommpage.kext was compiled for another collection %s; rebuild it for this '
                          'BootKC (tools/kernel-foundation): %s' % ('kernel code' if load_time else 'layout',
                                                                     '; '.join(code_bad)))
        mode = 'binder-only'
    out.mkdir(parents=True, exist_ok=False)
    (out/'EFI/OC').mkdir(parents=True)
    (out/'EFI/OC/config.plist').write_bytes(config)
    shutil.copytree(row/KEXT, out/KEXT, copy_function=shutil.copy2)
    if stamped != binary:
        (out/KEXT/BINARY).write_bytes(stamped)
    record['commpage_table'] = dict(load_time=load_time, restamped=stamped != binary,
                                    sha256=sha(table_data) if table_data else None,
                                    kext_binary_sha256=dict(row=sha(binary), installed=sha(stamped)))
    capture_bytes = (json.dumps(capture, indent=2)+'\n').encode()
    (out/'capture.json').write_bytes(capture_bytes)
    (out/'commpage-table.json').write_text(json.dumps(table, indent=1)+'\n')
    replacements = [dict(comment=p['Comment'], find=p['Find'].hex(), replace=p['Replace'].hex()) for p in patches]
    early = dict(capture=capture, capture_sha256=sha(capture_bytes), replacements=replacements)
    record.update(mode=mode, recipe=str(Path(recipe.path).relative_to(stage)),
                  points=report, patches=[dict(comment=p['Comment'], offset=hex(data.index(p['Find'])),
                                               length=len(p['Find']), find_sha256=sha(p['Find']),
                                               replace_sha256=sha(p['Replace'])) for p in patches])
    build = dict(unit='patches/hao-opencore', operation='kc-bind', mode=mode, inputs=dict(early_vm_repair=early),
                 validation='Static binding; boot of this collection is a separate check')
    (out/'build.json').write_text(json.dumps(build, indent=2)+'\n')
    record['files'] = files(out)
    return record


def files(out):
    return {str(p.relative_to(out)): sha(p.read_bytes()) for p in sorted(Path(out).rglob('*')) if p.is_file()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ('binder', 'boot-kc', 'row', 'profile', 'abi-header', 'out', 'record'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--row-bound', default='', help='repair_binding.py output; empty when it refused the BootKC')
    args = parser.parse_args(argv)
    try:
        record = bind(args.binder, args.boot_kc, args.row, args.profile, args.abi_header,
                      Path(args.row_bound) if args.row_bound else None, args.out)
    except (OSError, ValueError, KeyError) as error:
        print('kc_bind: %s' % error, file=sys.stderr)
        return 1
    args.record.write_text(json.dumps(record, indent=2)+'\n')
    print('kc_bind: %s (%s)' % (record['mode'], args.out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
