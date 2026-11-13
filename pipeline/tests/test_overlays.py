"""golden-run --from-overlays: the host plan (pipeline/overlays.py), the guest install step
(pipeline/stages/golden-overlays.py) and the tree comparison, on fixtures only (no VM, no disks, no root).
python3.14 -I -B -m unittest discover -s pipeline/tests -k overlays"""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import tarfile
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
O = runpy.run_path(str(TOP / 'pipeline' / 'overlays.py'))
M = runpy.run_path(str(TOP / 'pipeline' / 'materialize.py'))
L = runpy.run_path(str(TOP / 'pipeline' / 'ledger.py'))
_spec = importlib.util.spec_from_file_location('golden_overlays', TOP / 'pipeline/stages/golden-overlays.py')
G = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(G)
LIVEPATCH = G.load_livepatch(TOP / 'tools/hao/src/livepatch.py')
sha = lambda b: hashlib.sha256(b).hexdigest()


def writer(recipe, action='origin', **kw):
    return dict(recipe=recipe, action=action, **kw)


def entry(path, *writers):
    return {'path': path, 'base': {'kind': 'apple'}, 'writers': list(writers)}


def made(path, recipe, writers=None, paths=None, base=None, pack='base'):
    r = {'path': path, 'outcome': 'made', 'pack': pack, 'recipe': recipe, 'writers': writers or [recipe],
         'paths': paths or [path]}
    if base:
        r['base'] = base
    return r


def manifest(*paths, pack='base'):
    return {pack: {'format': 1, 'pack': pack, 'phase': 'installer', 'files': {
        p: {'sha256': sha(p.encode()), 'mode': '0644', 'owner': 'root:wheel', 'flags': []} for p in paths}}}


class Schedule(unittest.TestCase):
    rows = [('oclp-hook', 'kernel'), ('oclp-hook', 'userspace'), ('oclp-hook', 'fix'), ('oclp-hook', 'later'),
            ('oclp-hook', 'identities'), ('post', 'caches')]

    def plan(self, ledger, report, manifests, **kw):
        return O['schedule'](self.rows, ledger, report, manifests, links=['Library', 'private'], **kw)

    def test_modes_follow_what_was_made(self):
        ledger = {'kc': entry('kc', writer('kernel')), 'lib/a': entry('lib/a', writer('fix')),
                  'lib/b': entry('lib/b', writer('later')), 'list': entry('list', writer('identities', after=['overlays'])),
                  'Library/C/x': entry('Library/C/x', writer('caches'))}
        report = [{'path': 'kc', 'outcome': 'deferred', 'recipe': 'kernel', 'reason': 'kc.link', 'after': []},
                  made('lib/a', 'fix'), {'path': 'lib/b', 'outcome': 'unresolved', 'reason': 'no source'},
                  {'path': 'list', 'outcome': 'deferred', 'recipe': 'identities', 'reason': 'generated',
                   'after': ['overlays']}, made('Library/C/x', 'caches')]
        plan = self.plan(ledger, report, manifest('lib/a', 'Library/C/x'))
        modes = {s['name']: (s['mode'], s['reason']) for s in plan['steps']}
        self.assertEqual(modes['kernel'][0], 'hook'); self.assertIn('deferred', modes['kernel'][1])
        self.assertEqual(modes['userspace'], ('hook', 'declares no files'))
        self.assertEqual(modes['fix'][0], 'overlay')
        self.assertEqual(modes['later'][0], 'hook'); self.assertIn('unresolved', modes['later'][1])
        self.assertEqual(modes['caches'][0], 'overlay')
        steps = {s['name']: s for s in plan['steps']}
        self.assertEqual([(f['path'], f['volume']) for f in steps['caches']['files']], [('Library/C/x', 'data')])
        self.assertEqual([f['path'] for f in steps['fix']['files']], ['lib/a'])
        self.assertEqual(plan['counts'], {'overlay': 2, 'hook': 4, 'files': 2})
        self.assertEqual([d['runs_in'] for d in plan['deferred']], ['kernel', 'identities'])
        forced = self.plan(ledger, report, manifest('lib/a', 'Library/C/x'), force_hook={'fix'})
        self.assertEqual(forced['steps'][2]['mode'], 'hook')

    def test_an_overlay_writer_never_precedes_a_hook_writer(self):
        # fix edits lib/a first, then later (on its hook: it also writes a deferred file) edits it again: the overlay
        # only has the final file, so fix must run its hook too. userspace (hook) before fix (overlay) is fine.
        ledger = {'lib/a': entry('lib/a', writer('fix', 'edits'), writer('later', 'edits')),
                  'gen': entry('gen', writer('later')),
                  'plist': entry('plist', writer('userspace', 'edits'), writer('identities', 'edits')),
                  'cx': entry('cx', writer('userspace'))}
        report = [made('lib/a', 'later', ['fix', 'later']), {'path': 'gen', 'outcome': 'deferred', 'recipe': 'later',
                                                              'reason': 'generated', 'after': []},
                  made('plist', 'identities', ['userspace', 'identities'],
                       base={'path': 'plist', 'sha256': 'b' * 64, 'edited': True}),
                  {'path': 'cx', 'outcome': 'deferred', 'recipe': 'userspace', 'reason': 'cryptex', 'after': []}]
        plan = self.plan(ledger, report, manifest('lib/a', 'plist'))
        steps = {s['name']: s for s in plan['steps']}
        self.assertEqual(steps['fix']['mode'], 'hook'); self.assertIn('later edits it after', steps['fix']['reason'])
        self.assertEqual(steps['identities']['mode'], 'overlay')
        # installed at its last writer's turn, without the base check: userspace's hook changed it first
        self.assertEqual([f['path'] for f in steps['identities']['files']], ['plist'])
        self.assertNotIn('base', steps['identities']['files'][0])

    def test_base_check_only_when_every_writer_is_an_overlay(self):
        ledger = {'lib/a': entry('lib/a', writer('fix', 'edits'), writer('later', 'edits'))}
        base = {'path': 'lib/a', 'sha256': 'a' * 64, 'edited': True}
        plan = self.plan(ledger, [made('lib/a', 'later', ['fix', 'later'], base=base)], manifest('lib/a'))
        steps = {s['name']: s for s in plan['steps']}
        self.assertEqual(steps['fix']['files'], [])
        self.assertEqual(steps['later']['files'][0]['base'], base)

    def test_refusals(self):
        # a writer the build leaves out (a skipped optional recipe), a post writing System
        ledger = {'lib/a': entry('lib/a', writer('fix', 'edits'), writer('skipped', 'edits')),
                  'usr/lib/x': entry('usr/lib/x', writer('caches'))}
        plan = self.plan(ledger, [made('lib/a', 'skipped', ['fix', 'skipped']), made('usr/lib/x', 'caches')],
                         manifest('lib/a', 'usr/lib/x'))
        steps = {s['name']: s for s in plan['steps']}
        self.assertIn('skipped, which this build leaves out', steps['fix']['reason'])
        self.assertIn('read-only after the snapshot', steps['caches']['reason'])
        # native-identities (after overlays) with an overlay System file installed after it
        rows = [('oclp-hook', 'identities'), ('oclp-hook', 'fix')]
        ledger = {'list': entry('list', writer('identities')), 'lib/a': entry('lib/a', writer('fix'))}
        report = [{'path': 'list', 'outcome': 'deferred', 'recipe': 'identities', 'reason': 'g', 'after': ['overlays']},
                  made('lib/a', 'fix')]
        with self.assertRaisesRegex(O['PlanError'], 'waits for every overlay file'):
            O['schedule'](rows, ledger, report, manifest('lib/a'))
        report[0]['after'] = ['lib/a']
        with self.assertRaisesRegex(O['PlanError'], 'waits for lib/a'):
            O['schedule'](rows, ledger, report, manifest('lib/a'))

    def test_volumes(self):
        v = O['volume_of']
        self.assertEqual(v('Library/Caches/x', 'oclp-hook', ['Library']), 'system')     # as its hook writes $S/...
        self.assertEqual(v('Library/Caches/x', 'post', ['Library']), 'data')
        self.assertEqual(v('EFI/OC/config.plist', 'post', ['Library']), 'esp')
        self.assertIsNone(v('EFI/OC/config.plist', 'oclp-hook', ['Library']))
        self.assertIsNone(v('System/Volumes/Preboot/Cryptexes/OS', 'post', ['Library']))
        self.assertEqual(v('usr/lib/x', 'post', ['Library']), 'system')


class FromOverlays(unittest.TestCase):
    """Materialise a fixture set, plan, stage, install the overlay recipes into a fixture image as the guest does,
    run the hooks of the rest as stand-ins, and compare the result with the tree the hooks alone make."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)
        self.patches = self.d / 'patches'; self.patches.mkdir()
        system = self.d / 'golden' / 'usr/lib'; system.mkdir(parents=True)
        (system / 'libz.dylib').write_bytes(b'golden gate libz')
        (self.d / 'golden/usr/share').mkdir()
        (self.d / 'golden/usr/share/firmlinks').write_text('/Library\tLibrary\n/private\tprivate\n')
        rows = self.d / 'rows'; rows.mkdir()
        (rows / 'one.dylib').write_bytes(b'\xcf\xfa\xed\xfe one')
        with tarfile.open(rows / 'data.tar', 'w') as t:
            for name, mode, uid in (('Library', 0o755, 0), ('Library/Caches', 0o755, 0), ('Library/Caches/m', 0o700, 248),
                                    ('Library/Caches/n', 0o755, 248), ('Library/Caches/n/fe', 0o755, 248)):
                info = tarfile.TarInfo(name); info.type = tarfile.DIRTYPE; info.mode = mode; info.uid = info.gid = uid
                info.mtime = 1600000000                     # n and n/fe hold nothing: tar -x makes them anyway
                t.addfile(info)
            info = tarfile.TarInfo('Library/Caches/m/cache'); info.size = 5
            info.mode, info.uid, info.gid, info.mtime = 0o600, 248, 248, 1700000000
            t.addfile(info, io.BytesIO(b'cache'))
        self.sources = M['Sources'](rows={'one': str(rows / 'one.dylib'), 'caches': str(rows)},
                                    system=str(self.d / 'golden'))
        self.order = []

    def recipe(self, name, stage, files, specs=None):
        d = self.patches / name; d.mkdir()
        (d / 'patch.json').write_text(json.dumps({'revision': 1, 'stage': stage, 'applies': 'any', 'files': files}))
        for fname, spec in (specs or {}).items():
            (d / 'edits').mkdir(exist_ok=True)
            (d / 'edits' / fname).write_text(json.dumps(spec))
        self.order.append((stage, name))

    def build(self):
        self.recipe('zfix', 'oclp-hook', [{'path': 'usr/lib/libz.dylib', 'edits': 'edits/z.json',
                                           'install': {'mode': '0755', 'owner': 'root:wheel', 'flags': ['restricted']}},
                                          {'path': 'usr/lib/one.dylib', 'origin': {'kind': 'hao', 'row': 'one'}}],
                    {'z.json': {'format': 1, 'path': 'usr/lib/libz.dylib', 'origin': {'kind': 'apple'},
                                'edits': [{'id': 'z', 'kind': 'literal', 'at': 0, 'find': '67', 'replace': '47',
                                           'reason': 'x'}]}})
        self.recipe('gen', 'oclp-hook', [{'path': 'usr/lib/generated', 'origin': {'kind': 'hao'}}])
        self.recipe('caches', 'post', [{'path': 'Library/Caches/m/cache', 'origin': {'kind': 'hao', 'row': 'caches'},
                                        'source': 'data.tar!Library/Caches/m/cache'}])
        s = self.d / 'set.txt'; s.write_text('\n'.join(n for _, n in self.order) + '\n')
        t = self.d / 'mbp92.json'; t.write_text(json.dumps({'model': 'MacBookPro9,2'}))
        ledger = L['build'](s, t, '25G342', root=self.patches, layer_data=([], []))
        out = self.d / 'overlays'
        report = M['materialize'](ledger, self.patches, self.sources, out, self.d / 'store', lambda r: 'base',
                                  lambda p: 'installer', repo=self.d)
        plan = O['schedule'](self.order, ledger, report, O['load_manifests'](out),
                             links=O['firmlinks'](self.d / 'golden'))
        files = self.d / 'share/overlay-files'
        O['stage_files'](plan, out, files)
        planfile = self.d / 'share/overlay-plan.json'; planfile.write_text(json.dumps(plan))
        return plan, files

    def image(self, name):
        """A fixture image after the base layers: System with Golden Gate's libz and firmlinks, an empty Data."""
        root = self.d / name
        (root / 'system/usr').mkdir(parents=True)
        shutil.copytree(self.d / 'golden/usr', root / 'system/usr', dirs_exist_ok=True)
        (root / 'data').mkdir()
        return {'system': str(root / 'system'), 'data': str(root / 'data')}

    def attributes(self, roots, calls):
        return LIVEPATCH.Attributes(roots['data'], privileged=True,
                                    chown=lambda p, u, g: calls.append(('chown', Path(p).name, u, g)),
                                    chflags=lambda p, bits: calls.append(('chflags', Path(p).name, bits)))

    def test_install_matches_the_hooks(self):
        plan, files = self.build()
        modes = {s['name']: s['mode'] for s in plan['steps']}
        self.assertEqual(modes, {'zfix': 'overlay', 'gen': 'hook', 'caches': 'overlay'})
        # what the hooks would leave (stand-ins for zfix's install + tar -xp of the caches)
        hooks = self.image('hooks')
        Path(hooks['system'], 'usr/lib/libz.dylib').write_bytes(b'Golden gate libz')
        Path(hooks['system'], 'usr/lib/one.dylib').write_bytes(b'\xcf\xfa\xed\xfe one')
        os.chmod(Path(hooks['system'], 'usr/lib/libz.dylib'), 0o755)
        os.chmod(Path(hooks['system'], 'usr/lib/one.dylib'), 0o755)
        Path(hooks['data'], 'Library/Caches/m').mkdir(parents=True)
        Path(hooks['data'], 'Library/Caches/m/cache').write_bytes(b'cache')
        os.chmod(Path(hooks['data'], 'Library/Caches/m/cache'), 0o600)
        os.chmod(Path(hooks['data'], 'Library/Caches/m'), 0o700)
        os.utime(Path(hooks['data'], 'Library/Caches/m/cache'), (1700000000, 1700000000))
        Path(hooks['data'], 'Library/Caches/n/fe').mkdir(parents=True)
        # the overlay build, as golden-image.py runs it: zfix and caches from the overlays, gen on its "hook"
        roots = self.image('overlay')
        calls = []
        for step in plan['steps']:
            if step['mode'] == 'overlay':
                rec = G.install(json.loads((self.d / 'share/overlay-plan.json').read_text()), files, step['name'],
                                roots, LIVEPATCH, attributes=self.attributes(roots, calls))
                self.assertEqual(rec['warnings'], [])
        self.assertIn(('chflags', 'libz.dylib', 0x80000), calls)                   # restricted, as the hook's chflags
        self.assertIn(('chown', 'cache', 248, 248), calls)                          # the tar member's numeric owner
        self.assertIn(('chown', 'm', 248, 248), calls)                              # and its folder's, from "dirs"
        self.assertEqual(os.stat(Path(roots['data'], 'Library/Caches/m/cache')).st_mtime, 1700000000)
        self.assertEqual(oct(os.stat(Path(roots['data'], 'Library/Caches/m')).st_mode & 0o777), '0o700')
        self.assertIn(('chown', 'fe', 248, 248), calls)                             # an empty tar folder, made too
        self.assertEqual(os.stat(Path(roots['data'], 'Library/Caches/n/fe')).st_mtime, 1600000000)
        self.assertEqual(os.stat(Path(roots['data'], 'Library/Caches/m')).st_mtime, 1600000000)  # after its file
        caches = next(s for s in plan['steps'] if s['name'] == 'caches')
        self.assertEqual(sorted(caches['dirs']), ['Library', 'Library/Caches', 'Library/Caches/m', 'Library/Caches/n',
                                                  'Library/Caches/n/fe'])
        self.assertEqual({a['volume'] for a in caches['dirs'].values()}, {'data'})
        # owners and flags were recorded, not set (no root): compare content, modes, links and mtimes
        a = {v: O['tree'](hooks[v]) for v in hooks}
        b = {v: O['tree'](roots[v]) for v in roots}
        for v in a:
            for t in (a[v], b[v]):
                for e in t['entries'].values():
                    e.update(uid=0, gid=0, flags=[])
            r = O['compare'](a[v], b[v], hooks[v], roots[v], O['load_manifests'](self.d / 'overlays'))
            self.assertEqual(r['real'], 0, (v, r['differences']))

    def test_refuses_a_different_base_and_a_bad_staged_file(self):
        plan, files = self.build()
        roots = self.image('wrong')
        Path(roots['system'], 'usr/lib/libz.dylib').write_bytes(b'another libz')
        with self.assertRaisesRegex(ValueError, 'the overlay was made from [0-9a-f]+ .an edit of that file: refusing'):
            G.install(plan, files, 'zfix', roots, LIVEPATCH, attributes=self.attributes(roots, []))
        roots = self.image('bad')
        Path(files, 'usr/lib/one.dylib').unlink()
        Path(files, 'usr/lib/one.dylib').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'staged file does not match'):
            G.install(plan, files, 'zfix', roots, LIVEPATCH, attributes=self.attributes(roots, []))
        with self.assertRaisesRegex(ValueError, 'not installed from the overlays'):
            G.install(plan, files, 'gen', roots, LIVEPATCH)
        # a Data path the image's own firmlinks do not cover
        Path(roots['system'], 'usr/share/firmlinks').write_text('/private\tprivate\n')
        with self.assertRaisesRegex(ValueError, 'no firmlink of this image covers it'):
            G.install(plan, files, 'caches', roots, LIVEPATCH, attributes=self.attributes(roots, []))


class Compare(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.d)

    def tree(self, name, files):
        root = self.d / name
        for p, data in files.items():
            (root / p).parent.mkdir(parents=True, exist_ok=True)
            if isinstance(data, tuple):
                os.symlink(data[1], root / p)
            else:
                (root / p).write_bytes(data)
        return root, O['tree'](root)

    def test_kinds(self):
        ident, rec = 'usr/lib/hao/native-identities', 'System/Library/CoreServices/golden-gate-native-identities.json'
        tmp = lambda o: '/private/var/tmp/native-identities.' + o + 'Q1/fs'       # kc-hook.sh's mktemp, per build
        record = lambda o, w: json.dumps({'rows': [{'path': 'p', 'observed': o, 'reference': 'ref1',
                                                    'source': tmp(o) + '/p'}],
                                          'sources': [tmp(o)], 'list_sha256': o,
                                          'skipped': {'q': tmp(o) + ': ' + w}}).encode()
        a_root, a = self.tree('hook', {'same': b'x', 'content': b'a', 'noisy': b'1', 'gone': b'g', ident:
                                       b'# list\naaaa  ref1  x86_64  usr/lib/hao/originals/p\n', 'link': ('l', 'x'),
                                       rec: record('aaaa', 'why'), 'rec2.json': record('aaaa', 'why')})
        b_root, b = self.tree('ovl', {'same': b'x', 'content': b'b', 'noisy': b'2', 'new': b'n', ident:
                                      b'# list\nbbbb  ref1  x86_64  usr/lib/hao/originals/p\n', 'link': ('l', 'y'),
                                      rec: record('bbbb', 'why'), 'rec2.json': record('bbbb', 'why')})
        noise = {'differences': [{'path': 'noisy', 'kind': 'content', 'detail': ''}]}
        r = O['compare'](a, b, a_root, b_root, noise=noise)
        kinds = {d['path']: d['kind'] for d in r['differences']}
        self.assertEqual(kinds, {'content': 'content', 'noisy': 'noise', 'gone': 'only-hook', 'new': 'only-overlay',
                                 ident: 'derived', rec: 'derived', 'link': 'attrs', 'rec2.json': 'content'})
        self.assertEqual(r['real'], 5)
        self.assertEqual(r['counts']['equal'], 7)          # same + the folders usr, usr/lib, usr/lib/hao, System...                                   # same + usr, usr/lib, usr/lib/hao

    def test_signature_padding_is_not_a_difference(self):
        """Same code, another CDHash (another page size here; the builder VM's padding in an image) is signature-only;
        another signing identifier is a real difference. Signs copies of /usr/bin/true's x86_64 slice."""
        import subprocess
        if not shutil.which('codesign') or not shutil.which('lipo'):
            self.skipTest('needs codesign and lipo')
        def signed(name, *args):
            out = self.d / name
            subprocess.run(['lipo', '/usr/bin/true', '-thin', 'x86_64', '-output', str(out)], check=True,
                           capture_output=True)
            subprocess.run(['codesign', '-f', '-s', '-', *args, str(out)], check=True, capture_output=True)
            return out.read_bytes()
        hook = signed('one', '-i', 'com.example.a')
        a_root, a = self.tree('h', {'bin': hook, 'other': hook})
        b_root, b = self.tree('o', {'bin': signed('two', '-i', 'com.example.a', '--pagesize', '16384'),
                                    'other': signed('three', '-i', 'com.example.b')})
        r = O['compare'](a, b, a_root, b_root)
        kinds = {d['path']: d['kind'] for d in r['differences']}
        self.assertEqual(kinds, {'bin': 'signature-only', 'other': 'content'})
        self.assertEqual(r['real'], 1)

if __name__ == '__main__':
    unittest.main()
