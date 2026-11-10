"""Register bootstrap aliases before zone relocation removes its early mappings."""
from pathlib import Path
import runpy
import struct
import subprocess
import tempfile


def generate(capture, collection):
    here = Path(__file__).resolve().parent
    cave, hook = capture['cave'], capture['hook']
    original = bytes.fromhex(cave['original'])
    pos, origin = int(cave['offset'], 0), int(cave['address'], 0)
    capacity = cave['capacity']
    if collection[pos:pos+len(original)] != original or any(original[:capacity]):
        raise ValueError('Bootstrap PV code padding differs')
    before = bytes.fromhex(hook['original'])
    pos = int(hook['offset'], 0)
    if collection[pos:pos+len(before)] != before:
        raise ValueError('Bootstrap PV registration instructions differ')
    macho = runpy.run_path(str(here/'commpage_build.py'))
    lines = ['.text', f'.set origin, {origin:#x}']
    lines += [f'.set {name}, {address}' for name, address in capture['addresses'].items()]
    lines.append((here/'src/bootstrap_pv_alias.S').read_text())
    with tempfile.TemporaryDirectory(prefix='kernel-foundation-pv-') as temporary:
        source, obj = Path(temporary)/'pv.S', Path(temporary)/'pv.o'
        source.write_text(('\n'.join(lines)+'\n').replace('pv_alias_start', 'Lpv_alias_start'))
        subprocess.run(['xcrun', 'clang', '-target', 'x86_64-apple-macos10.13',
                        '-c', str(source), '-o', str(obj)], check=True)
        data = obj.read_bytes()
    segment = next(p for c, p in macho['commands'](data) if c == 0x19)
    section = segment+72
    _, size, offset = struct.unpack_from('<QQI', data, section+32)
    if struct.unpack_from('<I', data, section+60)[0]:
        raise ValueError('Bootstrap PV repair has unresolved relocations')
    code = data[offset:offset+size]
    if len(code) > capacity:
        raise ValueError('Bootstrap PV repair exceeds captured padding')
    replacement = b'\xe8'+struct.pack('<i', origin-int(hook['address'], 0)-5)
    replacement += b'\x90'*(hook['overwrite_size']-5)+before[hook['overwrite_size']:]
    result = [('Preserve bootstrap physical-page aliases: code', original,
               code+original[len(code):]),
              ('Preserve bootstrap physical-page aliases: registration', before, replacement)]
    for comment, find, _ in result:
        if collection.count(find) != 1:
            raise ValueError('Bootstrap PV match is not unique: '+comment)
    return result
