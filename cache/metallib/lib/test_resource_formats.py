import json
from pathlib import Path
import struct
import unittest
import resource_formats as reader
from test_mtlb import fixture


def wrapper(payload=b'BC\xc0\xdeTEST'):
    return struct.pack('<5I', 0x0b17c0de, 0, 20, len(payload), 0xffffffff) + payload


def indexed(names=('one', 'two')):
    entries=[];pos=4+sum(len(n.encode())+5 for n in names)
    blobs=[]
    for n in names:
        blob=wrapper();entries.append(n.encode()+b'\0'+struct.pack('<I',pos));blobs.append(struct.pack('<I',len(blob))+blob);pos+=len(blob)+4
    return struct.pack('<I',len(names))+b''.join(entries)+b''.join(blobs)


def fat():
    blobs=[fixture(shared=False),struct.pack('<8I',0xfeedfacf,0x1000013,0x53,13,1,8,0,0)+struct.pack('<2I',1,8)]
    table=[];body=bytearray();pos=48
    for (cpu,sub),blob in zip([(0x1000017,12),(0x1000013,0x53)],blobs):
        padding=(-pos)%16;body+=bytes(padding);pos+=padding
        table.append(struct.pack('>5I',cpu,sub,pos,len(blob),4));body+=blob;pos+=len(blob)
    return b'\xca\xfe\xba\xbe'+struct.pack('>I',2)+b''.join(table)+body


class ResourceFormatTests(unittest.TestCase):
    def test_indexed_modules_and_explicit_native_coverage(self):
        r=reader.decode(indexed());self.assertEqual(r['kind'],'indexed-LLVM');self.assertEqual(r['symbols'],2)
        self.assertEqual(r['modules'][0]['blob'],wrapper())
        r=reader.decode(fat());self.assertEqual(len(r['modules']),1);self.assertEqual(r['exports'],1)
        self.assertEqual(len(r['native_slices']),1);self.assertEqual(r['native_slices'][0]['status'],'opaque-native')

    def test_indexed_mutations_refuse(self):
        original=indexed()
        for kind in ['count','duplicate','offset','size','magic','version','cpu','range','padding','tail','name']:
            data=bytearray(original);start=struct.unpack_from('<I',data,8)[0]
            if kind=='count':struct.pack_into('<I',data,0,100001)
            elif kind=='duplicate':data=bytearray(indexed(('one','one')))
            elif kind=='offset':struct.pack_into('<I',data,8,start+1)
            elif kind=='size':struct.pack_into('<I',data,start,0xffffffff)
            elif kind=='magic':data[start+4]^=1
            elif kind=='version':struct.pack_into('<I',data,start+8,1)
            elif kind=='cpu':struct.pack_into('<I',data,start+20,1)
            elif kind=='range':struct.pack_into('<I',data,start+16,0xffffffff)
            elif kind=='padding':
                blob=wrapper()+b'X';data=bytearray(struct.pack('<I',1)+b'one\0'+struct.pack('<I',12)+struct.pack('<I',len(blob))+blob)
            elif kind=='tail':data+=b'X'
            else:data[4]=0xff
            with self.subTest(kind=kind),self.assertRaises(ValueError):reader.decode(bytes(data))

    def test_fat_range_cpu_command_and_tail_mutations_refuse(self):
        original=fat();second=struct.unpack_from('>I',original,36)[0]
        for kind in ['count','alignment','overlap','size','padding','cpu','command','tail','magic']:
            data=bytearray(original)
            if kind=='count':struct.pack_into('>I',data,4,101)
            elif kind=='alignment':struct.pack_into('>I',data,24,32)
            elif kind=='overlap':struct.pack_into('>I',data,36,48)
            elif kind=='size':struct.pack_into('>I',data,40,0xffffffff)
            elif kind=='padding':data[second-1]=1
            elif kind=='cpu':struct.pack_into('<I',data,second+4,1)
            elif kind=='command':struct.pack_into('<I',data,second+36,4)
            elif kind=='tail':data+=b'X'
            else:data[second]^=1
            with self.subTest(kind=kind),self.assertRaises(ValueError):reader.decode(bytes(data))

