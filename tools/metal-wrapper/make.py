#!/usr/bin/env python3
"""make.py: build hao's Metal wrapper, the x86_64 Metal.framework/Versions/A/Metal for the legacy Metal stack.

  make.py generate SPEC OUT.m [--sdk PATH]                write the stubs for SPEC's classes, functions and data
  make.py build SPEC --metalold METALOLD --out DIR [--sdk PATH] [--extra FILE.m ...]

The wrapper re-exports MetalOld.dylib (Apple's legacy Metal, shipped by OCLP's PatcherSupportPkg; the builder
downloads it, we never redistribute it) and adds what newer clients import but MetalOld lacks. SPEC is JSON:
{"classes": [NAME...], "functions": [NAME...], "data": [NAME...], "drop_present_in": "metalold"}. Every stub is
generated from the SDK's own Metal headers (clang's syntax tree), so signatures are Apple's:
  classes    @implementation with the header's superclass; properties synthesized; every other method declared
             in the header returns zero (init* returns [super init]); NSCopying adopters copy by retain
  functions  the header's prototype, returning zero
  data       NSString constants hold their own name; other types are zero
Names the SDK headers don't declare (private API) must come from an --extra source; build refuses otherwise.
Names MetalOld already exports are skipped, so no class is defined twice.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

INSTALL_NAME = '/System/Library/Frameworks/Metal.framework/Versions/A/Metal'
COMPAT, CURRENT = '1.0.0', '1.0.0'          # as OCLP's wrapper; clients link Metal at compatibility 1.0.0
HEADER = '#import <Metal/Metal.h>\n'


def sdk_path(sdk=None):
    return sdk or subprocess.run(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], capture_output=True, text=True,
                                 check=True).stdout.strip()


def ast_objects(name, sdk, scratch):
    """Every top-level declaration clang dumps for -ast-dump-filter=NAME (a substring filter)."""
    src = Path(scratch) / 'h.m'
    src.write_text(HEADER)
    out = subprocess.run(['xcrun', 'clang', '-x', 'objective-c', '-isysroot', sdk, '-target', 'x86_64-apple-macos27.0',
                          '-fsyntax-only', '-Xclang', '-ast-dump=json', '-Xclang', '-ast-dump-filter=' + name, str(src)],
                         capture_output=True, text=True)
    raw, dec, i, objs = out.stdout, json.JSONDecoder(), 0, []
    while i < len(raw):
        while i < len(raw) and raw[i].isspace():
            i += 1
        if i >= len(raw):
            break
        obj, i = dec.raw_decode(raw, i)
        objs.append(obj)
    return objs


def exported(dylib):
    """(classes, symbols) a Mach-O exports, from nm."""
    out = subprocess.run(['nm', '-gU', str(dylib)], capture_output=True, text=True, check=True).stdout
    syms = {l.split()[-1] for l in out.splitlines() if l.strip()}
    classes = {s[len('_OBJC_CLASS_$_'):] for s in syms if s.startswith('_OBJC_CLASS_$_')}
    return classes, syms


def zero_return(ret):
    if ret == 'void':
        return ''
    bare = re.sub(r'\b(_Nullable|_Nonnull|_Null_unspecified|const|__kindof|__strong|__weak)\b', '', ret).strip()
    if bare in ('instancetype', 'id', 'Class', 'SEL') or bare.startswith('id<') or bare.endswith('*') or '(^' in bare:
        return '    return 0;\n'
    return '    %s __r; memset(&__r, 0, sizeof(__r)); return __r;\n' % ret


def method_stub(m):
    ret = m['returnType']['qualType']
    params = [p for p in m.get('inner', []) if p.get('kind') == 'ParmVarDecl']
    sel = m['name']
    if params:
        parts = sel.split(':')[:-1]
        head = ' '.join('%s:(%s)a%d' % (part, p['type']['qualType'], i) for i, (part, p) in enumerate(zip(parts, params)))
    else:
        head = sel
    sign = '-' if m.get('instance') else '+'
    if m.get('instance') and sel.startswith('init') and ret in ('instancetype', 'id'):
        body = '    return [super init];\n'
    else:
        body = zero_return(ret)
    return '%s (%s)%s {\n%s}\n' % (sign, ret, head, body)


def class_stub(name, objs):
    decls = [o for o in objs if o.get('kind') == 'ObjCInterfaceDecl' and o.get('name') == name]
    if not decls:
        return None
    methods, props, protocols, seen = [], [], set(), set()
    for d in decls:
        protocols |= {p.get('name') for p in d.get('protocols', [])}
        for c in d.get('inner', []):
            if c.get('kind') == 'ObjCPropertyDecl':
                props.append(c['name'])
            elif c.get('kind') == 'ObjCMethodDecl' and not c.get('isImplicit'):
                key = ('-' if c.get('instance') else '+') + c['name']
                if key not in seen:
                    seen.add(key)
                    methods.append(c)
    accessors = set()
    for p in props:
        accessors |= {'-' + p, '-set%s%s:' % (p[:1].upper(), p[1:])}
    lines = ['@implementation %s' % name]
    lines += ['@synthesize %s = _%s;' % (p, p) for p in dict.fromkeys(props)]
    if 'NSCopying' in protocols:
        lines.append('- (id)copyWithZone:(NSZone *)zone {\n    return [self retain];\n}')
    for m in methods:
        key = ('-' if m.get('instance') else '+') + m['name']
        if key in accessors or key == '-copyWithZone:':
            continue
        lines.append(method_stub(m).rstrip('\n'))
    lines.append('@end\n')
    return '\n'.join(lines)


def function_stub(name, objs):
    for o in objs:
        if o.get('kind') == 'FunctionDecl' and o.get('name') == name and o.get('storageClass') != 'static' \
                and not o.get('inline'):
            ret = o['type']['qualType'].split('(')[0].strip()
            params = [p for p in o.get('inner', []) if p.get('kind') == 'ParmVarDecl']
            args = ', '.join('%s a%d' % (p['type']['qualType'], i) for i, p in enumerate(params)) or 'void'
            return '%s %s(%s) {\n%s}\n' % (ret, name, args, zero_return(ret))
    return None


def data_stub(name, objs):
    for o in objs:
        if o.get('kind') == 'VarDecl' and o.get('name') == name:
            t = o['type']['qualType']
            if 'NSString' in t:
                return '%s %s = @"%s";\n' % (t, name, name)
            return '%s %s;\n' % (t, name)
    return None


def generate(spec, sdk=None, metalold=None, scratch=None):
    """(source text, report). report lists skipped (already in MetalOld) and undeclared (not in the SDK) names."""
    sdk = sdk_path(sdk)
    scratch = scratch or os.environ.get('TMPDIR', '/tmp')
    present_classes, present_syms = exported(metalold) if metalold else (set(), set())
    parts = ['// Generated by tools/metal-wrapper/make.py from the SDK\'s Metal headers. Do not edit.',
             HEADER.rstrip(), '#import <string.h>',
             '#pragma clang diagnostic ignored "-Wobjc-property-implementation"',
             '#pragma clang diagnostic ignored "-Wincomplete-implementation"',
             '#pragma clang diagnostic ignored "-Wunguarded-availability-new"',
             '#pragma clang diagnostic ignored "-Wdeprecated-implementations"',
             '#pragma clang diagnostic ignored "-Wobjc-designated-initializers"', '']
    report = {'classes': [], 'functions': [], 'data': [], 'skipped': [], 'undeclared': []}
    for kind, names, make in (('classes', spec.get('classes', []), class_stub),
                              ('functions', spec.get('functions', []), function_stub),
                              ('data', spec.get('data', []), data_stub)):
        for name in names:
            if (kind == 'classes' and name in present_classes) or (kind != 'classes' and '_' + name in present_syms):
                report['skipped'].append(name)
                continue
            text = make(name, ast_objects(name, sdk, scratch))
            if text is None:
                report['undeclared'].append(name)
                continue
            parts.append(text)
            report[kind].append(name)
    return '\n'.join(parts) + '\n', report


def build(spec, metalold, out, sdk=None, extra=()):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    source, report = generate(spec, sdk, metalold, scratch=str(out))
    extra_text = ''.join(Path(e).read_text() for e in extra)
    missing = [n for n in report['undeclared'] if n not in extra_text]
    if missing:
        raise SystemExit('make.py: not in the SDK headers and not in --extra: %s' % ', '.join(missing))
    (out / 'stubs.m').write_text(source)
    (out / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    cmd = ['xcrun', 'clang', '-arch', 'x86_64', '-isysroot', sdk_path(sdk), '-mmacosx-version-min=27.0',
           '-dynamiclib', '-fno-objc-arc', '-O1', '-Wall', '-Wno-unused-parameter',
           '-install_name', INSTALL_NAME, '-compatibility_version', COMPAT, '-current_version', CURRENT,
           '-framework', 'Foundation', '-lobjc', '-Wl,-reexport_library,' + str(metalold),
           str(out / 'stubs.m'), *map(str, extra), '-o', str(out / 'Metal')]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('make.py: clang failed:\n' + r.stderr[-4000:])
    subprocess.run(['codesign', '-f', '-s', '-', '-i', 'Metal', str(out / 'Metal')], check=True, capture_output=True)
    return report


def main(argv):
    import argparse
    p = argparse.ArgumentParser(prog='make.py', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='action', required=True)
    g = sub.add_parser('generate')
    g.add_argument('spec'); g.add_argument('out'); g.add_argument('--sdk'); g.add_argument('--metalold')
    b = sub.add_parser('build')
    b.add_argument('spec'); b.add_argument('--metalold', required=True); b.add_argument('--out', required=True)
    b.add_argument('--sdk'); b.add_argument('--extra', action='append', default=[])
    a = p.parse_args(argv)
    spec = json.loads(Path(a.spec).read_text())
    if a.action == 'generate':
        source, report = generate(spec, a.sdk, a.metalold)
        Path(a.out).write_text(source)
        print(json.dumps({k: len(v) if k != 'undeclared' else v for k, v in report.items()}))
        return 0
    report = build(spec, a.metalold, a.out, a.sdk, a.extra)
    print('make.py: %d classes, %d functions, %d data stubbed; %d already in MetalOld' % (
        len(report['classes']), len(report['functions']), len(report['data']), len(report['skipped'])))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
