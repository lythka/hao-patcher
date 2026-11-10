import copy
import json
from pathlib import Path
import unittest
from replay_profiles import BVH_INPUT, BVH_FUNCTIONS, controls_for


class ReplayProfileTests(unittest.TestCase):
    def test_only_measured_library_and_functions_get_nonzero_threads(self):
        self.assertEqual(len(BVH_FUNCTIONS), 14)
        for function in BVH_FUNCTIONS:
            self.assertEqual(controls_for(BVH_INPUT, function),
                             {'MPSMaxBatchSize': 1, 'threadsPerThreadgroup': 32})
            self.assertEqual(controls_for('different-library', function), {'MPSMaxBatchSize': 1})
        self.assertEqual(controls_for(BVH_INPUT, 'refitTriangleLeafNodeKernel'), {'MPSMaxBatchSize': 1})
        controls_for(BVH_INPUT, next(iter(BVH_FUNCTIONS))).clear()
        self.assertEqual(len(controls_for(BVH_INPUT, next(iter(BVH_FUNCTIONS)))), 2)

