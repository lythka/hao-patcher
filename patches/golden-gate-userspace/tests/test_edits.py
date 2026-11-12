"""patch.json "files" and edits/*.json declare what src/install.py does; these tests tie each declaration to the code.
python3.14 -I -B -m unittest discover -s patches/golden-gate-userspace/tests -k test_edits

Always: every spec follows the edit-spec format and is listed; declared paths and rows are the ones install.py and
the display installer helper use; the two disabled-job specs write the same bytes as install.disabled_job on
synthetic plists (XML and binary); `macho.add-dylib` writes the same bytes as macho.add_load_dylib; the 26 hao stub
specs are consistent with each other and with the two cryptex build specs.

With real inputs (environment variable: what it points at):
  USERSPACE_X86_CACHE        the 26B131 RosettaOS x86_64 dyld cache folder (read only). The resolved sites of
                             cache-*.json must equal, site for site, what the recipe's own planners output on it
                             (legacy_trackpad, endpoint_security, core_routine, swift_refs, codeid_carrier,
                             tools/metal-fixes mps-plan, tools/audiomxd-fix plan); the engine applies each spec
  USERSPACE_ARM_CACHE        the same build's arm64e cache folder (swift_refs reads its reference types there)
  USERSPACE_CODEID_CARRIER   the row codeid_carrier folder (the carrier's objects)
  USERSPACE_SYSTEM_ROOT      a pristine 26B131 System volume: the two plists (disabled job) and the 27.x WindowServer
  USERSPACE_OCLP_IO80211, USERSPACE_OCLP_WIFIP2P   OCLP 13.7.2-25 wrapper files; USERSPACE_DONOR_COREWIFI: Tahoe's
                             CoreWiFi from the native donors. Spec output (then signed) equals install.py's rename
  USERSPACE_DONOR_WINDOWSERVER  Tahoe's WindowServer from the native donors: spec output equals
                             install.resign_with_entitlements (the 27.x entitlements are not in the spec: the test
                             adds them from the 27.x program, as install.py does)
  HAO_RUNTIME_PAYLOAD        the row hao_runtime folder: the embed-*.json specs are exactly its cryptex manifest entries
  GOLDEN_OS_CRYPTEX / GOLDEN_APP_CRYPTEX  mounted SystemOS / AppOS images: each spec's source hash is the program's
                             and the payload's hao_fat.combine gives the spec's result hash
The cache edits are the sites install.py applies, then it re-signs the cache with 4 KiB pages (src/cache_pages.py),
a step the engine does not do: `cryptex.build` declares it. Likewise the cryptex image builds, and the stub embedding
(`hao.embed-stub`) are declarations whose bytes are proven through the payload's own installer above, not applied by
the engine."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import runpy
import subprocess
import sys
import tempfile
import unittest

PATCH = Path(__file__).resolve().parents[1]
TOP = PATCH.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# install.py imports this recipe's own macho.py (and the planners it names) from src/; the edit engine has a module of
# the same name: the recipe's code is loaded first, its `macho` is set aside while the engine is imported, and put
# back (the other test modules of this folder load install.py again and need it).
install = load('golden_install', PATCH/'src/install.py')
RECIPE_MACHO = sys.modules.pop('macho')
ENGINE_PATHS = [str(TOP/'tools/edit-engine/src'), str(TOP/'tools/edit-engine/tests')]
sys.path[0:0] = ENGINE_PATHS
import engine   # noqa: E402
import fixture   # noqa: E402
import spec as S   # noqa: E402
for entry in ENGINE_PATHS:
    sys.path.remove(entry)
sys.modules['macho'] = RECIPE_MACHO
display = runpy.run_path(str(TOP/'patches/golden-gate-windowserver-filter/src/install_dss.py'))
PATCH_JSON = json.loads((PATCH/'patch.json').read_text())
FILES = {f['path']: f for f in PATCH_JSON['files']}
SPECS = {n.name: S.load(n) for n in sorted((PATCH/'edits').glob('*.json'))}
BY_PATH = {s['path']: (n, s) for n, s in SPECS.items()}
CACHE = 'System/Volumes/Preboot/Cryptexes/OS/System/Library/dyld/'
CRYPTEX = 'System/Volumes/Preboot/Cryptexes/'
EXTERNAL = {'cryptex.build', 'hao.embed-stub'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 24), b''):
            h.update(block)
    return h.hexdigest()


def without_external(spec):
    """The spec the engine can apply: the edits the engine does not carry out are dropped."""
    out = copy.deepcopy(spec)
    out['edits'] = [e for e in out['edits'] if not (e['kind'] == 'op' and e['op'] in S.EXTERNAL_OPS)]
    return out


def at_path(value):
    return Path(value) if value else None


class Declaration(unittest.TestCase):
    def test_specs_follow_the_format_and_are_all_listed(self):
        self.assertEqual(len(SPECS), len(BY_PATH))
        for name, spec in SPECS.items():
            self.assertEqual(S.validate(spec), [], name)
            self.assertTrue(any(f['path'] == spec['path'] and f.get('edits') == 'edits/' + name for f in PATCH_JSON['files']),
                        name)
        self.assertEqual({f['edits'][6:] for f in PATCH_JSON['files'] if 'edits' in f}, set(SPECS))

    def test_origin_entries_are_whole_files_from_this_patchs_rows(self):
        for path, entry in FILES.items():
            if 'origin' in entry:
                self.assertEqual(entry['origin']['kind'], 'hao', path)
                self.assertTrue(entry['origin'].get('note'), path)
                if 'row' in entry['origin']:
                    self.assertIn(entry['origin']['row'], PATCH_JSON['inputs'], path)

    def test_declared_paths_are_the_ones_install_py_uses(self):
        self.assertIn(display['DISPLAY_STATE_SHIM'], FILES)
        self.assertEqual(FILES[display['DISPLAY_STATE_SHIM']]['origin']['row'], 'display_state_shim')
        for folder, leaf, row, renamed in ((install.IO80211, 'IO80211', 'io80211_compat', 'IO80211OCLP.dylib'),
                                           (install.WIFIP2P, 'WiFiPeerToPeer', 'wifip2p_compat', 'WiFiPeerToPeerOCLP.dylib'),
                                           (install.COREWIFI, 'CoreWiFi', 'corewifi_compat', 'CoreWiFiTahoe.dylib')):
            self.assertEqual(FILES[folder + leaf]['origin']['row'], row)
            self.assertIn(folder + renamed, BY_PATH)   # install_wrapper_compat: LEAF + SUFFIX + '.dylib'
        for label, job in install.DISABLED_JOBS.items():
            self.assertIn(job, BY_PATH)
        self.assertIn(install.LAUNCHD_CACHE, BY_PATH)
        for client in install.RESIGN_WITH_27_ENTITLEMENTS:
            self.assertIn(client, BY_PATH)
        self.assertIn('golden-gate-userspace.json', Path(install.__file__).read_text())
        self.assertIn('System/Library/CoreServices/golden-gate-userspace.json', FILES)
        self.assertIn(CRYPTEX + 'Rosetta/System/Library/dyld', FILES)
        self.assertIn('cryptex-cache-alias', Path(install.__file__).read_text())

    def test_windowserver_spec_is_what_resign_with_entitlements_does_to_the_bytes(self):
        """install.resign_with_entitlements: the 27.x Info.plist (matcher), LC_LOAD_DYLIB of the display shim
        (macho.add-dylib: command 0xc, timestamp 2, versions 0x10000), then an ad hoc signature with the 27.x
        identifier and entitlements (the spec's sign names the identifier; the entitlements come from the 27.x
        program, which the format cannot name)."""
        name, spec = BY_PATH[display['DISPLAY_STATE_CLIENTS'][0]]
        edits = [e for e in spec['edits'] if e['kind'] == 'op']
        self.assertEqual([e['args']['path'] for e in edits], ['/' + display['DISPLAY_STATE_SHIM']])
        self.assertEqual(edits[0]['args'], {'path': '/usr/lib/dss_shim.dylib', 'weak': False, 'timestamp': 2,
                                            'current_version': 0x10000, 'compat_version': 0x10000})
        self.assertEqual(spec['sign']['identifier'], 'com.apple.WindowServer')

    def test_add_dylib_writes_the_same_bytes_as_add_load_dylib(self):
        data = fixture.dylib(size=0x4000, text_at=0x1800, text_size=0x1000, symbols={'_main': 0x1810})
        mine = bytearray(data)
        self.assertTrue(install.add_load_dylib(mine, b'/usr/lib/dss_shim.dylib', install.CPU_X86_64))
        spec = {'format': 1, 'path': 'x', 'arch': 'x86_64', 'origin': {'kind': 'tahoe'},
                'edits': [e for e in BY_PATH[display['DISPLAY_STATE_CLIENTS'][0]][1]['edits'] if e['kind'] == 'op'],
                'sign': None}
        out, _ = engine.apply(spec, data, repo=str(TOP))
        self.assertEqual(out, bytes(mine))

    def test_disabled_job_specs_write_the_same_bytes_as_disabled_job(self):
        label, job = next(iter(install.DISABLED_JOBS.items()))
        job_plist = {'Label': label, 'ProgramArguments': ['/usr/libexec/hybridsearchd'],
                     'Disabled': {'feature': 'condition'}}
        cache = {'LaunchDaemons': {'/' + job: dict(job_plist), '/other': {'Label': 'x'}}}
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            for rel, content in ((job, job_plist), (install.LAUNCHD_CACHE, cache)):
                data = plistlib.dumps(content, fmt=fmt, sort_keys=False)
                out, _ = engine.apply(BY_PATH[rel][1], data, repo=str(TOP))
                self.assertEqual(out, install.disabled_job(data, label, job), (rel, fmt))
                self.assertIs(plistlib.loads(out)['Disabled'] if rel == job else
                              plistlib.loads(out)['LaunchDaemons']['/' + job]['Disabled'], True)

    def test_rename_matcher_is_the_difference_set_install_name_makes(self):
        """The resolved sites of the three renames are the changed byte runs of set_install_name on that file. On a
        synthetic dylib the same construction, applied by the engine, gives set_install_name's bytes."""
        folder = install.IO80211
        old, new = ('/' + folder + 'IO80211').encode(), ('/' + folder + 'IO80211OCLP.dylib').encode()
        data = fixture.dylib(size=0x4000, text_at=0x1000, text_size=0x1000, ident=old)
        mine = bytearray(data)
        install.set_install_name(mine, old, new)
        sites = []
        for i, (a, b) in enumerate(zip(data, mine)):
            if a != b:
                if sites and i - (sites[-1]['at'] + len(sites[-1]['before']) // 2) < 16:
                    end = sites[-1]['at'] + len(sites[-1]['before']) // 2
                    sites[-1]['before'] += data[end:i + 1].hex()
                    sites[-1]['after'] += bytes(mine[end:i + 1]).hex()
                else:
                    sites.append({'at': i, 'before': data[i:i + 1].hex(), 'after': bytes(mine[i:i + 1]).hex()})
        template = BY_PATH[folder + 'IO80211OCLP.dylib'][1]
        fake = copy.deepcopy(template)
        fake['edits'][0]['resolved'] = {sha(data): sites}
        fake['sign'] = None
        fake['expect'] = {}
        out, _ = engine.apply(fake, data, repo=str(TOP))
        self.assertEqual(out, bytes(mine))
        self.assertEqual(template['sign'], {'mode': 'adhoc', 'preserve': ['identifier', 'entitlements', 'flags', 'runtime']})

    def test_cache_specs_follow_install_py_order_and_do_not_overlap(self):
        planners = ['legacy-trackpad-v3', 'endpoint-security-selectors', 'core-routine-console-uid',
                    'mps-matrix-initializer', 'audio-no-console-notification', 'swift-conformance-types',
                    'code-identity-carrier']   # install.py applies them in this order to each cache file
        names = set()
        for path, (name, spec) in BY_PATH.items():
            if not path.startswith(CACHE):
                continue
            names.add(path[len(CACHE):])
            ids = [e['id'] for e in spec['edits']]
            self.assertEqual(ids[-1], 'resign-4k-pages')
            self.assertEqual(ids[:-1], sorted(ids[:-1], key=planners.index), name)
            claimed = []
            for e in spec['edits'][:-1]:
                self.assertEqual(len(e['resolved']), 1)
                for sites in e['resolved'].values():
                    claimed += [(s['at'], s['at'] + len(S.hexbytes(s['before']))) for s in sites]
            claimed.sort()
            self.assertTrue(all(a[1] <= b[0] for a, b in zip(claimed, claimed[1:])), name)
        self.assertEqual(names, {'dyld_shared_cache_x86_64'} | {'dyld_shared_cache_x86_64.%s' % n
                                                                  for n in ('01', '03', '04', '06')})

    def test_cryptex_specs_count_the_stub_specs(self):
        stubs = {cx: [s for p, (n, s) in BY_PATH.items() if p.startswith(CRYPTEX + cx + '/')
                      and s['edits'][0].get('op') == 'hao.embed-stub'] for cx in ('OS', 'App')}
        for cx in ('OS', 'App'):
            spec = BY_PATH[CRYPTEX + cx][1]
            counted = [e for e in spec['edits'] if e['id'].startswith('hao-stubs')]
            self.assertEqual(len(counted), 1)
            self.assertEqual(counted[0]['args']['files'], len(stubs[cx]), cx)
            self.assertTrue(all(e['op'] == 'cryptex.build' for e in spec['edits']))
        self.assertEqual((len(stubs['OS']), len(stubs['App'])), (24, 2))

    def test_stub_specs_are_consistent(self):
        seen = set()
        for path, (name, spec) in BY_PATH.items():
            edit = spec['edits'][0]
            if not (edit['kind'] == 'op' and edit['op'] == 'hao.embed-stub'):
                continue
            args = edit['args']
            self.assertTrue(name.startswith('embed-'), name)
            self.assertEqual(spec['expect'], {args['source_sha256']: args['result_sha256']})
            self.assertEqual(args['row'], 'hao_runtime')
            self.assertNotIn(args['stub'], seen)
            seen.add(args['stub'])
            self.assertEqual(len(spec['edits']), 1)
            self.assertIsNone(spec['sign'])


def plan_all(cache_dir, reference, carrier):
    """{cache file name: {edit id: [(offset, before hex, after hex, reason)]}} from the recipe's own planners."""
    import codeid_carrier
    import core_routine
    import endpoint_security
    import legacy_trackpad
    import swift_refs
    out = {}

    def add(edit_id, plan):
        for path, changes in plan.items():
            out.setdefault(Path(path).name, {})[edit_id] = [(o, b.hex(), a.hex(), r) for o, b, a, r in changes]
    add('legacy-trackpad-v3', legacy_trackpad.plan_cache_family(cache_dir))
    add('endpoint-security-selectors', endpoint_security.plan_cache_family(cache_dir))
    add('core-routine-console-uid', core_routine.plan_cache_family(cache_dir))
    for edit_id, tool, args in (('mps-matrix-initializer', 'tools/metal-fixes/metal-fixes', ['mps-plan']),
                                ('audio-no-console-notification', 'tools/audiomxd-fix/audiomxd-fix', ['plan'])):
        result = subprocess.check_output([sys.executable, '-I', str(TOP/tool), *args, '--cache', str(cache_dir)],
                                         text=True)
        for path, changes in json.loads(result).items():
            out.setdefault(Path(path).name, {})[edit_id] = [tuple(c) for c in changes]
    add('swift-conformance-types', swift_refs.plan_cache_family(cache_dir, reference=reference))
    plan, _ = codeid_carrier.plan_cache_family(cache_dir, carrier)
    add('code-identity-carrier', plan)
    return out


@unittest.skipUnless(at_path(os.environ.get('USERSPACE_X86_CACHE')) and at_path(os.environ.get('USERSPACE_ARM_CACHE')) and at_path(os.environ.get('USERSPACE_CODEID_CARRIER')),
                     'set USERSPACE_X86_CACHE, USERSPACE_ARM_CACHE and USERSPACE_CODEID_CARRIER')
class RealCaches(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = at_path(os.environ.get('USERSPACE_X86_CACHE'))
        cls.planned = plan_all(cls.cache, at_path(os.environ.get('USERSPACE_ARM_CACHE')), at_path(os.environ.get('USERSPACE_CODEID_CARRIER')))

    def test_resolved_sites_equal_the_planners_output(self):
        specs = {p[len(CACHE):]: s for p, (n, s) in BY_PATH.items() if p.startswith(CACHE)}
        self.assertEqual(set(specs), set(self.planned))   # every cache file with a plan is declared, and no other
        for fname, spec in specs.items():
            key = sha_file(self.cache/fname)
            declared = {}
            for e in spec['edits'][:-1]:
                self.assertEqual(list(e['resolved']), [key], '%s: the sites are for another input' % fname)
                declared[e['id']] = [(s['at'], s['before'], s['after'], s['reason']) for s in e['resolved'][key]]
            self.assertEqual(declared, {k: [tuple(c) for c in v] for k, v in self.planned[fname].items()}, fname)

    def test_the_engine_applies_each_cache_spec_to_the_real_file(self):
        for path, (name, spec) in BY_PATH.items():
            if not path.startswith(CACHE):
                continue
            data = (self.cache/path[len(CACHE):]).read_bytes()
            out, record = engine.apply(without_external(spec), data, repo=str(TOP))
            self.assertEqual(len(out), len(data))
            self.assertEqual(sum(e['found'] for e in record['edits']),
                             sum(len(e['resolved'][sha(data)]) for e in spec['edits'][:-1]), name)
            for e in spec['edits'][:-1]:
                for s in e['resolved'][sha(data)]:
                    self.assertEqual(out[s['at']:s['at'] + len(S.hexbytes(s['after']))], S.hexbytes(s['after']))


@unittest.skipUnless(at_path(os.environ.get('USERSPACE_SYSTEM_ROOT')), 'set USERSPACE_SYSTEM_ROOT to a pristine 26B131 System volume')
class RealPlistsAndWindowServer(unittest.TestCase):
    def test_disabled_job_specs_on_the_real_plists(self):
        label, job = next(iter(install.DISABLED_JOBS.items()))
        for rel in (job, install.LAUNCHD_CACHE):
            data = (at_path(os.environ.get('USERSPACE_SYSTEM_ROOT'))/rel).read_bytes()
            out, record = engine.apply(BY_PATH[rel][1], data, repo=str(TOP))
            self.assertEqual(out, install.disabled_job(data, label, job))
            self.assertEqual(record['expected'], 'match')

    @unittest.skipUnless(at_path(os.environ.get('USERSPACE_DONOR_WINDOWSERVER')), 'set USERSPACE_DONOR_WINDOWSERVER')
    def test_windowserver_spec_equals_resign_with_entitlements(self):
        rel = display['DISPLAY_STATE_CLIENTS'][0]
        golden = at_path(os.environ.get('USERSPACE_SYSTEM_ROOT'))/rel
        original = at_path(os.environ.get('USERSPACE_DONOR_WINDOWSERVER')).read_bytes()
        name, spec = BY_PATH[rel]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            kept = tmp/'reference'
            kept.write_bytes(original)
            install.resign_with_entitlements(kept, golden, tmp, (display['DISPLAY_STATE_SHIM'],))
            out, record = engine.apply(spec, original, repo=str(TOP))   # unsigned
            mine = tmp/'declared'
            mine.write_bytes(out)
            entitlements = tmp/'entitlements.plist'
            entitlements.write_bytes(subprocess.run(['codesign', '-d', '--entitlements', '-', '--xml', str(golden)],
                                                    check=True, capture_output=True).stdout)
            subprocess.run([*S.codesign_args(spec['sign']), '--entitlements', str(entitlements), str(mine)],
                           check=True, capture_output=True)
            self.assertEqual(mine.read_bytes(), kept.read_bytes())
            # the unsigned bytes differ from the reference only in the signature
            self.assertEqual(len(out), len(original))


@unittest.skipUnless(at_path(os.environ.get('USERSPACE_OCLP_IO80211')) and at_path(os.environ.get('USERSPACE_OCLP_WIFIP2P')) and at_path(os.environ.get('USERSPACE_DONOR_COREWIFI')),
                     'set USERSPACE_OCLP_IO80211, USERSPACE_OCLP_WIFIP2P and USERSPACE_DONOR_COREWIFI')
class RealRenames(unittest.TestCase):
    def test_renamed_wrappers_equal_install_wrapper_compat(self):
        for source, where, leaf, suffix in (
                (at_path(os.environ.get('USERSPACE_OCLP_IO80211')), install.IO80211, 'IO80211', 'OCLP'),
                (at_path(os.environ.get('USERSPACE_OCLP_WIFIP2P')), install.WIFIP2P, 'WiFiPeerToPeer', 'OCLP'),
                (at_path(os.environ.get('USERSPACE_DONOR_COREWIFI')), install.COREWIFI, 'CoreWiFi', 'Tahoe')):
            spec = BY_PATH[where + leaf + suffix + '.dylib'][1]
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                # install_wrapper_compat, step by step: the install name, then codesign with the file's identifier
                data = bytearray(source.read_bytes())
                install.set_install_name(data, ('/' + where + leaf).encode(), ('/' + where + leaf + suffix + '.dylib').encode())
                reference = tmp/'reference.dylib'
                reference.write_bytes(data)
                details = subprocess.run(['codesign', '-dv', str(source)], check=True, capture_output=True,
                                         text=True).stderr
                identifier = next(l.split('=', 1)[1] for l in details.splitlines() if l.startswith('Identifier='))
                subprocess.run(['codesign', '-f', '-s', '-', '-i', identifier, '--preserve-metadata=entitlements,flags,runtime',
                                str(reference)], check=True, capture_output=True)
                out, record = engine.apply(spec, source.read_bytes(), repo=str(TOP))
                mine = tmp/'declared.dylib'
                mine.write_bytes(out)
                subprocess.run(S.codesign_args(spec['sign'], identifier) + [str(mine)], check=True, capture_output=True)
                self.assertEqual(mine.read_bytes(), reference.read_bytes(), leaf)
                self.assertEqual(sha(mine.read_bytes()), spec['expect'][sha(source.read_bytes())])


def stub_args(entry):
    return {'row': 'hao_runtime', 'identifier': entry['identifier'], 'stub': entry['stub'],
            'stub_sha256': entry['stub_sha256'], 'source_sha256': entry['source_sha256'],
            'result_sha256': entry['result_sha256'], 'bound': entry.get('bound', [])}


@unittest.skipUnless(at_path(os.environ.get('HAO_RUNTIME_PAYLOAD')), 'set HAO_RUNTIME_PAYLOAD to the hao_runtime row')
class RealStubs(unittest.TestCase):
    def setUp(self):
        self.hao = at_path(os.environ.get('HAO_RUNTIME_PAYLOAD'))
        self.cryptex_stubs = load('cryptex_stubs', PATCH/'src/cryptex_stubs.py')

    def test_embed_specs_are_the_payloads_cryptex_entries(self):
        for cx in ('OS', 'App'):
            entries = self.cryptex_stubs.entries(self.hao, cx)
            declared = {p: s for p, (n, s) in BY_PATH.items() if p.startswith(CRYPTEX + cx + '/')
                        and s['edits'][0]['kind'] == 'op' and s['edits'][0]['op'] == 'hao.embed-stub'}
            self.assertEqual(set(declared), {e['path'] for e in entries}, cx)
            for e in entries:
                self.assertEqual(declared[e['path']]['edits'][0]['args'], stub_args(e), e['path'])
                self.assertEqual(sha((self.hao/'embed'/e['stub']).read_bytes()), e['stub_sha256'])

    def test_sources_and_merged_results_on_the_mounted_cryptexes(self):
        installer = self.cryptex_stubs.load_installer(self.hao)
        checked = 0
        for cx, root in (('OS', at_path(os.environ.get('GOLDEN_OS_CRYPTEX'))),
                         ('App', at_path(os.environ.get('GOLDEN_APP_CRYPTEX')))):
            if not root:
                continue
            for e in self.cryptex_stubs.entries(self.hao, cx):
                entry = self.cryptex_stubs.rebase(e, cx)
                data = (root/entry['path']).read_bytes()
                self.assertEqual(sha(data), entry['source_sha256'], entry['path'])
                merged = installer.hao_fat.combine(data, (self.hao/'embed'/entry['stub']).read_bytes(), drop_x86_64=False)
                self.assertEqual(sha(merged), entry['result_sha256'], entry['path'])
                checked += 1
        self.assertTrue(checked, 'set GOLDEN_OS_CRYPTEX (and GOLDEN_APP_CRYPTEX) to mounted cryptex images')


if __name__ == '__main__':
    unittest.main()
