"""src/progress.py: a backend's output goes to the log; the screen gets the install's phases in plain words."""
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import console as CON      # noqa: E402
import progress as PROG    # noqa: E402

ASSEMBLY = '''golden-assemble.sh: caffeinate -dimsu keeps this Mac awake until the assembly ends (pid 1241)
tar: Could not pack extended attributes: Too many levels of symbolic links
STEP layout
Started partitioning on disk0
STEP golden-system
STEP golden-system
RESULT rc=0
STEP group-volumes
STEP golden-finalize
STEP golden-data
STEP tahoe-extensions
STEP boot-and-esp
STEP hardware
{"installed": ["AppleIntelHD4000Graphics.kext"]}
STEP recipes
STEP recipe darwin27-kernel
STEP recipe golden-gate-userspace
@hao {"ev": "progress", "done": 3, "total": 4, "unit": "files"}
STEP recipe golden-gate-hello
STEP recipe golden-gate-store
STEP snapshot
STEP posts
STEP post hao-opencore
STEP post golden-gate-shader-caches
STEP efi-bundle
Volume EFI on disk0s1 unmounted
RESULT rc=0
'''
GROUPS = {'base': ['darwin27-kernel', 'golden-gate-userspace', 'hao-opencore'],
          'metal-3802': ['golden-gate-hello'], 'intel-hd4000': ['golden-gate-shader-caches'],
          '(no pack)': ['golden-gate-store']}


def sq(text):
    """TEXT with each run of spaces after a word made two: the check-mark column's padding is not what is tested."""
    return re.sub(r'(?<=\S) {2,}', '  ', text)


class Pipe(io.StringIO):
    encoding = 'utf-8'

    def isatty(self):
        return False


class Tty(Pipe):
    def isatty(self):
        return True


class FakeProcess:
    def __init__(self, text, rc):
        self.stdout = io.BytesIO(text.encode())
        self.rc = rc

    def wait(self):
        return self.rc

    def terminate(self):
        pass


def popen_of(text, rc=0, seen=None):
    def popen(argv, **kw):
        if seen is not None:
            seen.append((argv, kw))
        return FakeProcess(text, rc)
    return popen


def render(text=ASSEMBLY, rc=0, level='steps', out=None, env=None):
    out = out if out is not None else Pipe()
    c = CON.Console(level, out=out, err=out, env=env if env is not None else {'TERM': 'xterm-256color', 'HAO_THEME': 'dark'})
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / 'install.log'
        r = PROG.Runner(c, 'install', groups=GROUPS, log=log, popen=popen_of(text, rc), env={})
        got = r(['sh', '/Volumes/HAO/kit/assembly-source/golden-assemble.sh', 'KIT', 'LOCAL'])
        logged = log.read_text()
    return got, sq(out.getvalue()), logged, r, c


class Steps(unittest.TestCase):
    def test_phases_in_plain_words(self):
        rc, out, logged, _, _ = render()
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        heads = [l for l in lines if l.startswith('[')]
        self.assertEqual(heads[0], '[1/7] Lay out the disk')
        self.assertEqual([h.split('  ')[0] for h in heads],
                         ['[1/7] Lay out the disk', '[2/7] Copy macOS 27', '[3/7] Add Intel support',
                          '[4/7] Link the kernel', '[5/7] Apply packs', '[6/7] Seal', '[7/7] Make it bootable'])
        self.assertIn('  copy the installer\'s tools and Python  ✓', lines)
        self.assertIn("  kernel collections linked with Tahoe's kmutil  ✓", lines)
        self.assertIn(sq('  base                   1 recipe  ✓'), lines)          # userspace (the kernel is [4/7])
        self.assertIn(sq('  metal-3802             1 recipe  ✓'), lines)
        self.assertIn(sq('  other fixes            1 recipe  ✓'), lines)
        self.assertIn('  settings, caches and the OpenCore configuration  2 recipes  ✓', lines)
        self.assertEqual(sum('the System volume' in l for l in lines), 1)          # golden-pristine's repeat
        self.assertTrue(lines[-1].startswith('  unmount the new volumes  ✓'))

    def test_internal_names_and_chatter_only_in_the_log(self):
        _, out, logged, _, _ = render()
        for word in ('golden-gate-userspace', 'caffeinate', 'tar:', 'RESULT', 'Volume EFI', 'installed', 'recipe darwin'):
            self.assertNotIn(word, out)
            self.assertIn(word, logged)
        self.assertIn('### exit 0', logged)

    def test_failure_names_the_last_error_and_the_log(self):
        text = ASSEMBLY.split('STEP snapshot')[0] + 'STEP snapshot\nunmount of /Volumes/macOS refused: Resource busy\nRESULT rc=1\n'
        rc, out, _, r, c = render(text, rc=1)
        self.assertEqual(rc, 1)
        self.assertIn('  update Preboot, bless, take the system snapshot  ✗', out)
        x = r.explain(CON.Stopped('the build onto /dev/disk0 failed (exit 1) in step snapshot',
                                  why='the disk is erased and incomplete; it will not start', options=['run it again']))
        self.assertIn('Resource busy', x.cause)
        self.assertTrue(x.log.endswith('install.log'))
        err = Pipe()
        c2 = CON.Console('steps', out=err, err=err, env={})
        c2.stopped(x)
        report = sq(err.getvalue())
        for part in ('STOPPED', 'What happened', 'Why it matters', 'Likely cause', 'What you can do', '1  run it again',
                     'Log: '):
            self.assertIn(part, report)

    def test_a_stop_between_lines_marks_its_step(self):
        text = ASSEMBLY.split('STEP post hao-opencore')[0] + 'unmount of /Volumes/macOS refused\n'
        _, out, _, _, _ = render(text, rc=1)
        self.assertIn('  settings, caches and the OpenCore configuration  ✗  unmount of /Volumes/macOS refused', out.splitlines())

    def test_a_backend_stop_event_replaces_the_generic_report(self):
        text = 'STEP layout\n@hao {"ev": "stop", "what": "the disk is too small", "options": ["use a 256 GB disk"]}\n'
        _, _, _, r, _ = render(text, rc=2)
        x = r.explain(CON.Stopped('the build failed (exit 2)', options=['run it again']))
        self.assertEqual(x.what, 'the disk is too small')
        self.assertEqual(x.options, ['use a 256 GB disk', 'run it again'])

    def test_quiet_is_one_bar(self):
        _, out, _, _, _ = render(level='quiet')
        self.assertNotIn('[1/7]', out)
        self.assertNotIn('Apply packs', out)
        self.assertEqual(out.strip().splitlines()[-1].split()[:3], ['Installing', 'macOS', '27'])
        self.assertIn('100%', out)

    def test_json_level_prints_events_only(self):
        _, out, _, _, c = render(level='json')
        import json
        evs = [json.loads(l) for l in out.splitlines()]
        self.assertIn('step.start', {e['ev'] for e in evs})
        self.assertIn('backend', {e['ev'] for e in evs})
        self.assertFalse(any('\033[' in l for l in out.splitlines()))

    def test_terminal_redraws_in_place_with_colour(self):
        _, out, _, _, c = render(out=Tty())
        self.assertIsInstance(c.surface, CON.RichSurface)             # the vendored Rich draws the live line
        self.assertIn('\033[?25l', out)                                # cursor hidden while a line is live
        self.assertIn('\033[2K', out)                                  # the live line erased, its final line drawn
        self.assertIn('\033[1m[1/7] Lay out the disk\033[0m', out)
        self.assertIn('\033[38;5;78m✓\033[0m', out)

    def test_the_given_environment_wins_over_the_process(self):
        # Rich used to read os.environ: a runner started from TERM=dumb or with NO_COLOR set drew nothing live and
        # no colour although the console had been given a colour terminal (the v0.0.1 pre-release CI run).
        from unittest import mock
        with mock.patch.dict(os.environ, {'TERM': 'dumb', 'NO_COLOR': '1'}):
            _, out, _, _, c = render(out=Tty())
        self.assertIsInstance(c.surface, CON.RichSurface)
        self.assertIn('\033[?25l', out)
        self.assertIn('\033[38;5;78m✓\033[0m', out)

    def test_without_rich_the_console_redraws_itself(self):
        _, out, _, _, c = render(out=Tty(), env={'TERM': 'xterm-256color', 'HAO_THEME': 'dark', 'HAO_NO_RICH': '1'})
        self.assertIsInstance(c.surface, CON.PlainSurface)
        self.assertIn('\r\033[K', out)
        self.assertIn('\033[38;5;78m✓\033[0m', out)

    def test_record_saves_what_was_drawn(self):
        with tempfile.TemporaryDirectory() as d:
            svg = Path(d) / 'run.svg'
            _, _, _, _, c = render(out=Tty(), env={'TERM': 'xterm-256color', 'HAO_THEME': 'dark', 'HAO_RECORD': str(svg)})
            self.assertTrue(c.save_record())
            text = svg.read_text()
        self.assertIn('<svg', text)
        self.assertIn('Lay&#160;out&#160;the&#160;disk', text)
        self.assertNotIn('0s</text>', text)                        # finished lines only, no live frames

    def test_no_colour_and_dumb_terminal_are_plain(self):
        for env in ({'NO_COLOR': '1', 'TERM': 'xterm'}, {'TERM': 'dumb'}):
            _, out, _, _, _ = render(out=Tty(), env=env)
            self.assertNotIn('\033[', out)
            self.assertNotIn('\r', out)

    def test_the_child_knows_it_is_rendered(self):
        seen = []
        c = CON.Console('steps', out=Pipe(), env={})
        with tempfile.TemporaryDirectory() as d:
            PROG.Runner(c, 'x', log=Path(d) / 'x.log', popen=popen_of('', 0, seen), env={'A': 'b'})(['true'])
        self.assertEqual(seen[0][1]['env']['HAO_EVENTS'], '1')
        self.assertEqual(seen[0][1]['env']['A'], 'b')

    def test_stage_kit_lines_and_packs(self):
        with tempfile.TemporaryDirectory() as d:
            share = Path(d) / 'w' / 'share'
            for folder, size in (('hooks/01-darwin27-kernel', 3000), ('hooks/06-golden-gate-hello', 1000),
                                 ('post/03-golden-gate-shader-caches', 2000), ('hooks/12-golden-gate-store', 10)):
                (share / folder).mkdir(parents=True)
                (share / folder / 'f').write_bytes(b'x' * size)
            src = Path(d) / 'minimal-native-r15'
            src.mkdir()
            (src / 'a').write_bytes(b'y' * 5000)
            text = ('+ zsh /x/pipeline/stages/inputs.sh %s t\nINFO:root:- Adding Lilu.kext 1.7.2\n\033[3J\033[H\033[2J\n'
                    '+ ditto %s %s/assembly-inputs/native\n+ /k/py/bin/python3.12 -I /x/build-efi.py --out e\n'
                    'Kit staged: /k\n' % (share, src, share))
            out = Pipe()
            c = CON.Console('steps', out=out, env={})
            r = PROG.Runner(c, 'create-installer', groups=GROUPS, log=Path(d) / 'c.log', popen=popen_of(text), env={})
            rc = r(['python3', '-I', 'golden-run', 'a.json', '--stage-kit', '--work', str(share.parent)])
        self.assertEqual(rc, 0)
        self.assertEqual([sq(l) for l in out.getvalue().splitlines()], [sq(l) for l in [
            "  stage each recipe's inputs, every one checked against its sha256  ✓",
            '  base  1 files  ✓',
            '  metal-3802  1 files  ✓',
            '  intel-hd4000  1 files  ✓',
            '  other fixes  1 files  ✓',
            "  copy Tahoe's Intel system programs  ████████████████████ 5.0 / 5.0 KB  ✓",
            '  OpenCore for this Mac  ✓']])

    def test_kernel_lines_and_the_proven_build(self):
        with tempfile.TemporaryDirectory() as d:
            kit = Path(d) / 'kit'
            pre = kit / 'hooks' / '01-darwin27-kernel' / 'prelinked'
            pre.mkdir(parents=True)
            (pre / 'prelinked.json').write_text('{"files": {"BootKernelExtensions.kc": "875abf7d221c8ea0ffff"}}')
            text = ('STEP recipes\nSTEP recipe darwin27-kernel\n'
                    'Linked the Darwin 27 kernel (prelinked): Boot 875abf7d221c8ea0 and System collections\n'
                    'STEP posts\nSTEP post hao-opencore\nstamped /x/HaoExec.kext for kernel 037e7b860ac83c67afb8\n'
                    'Installed 4 recorded kernel repairs and IntelCommpage.kext; retained target OCLP settings\n')
            out = Pipe()
            c = CON.Console('steps', out=out, env={})
            r = PROG.Runner(c, 'install', groups=GROUPS, log=Path(d) / 'i.log', popen=popen_of(text), env={})
            r(['sh', str(kit / 'assembly-source' / 'golden-assemble.sh'), str(kit), 'LOCAL'])
        got = sq(out.getvalue())
        self.assertIn('  BootKC and SystemKC linked, Boot 875abf7d221c…  ✓  matches the proven build', got)
        self.assertNotIn('HaoExec stamped', got)                      # at -v only; the bind line says it
        self.assertIn('  4 kernel repairs bound to this kernel, IntelCommpage added  ✓', got)


class Round5(unittest.TestCase):
    def test_binder_record_gives_the_bind_lines(self):
        with tempfile.TemporaryDirectory() as d:
            rec = Path(d) / 'local' / 'work' / 'post' / 'hao-opencore'
            rec.mkdir(parents=True)
            (rec / 'kc-binder.json').write_text(json.dumps({
                'mode': 'binder', 'commpage_table': {'load_time': True},
                'points': {'site': {'segment': '__TEXT', 'offset': '0x8ffa02', 'length': 104, 'fields': 9},
                           'pv_addresses.pv_zone': {'segment': '__DATA_CONST', 'offset': '0x111a0'}},
                'patches': [{'comment': 'Initialize early VM range', 'offset': '0x9efa02', 'length': 104}]}))
            text = 'STEP posts\nSTEP post hao-opencore\nInstalled 4 recorded kernel repairs and IntelCommpage.kext\n'
            for level, want, absent in (('steps', 'bind  4 kernel repairs against the BootKC just linked  ✓', 'early-vm.site'),
                                        ('edits', 'early-vm.site  __TEXT +0x8ffa02  104 bytes  9 fields  ✓', None)):
                out = Pipe()
                c = CON.Console(level, out=out, env={})
                PROG.Runner(c, 'install', groups=GROUPS, log=Path(d) / 'i.log', popen=popen_of(text), env={})(
                    ['sh', 'golden-assemble.sh', str(Path(d) / 'kit'), str(Path(d) / 'local')])
                got = sq(out.getvalue())
                self.assertIn(want, got)
                self.assertIn('→ 1 OpenCore patches written · HaoExec stamped · IntelCommpage table filled', got)
                if absent:
                    self.assertNotIn(absent, got)

    def test_the_system_copy_is_a_bar(self):
        out = Pipe()
        c = CON.Console('steps', out=out, env={})
        r = PROG.Runner(c, 'install', log='/dev/null', popen=popen_of(''), env={})
        view = PROG.Assembly(r, local='/L')
        view.start()
        view.step('layout')
        view.line('/dev/disk11s1  41504653-0000-11AA-AA11-0030654  /V/job/tmp/golden-pristine.x/filesystem')
        view.step('golden-system')
        used = {'/V/job/tmp/golden-pristine.x/filesystem': 10 * 10 ** 9, '/L/work/mnt/system': 1 * 10 ** 9}
        saved = PROG.used_bytes
        PROG.used_bytes = lambda path: used.get(str(path))
        try:
            view.tick()                                    # the base: what the new volume held before the copy
            used['/L/work/mnt/system'] = 5 * 10 ** 9
            view.tick()
        finally:
            PROG.used_bytes = saved
        self.assertEqual((view.task.done, view.task.total, view.task.unit), (4 * 10 ** 9, 10 * 10 ** 9, 'bytes'))
        self.assertIn('4.0 / 10.0 GB', CON.plain(view.task.render()))

    def test_stopped_says_what_was_left_untouched(self):
        text = 'STEP layout\nSTEP golden-system\nerror: copy failed\n'
        _, _, _, r, _ = render(text, rc=1)
        x = r.explain(CON.Stopped('the build onto /dev/disk0 failed (exit 1)'))
        self.assertEqual(x.untouched, 'before anything was written to the EFI partition. This Mac will not start from disk0 yet.')

    def test_unproven_mac_lists_what_is_not_proven(self):
        x = CON.Stopped('this Mac is not proven for this release', code=5,
                        unknown=[('GPU 8086:0126', 'Intel HD 3000: no pack')],
                        options=['install anyway: /Volumes/HAO/install --unproven'])
        for env, out in (({}, Pipe()), ({'TERM': 'xterm-256color', 'HAO_THEME': 'dark'}, Tty())):
            c = CON.Console('steps', out=out, err=out, env=env)
            c.stopped(x)
            text = CON.plain(out.getvalue())
            self.assertIn('Not proven on this Mac', text)
            self.assertIn('GPU 8086:0126', text)
            self.assertIn('Intel HD 3000: no pack', text)
        self.assertEqual(c.events[-1]['unknown'], [['GPU 8086:0126', 'Intel HD 3000: no pack']])

    def test_commands_in_what_you_can_do_are_bold(self):
        err = Tty()
        c = CON.Console('steps', out=err, err=err, env={'TERM': 'xterm-256color', 'HAO_THEME': 'dark', 'HAO_NO_RICH': '1'})
        c.stopped(CON.Stopped('x', options=['run it again: sudo ./hao create-installer', 'save a report: /Volumes/HAO/report (attach it)']))
        out = err.getvalue()
        self.assertIn('\033[1msudo ./hao create-installer\033[0m', out)
        self.assertIn('\033[1m/Volumes/HAO/report\033[0m (attach it)', out)
        c2 = CON.Console('steps', out=Tty(), err=Tty(), env={'TERM': 'xterm-256color', 'HAO_THEME': 'dark', 'HAO_NO_RICH': '1'})
        c2.stopped(CON.Stopped('x', options=['the logs: /Volumes/HAO/hao-install-1/local/work']))
        self.assertNotIn('\033[1m', c2.err.getvalue().split('What you can do')[1])      # a folder, not a command

    def test_truecolor_uses_the_mockups_colours(self):
        c = CON.Console('steps', out=Tty(), env={'TERM': 'xterm-256color', 'HAO_THEME': 'dark', 'COLORTERM': 'truecolor'})
        self.assertEqual(c.ok, '\033[38;2;98;211;148m✓\033[0m')


class Sizes(unittest.TestCase):
    def test_a_shared_input_row_counts_once(self):
        with tempfile.TemporaryDirectory() as d:
            for recipe in ('02-golden-gate-userspace', '16-golden-gate-native-identities'):
                f = Path(d) / 'hooks' / recipe / 'inputs' / 'golden_gate_userspace' / 'os.dmg'
                f.parent.mkdir(parents=True)
                f.write_bytes(b'z' * 4096)
                (Path(d) / 'hooks' / recipe / 'make.sh').write_bytes(b'#' * 10)
            got = PROG.recipe_folders(d)
        self.assertEqual(got, {'golden-gate-userspace': (2, 4106), 'golden-gate-native-identities': (2, 10)})


class Downloads(unittest.TestCase):
    def test_each_download_is_a_bar_with_its_check(self):
        rows = [{'name': 'InstallAssistant-26B131.pkg', 'size': 2000, 'url': 'https://swcdn.apple.com/x'},
                {'name': 'KDK_25G330.kdk', 'size': 10, 'url': 'https://x'}]
        seen = []

        def fetch(rows_, cache, on_event=None):
            on_event({'ev': 'fetch', 'name': rows_[0]['name'], 'url': 'u', 'size': 2000})
            for done in (500, 2000):
                on_event({'ev': 'progress', 'name': rows_[0]['name'], 'done': done, 'size': 2000})
            on_event({'ev': 'ok', 'name': rows_[0]['name'], 'sha256': 'ab'})
            on_event({'ev': 'reused', 'name': rows_[1]['name'], 'sha256': 'cd'})
        out = Pipe()
        c = CON.Console('steps', out=out, env={})
        acq = PROG.download_view(c, {'fetch': fetch, 'load': None}, lambda r: 'apple' if 'apple' in r.get('url', '') else '')
        acq['fetch'](rows, '/cache', on_event=seen.append)
        lines = [sq(l) for l in out.getvalue().splitlines()]
        self.assertEqual(lines, ['  Apple  InstallAssistant-26B131.pkg  ' + '█' * 20 + ' 2.0 / 2.0 KB  sha256 ✓',
                                 '         KDK_25G330.kdk  already downloaded, sha256 ✓'])
        self.assertEqual([e['ev'] for e in seen], ['fetch', 'ok', 'reused'])     # byte counts stay on the bar
        self.assertIs(acq['load'], None)


class Edits(unittest.TestCase):
    def test_show_edits_prints_a_recipes_declared_edits(self):
        out = Pipe()
        c = CON.Console('edits', out=out, env={})
        PROG.show_edits(c, 'golden-gate-hello')
        text = out.getvalue()
        for part in ('golden-gate-hello', 'System/Library/Frameworks/Metal.framework/Versions/A/Metal', 'origin  OCLP',
                     'edit 1/3', 'hello-not-skipped', 'bytes  B0 01', '→   B0 00', 'asm    mov al,1  →  mov al,0', 'why    ',
                     'sign    ad hoc, once'):
            self.assertIn(part, text)

    def test_a_tree_on_a_rich_terminal(self):
        out = Tty()
        c = CON.Console('edits', out=out, env={'TERM': 'xterm-256color', 'HAO_THEME': 'dark'})
        PROG.show_edits(c, 'golden-gate-hello', pack='metal-3802')
        text = CON.plain(out.getvalue())
        self.assertIn('golden-gate-hello  metal-3802', text)
        self.assertIn('└── ', text)                                     # Rich's tree guides
        self.assertIn('hello-not-skipped', text)

    def test_not_shown_below_the_edits_level(self):
        out = Pipe()
        PROG.show_edits(CON.Console('files', out=out, env={}), 'golden-gate-hello')
        self.assertEqual(out.getvalue(), '')


class Live(unittest.TestCase):
    def test_bars_fit_80_columns(self):
        c = CON.Console('steps', out=Tty(), env={'TERM': 'xterm-256color', 'HAO_THEME': 'dark', 'HAO_NO_RICH': '1'})
        c.width = lambda: 80
        t = c.task('  copy Recovery files', total=1500 * 10 ** 6, unit='bytes')
        t.update(done=10 ** 9)
        self.assertLessEqual(len(CON.plain(t.render())), 80)
        q = c.task('Installing macOS 27', 'quiet', total=1000, unit='%')
        q.width, q.bar_at = 30, 0
        q.update(done=500)
        self.assertIn('█' * 15, CON.plain(q.render()))                 # the --quiet bar keeps its 30 cells
        q.drop()
        t.drop()

    def test_heartbeat_without_a_terminal(self):
        now = [0.0]
        out = Pipe()
        c = CON.Console('steps', out=out, env={}, clock=lambda: now[0])
        t = c.task('  build the system cryptex')
        now[0] = 31
        t.update()
        now[0] = 45
        t.update()
        now[0] = 62
        t.update()
        t.end(True)
        self.assertEqual([sq(l) for l in out.getvalue().splitlines()], [
            '  ... build the system cryptex (31s so far)',
            '  ... build the system cryptex (1m 02s so far)',
            '  build the system cryptex  ✓  1m 02s'])

    def test_ascii_when_not_utf8_or_asked(self):
        c = CON.Console('steps', out=Pipe(), env={'HAO_ASCII': '1'})
        self.assertFalse(c.utf)
        self.assertEqual(CON.bar(0.5, 4, utf=False), '##..')
        self.assertEqual(sq(plain_task(c, 2, 4)), '  x  ##########.......... 2/4 files')

    def test_palette_choice(self):
        c = CON.Console('steps', out=Tty(), env={'TERM': 'xterm-256color', 'HAO_PALETTE': '8'})
        self.assertEqual(c.ok, '\033[32m✓\033[0m')
        c = CON.Console('steps', out=Tty(), env={'TERM': 'xterm-256color', 'HAO_THEME': 'dark'})
        self.assertEqual(c.ok, '\033[38;5;78m✓\033[0m')
        c = CON.Console('steps', out=Tty(), env={'TERM': 'xterm-256color', 'HAO_THEME': 'light'})
        self.assertEqual(c.ok, '\033[38;5;29m✓\033[0m')

    def test_no_question_to_the_terminal_without_colour(self):
        asked = []
        saved = CON.ask_background
        CON.ask_background = lambda timeout=0.2: asked.append(1)
        try:
            del CON._THEME[:]
            CON.Console('steps', out=Pipe(), env={'TERM': 'xterm-256color'})
            CON.Console('json', out=Tty(), env={'TERM': 'xterm-256color'})
        finally:
            CON.ask_background = saved
        self.assertEqual(asked, [])

    def test_theme_from_the_terminals_answer(self):
        del CON._THEME[:]
        self.assertEqual(CON.theme({}, ask=lambda: (1.0, 1.0, 1.0)), 'light')
        del CON._THEME[:]
        self.assertEqual(CON.theme({}, ask=lambda: None), 'dark')
        del CON._THEME[:]

    def test_spinner_is_the_mockups(self):
        self.assertEqual(CON.SPIN[True], '-\\|/')

    def test_fit_keeps_a_redrawn_line_on_one_row(self):
        s = '\033[1mabcdefghij\033[0m'
        self.assertEqual(CON.plain(CON.fit(s, 5)), 'abcd…')
        self.assertEqual(CON.fit('abc', 5), 'abc')

    def test_elapsed(self):
        self.assertEqual([CON.elapsed(s) for s in (5, 232, 3725)], ['5s', '3m 52s', '1h 02m'])


def plain_task(c, done, total):
    t = c.task('  x', total=total, unit='files')
    t.update(done=done)
    line = CON.plain(t.render(final=True))
    t.drop()
    return line


class Hooks(unittest.TestCase):
    def test_entry_point_routes_backends_through_the_console(self):
        import importlib.machinery
        import importlib.util
        import types
        loader = importlib.machinery.SourceFileLoader('hao_entry', str(HERE.parent / 'hao'))
        spec = importlib.util.spec_from_loader('hao_entry', loader)
        hao = importlib.util.module_from_spec(spec)
        loader.exec_module(hao)
        calls = []
        build = types.SimpleNamespace(
            resolve=lambda a, c, cmd: ({'model': 'MacBookPro9,2', 'packs': ['base']}, 'path', {'golden_build': '26B131'},
                                       {'base': ['x']}),
            show_steps=lambda c, cmd, groups: calls.append(('plan', cmd)),
            STEPS={'install': ('a', 'b')})
        inst = types.SimpleNamespace(prepare_kit=lambda a, c, path, run=None: calls.append(('kit', run)))
        c = CON.Console('steps', out=Pipe(), env={})
        c.run = PROG.Runner(c, 'install', log='/dev/null')
        a = types.SimpleNamespace(dry_run=False)
        hao.hook_backends(a, c, build, inst)
        build.resolve(a, c, 'install')
        self.assertEqual(c.run.groups, {'base': ['x']})
        build.show_steps(c, 'install', {})                  # default level, a real install: no plan before ERASE
        build.show_steps(c, 'create-installer', {})
        inst.prepare_kit(a, c, 'p')
        self.assertEqual(calls, [('plan', 'create-installer'), ('kit', c.run)])
        hao.hook_backends(a, c, build, inst)                  # twice wraps once
        out = Pipe()
        hao.detection(CON.Console('edits', out=out, env={}), {'packs': ['intel-hd4000', 'mbp92'], 'board_id': 'Mac-X',
                      'model': 'MacBookPro9,2', 'gpu_name': 'Intel HD Graphics 4000 (Ivy Bridge)',
                      'oclp_patchsets': ['Intel Ivy Bridge']})
        self.assertIn(sq('  GPU     Intel HD 4000                 → Intel Ivy Bridge          → intel-hd4000'), sq(out.getvalue()))
        self.assertIn(sq('  Board   Mac-X                         → MacBookPro9,2             → mbp92'), sq(out.getvalue()))
        self.assertEqual(hao.dev_label('v0.1.0-alpha.1-78-g73b28ae2e743'), 'dev 73b28ae2')
        self.assertEqual(hao.dev_label('v0.1.0-alpha.1-78-g73b28ae2e743-dirty'), 'dev 73b28ae2+')
        self.assertEqual(hao.dev_label('73b28ae2e743'), 'dev 73b28ae2')
        self.assertEqual(build.resolve.original.__name__, '<lambda>')


if __name__ == '__main__':
    unittest.main()



class RunnerEnv(unittest.TestCase):
    """installer.prepare_kit passes env= (KIT_ON_DRIVE) through the console's runner: it reaches the process once."""
    def test_caller_env_reaches_the_process(self):
        seen = []
        c = CON.Console('quiet', out=Pipe(), err=Pipe(), env={})
        with tempfile.TemporaryDirectory() as d:
            PROG.Runner(c, 'create-installer', log=Path(d) / 'c.log', popen=popen_of('', 0, seen), env={'A': 'b'})(
                ['true'], env={'KIT_ON_DRIVE': '/Volumes/HAO/.hao-build'})
        env = seen[0][1]['env']
        self.assertEqual(env.get('KIT_ON_DRIVE'), '/Volumes/HAO/.hao-build')
        self.assertEqual(env.get('HAO_EVENTS'), '1')
