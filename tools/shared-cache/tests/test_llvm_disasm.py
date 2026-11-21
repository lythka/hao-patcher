"""Compare the standalone and Xcode decoders across their host architectures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dyld_rebuild import x86

CORPUS = [
    'e878563412', 'e8fbffffff', 'e9fbffffff', 'ebfe', '0f85f0ffffff',
    'ff15f0ffffff', 'ff25f0ffffff', '488b05f0ffffff', '488d0500000000',
    '48890578563412', 'f00fc105f0ffffff', 'c5f877', 'c5f858c1',
    'c4e27d18c1', '62f17c4858c1', '62f17c48100510000000',
    'f30f1efa', '660f1f440000', '0f0b', '0f', '62', 'c4', 'f0c3',
    '66' * 15, '',
]


def decode_corpus(library):
    dis = x86.Disassembler(library)
    try:
        result = []
        for raw in CORPUS:
            ins = dis.decode(bytes.fromhex(raw), 0x100000000)
            result.append(None if ins is None else [len(ins.data), ins.text])
        return result
    finally:
        dis.close()


class SelectionTests(unittest.TestCase):
    def test_override_row_fallback_and_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            row = repo / 'pipeline/inputs/common/llvm_disasm'
            row.parent.mkdir(parents=True)
            self.assertIsNone(x86.row_library(repo))
            lib = repo / 'decoder.dylib'
            digest = hashlib.sha256(b'library').hexdigest()
            row.write_text(f'# pinned\n{digest} decoder.dylib\n')
            self.assertIsNone(x86.row_library(repo))
            lib.write_bytes(b'library')
            self.assertEqual(x86.row_library(repo), lib)
            lib.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                x86.row_library(repo)
        with patch.dict(os.environ, {'SHARED_CACHE_DISASM': '/explicit/library'}), patch.object(x86, 'row_library') as lookup:
            self.assertEqual(x86.default_library(), Path('/explicit/library'))
            lookup.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), patch.object(x86, 'row_library', return_value=Path('/row/library')):
            self.assertEqual(x86.default_library(), Path('/row/library'))
        with patch.dict(os.environ, {}, clear=True), patch.object(x86, 'row_library', return_value=None):
            self.assertEqual(x86.default_library(), Path(x86.LTO))



class EquivalenceTests(unittest.TestCase):
    def test_corpus_matches_xcode(self):
        library = x86.row_library()
        if library is None or not Path(x86.LTO).exists():
            self.skipTest('standalone decoder and Xcode libLTO both required')
        results = []
        for arch, path in (('arm64', library), ('x86_64', library), ('arm64', Path(x86.LTO))):
            output = subprocess.check_output(['arch', '-' + arch, sys.executable, '-I', '-B',
                                              str(Path(__file__).resolve()), '--decode', str(path)], text=True)
            results.append(json.loads(output))
        for index, raw in enumerate(CORPUS):
            with self.subTest(bytes=raw):
                for result in results[1:]:
                    self.assertEqual(results[0][index], result[index])


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--decode':
        print(json.dumps(decode_corpus(sys.argv[2])))
    else:
        unittest.main()
