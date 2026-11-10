"""Generated fixture adapted from the project Mach-O editor tests; no Apple bytes."""
import struct
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('fixture_editor', Path(__file__).resolve().parents[1]/'src/macho_dependencies.py')
editor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor)
digest = editor.digest

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
