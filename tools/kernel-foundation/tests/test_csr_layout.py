"""CSR layout regression relocated unchanged from the assembler host tests."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('cb', Path(__file__).resolve().parents[1]/'commpage_build.py')
cb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cb)


class CsrLayout(unittest.TestCase):
    # 26A428 kernel ce09da26: csr_get_active_config and PE_state.
    CODE = bytes.fromhex('554889e5488b0d25688a0031c0f641060874 0bb8ff1f0000238198040000'.replace(' ', ''))

    def test_offsets_from_code(self):
        self.assertEqual(cb.csr_layout(self.CODE, 0xffffff80007b50d0, 0xffffff800105b860), (0xa0, 0x498))
        with self.assertRaises(ValueError):
            cb.csr_layout(b'\x90'*32, 0, 0)


if __name__ == '__main__':
    unittest.main()
