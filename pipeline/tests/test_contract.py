"""hao-patcher keeps its contract (contract.json, checked by tools/hao-contract, the checker's home).
python3.14 -I -B -m unittest discover -s pipeline/tests -k contract"""
from pathlib import Path
import runpy
import unittest

TOP = Path(__file__).resolve().parents[2]
CHECKER = TOP / 'tools/hao-contract/hao_contract.py'


class Contract(unittest.TestCase):
    def test_repository_keeps_its_contract(self):
        self.assertEqual(runpy.run_path(str(CHECKER))['check'](TOP), [])


if __name__ == '__main__':
    unittest.main()
