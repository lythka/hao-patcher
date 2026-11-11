"""Authored cache-image fixtures, with movable instruction runs and relocated branches."""
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]/'src'
BASE = 0x400000


def fixture(image, shift=0):
    recipes = json.loads((ROOT/'compat-locators.json').read_text())[image]
    data = bytearray(0x10000)
    struct.pack_into('<8I', data, 0, 0xfeedfacf, 0x1000007, 3, 6, 2, 256, 0, 0)
    struct.pack_into('<II16s', data, 32, 0x1b, 24, bytes(range(16)))
    struct.pack_into('<II16sQQQQIIII', data, 56, 0x19, 232,
                     b'__TEXT', BASE, len(data), 0, len(data), 5, 5, 2, 0)
    struct.pack_into('<16s16sQQ', data, 128, b'__text', b'__TEXT', BASE+0x1100, 0xd000)
    struct.pack_into('<16s16sQQ', data, 208, b'__stubs', b'__TEXT', BASE+0xf000, 0x100)
    sites=[]
    for i, row in enumerate(recipes):
        start=0x1200+i*0x100+shift
        pattern=bytes(0 if token=='??' else int(token,16) for token in row['pattern'].split())
        data[start:start+len(pattern)]=pattern
        sites.append(start+row['offset'])
    if image=='MultitouchSupport':
        data[sites[0]+4]=0x2a
        struct.pack_into('<i',data,sites[3]+13,0x100)
        struct.pack_into('<i',data,sites[3]+27,0x100-14)
    if image=='CoreRoutine':
        struct.pack_into('<i',data,sites[0]+9,0xf000-(sites[0]+13))
    return data, sites


def reader(data):
    return lambda address,size: bytes(data[address-BASE:address-BASE+size])
