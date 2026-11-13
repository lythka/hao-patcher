"""signlint: a recipe whose hook signs with metadata its specs do not declare.

A spec's "sign" says how the declared file is signed; the hook still does the signing until the builder takes
over. The two must agree on more than the bytes outside the signature: a hook that signs with another file's
entitlements (`codesign ... --entitlements FILE`) needs a signed spec with "entitlements_from", and a hook that keeps
the input's metadata (`--preserve-metadata=A,B`) needs a signed spec that preserves A and B. This reads the hook
sources (apply.sh, hook.sh and everything under src/, not producers such as make or build/) and reports each
signing call no spec of the recipe accounts for. It is a static check: signing.compare() on a reference file is the
proof, this catches the gap before anyone has one.

  python3.14 -I tools/edit-engine/src/signlint.py patches/NAME ...
"""
import json
from pathlib import Path
import re
import sys

HOOKS = ('apply.sh', 'hook.sh')
TOKEN = re.compile(r"""(?<![\w$])['"]?(--?[A-Za-z][-\w]*(?:=[\w,]+)?)['"]?""")


def hook_files(recipe):
    d = Path(recipe)
    out = [d / h for h in HOOKS if (d / h).is_file()]
    if (d / 'src').is_dir():
        out += sorted(p for p in (d / 'src').rglob('*') if p.is_file() and p.suffix in ('.sh', '.py', '')
                      and p.name != 'make' and '__pycache__' not in p.parts)
    return out


def signing_calls(text):
    """[(line number, {'entitlements': bool, 'preserve': set})] for each line that signs with codesign."""
    calls = []
    for n, line in enumerate(text.splitlines(), 1):
        if 'codesign' not in line or line.lstrip().startswith('#'):
            continue
        tokens = TOKEN.findall(line.split('codesign', 1)[1])
        if '-s' not in tokens or '-d' in tokens or '-dv' in tokens or '--verify' in tokens:
            continue
        preserve = set()
        for t in tokens:
            if t.startswith('--preserve-metadata='):
                preserve |= set(t.split('=', 1)[1].split(','))
        calls.append((n, {'entitlements': '--entitlements' in tokens, 'preserve': preserve}))
    return calls


def signed_specs(recipe):
    d = Path(recipe)
    try:
        files = json.loads((d / 'patch.json').read_text()).get('files', [])
    except (OSError, ValueError):
        return []
    out = []
    for f in files:
        if 'edits' in f:
            try:
                s = json.loads((d / f['edits']).read_text())
            except (OSError, ValueError):
                continue
            if s.get('sign'):
                out.append((f['edits'], s['sign']))
    return out


def problems(recipe):
    specs = signed_specs(recipe)
    if not specs:
        return []                      # nothing declared signed: the declaration is about other files
    bad = []
    for path in hook_files(recipe):
        try:
            text = path.read_text(errors='replace')
        except OSError:
            continue
        for n, call in signing_calls(text):
            where = '%s:%d' % (path.relative_to(Path(recipe).parent), n)
            if call['entitlements'] and not any(s.get('entitlements_from') for _, s in specs):
                bad.append('%s signs with --entitlements FILE, but no signed spec of %s has "entitlements_from"'
                           % (where, Path(recipe).name))
            if call['preserve'] and not any(call['preserve'] <= set(s.get('preserve', [])) | (
                    {'identifier'} if s.get('identifier') else set()) for _, s in specs):
                bad.append('%s keeps the input\'s %s, but no signed spec of %s preserves them'
                           % (where, ', '.join(sorted(call['preserve'])), Path(recipe).name))
    return bad


def main(argv):
    bad = [p for r in argv for p in problems(r)]
    for p in bad:
        print(p)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
