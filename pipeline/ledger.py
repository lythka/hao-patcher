#!/usr/bin/python3 -I
"""ledger: every file the recipes of a set declare, as one origin + ordered writes, and the chains it refuses.

  ledger.py SET TARGET BUILD [--json]          SET = pipeline/sets/SET.txt, TARGET = pipeline/targets/NAME.json
  ledger.py SET TARGET BUILD --write-expected  record this set's chains in pipeline/ledger-expected.json (review it)

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
and always refuses an edit spec whose origin kind is not the kind of the file it edits. A recipe that edits a file
inside its own tree edits what that tree installed (no chain). An expected entry that no longer matches is refused
too, so the list only ever shrinks as chains are collapsed.

pipeline/ledger-expected.json is {"chains": {PATH: {"chain", "writers"}}}, one list for every set: a chain is
expected by what it is (its path, kind and writers), not by the set it was first seen in, so any set that applies
the same recipes passes with the same entries. An entry applies to a set when the set applies every recipe it names;
only then can it be stale.
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
                                     'origin_kind': spec['origin']['kind'], 'install': f.get('install'), 'source': f.get('source'),
                                     'input': f.get('input'), 'after': f.get('after')})
            else:
                e['writers'].append({'recipe': name, 'action': 'origin', 'origin': f['origin'],
                                     'tree': bool(f.get('tree')), 'install': f.get('install'), 'source': f.get('source'),
                                     'after': f.get('after')})
    tree_writers = [(p, w) for p, e in ledger.items() for w in e['writers'] if w.get('tree')]
    for path, e in ledger.items():
        first = e['writers'][0]['recipe']             # a recipe editing a file of its own tree edits the tree's file
        own = next(((t, w) for t, w in tree_writers if w['recipe'] == first and path != t and under(path, t)), None)
        classify(e, oclp, own_tree=own)
    trees = [(p, w['recipe']) for p, w in tree_writers]
    for path, e in ledger.items():                     # another recipe's file inside a recipe's whole tree
        for tree, owner in trees:
            if path != tree and under(path, tree) and any(w['recipe'] != owner for w in e['writers']):
                e['inside_tree'] = {'tree': tree, 'recipe': owner}
                e['chain'] = ','.join(sorted(set(filter(None, (e['chain'] or '').split(','))) | {'inside-tree'}))
    return ledger


def classify(e, oclp, own_tree=None):
    """Set e['chain'] (the kind of multi-writer pattern, or None) and e['problems'] (always refused). OWN_TREE
    (tree path, its writer): the file is inside a tree its first writer installs whole, so that recipe wrote it
    first (e['own_tree'] says where the materialiser finds those bytes)."""
    writers, base = e['writers'], e['base']
    kinds = []
    current = None                                   # the recipe that last wrote the whole file or edited it
    current_kind = base['kind']                      # what the file is, for an edit spec's origin
    if own_tree:
        tree, tw = own_tree
        e['own_tree'] = {'tree': tree, 'recipe': tw['recipe'], 'row': tw['origin'].get('row'), 'source': tw.get('source')}
        current, current_kind = tw['recipe'], 'hao'
    edited_by_other = False
    for w in writers:
        if w['action'] == 'edits':
            if current is not None and current != w['recipe']:
                kinds.append('chain')
                if w['origin_kind'] not in (current_kind, 'hao'):
                    e['problems'].append('%s: spec origin %s, but the file is %s from %s' % (
                        w['recipe'], w['origin_kind'], current_kind, current))
            elif current is None and w.get('input') and w['input'].get('row'):
                if w['input']['kind'] != w['origin_kind']:
                    e['problems'].append('%s: spec origin %s, but its input row is %s' % (
                        w['recipe'], w['origin_kind'], w['input']['kind']))
                if base['kind'] == 'oclp':                 # our file in place of one OCLP installs
                    kinds.append('superseded')
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


def named_recipes(writers):
    """The recipes an expected entry's writer list names (not the base layer; a tree owner by its name)."""
    return {w.split(' (tree ', 1)[0] for w in writers[1:]}


def applies(want, ledger):
    """Whether the expected entry WANT ({"chain", "writers"}) is about this ledger's set: it applies every recipe
    the entry names."""
    applied = {w['recipe'] for e in ledger.values() for w in e['writers']}
    return named_recipes(want.get('writers', [])) <= applied


def dev_recipes(packs_dir=os.path.join(TOP, 'packs')):
    """Recipes of opt-in packs (pack dev, dev trials): bench images only."""
    out = set()
    for n in sorted(os.listdir(packs_dir)) if os.path.isdir(packs_dir) else []:
        if n.endswith('.json'):
            with open(os.path.join(packs_dir, n)) as f:
                p = json.load(f)
            if p.get('opt_in'):
                out.update(p.get('recipes', []))
    return out


def lint(ledger, expected, dev=None):
    """Problems: refused chains not in EXPECTED ({path: {"chain", "writers"}}, shared by every set), stale expected
    entries (only those whose recipes this set all applies), and every entry's own problems. A dev set's chain may
    end in writers of opt-in packs (DEV, default dev_recipes()): it is then the expected chain plus those, e.g.
    dev-diagnostics after the end-user writers of EFI/OC/config.plist."""
    dev = dev_recipes() if dev is None else set(dev)
    bad = []
    for path, e in sorted(ledger.items()):
        bad += ['%s: %s' % (path, p) for p in e['problems']]
        if e['chain']:
            want = expected.get(path)
            got = {'chain': e['chain'], 'writers': writer_names(e)}
            names = got['writers']
            while want and names and names[-1] in dev and names != want['writers']:
                names = names[:-1]
            if want and want.get('chain') == got['chain'] and names == want['writers']:
                continue
            if want != got:
                bad.append('%s: %s by %s is not in ledger-expected.json%s' % (
                    path, e['chain'], ' -> '.join(got['writers']), ' (expected %s)' % json.dumps(want) if want else ''))
    for path, want in sorted(expected.items()):
        e = ledger.get(path)
        if applies(want, ledger) and (not e or not e['chain']):
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


def expected_chains(path=EXPECTED):
    """{path: {"chain", "writers"}}: the expected chains, the same for every set."""
    data = read_json(path) if os.path.isfile(path) else {}
    return data.get('chains', {})


def expected_for(setname=None, path=EXPECTED):
    """The expected chains SETNAME is checked against: the shared list (SETNAME is kept for callers; the list is
    keyed by chain, not by set)."""
    return expected_chains(path)


def write_expected(ledger, path=EXPECTED):
    """Record LEDGER's chains in the shared list: entries about this set (applies()) are replaced by its chains;
    entries about other sets are kept. Returns the number of chains this set has."""
    chains = {p: w for p, w in expected_chains(path).items() if not applies(w, ledger)}
    mine = {p: {'chain': e['chain'], 'writers': writer_names(e)} for p, e in sorted(ledger.items()) if e['chain']}
    clash = [p for p in mine if p in chains and chains[p] != mine[p]]
    if clash:
        raise ValueError('another set expects a different chain for %s' % ', '.join(clash))
    chains.update(mine)
    with open(path, 'w') as f:
        f.write(json.dumps({'chains': chains}, indent=1, sort_keys=True) + '\n')
    return len(mine)


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
        try:
            n = write_expected(ledger)
        except ValueError as x:
            sys.exit('ledger: %s' % x)
        print('ledger: recorded %d expected chains for %s' % (n, setname))
        return 0
    expected = expected_for(setname)
    problems = lint(ledger, expected)
    if '--json' in argv:
        print(json.dumps({'set': setname, 'files': ledger, 'problems': problems}, indent=1))
    else:
        print(render(ledger))
        print('\nledger: %d declared files, %d chains (%d expected for this set), %d problems' % (
            len(ledger), sum(1 for e in ledger.values() if e['chain']),
            sum(1 for w in expected.values() if applies(w, ledger)), len(problems)))
        for p in problems:
            print('PROBLEM: %s' % p)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
