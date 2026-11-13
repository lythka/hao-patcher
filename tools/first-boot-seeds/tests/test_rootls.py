import datetime, hashlib, importlib.machinery, importlib.util, json, os, shutil, sqlite3, stat, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader('rootls', str(HERE.parent / 'rootls'))
spec = importlib.util.spec_from_loader('rootls', loader)
rl = importlib.util.module_from_spec(spec)
loader.exec_module(rl)

SCHEMA, BUILD = 35651614, '26B131'
MAIN = 'com.apple.LaunchServices-%d-v2.csstore' % SCHEMA
SDO = 'SystemDataOnly-' + MAIN
# What a clean root store holds besides binary: Apple's own paths and rules, which check must let through.
CLEAN = (b'bdsl\x02\x00' + b'\x00' * 10 + b'/System/Applications/Safari.app\x00/System/Volumes/Preboot/Cryptexes/App\x00'
         b'(regex #"^/Users/Shared")\x00State:/Users/ConsoleUser\x00'
         b'(regex (string-append "^/Volumes/" (uuid-regex-string) "(/|$)"))\x00Y/Volumes/_\x00')


def auth_db(path, wal=False):
    con = sqlite3.connect(str(path))
    if wal:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA wal_autocheckpoint=0')
    con.execute("CREATE TABLE config ('key' TEXT PRIMARY KEY NOT NULL UNIQUE,value)")
    con.execute('CREATE TABLE rules (id INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE,name TEXT NOT NULL UNIQUE)')
    con.execute("INSERT INTO config VALUES ('version', 2)")
    con.execute("INSERT INTO rules (name) VALUES ('system.login.console')")
    con.commit()
    return con


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.data = self.t / 'Data'
        self.ls = self.data / rl.ROOT_LS
        self.ls.mkdir(parents=True)
        (self.ls / MAIN).write_bytes(CLEAN + b'main')
        (self.ls / SDO).write_bytes(CLEAN + b'system')
        (self.ls / '.lock').write_bytes(b'')
        (self.data / 'private/var/db').mkdir(parents=True)
        auth_db(self.data / rl.AUTH_DB).close()
        (self.data / 'Users/Shared').mkdir(parents=True)
        self.out = self.t / 'artifacts'

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, **kw):
        return rl.collect(self.data, BUILD, SCHEMA, self.out, 'qemu run x', today=datetime.date(2026, 11, 12), **kw)


class Collect(Fixture):
    def test_artifact(self):
        a = self.collect()
        self.assertRegex(a.name, r'^root-launchservices-26B131-20261112-[0-9a-f]{12}$')
        self.assertEqual(sorted(p.name for p in (a / 'ls').iterdir()), sorted([MAIN, SDO]))
        b = json.loads((a / 'build.json').read_text())
        self.assertEqual((b['build'], b['schema'], b['auth_rules'], b['not_copied']), (BUILD, SCHEMA, 1, ['.lock']))
        self.assertTrue(b['origin'].startswith('generated'))
        sums = (a / 'SHA256SUMS').read_text()
        self.assertEqual(a.name[-12:], hashlib.sha256(sums.encode()).hexdigest()[:12])
        for line in sums.splitlines():
            h, p = line.split('  ')
            self.assertEqual(h, rl.sha_file(a / p))
        self.assertEqual(sorted(line.split('  ')[1] for line in sums.splitlines()),
                         sorted(['auth.db', 'build.json', 'ls/' + MAIN, 'ls/' + SDO]))
        self.assertEqual(rl.check(a, BUILD, SCHEMA)['auth']['rules'], 1)
        self.assertEqual([p.name for p in self.out.iterdir()], [a.name])   # no work folder left behind

    def test_never_overwrites(self):
        self.collect()
        with self.assertRaises(rl.Refused):
            self.collect()
        self.assertEqual(len(list(self.out.iterdir())), 1)

    def test_wal_folded(self):
        (self.data / rl.AUTH_DB).unlink()
        con = auth_db(self.data / rl.AUTH_DB, wal=True)     # held open: the rules are still only in the -wal
        try:
            self.assertTrue(Path(str(self.data / rl.AUTH_DB) + '-wal').stat().st_size > 0)
            a = self.collect()
        finally:
            con.close()
        self.assertEqual(rl.check(a, BUILD)['auth']['rules'], 1)
        self.assertFalse(Path(str(a / 'auth.db') + '-wal').exists())

    def test_refuses_after_setup(self):
        (self.data / 'Users/benchtest').mkdir()
        with self.assertRaisesRegex(rl.Refused, 'accounts'):
            self.collect()

    def test_refuses_unbooted(self):
        shutil.rmtree(self.ls)
        with self.assertRaisesRegex(rl.Refused, 'no .csstore'):
            self.collect()
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_refuses_wrong_schema_and_leaves_nothing(self):
        with self.assertRaisesRegex(rl.Refused, 'schema'):
            rl.collect(self.data, BUILD, 20971545, self.out)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_reads_only_its_two_paths(self):
        (self.data / 'Library/Keychains').mkdir(parents=True)
        (self.data / 'Library/Keychains/System.keychain').write_bytes(b'secret')
        a = self.collect()
        self.assertFalse(any('keychain' in p.name.lower() for p in a.rglob('*')))


class Check(Fixture):
    def setUp(self):
        super().setUp()
        self.a = self.collect()

    def put(self, name, data):
        p = self.a / 'ls' / name
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR) if p.exists() else None
        p.write_bytes(data)

    def test_clean_passes(self):
        self.assertEqual(rl.check(self.a, BUILD, SCHEMA)['schema'], SCHEMA)

    def test_account_home(self):
        self.put(MAIN, CLEAN + b'/Users/benchtest/Library/Fonts\x00')
        with self.assertRaisesRegex(rl.Refused, "account home '/Users/benchtest'"):
            rl.check(self.a, BUILD)

    def test_builder_mount(self):
        self.put(SDO, CLEAN + b'/Volumes/builder-work-20261112/System\x00')
        with self.assertRaisesRegex(rl.Refused, 'mount outside'):
            rl.check(self.a, BUILD)

    def test_harness(self):
        self.put(MAIN, CLEAN + b'LaunchDaemons/local.testbench.plist\x00')
        with self.assertRaisesRegex(rl.Refused, 'test harness'):
            rl.check(self.a, BUILD)

    def test_deny(self):
        self.put(MAIN, CLEAN + b'serial C02XYZ123\x00')
        rl.check(self.a, BUILD)
        with self.assertRaisesRegex(rl.Refused, '--deny'):
            rl.check(self.a, BUILD, deny=['C02XYZ123'])

    def test_magic_name_schema_main(self):
        self.put(MAIN, b'xxxx' + CLEAN)
        with self.assertRaisesRegex(rl.Refused, 'bdsl'):
            rl.check(self.a, BUILD)
        self.put(MAIN, CLEAN)
        with self.assertRaisesRegex(rl.Refused, 'schema'):
            rl.check(self.a, BUILD, 20971545)
        self.put('com.apple.LaunchServices-1-v2.csstore', CLEAN)
        with self.assertRaisesRegex(rl.Refused, 'several schemas'):
            rl.check(self.a, BUILD)
        (self.a / 'ls/com.apple.LaunchServices-1-v2.csstore').unlink()
        (self.a / 'ls' / MAIN).unlink()
        with self.assertRaisesRegex(rl.Refused, 'no main store'):
            rl.check(self.a, BUILD)

    def test_auth(self):
        os.chmod(self.a / 'auth.db', 0o600)
        (self.a / 'auth.db').unlink()
        con = auth_db(self.a / 'auth.db')
        con.execute("UPDATE config SET value = 1 WHERE key = 'version'")
        con.commit(); con.close()
        with self.assertRaisesRegex(rl.Refused, 'config version'):
            rl.check(self.a, BUILD)

    def test_build_json(self):
        with self.assertRaisesRegex(rl.Refused, 'build.json is for 26B131'):
            rl.check(self.a, '26B140')

    def test_dump_guard(self):
        fake = self.t / 'lsregister'
        fake.write_text('#!/bin/sh\necho "Checking data integrity..."\necho "Seeded System Version:      %s"\n'
                        'echo "bundle id:  1"\necho "bundle id:  2"\n')
        fake.chmod(0o755)
        fake.write_text(fake.read_text() % '27.2.1 (26B131)')
        self.assertEqual(rl.check(self.a, BUILD, dump=True, lsregister=str(fake))['dump_bundles'], 2)
        # lsregister falls back to the running system's own database when it cannot read the file
        fake.write_text(fake.read_text().replace('27.2.1 (26B131)', '27.2 (26B123)'))
        with self.assertRaisesRegex(rl.Refused, 'cannot read its schema'):
            rl.check(self.a, BUILD, dump=True, lsregister=str(fake))


if __name__ == '__main__':
    unittest.main()
