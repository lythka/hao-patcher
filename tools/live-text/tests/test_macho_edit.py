"""lib/macho_edit.py on authored Mach-O fixtures (no Apple binary)."""
from pathlib import Path
import struct
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
from macho_edit import append_dylibs, dependencies, digest, load_commands, signature_directories, x86_range


def fixture():
    mo=bytearray(8192)
    struct.pack_into('<8I',mo,0,0xfeedfacf,0x1000007,3,2,3,200,0x200085,0)
    struct.pack_into('<II16sQQQQIIII',mo,32,0x19,152,b'__TEXT',0x100000000,8192,0,8192,7,5,1,0)
    struct.pack_into('<16s16sQQIIIIIIII',mo,104,b'__text',b'__TEXT',0x100001000,64,4096,4,0,0,0x80000400,0,0,0)
    struct.pack_into('<6I',mo,184,0xc,32,24,0,0,0);mo[208:215]=b'/a.dyl\0'
    ident=b'authored.test\0';hashoff=96+len(ident)+7*32
    cd=bytearray(hashoff+64)
    struct.pack_into('>9I4B',cd,0,0xfade0c02,len(cd),0x20500,0x10002,hashoff,96,7,2,8192,32,2,0,12)
    cd[96:96+len(ident)]=ident;cd[hashoff-224:hashoff]=bytes(range(224))
    cms=struct.pack('>II',0xfade0b01,8)
    xml=struct.pack('>II',0xfade7171,12)+b'test'
    blobs=[(0,cd),(5,xml),(0x10000,cms)]
    table=12+8*len(blobs);total=table+sum(len(b) for _,b in blobs)
    struct.pack_into('<4I',mo,216,0x1d,16,8192,total)
    for i in range(2):cd[hashoff+i*32:hashoff+(i+1)*32]=digest(2,mo[i*4096:(i+1)*4096])
    sig=bytearray(struct.pack('>III',0xfade0cc0,total,len(blobs)));offset=table
    for kind,b in blobs:sig.extend(struct.pack('>II',kind,offset));offset+=len(b)
    for _,b in blobs:sig.extend(b)
    return bytes(mo+sig)


def universal(mo):
    arm=struct.pack('<8I',0xfeedfacf,0x100000c,2,2,0,0,0,0)+b'ARM unmodified signature payload'
    start=4096;end=8192
    out=bytearray(struct.pack('>II',0xcafebabe,2)+struct.pack('>5I',0x100000c,2,start,len(arm),12)+struct.pack('>5I',0x1000007,3,end,len(mo),12))
    out.extend(bytes(start-len(out)));out.extend(arm);out.extend(bytes(end-len(out)));out.extend(mo)
    return bytes(out)


class Tests(unittest.TestCase):
    def test_explicit_dylib_mode_preserves_original_commands_and_requires_signing(self):
        before=bytearray(fixture());struct.pack_into('<I',before,12,6)
        after,report=append_dylibs(bytes(before),['/test.dylib'],resign_filetype=6)
        self.assertEqual(after[32:232],before[32:232])
        self.assertEqual(after[4096:],before[4096:])
        self.assertTrue(report['requires_resigning'])
        with self.assertRaises(ValueError):append_dylibs(bytes(before),['/test.dylib'])
        with self.assertRaises(ValueError):append_dylibs(universal(bytes(before)),['/test.dylib'],resign_filetype=6)

    def test_preserves_slices_policy_slots_and_ordinals(self):
        before=universal(fixture());after,report=append_dylibs(before,['/Library/test/one.dylib','/Library/test/two.dylib'])
        off,size=x86_range(before);old=before[off:off+size];new=after[off:off+size]
        self.assertEqual(before[:off],after[:off]);self.assertEqual(len(before),len(after))
        self.assertEqual(old[24:32],new[24:32])
        self.assertEqual(old[32:232],new[32:232])
        self.assertEqual(dependencies(new,load_commands(new)),[(0xc,'/a.dyl'),(0xc,'/Library/test/one.dylib'),(0xc,'/Library/test/two.dylib')])
        for cd,hashes,pages,limit,ps,kind,hs,flags,special in signature_directories(old,load_commands(old)):
            self.assertEqual(flags,0x10002)
            self.assertEqual(old[cd:cd+hashes],new[cd:cd+hashes])
            self.assertEqual(old[cd+hashes+pages*hs:],new[cd+hashes+pages*hs:])
        signature_directories(new,load_commands(new))
        self.assertTrue(report['other_slices_byte_identical'])

    def test_rejects_bad_paths_duplicates_and_insufficient_padding(self):
        for paths in [[],['relative'],['/a\0b'],['/a','/a'],['/a.dyl'],['/'+('x'*4000)]]:
            with self.subTest(paths=paths),self.assertRaises(ValueError):append_dylibs(fixture(),paths)

    def test_rejects_changed_original_code_pages(self):
        b=bytearray(fixture());b[4096]^=1
        with self.assertRaisesRegex(ValueError,'code-page hash'):append_dylibs(bytes(b),['/test'])

    def test_rejects_nonadhoc_signature(self):
        b=bytearray(fixture());cd=signature_directories(b,load_commands(b))[0][0]
        struct.pack_into('>I',b,cd+12,0x10000)
        with self.assertRaisesRegex(ValueError,'ad-hoc'):append_dylibs(bytes(b),['/test'])

    def test_rejects_scatter(self):
        b=bytearray(fixture());cd=signature_directories(b,load_commands(b))[0][0]
        struct.pack_into('>I',b,cd+44,96)
        with self.assertRaisesRegex(ValueError,'scatter'):append_dylibs(bytes(b),['/test'])

    def test_refuses_second_application(self):
        once,_=append_dylibs(fixture(),['/test'])
        with self.assertRaisesRegex(ValueError,'already present'):append_dylibs(once,['/test'])

    def test_refuses_overlapping_fat_slices(self):
        b=bytearray(universal(fixture()));struct.pack_into('>I',b,8+20+8,4096)
        with self.assertRaisesRegex(ValueError,'overlapping'):append_dylibs(bytes(b),['/test'])

if __name__=='__main__':unittest.main()
