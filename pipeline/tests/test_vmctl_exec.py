"""pipeline/vm/vmctl exec against a fake slow guest agent, and golden-run's job submission on top of it."""
from pathlib import Path
import runpy
import subprocess
import tempfile
import threading
import time
import unittest

TOP = Path(__file__).resolve().parents[2]
V = runpy.run_path(str(TOP / 'pipeline' / 'vm' / 'vmctl'))
G = runpy.run_path(str(TOP / 'pipeline' / 'golden-run'))


def slow_agent(q, delay, rc='0', out=b'started\n'):
    """What the guest agent and vmctl's server do with a queued command: claim CID.cmd (-> CID.run) after DELAY
    seconds, then write CID.out and CID.rc."""
    def run():
        end = time.time() + 10
        while time.time() < end:
            for c in q.glob('*.cmd'):
                time.sleep(delay)
                try:
                    c.rename(c.with_suffix('.run'))
                except OSError:
                    return
                (q / (c.stem + '.out')).write_bytes(out)
                (q / (c.stem + '.rc')).write_text(rc)
                return
            time.sleep(0.05)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


class Exec(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.q = Path(self.d.name)

    def tearDown(self):
        self.d.cleanup()

    def test_a_slow_agent_still_answers(self):
        slow_agent(self.q, 1.0)
        self.assertEqual(V['exec_cmd'](None, 'echo started', timeout=5, q=self.q), 0)
        self.assertEqual(sorted(p.suffix for p in self.q.iterdir()), ['.run'])

    def test_a_command_never_taken_is_withdrawn(self):
        self.assertEqual(V['exec_cmd'](None, 'echo started', timeout=0.5, q=self.q), V['EXEC_UNDELIVERED'])
        self.assertEqual([p.suffix for p in self.q.iterdir()], ['.withdrawn'])
        self.assertEqual(V['EXEC_UNDELIVERED'], G['EXEC_UNDELIVERED'])

    def test_a_command_taken_but_unanswered_is_not_withdrawn(self):
        def claim_only():
            for _ in range(100):
                for c in self.q.glob('*.cmd'):
                    c.rename(c.with_suffix('.run'))
                    return
                time.sleep(0.02)
        threading.Thread(target=claim_only, daemon=True).start()
        with self.assertRaises(SystemExit) as e:
            V['exec_cmd'](None, 'echo started', timeout=0.5, q=self.q)
        self.assertIn('took the command', str(e.exception.code))
        self.assertEqual([p.suffix for p in self.q.iterdir()], ['.run'])


class Submit(unittest.TestCase):
    def reply(self, rc, out='', err=''):
        return subprocess.CompletedProcess([], rc, out, err)

    def test_started_at_once(self):
        said = []
        n = G['submit_job'](lambda a: self.reply(0, 'started\n'), 'vmctl', 'vm', 'job', '/no/such', say=lambda *a, **k: said.append(a))
        self.assertEqual((n, said), (1, []))

    def test_an_undelivered_job_is_sent_again_and_stderr_shown(self):
        replies = [self.reply(75, err='vmctl: no reply within 600s and the guest never took the command; withdrawn'),
                   self.reply(0, 'started\n')]
        said = []
        n = G['submit_job'](lambda a: replies.pop(0), 'vmctl', 'vm', 'job', '/no/such',
                            say=lambda *a, **k: said.append(a[0]))
        self.assertEqual(n, 2)
        self.assertIn('never took the command', said[0])

    def test_a_job_that_started_is_never_sent_again(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            n = G['submit_job'](lambda a: calls.append(a) or self.reply(75, err='slow'), 'vmctl', 'vm', 'job', d,
                                say=lambda *a, **k: None)
            self.assertEqual((n, len(calls)), (1, 1))

    def test_a_job_that_may_have_been_delivered_is_not_resent(self):
        calls = []
        with self.assertRaises(RuntimeError) as e:
            G['submit_job'](lambda a: calls.append(a) or self.reply(1, err='vmctl: no reply; the guest took the command'),
                            'vmctl', 'vm', 'job', '/no/such', say=lambda *a, **k: None)
        self.assertEqual(len(calls), 1)
        self.assertIn('not resent', str(e.exception))

    def test_attempts_run_out(self):
        with self.assertRaises(RuntimeError):
            G['submit_job'](lambda a: self.reply(75), 'vmctl', 'vm', 'job', '/no/such', say=lambda *a, **k: None)


class AgentScript(unittest.TestCase):
    def test_transfers_allow_a_loaded_host(self):
        self.assertIn('--connect-timeout 30 --max-time 300', V['HTTP_AGENT'])
        self.assertNotIn('--max-time 30 ', V['HTTP_AGENT'])


if __name__ == '__main__':
    unittest.main()
