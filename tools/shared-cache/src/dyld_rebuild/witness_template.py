"""Place the freestanding witness runtime using its checked relocation template.

The producer links once. Runtime placement adjusts only segment/section addresses
and the object's supported signed PC-relative data references; unknown relocations
are refused during production instead of guessed.
"""
import hashlib
import json
from pathlib import Path
import struct

from .exports import require


def commands(raw):
    require(struct.unpack_from('<I', raw)[0] == 0xfeedfacf, 'expected thin Mach-O 64')
    pos = 32
    for _ in range(struct.unpack_from('<I', raw, 16)[0]):
        kind, size = struct.unpack_from('<II', raw, pos)
        require(size >= 8 and pos + size <= len(raw), 'invalid load command')
        yield kind, pos
        pos += size


def produce(toolchain, output):
    from .witness_runtime import developer_link
    code, data = 0x100000000, 0x101000000
    blob, symbols, _ = developer_link(toolchain, code, data, output)
    obj = (toolchain / 'runtime.o').read_bytes()
    text_offset = None
    pointers = []
    for kind, pos in commands(blob):
        if kind != 0x19:
            continue
        segment = blob[pos+8:pos+24].rstrip(b'\0').decode()
        require(segment in ('__TEXT', '__DATA', '__LINKEDIT'), 'unexpected runtime segment')
        base = 'code' if segment == '__TEXT' else 'data'
        pointers.append([pos+24, base])
        for i in range(struct.unpack_from('<I', blob, pos+64)[0]):
            section = pos+72+80*i
            pointers.append([section+32, base])
            if blob[section:section+16].rstrip(b'\0') == b'__text':
                text_offset = struct.unpack_from('<I', blob, section+48)[0]
    require(text_offset is not None, 'missing linked text')
    relocations = []
    for kind, pos in commands(obj):
        if kind != 0x19:
            continue
        for i in range(struct.unpack_from('<I', obj, pos+64)[0]):
            section = pos+72+80*i
            rel, count = struct.unpack_from('<II', obj, section+56)
            if count:
                require(obj[section:section+16].rstrip(b'\0') == b'__text', 'non-text relocations')
            for j in range(count):
                address, bits = struct.unpack_from('<II', obj, rel+8*j)
                # X86_64_RELOC_SIGNED, external, 32-bit, PC-relative.
                require(bits >> 24 == 0x1d and address + 4 <= len(blob)-text_offset,
                        'unsupported witness object relocation')
                offset = text_offset+address
                displacement = struct.unpack_from('<i', blob, offset)[0]
                target = code+offset+4+displacement
                require(data <= target < data+4096, 'relocation is not a data reference')
                relocations.append(offset)
    record = dict(schema=1, object_sha256=hashlib.sha256(obj).hexdigest(), code=code, data=data,
                  text=blob.hex(), symbols=symbols, address_fields=pointers,
                  data_relocations=sorted(relocations))
    (output / 'witness-template.json').write_text(json.dumps(record, sort_keys=True, indent=2)+'\n')
    return record


def emit(template, object_file, code, data):
    record = json.loads(Path(template).read_text())
    require(record['schema'] == 1, 'unsupported witness template schema')
    require(hashlib.sha256(Path(object_file).read_bytes()).hexdigest() == record['object_sha256'],
            'witness runtime object differs from pinned template')
    require(code % 4096 == 0 and data % 4096 == 0 and data >= code + len(bytes.fromhex(record['text'])),
            'invalid witness runtime placement')
    changes = dict(code=code-record['code'], data=data-record['data'])
    blob = bytearray.fromhex(record['text'])
    for offset, base in record['address_fields']:
        value = struct.unpack_from('<Q', blob, offset)[0]+changes[base]
        require(0 <= value < 2**64, 'witness address overflow')
        struct.pack_into('<Q', blob, offset, value)
    for offset in record['data_relocations']:
        value = struct.unpack_from('<i', blob, offset)[0]+changes['data']-changes['code']
        require(-2**31 <= value < 2**31, 'witness data reference out of range')
        struct.pack_into('<i', blob, offset, value)
    symbols = {name: value + changes['data' if value >= record['data'] else 'code']
               for name, value in record['symbols'].items()}
    return bytes(blob), symbols, ['pinned-witness-template', str(template), hex(code), hex(data)]
