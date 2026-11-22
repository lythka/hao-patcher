"""hao-runtime-fold-catalog: hao-runtime's install step, on the B+catalog payload row."""
import json
from pathlib import Path
import unittest

PATCH = Path(__file__).resolve().parents[1]
BASE = PATCH.parent/'hao-runtime'


class Recipe(unittest.TestCase):
    def test_same_install_as_hao_runtime_on_its_own_row(self):
        mine = (PATCH/'src/kc-hook.sh').read_text().splitlines()[-1]
        base = (BASE/'src/kc-hook.sh').read_text().splitlines()[-1]
        self.assertEqual(mine, base.replace('hao_runtime', 'hao_runtime_fold_catalog'))
        p = json.loads((PATCH/'patch.json').read_text())
        self.assertEqual(p['inputs'], ['hao_runtime_fold_catalog'])
        self.assertEqual((p['variant'], p['replaces']), ('cache-catalog', 'hao-runtime-fold'))
        self.assertEqual({f['origin'].get('row') for f in p['files']} - {None}, {'hao_runtime_fold_catalog'})

    def test_only_in_the_catalog_set_in_place_of_both_runtimes(self):
        sets = PATCH.parents[1]/'pipeline/sets'
        names = lambda p: [l.split()[0] for l in p.read_text().splitlines() if l.strip() and not l.startswith('#')]
        users = sorted(p.name for p in sets.glob('*.txt') if 'hao-runtime-fold-catalog' in names(p))
        self.assertEqual(users, ['beta-v0.0.1.txt', 'dev-beta-v0.0.1.txt', 'dev-bs-test-fold-catalog.txt', 'dev-ssh-alpha-v0.1.0-fold-catalog.txt', 'dev-ssh-full-witness.txt'])
        for user in users:
            self.assertNotIn('hao-runtime', names(sets/user), user)
            self.assertNotIn('hao-runtime-fold', names(sets/user), user)


if __name__ == '__main__':
    unittest.main()
