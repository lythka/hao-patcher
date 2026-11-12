#!/usr/bin/python3 -I
"""ledger: every file the recipes of a set declare, as one origin + ordered writes, and the chains it refuses.

  ledger.py SET TARGET BUILD [--json]          SET = pipeline/sets/SET.txt, TARGET = pipeline/targets/NAME.json
  ledger.py SET TARGET BUILD --write-expected  record today's chains as pipeline/ledger-expected.json (review it)

Layers, lowest first: Golden Gate's own System ("apple"), the Tahoe donor roots (tools/tahoe-donors/recipes/
intel-donors.json, "tahoe"), OCLP's hardware overlay for the target (tools/oclp-hardware/profiles/TARGET.json, and
the EFI partition, "oclp"), then the recipes the set applies, in set order, each through patch.json "files".

For each declared path the ledger finds the file's base (the layer that supplies it) and lists every recipe write:
"origin" (the recipe installs the whole file) or "edits" (an edit spec, tools/edit-engine). It refuses, unless the
path is listed with the same writers in pipeline/ledger-expected.json:
  chain        a recipe edits a file another recipe wrote or edited (patch over patch)
  two-origins  two recipes install the whole file
  dead-edits   a recipe installs the whole file over another recipe's edits, so those edits are lost
  superseded   a recipe installs the whole file over a file OCLP installs itself (OCLP's write is wasted)
  inside-tree  a recipe writes a file inside a folder another recipe declares whole ("tree": true)
and always refuses an edit spec whose origin kind is not the kind of the file it edits. An expected entry that no
longer matches is refused too, so the list only ever shrinks as chains are collapsed.
"""
import json
import os
import runpy
import sys

TOP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED = os.path.join(TOP, 'pipeline', 'ledger-expected.json')


def read_json(path):
    with open(path) as f:
        return json.load(f)


def layers(target):
    """(oclp entries [(destination, operation, patchset)], tahoe roots [path]) for TARGET (a targets/*.json path)."""
    name = os.path.splitext(os.path.basename(target))[0]
    profile = os.path.join(TOP, 'tools', 'oclp-hardware', 'profiles', name + '.json')
    oclp = []
    if os.path.isfile(profile):
        for e in read_json(profile).get('entries', []):
            oclp.append((e['destination'].lstrip('/'), e['operation'], e['patch']))
    donors = read_json(os.path.join(TOP, 'tools', 'tahoe-donors', 'recipes', 'intel-donors.json'))
    tahoe = list(donors.get('system_roots', [])) + list(donors.get('extensions_roots', []))
    return oclp, tahoe


def under(path, root):
    return path == root or path.startswith(root.rstrip('/') + '/')


def base_of(path, oclp, tahoe):
    """(kind, detail) of the layer that supplies PATH before any recipe."""
    if under(path, 'EFI'):
        return 'oclp', 'EFI partition (OpenCore)'
    hit = None
    for dest, op, patchset in oclp:                      # later patch sets win, as OCLP applies them in order
        if under(path, dest):                            # an overwrite may name a whole bundle, a merge a tree
            exact = op == 'Overwrite System Volume' and path == dest
            hit = ('oclp', '%s (%s)' % (patchset, 'overwrite' if exact else 'overwrite of ' + dest if
                                        op.startswith('Overwrite') else 'merge of ' + dest))
    if hit:
        return hit
    if any(under(path, r) for r in tahoe):
        return 'tahoe', 'Tahoe donor root'
    return 'apple', 'Golden Gate'


def build(setfile, target, build_name, root=os.path.join(TOP, 'patches'), layer_data=None):
    """{path: entry} for every path the applied recipes declare, in set order. LAYER_DATA (oclp entries, tahoe
    roots) replaces the target's own, for tests."""
    patches = runpy.run_path(os.path.join(TOP, 'pipeline', 'patches.py'))
    spec_mod = patches['edit_spec']()
    oclp, tahoe = layer_data if layer_data is not None else layers(target)
    applied = [name for _, name, _ in patches['plan'](setfile, target, build_name, root=root, locks=None)]
    ledger = {}
    for name in applied:
        p = read_json(os.path.join(root, name, 'patch.json'))
        for f in p.get('files', []):
            path = f['path']
            e = ledger.get(path)
            if e is None:
                kind, detail = base_of(path, oclp, tahoe)
                e = ledger[path] = {'path': path, 'base': {'kind': kind, 'detail': detail}, 'writers': [],
                                    'problems': [], 'chain': None}
            if 'edits' in f:
                spec = spec_mod['load'](os.path.join(root, name, f['edits']))
                e['writers'].append({'recipe': name, 'action': 'edits', 'spec': f['edits'],
                                     'edits': [x['id'] for x in spec['edits']], 'sign': bool(spec.get('sign')),
                                     'origin_kind': spec['origin']['kind'], 'install': f.get('install'), 'source': f.get('source')})
            else:
                e['writers'].append({'recipe': name, 'action': 'origin', 'origin': f['origin'],
                                     'tree': bool(f.get('tree')), 'install': f.get('install'), 'source': f.get('source')})
    for e in ledger.values():
        classify(e, oclp)
    trees = [(p, w['recipe']) for p, e in ledger.items() for w in e['writers'] if w.get('tree')]
    for path, e in ledger.items():                     # another recipe's file inside a recipe's whole tree
        for tree, owner in trees:
            if path != tree and under(path, tree) and any(w['recipe'] != owner for w in e['writers']):
                e['inside_tree'] = {'tree': tree, 'recipe': owner}
                e['chain'] = ','.join(sorted(set(filter(None, (e['chain'] or '').split(','))) | {'inside-tree'}))
    return ledger


def classify(e, oclp):
    """Set e['chain'] (the kind of multi-writer pattern, or None) and e['problems'] (always refused)."""
    writers, base = e['writers'], e['base']
    kinds = []
    current = None                                   # the recipe that last wrote the whole file or edited it
    current_kind = base['kind']                      # what the file is, for an edit spec's origin
    edited_by_other = False
    for w in writers:
        if w['action'] == 'edits':
            if current is not None and current != w['recipe']:
                kinds.append('chain')
                if w['origin_kind'] not in (current_kind, 'hao'):
                    e['problems'].append('%s: spec origin %s, but the file is %s from %s' % (
                        w['recipe'], w['origin_kind'], current_kind, current))
            elif current is None and w['origin_kind'] != current_kind and not (
                    w['origin_kind'] == 'hao' and current_kind == 'apple'):
                e['problems'].append('%s: spec origin %s, but %s comes from %s (%s)' % (
                    w['recipe'], w['origin_kind'], e['path'], current_kind, base['detail']))
            edited_by_other = edited_by_other or (current is not None and current != w['recipe'])
            current = w['recipe']
        else:
            if current is not None and current != w['recipe']:
                previous_edits = any(x['action'] == 'edits' and x['recipe'] != w['recipe'] for x in writers[:writers.index(w)])
                kinds.append('dead-edits' if previous_edits else 'two-origins')
            elif current is None and base['kind'] == 'oclp' and base['detail'].endswith('(overwrite)'):
                kinds.append('superseded')
            current, current_kind = w['recipe'], 'hao'
    signs = sum(1 for w in writers if w['action'] == 'edits' and w['sign'])
    e['signs'] = signs
    e['chain'] = ','.join(sorted(set(kinds))) or None


def writer_names(e):
    tree = ['%s (tree %s)' % (e['inside_tree']['recipe'], e['inside_tree']['tree'])] if e.get('inside_tree') else []
    return [e['base']['kind']] + tree + [w['recipe'] for w in e['writers']]


def lint(ledger, expected):
    """Problems: refused chains not in EXPECTED ({path: {"chain", "writers"}}), stale expected entries, and every
    entry's own problems."""
    bad = []
    for path, e in sorted(ledger.items()):
        bad += ['%s: %s' % (path, p) for p in e['problems']]
        if e['chain']:
            want = expected.get(path)
            got = {'chain': e['chain'], 'writers': writer_names(e)}
            if want != got:
                bad.append('%s: %s by %s is not in ledger-expected.json%s' % (
                    path, e['chain'], ' -> '.join(got['writers']), ' (expected %s)' % json.dumps(want) if want else ''))
    for path, want in sorted(expected.items()):
        e = ledger.get(path)
        if not e or not e['chain']:
            bad.append('%s: listed in ledger-expected.json but no longer a chain: remove it' % path)
    return bad


def render(ledger):
    out = []
    for path, e in sorted(ledger.items()):
        out.append(path)
        out.append('  base     %s · %s' % (e['base']['kind'], e['base']['detail']))
        for w in e['writers']:
            if w['action'] == 'origin':
                o = w['origin']
                out.append('  origin   %s  whole file (%s%s)' % (w['recipe'], o['kind'], ', row ' + o['row'] if o.get('row') else ''))
            else:
                out.append('  edits    %s  %s%s' % (w['recipe'], ', '.join(w['edits']), '  + sign' if w['sign'] else ''))
        if e['chain']:
            out.append('  CHAIN    %s' % e['chain'])
        if e['signs'] > 1:
            out.append('  note     signed %d times' % e['signs'])
        for p in e['problems']:
            out.append('  PROBLEM  %s' % p)
    return '\n'.join(out)


def expected_for(setname):
    data = read_json(EXPECTED) if os.path.isfile(EXPECTED) else {}
    return data.get(setname, {})


def main(argv):
    args = [a for a in argv if not a.startswith('--')]
    if len(args) != 3:
        sys.exit(__doc__)
    setfile, target, build_name = args
    setname = os.path.splitext(os.path.basename(setfile))[0]
    try:
        ledger = build(setfile, target, build_name)
    except (ValueError, OSError, KeyError) as x:
        sys.exit('ledger: %s' % x)
    if '--write-expected' in argv:
        data = read_json(EXPECTED) if os.path.isfile(EXPECTED) else {}
        data[setname] = {p: {'chain': e['chain'], 'writers': writer_names(e)} for p, e in sorted(ledger.items())
                         if e['chain']}
        with open(EXPECTED, 'w') as f:
            f.write(json.dumps(data, indent=1, sort_keys=True) + '\n')
        print('ledger: wrote %d expected chains for %s' % (len(data[setname]), setname))
        return 0
    problems = lint(ledger, expected_for(setname))
    if '--json' in argv:
        print(json.dumps({'set': setname, 'files': ledger, 'problems': problems}, indent=1))
    else:
        print(render(ledger))
        print('\nledger: %d declared files, %d chains (%d expected), %d problems' % (
            len(ledger), sum(1 for e in ledger.values() if e['chain']), len(expected_for(setname)), len(problems)))
        for p in problems:
            print('PROBLEM: %s' % p)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
