"""cache/seeds: collect-template.sh on a fixture home, template on its harvest (no Apple files, no laptop)."""
import json
import os
import plistlib
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
COLLECT = HERE / 'collect-template.sh'
TEMPLATE = HERE / 'template'
NL = 'Library/User Template/Non_localized'
META = '.com.apple.containermanagerd.metadata.plist'
PLACEHOLDER = 'Data/SystemData/com.apple.chrono/placeholders/systemSmall/w----158w-158h.chrono-timeline'
PEOPLE_KEY = 'Ƭ\x01\x2e::com.apple.PeopleViewService.PeopleWidget-macOS'
WEATHER_KEY = 'Ƭ\x01\x1a::com.apple.weather.widget'


def xattr(path, name, value):
    subprocess.run(['xattr', '-w', name, value, str(path)], check=True)


def xattrs(path):
    return subprocess.run(['xattr', str(path)], capture_output=True, text=True).stdout.split()


class Home:
    """A first-login home as macOS 27 leaves it: containers with links, app data, metadata with a compiled profile."""

    def __init__(self, d, build='26B131', placeholder=b'placeholder archive'):
        self.build = build
        self.home = Path(d) / 'Users/benchtest'
        lib = self.home / 'Library'
        for name, personality in (('com.apple.weather', False), ('com.apple.stocks.widget', True)):
            c = lib / 'Containers' / name
            (c / 'Data/Library/Caches').mkdir(parents=True)
            (c / 'Data/Library/Caches/app.db').write_bytes(b'user data of benchtest')
            os.symlink('../../../../Preferences', c / 'Data/Library/Preferences')
            os.symlink('../../../../Keychains', c / 'Data/Library/Keychains')
            info = {'com.apple.MobileInstallation.ContentProtectionClass': -1,
                    'SandboxProfileData': b'\0compiled /Users/benchtest/Library and C/dg6v',
                    'SandboxProfileDataValidationInfo': {'Parameters': {'_HOME': '/Users/benchtest', '_USER': 'benchtest'}}}
            if personality:
                info['LSDataContainerPersonalityInfo'] = True
            (c / META).write_bytes(plistlib.dumps({'MCMMetadataIdentifier': name, 'MCMMetadataSchemaVersion': 44,
                                                   'MCMMetadataInfo': info}, fmt=plistlib.FMT_BINARY))
            xattr(c, 'com.apple.containermanager.identity', 'v: 01\nschema: 044\nidentifier: ' + name)
            if personality:
                xattr(c, 'com.apple.data-container-personality', 'bplist00 icns')
                p = c / PLACEHOLDER
                p.parent.mkdir(parents=True)
                p.write_bytes(placeholder)
                (c / 'Data/SystemData/com.apple.chrono/timelines').mkdir()
                (c / 'Data/SystemData/com.apple.chrono/timelines/t.chrono-timeline').write_bytes(b'timeline')
        g = lib / 'Group Containers/group.com.apple.chronod'
        (g / 'chronod/replicator').mkdir(parents=True)
        (g / 'chronod/replicator/ReplicatorClient.sql').write_bytes(b'replicator')
        (g / META).write_bytes(plistlib.dumps({'MCMMetadataSchemaVersion': 44, 'MCMMetadataInfo': {}}))
        db = sqlite3.connect(g / 'chronod/chrono.sql')
        db.execute('PRAGMA journal_mode = WAL')
        db.executescript('''
            CREATE TABLE ExtensionMetadata (bundleIdentifier TEXT NOT NULL, version TEXT NOT NULL,
                                            ingestedByVersion TEXT NULLABLE, PRIMARY KEY (bundleIdentifier));
            CREATE TABLE Descriptors (bundleIdentifier TEXT NOT NULL, data BLOB NOT NULL,
                                      reloadRequired INTEGER NOT NULL DEFAULT 0, descriptorType INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE HostConfigs (host TEXT, data BLOB);
            CREATE TABLE ReloadState (k TEXT, v REAL);''')
        for key, data in ((WEATHER_KEY, b'weather intent'), (PEOPLE_KEY, b'contact card Bench Test')):
            db.execute('INSERT INTO ExtensionMetadata VALUES (?, ?, ?)', (key, '1-1-(1970)', build))
            db.execute('INSERT INTO Descriptors (bundleIdentifier, data) VALUES (?, ?)', (key, data))
        db.execute("INSERT INTO HostConfigs VALUES ('NotificationCenter', X'00')")
        db.execute("INSERT INTO ReloadState VALUES ('calendar', 1794422858.6)")
        db.commit()
        self.db = db   # left open: the WAL is not checkpointed, as on a running laptop
        r = lib / 'Group Containers/group.com.apple.reminders'
        (r / 'Container_v1/Stores').mkdir(parents=True)
        (r / META).write_bytes(plistlib.dumps({'MCMMetadataSchemaVersion': 44, 'MCMMetadataInfo': {}}))
        s = sqlite3.connect(r / 'Container_v1/Stores/Data-local.sqlite')
        s.execute('CREATE TABLE ZREMCDACCOUNT (Z_PK INTEGER, ZIDENTIFIER TEXT)')
        s.execute("INSERT INTO ZREMCDACCOUNT VALUES (1, '5D18E4D0')")
        s.commit()
        s.close()
        (r / 'Container_v1/Stores/notes.txt').write_bytes(b'other')
        (lib / 'Group Containers/group.com.apple.notes').mkdir()
        (lib / 'Group Containers/group.com.apple.notes/NoteStore.sqlite').write_bytes(b'notes of benchtest')
        (lib / 'Preferences').mkdir()
        (lib / 'Preferences/com.apple.chronod.plist').write_bytes(plistlib.dumps(
            {'migrationState': b'{"systemVersion":"%s"}' % build.encode(), 'lastKnownTimes': {'world': 1.0},
             'NetworkEnabledAfterBootNotification.lastLaunchBootSessionUUID': 'B00T',
             'extensionsPendingDescriptorRefetch': []}, fmt=plistlib.FMT_BINARY))
        (lib / 'Preferences/com.apple.finder.plist').write_bytes(b'not collected')

    def collect(self, out):
        env = dict(os.environ, SEED_USER='benchtest', SEED_HOME=str(self.home), SEED_REALNAME='Bench Test')
        with open(out, 'wb') as f:
            r = subprocess.run(['sh', str(COLLECT)], stdout=f, stderr=subprocess.PIPE, env=env)
        self.db.close()
        if r.returncode:
            raise AssertionError(r.stderr.decode())
        return out


def template(harvest, out):
    return subprocess.run(['python3.14', '-I', str(TEMPLATE), str(harvest), '--out', str(out)],
                          capture_output=True, text=True)


def extract(cpio, d):
    subprocess.run(['ditto', '-x', str(cpio), str(d)], check=True)
    return Path(d)


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_collect(self):
        home = Home(self.d)
        h = extract(home.collect(self.d / 'h.cpio'), self.d / 'x')
        lib = h / NL / 'Library'
        self.assertIn('BuildVersion', (h / 'diag/system.txt').read_text())
        self.assertIn('home\t%s\n' % home.home, (h / 'diag/account.txt').read_text())
        self.assertIn('com.apple.weather', (h / 'diag/containers.tsv').read_text())
        # no app data and no other roots leave the home
        self.assertFalse((lib / 'Containers/com.apple.weather/Data/Library/Caches/app.db').exists())
        self.assertFalse((lib / 'Group Containers/group.com.apple.notes').exists())
        self.assertFalse((lib / 'Preferences/com.apple.finder.plist').exists())
        self.assertFalse((lib / 'Containers/com.apple.stocks.widget/Data/SystemData/com.apple.chrono/timelines/'
                                't.chrono-timeline').exists())
        self.assertTrue((lib / 'Containers/com.apple.stocks.widget' / PLACEHOLDER).is_file())
        self.assertIn('com.apple.data-container-personality', xattrs(lib / 'Containers/com.apple.stocks.widget'))
        # the open WAL is in the backup
        db = sqlite3.connect(lib / 'Group Containers/group.com.apple.chronod/chronod/chrono.sql')
        self.assertEqual(db.execute('SELECT count(*) FROM Descriptors').fetchone()[0], 2)
        db.close()

    def test_template(self):
        home = Home(self.d)
        harvest = home.collect(self.d / 'h.cpio')
        (self.d / 'out').mkdir()
        r = template(harvest, self.d / 'out')
        self.assertEqual(r.returncode, 0, r.stderr)
        art = next((self.d / 'out').glob('user-template-*'))   # the build is the collecting machine's (sw_vers)
        build = subprocess.run(['sw_vers', '-buildVersion'], capture_output=True, text=True).stdout.strip()
        self.assertTrue(art.name.startswith('user-template-%s-' % build), art.name)
        self.assertIn('user_template_golden_gate', r.stdout)
        record = json.loads((art / 'build.json').read_text())
        self.assertEqual(record['build'], build)
        c = record['counts']
        self.assertEqual((c['containers'], c['personality_xattrs'], c['personality_flags_added'],
                          c['sandbox_profiles_removed'], c['chrono_people_rows_removed']), (2, 1, 1, 2, 2))
        sums = (art / 'SHA256SUMS').read_text()
        self.assertIn('user-template.cpio', sums)
        self.assertTrue(art.name.endswith(__import__('hashlib').sha256(sums.encode()).hexdigest()[:12]))
        x = extract(art / 'user-template.cpio', self.d / 'x')
        self.assertEqual(sorted(p.name for p in x.iterdir()), ['Library'])
        lib = x / NL / 'Library'
        self.assertEqual([p for p in lib.rglob('*') if p.is_symlink()], [])
        for name in ('com.apple.weather', 'com.apple.stocks.widget'):
            m = plistlib.loads((lib / 'Containers' / name / META).read_bytes())
            self.assertEqual(m['MCMMetadataSchemaVersion'], 0)
            self.assertIs(m['MCMMetadataInfo']['LSDataContainerPersonalityInfo'], True)
            self.assertNotIn('SandboxProfileData', m['MCMMetadataInfo'])
            self.assertNotIn('SandboxProfileDataValidationInfo', m['MCMMetadataInfo'])
            self.assertTrue((lib / 'Containers' / name / META).read_bytes().startswith(b'bplist'))
            self.assertIn('com.apple.containermanager.identity', xattrs(lib / 'Containers' / name))
            self.assertTrue((lib / 'Containers' / name / 'Data/Library').is_dir())
        self.assertIn('com.apple.data-container-personality', xattrs(lib / 'Containers/com.apple.stocks.widget'))
        self.assertTrue((lib / 'Containers/com.apple.stocks.widget' / PLACEHOLDER).is_file())
        g = lib / 'Group Containers/group.com.apple.chronod'
        self.assertEqual(plistlib.loads((g / META).read_bytes())['MCMMetadataSchemaVersion'], 0)
        self.assertNotIn('LSDataContainerPersonalityInfo', plistlib.loads((g / META).read_bytes())['MCMMetadataInfo'])
        self.assertEqual(sorted(p.name for p in (g / 'chronod').iterdir()), ['chrono.sql'])
        db = sqlite3.connect(g / 'chronod/chrono.sql')
        self.assertEqual([r[0] for r in db.execute('SELECT bundleIdentifier FROM Descriptors')], [WEATHER_KEY])
        self.assertEqual([r[0] for r in db.execute('SELECT bundleIdentifier FROM ExtensionMetadata')], [WEATHER_KEY])
        self.assertEqual(db.execute('SELECT count(*) FROM HostConfigs').fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT count(*) FROM ReloadState').fetchone()[0], 0)
        db.close()
        self.assertEqual(sorted(p.name for p in (lib / 'Group Containers/group.com.apple.reminders/Container_v1/Stores')
                                .iterdir()), ['Data-local.sqlite'])
        prefs = plistlib.loads((lib / 'Preferences/com.apple.chronod.plist').read_bytes())
        self.assertEqual(sorted(prefs), ['migrationState'])

    def test_refuses_account_traces(self):
        home = Home(self.d, placeholder=b'tap url file:///Users/benchtest/Library/x')
        harvest = home.collect(self.d / 'h.cpio')
        (self.d / 'out').mkdir()
        r = template(harvest, self.d / 'out')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('names the harvest account', r.stderr)
        self.assertEqual(list((self.d / 'out').glob('user-template-*')), [])


if __name__ == '__main__':
    unittest.main()
