#!/usr/bin/env python3
"""Exercise every trace wrapper in a paused QEMU guest with GDB and QMP.

Boot the generated trace EFI with a fixed slide and QEMU -S -gdb unix:PATH,server=on.
This integration test resumes that owned VM and leaves it paused on failure.
"""
import argparse
import json
from pathlib import Path
import socket
import struct


class GDB:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(120)
        self.sock.connect(str(path))

    def command(self, value):
        value = value.encode()
        self.sock.sendall(b'$'+value+b'#'+f'{sum(value)%256:02x}'.encode())
        while True:
            char = self.sock.recv(1)
            if not char:
                raise EOFError('GDB disconnected')
            if char == b'$':
                break
        result = b''
        while True:
            char = self.sock.recv(1)
            if not char:
                raise EOFError('GDB disconnected')
            if char == b'#':
                break
            result += char
        checksum = self.sock.recv(2)
        assert int(checksum, 16) == sum(result) % 256
        self.sock.sendall(b'+')
        return result.decode()

    def memory(self, address, size):
        return bytes.fromhex(self.command(f'm{address:x},{size:x}'))

    def write(self, address, data):
        for pos in range(0, len(data), 256):
            part = data[pos:pos+256]
            assert self.command(f'M{address+pos:x},{len(part):x}:'+part.hex()) == 'OK'

    def registers(self):
        raw = bytes.fromhex(self.command('g'))
        names = ['rax', 'rbx', 'rcx', 'rdx', 'rsi', 'rdi', 'rbp', 'rsp']
        names += [f'r{i}' for i in range(8, 16)]+['rip']
        result = dict(zip(names, struct.unpack_from('<17Q', raw)))
        result['flags'] = struct.unpack_from('<I', raw, 136)[0]
        return result


def console_test(gdb, capture, slide, framebuffer, stride, width, height):
    """Invoke the installed mirror, stopping before the original console body.

    Restore registers, stack and diagnostic cursor afterwards. Compare pixels
    against the target's actual font, including newline, wrap and busy-lock paths.
    """
    console = capture['console']
    saved = bytes.fromhex(gdb.command('g'))
    original_rsp = struct.unpack_from('<Q', saved, 7*8)[0]
    stack_address = original_rsp-1024
    stack = gdb.memory(stack_address, 1024)
    state_address = int(console['state']['address'], 0)+slide
    state = gdb.memory(state_address, 16)
    font_address = int(console['font'], 0)+slide
    entries = {e['kind']: e for e in console['entries']}

    def invoke(kind, value):
        entry = int(entries[kind]['address'], 0)+slide
        branch = gdb.memory(entry, 5)
        assert branch[0] == 0xe9
        wrapper = entry+5+struct.unpack_from('<i', branch, 1)[0]
        registers = bytearray(saved)
        struct.pack_into('<Q', registers, 7*8, original_rsp-512)
        struct.pack_into('<Q', registers, 16*8, entry)
        if kind == 'buffer':
            gdb.write(original_rsp-128, value)
            struct.pack_into('<Q', registers, 5*8, original_rsp-128)
            struct.pack_into('<Q', registers, 4*8, len(value))
        else:
            struct.pack_into('<Q', registers, 5*8, value)
        assert gdb.command('G'+registers.hex()) == 'OK'
        before = gdb.registers()
        assert gdb.command(f'Z1,{wrapper+5:x},1') == 'OK'
        gdb.command('c')
        after = gdb.registers()
        assert gdb.command(f'z1,{wrapper+5:x},1') == 'OK'
        assert after['rip'] == wrapper+5, after
        assert {k:v for k,v in before.items() if k != 'rip'} == {
            k:v for k,v in after.items() if k != 'rip'}

    def glyphs(text, x, y):
        for index, char in enumerate(text):
            glyph = gdb.memory(font_address+char*16, 16)
            for row, bits in enumerate(glyph):
                actual = struct.unpack('<8I', gdb.memory(
                    framebuffer+(y+row)*stride+(x+index*8)*4, 32))
                expected = tuple(0x00ffffff if bits & (1 << bit) else 0x00131b26
                                 for bit in range(8))
                assert actual == expected, (chr(char), row, actual, expected)

    try:
        gdb.write(state_address, bytes(16))
        message = b'Framebuffer mirror: ASCII 09AZaz!?'
        invoke('buffer', message+b'\nNEXT')
        glyphs(message, 0, 32)
        glyphs(b'NEXT', 0, 48)
        invoke('char', ord('!'))
        glyphs(b'NEXT!', 0, 48)
        assert struct.unpack('<4I', gdb.memory(state_address, 16)) == (0, 40, 48, 1)
        gdb.write(state_address, b'\x01')
        invoke('char', ord('X'))
        assert struct.unpack('<4I', gdb.memory(state_address, 16)) == (1, 40, 48, 1)
        glyphs(b'NEXT!', 0, 48)
        last_row = 32+((height-32)//16-1)*16
        gdb.write(state_address, struct.pack('<4I', 0, (width//8-1)*8, last_row, 1))
        invoke('char', ord('#'))
        assert struct.unpack('<4I', gdb.memory(state_address, 16)) == (0, 0, 32, 1)
        invoke('buffer', b'Readable panic output\rR')
        glyphs(b'Readable panic output', 0, 32)
        return dict(registers_preserved=True, font_pixels_verified=True,
                    newline_and_wrap_verified=True, reentrant_output_dropped=True)
    finally:
        gdb.write(state_address, state)
        gdb.write(stack_address, stack)
        assert gdb.command('G'+saved.hex()) == 'OK'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('gdb', 'qmp', 'capture', 'out'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--slide', type=lambda s: int(s, 0), default=0,
                        help='Kernel slide in bytes (boot argument slide multiplied by 2 MiB)')
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text())
    args.out.mkdir(parents=True, exist_ok=False)
    gdb = GDB(args.gdb)
    qmp = socket.socket(socket.AF_UNIX)
    qmp.settimeout(15)
    qmp.connect(str(args.qmp))
    stream = qmp.makefile('rwb', buffering=0)
    stream.readline()

    def command(name, arguments=None):
        stream.write((json.dumps(dict(execute=name, arguments=arguments or {}))+'\n').encode())
        while True:
            response = json.loads(stream.readline())
            if 'error' in response:
                raise RuntimeError(response)
            if 'return' in response:
                return response['return']

    command('qmp_capabilities')
    results = []
    for stage, hook in enumerate(capture['calls'], 1):
        site = int(hook['address'], 0)+args.slide
        original = bytes.fromhex(hook['original'])
        destination = site+5+struct.unpack_from('<i', original, 1)[0]
        assert gdb.command(f'Z1,{site:x},1') == 'OK'
        gdb.command('c')
        before = gdb.registers()
        assert before['rip'] == site, before
        assert gdb.command(f'z1,{site:x},1') == 'OK'
        assert gdb.command(f'Z1,{destination:x},1') == 'OK'
        gdb.command('c')
        after = gdb.registers()
        assert gdb.command(f'z1,{destination:x},1') == 'OK'
        assert after['rip'] == destination, after
        assert after['rsp'] == before['rsp']-8
        assert int.from_bytes(gdb.memory(after['rsp'], 8), 'little') == site+5
        for name in before.keys()-{'rip', 'rsp'}:
            assert before[name] == after[name], (name, before[name], after[name])
        boot = int.from_bytes(gdb.memory(int(capture['boot_args_slot'], 0)+args.slide, 8), 'little')
        video = gdb.memory(boot, 0x4e0)
        physical = struct.unpack_from('<Q', video, 0x4d8)[0]
        stride = struct.unpack_from('<I', video, 0x4ac if physical else 0x420)[0]
        if not physical:
            physical = struct.unpack_from('<I', video, 0x418)[0]
        base = int.from_bytes(gdb.memory(int(capture['physmap_slot'], 0)+args.slide, 8), 'little')
        pixels = b''.join(gdb.memory(base+physical+(stage-1)*128+y*stride, 128)
                          for y in range(32))
        colors = set(struct.unpack('<1024I', pixels))
        assert colors == {0x003060a0, 0x00ffffff}, (stage, colors)
        command('screendump', {'filename': str(args.out/f'stage-{stage:X}.ppm')})
        results.append(dict(stage=f'{stage:X}', name=hook['name'],
                            registers_preserved=True, return_address_preserved=True,
                            framebuffer_verified=True))
        (args.out/'result.json').write_text(json.dumps(results, indent=2)+'\n')
        print(f'{stage:X}: registers, flags, return address and framebuffer PASS', flush=True)
    if capture.get('console'):
        width = struct.unpack_from('<I', video, 0x4b0 if struct.unpack_from('<Q', video, 0x4d8)[0] else 0x424)[0]
        height = struct.unpack_from('<I', video, 0x4b4 if struct.unpack_from('<Q', video, 0x4d8)[0] else 0x428)[0]
        checked = console_test(gdb, capture, args.slide, base+physical, stride, width, height)
        (args.out/'console.json').write_text(json.dumps(checked, indent=2)+'\n')
        command('screendump', {'filename': str(args.out/'console.ppm')})
        print('Console: registers, glyphs, newline, wrap and busy lock PASS', flush=True)
    assert gdb.command('D') == 'OK'


if __name__ == '__main__':
    main()
