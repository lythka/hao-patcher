import runpy
from pathlib import Path
import unittest

select = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'make'))['select_identity']


class IdentityTest(unittest.TestCase):
    def test_does_not_mix_upgrade_or_other_board_components(self):
        identities = [dict(Info=dict(DeviceClass=board, Variant=variant))
                      for board, variant in [('board-a', 'Customer Upgrade Install (IPSW)'),
                                             ('board-b', 'Customer Erase Install (IPSW)'),
                                             ('board-a', 'Customer Erase Install (IPSW)')]]
        self.assertIs(select({'BuildIdentities': identities}, 'board-a'), identities[2])

    def test_ambiguous_identity_is_not_chosen_arbitrarily(self):
        item = dict(Info=dict(DeviceClass='board-a', Variant='Customer Erase Install (IPSW)'))
        with self.assertRaisesRegex(ValueError, 'found 2'):
            select({'BuildIdentities': [item, item]}, 'board-a')
