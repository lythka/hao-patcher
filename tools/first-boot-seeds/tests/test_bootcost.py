import contextlib, importlib.machinery, importlib.util, io, json, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader('bootcost', str(HERE.parent / 'bootcost'))
spec = importlib.util.spec_from_loader('bootcost', loader)
bc = importlib.util.module_from_spec(spec)
loader.exec_module(bc)

ZERO = 1794533659.0          # 2026-11-12 17:34:19 PST, a boot's mach time zero


def stamp(t):
    import datetime
    d = datetime.datetime.fromtimestamp(t, datetime.timezone(datetime.timedelta(hours=-8)))
    return d.strftime('%Y-%m-%d %H:%M:%S.%f') + '-0800'


def ev(boot, t, proc, msg, uid=0, zero=ZERO):
    return {'bootUUID': boot, 'timestamp': stamp(zero + t), 'machTimestamp': int(t * 1e9),
            'processImagePath': '/usr/libexec/' + proc, 'userID': uid, 'eventMessage': msg}


FIRST = [
    ('lsd', 38.15, 'lsd starting with schema version 35651614 for effective user 0'),
    ('lsd', 38.2, '_CSStoreCreateWithURL failed with error Error Domain=NSCocoaErrorDomain Code=260 "no such file"'),
    ('authd', 38.56, 'authdb: open /var/db/auth.db (14) unable to open database file'),
    ('authd', 40.0, 'authdb: importing right system.print.admin success'),
    ('authd', 42.0, 'authdb: importing rule authenticate success'),
    ('lsd', 53.9, '#LSDatabaseBuilder Seeding database with UID: 0, EUID 0'),
    ('lsd', 54.5, '#LSDatabaseBuilder registered plugin <private>'),
    ('lsd', 60.0, 'Extension point <private> not in the binding map, scanning all extension points'),
    ('lsd', 93.15, '#LSDatabaseBuilder system plugin scan registered 486 plugins'),
    ('lsd', 93.49, '#LSDatabaseBuilder Saved system content database'),
    ('loginwindow', 94.36, 'Database was seeding; client will retry and block. This is not an error.'),
    ('fskitd', 95.4, 'Thread <private> Waiting for Launch Services seeding, UID 0, max wait 120.0000 sec'),
    ('airportd', 113.7, 'Database was seeding; client will retry and block. This is not an error.'),
    ('lsd', 94.46, '#LSDatabaseBuilder registered <private>'),
    ('lsd', 94.5, '#LSDatabaseBuilder registered library com.apple.x'),
    ('lsd', 122.55, '#LSDatabaseBuilder set seeding complete'),
    ('fskitd', 122.61, 'Launch Services seeding complete after 120.0000 sec wait'),
    ('storagekitd', 56.7, '-[SK_DM_DaemonConnection SK_DM_APFSEnsureBooter_updatePrebootForVolume:keepSessionAlive:'
                          'options:reply:]: disk1s2s1, keepSessionAlive=1'),
    ('storagekitd', 70.38, '-[x]_block_invoke: Update preboot of disk1s2s1 is complete, keepSessionAlive=0'),
    ('storagekitd', 116.75, '-[x]_block_invoke: Update preboot of disk1s2s1 is complete, keepSessionAlive=1'),
    ('mbsystemadministration', 106.6, '-[DIAttachParams newAttachWithError:]: entry: DiskImageParamsRAM_XPC'),
    ('mbsystemadministration', 132.08, '+[DICommonAttach DI2_attachWithParams:BSDName:error:]: Disk image attached'),
    ('SetupAssistantSpringboard', 101.49, 'Starting SetupAssistantSpringboard for user 0...'),
    ('loginwindow', 148.65, '-[LoginApp performAuthPluginRequest:] | ======= attempting login for user: _mbsetupuser'),
    ('Setup Assistant', 155.7, 'anything'),
]


def lines(boot, events, zero=ZERO, uid=0):
    return [ev(boot, t, p, m, uid, zero) for p, t, m in events]


class Analyse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rows = lines('AAAA-1', FIRST)
        rows += [ev('AAAA-1', 174.0 + i, 'fontworker', 'CFURLCreateBookmarkData', 248) for i in range(3)]
        rows += [ev('AAAA-1', 210.9, 'chronod', '[tips.Widget-1] Extension process termination requested (69)', 248),
                 ev('AAAA-1', 210.9, 'chronod', '[tips.Widget-1] Extension process termination requested (70)', 248),
                 ev('AAAA-1', 210.9, 'chronod', '[notes.Widget-2] Extension process termination requested (71)', 248)]
        z2 = ZERO + 520
        rows += lines('BBBB-2', [('lsd', 38.06, 'lsd starting with schema version 35651614 for effective user 0'),
                                 ('authd', 46.57, 'Database check OK'),
                                 ('UpdateSettingsTool', 37.36, 'Last run was on 27.2.1 build x. Skipping this time'),
                                 ('Language Chooser', 81.36, 'Retrieve User by ID')], z2)
        self.path = Path(self.tmp.name) / 'log.ndjson'
        self.path.write_text('Filtering the log data\n' + ''.join(json.dumps(r) + '\n' for r in rows[::-1]))
        self.reports = bc.analyse(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_boots_in_time_order(self):
        self.assertEqual([r['boot'] for r in self.reports], ['AAAA-1', 'BBBB-2'])
        self.assertEqual([r['first_boot'] for r in self.reports], [True, False])
        self.assertEqual({r['launchservices_schema'] for r in self.reports}, {35651614})

    def test_times_after_mach_zero(self):
        m = self.reports[0]['milestones']
        self.assertAlmostEqual(m['lsd_seed_start'], 53.9, places=2)
        self.assertAlmostEqual(m['loginwindow_ls_unblocked'], 94.36, places=2)
        self.assertAlmostEqual(m['ls_block_end'], 113.7, places=2)
        self.assertEqual(self.reports[0]['first_screen'], 155.7)
        self.assertEqual(self.reports[1]['first_screen'], 81.36)

    def test_spans(self):
        s = {x['name']: x['seconds'] for x in self.reports[0]['spans']}
        self.assertAlmostEqual(s['lsd root store build'], 84.4, places=1)
        self.assertAlmostEqual(s['  system plugin scan'], 39.25, places=1)
        self.assertAlmostEqual(s['fskitd waits for LaunchServices seeding'], 27.21, places=1)
        self.assertAlmostEqual(s['authd builds auth.db'], 3.44, places=1)
        # the kept-alive update (writeconfig's), not the quick one that finished at 70.38
        self.assertAlmostEqual(s['Preboot update kept alive (writeconfig for UpdateSettingsTool)'], 60.05, places=1)
        self.assertAlmostEqual(s['Setup RAM disk attach (mbsystemadministration)'], 25.48, places=1)
        self.assertEqual(self.reports[1]['spans'], [])

    def test_counts(self):
        c = self.reports[0]['counts']
        self.assertEqual(c['lsd_plugins'], 1)
        self.assertEqual(c['lsd_bundles'], 1)          # "registered library" is not a bundle
        self.assertEqual(c['authd_imports'], 2)
        self.assertEqual(c['ls_blocked_lines'], 2)
        self.assertEqual(self.reports[0]['ls_blocked_processes'], {'loginwindow': 1, 'airportd': 1})
        self.assertEqual(self.reports[0]['widgets_killed'], 2)
        self.assertEqual(self.reports[0]['fontworker_bookmarks']['248']['count'], 3)
        self.assertIn('ust_skipped', self.reports[1]['milestones'])

    def test_table_and_json(self):
        t = bc.table(self.reports)
        self.assertIn('lsd root store build', t)
        self.assertIn('84.4 s', t)
        out = Path(self.tmp.name) / 'r.json'
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            bc.main([str(self.path), '--json', str(out)])
        self.assertIn('first boot (lsd had no store)', printed.getvalue())
        self.assertEqual(json.loads(out.read_text())[0]['boot'], 'AAAA-1')


if __name__ == '__main__':
    unittest.main()
