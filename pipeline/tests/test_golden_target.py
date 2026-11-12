"""The image target issues exactly the commands it did before the Target refactor; fixtures only, no disk tools.

fixtures/golden-image-commands.json was recorded from golden-image.py as of 7df266df (before Target existed):
every command, its capture flag and its environment, and the build record, for four fixture runs. Regenerate it
only for an intended change: python3.14 -I -B pipeline/tests/test_golden_target.py --write
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
EXPECTED = HERE/'fixtures/golden-image-commands.json'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


golden = load('golden_image', HERE.parent/'stages/golden-image.py')
fixture = load('golden_image_fixture', HERE/'test_golden_image.py')
fixture.golden = golden   # the fake reads nothing from golden; keep one module instance anyway

# A fixed process environment, so the recorded environments do not depend on the host running the test.
ENVIRONMENT = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': '/var/root', 'LANG': 'C'}


class RecordingCommands(fixture.FakeCommands):
    """The existing fake, also keeping each call's capture flag."""
    def run(self, command, env=None, capture=False):
        try:
            return super().run(command, env=env, capture=capture)
        finally:
            self.entries[-1]['capture'] = capture


def normalise(value, root):
    if isinstance(value, dict):
        return {key: normalise(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [normalise(item, root) for item in value]
    if isinstance(value, str):
        return value.replace(root, '<root>')
    return value


def scenario(name, assemble=None):
    """One fixture run of the existing test's configuration: normalised commands, build record and error type."""
    case = fixture.GoldenImageTests('test_full_order_preserves_golden_identity_and_declared_roles')
    case.setUp()
    try:
        config = case.config
        fake = RecordingCommands(config)
        if name == 'displaced-preboot':
            fake.displace_preboot = True
        elif name == 'hardware-failure':
            fake.fail = lambda a: config['hardware']['tool'] in a
        elif name == 'unmount-failure':
            data = str(Path(config['work'])/'mnt/data')
            fake.fail = lambda a: a == ['diskutil', 'unmount', data]
        error = None
        with patch.dict(os.environ, ENVIRONMENT, clear=True), contextlib.redirect_stdout(io.StringIO()):
            try:
                (assemble or golden.assemble)(config, fake)
            except Exception as caught:
                error = type(caught).__name__
        record = json.loads((Path(config['work'])/'build.json').read_text())
        for key in ('date', 'source_sha256', 'configuration_sha256'):
            record.pop(key)
        for row in record['steps']:
            row.pop('started'); row.pop('seconds', None)
        root = str(case.root)
        environments = []
        def environment(value):
            # Most commands share one environment: keep each distinct one once and refer to it by index.
            value = normalise(value, root)
            if value not in environments:
                environments.append(value)
            return environments.index(value)
        rows = [dict(command=normalise(row['command'], root), capture=row['capture'],
                     environment=environment(row['environment'])) for row in fake.entries]
        record = normalise(record, root)
        record['commands'] = [dict(row, environment=environment(row['environment'])) for row in record['commands']]
        return dict(commands=rows, environments=environments, record=record, error=error)
    finally:
        case.doCleanups()


SCENARIOS = ('full', 'displaced-preboot', 'hardware-failure', 'unmount-failure')


def observe():
    return {name: scenario(name) for name in SCENARIOS}


class ImageTargetCommandSequence(unittest.TestCase):
    maxDiff = None

    def test_image_target_issues_the_recorded_commands(self):
        expected = json.loads(EXPECTED.read_text())
        actual = observe()
        self.assertEqual(sorted(expected), sorted(actual))
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                self.assertEqual(expected[name]['error'], actual[name]['error'])
                self.assertEqual(len(expected[name]['commands']), len(actual[name]['commands']))
                for index, (want, got) in enumerate(zip(expected[name]['commands'], actual[name]['commands'])):
                    self.assertEqual(want, got, 'command %d of %s' % (index, name))
                self.assertEqual(expected[name]['environments'], actual[name]['environments'])
                self.assertEqual(expected[name]['record'], actual[name]['record'])

    def test_recording_covers_every_layer(self):
        expected = json.loads(EXPECTED.read_text())
        steps = [row['name'] for row in expected['full']['record']['steps']]
        self.assertEqual([n for n in steps if not n.startswith(('recipe ', 'post '))], [n for n, _, _ in golden.LAYERS])
        self.assertEqual(expected['full']['commands'][-1]['command'], ['hdiutil', 'detach', '/dev/disk70'])


if __name__ == '__main__':
    if sys.argv[1:] == ['--write']:
        EXPECTED.write_text(json.dumps(observe(), indent=1, sort_keys=True)+'\n')
    else:
        unittest.main()
