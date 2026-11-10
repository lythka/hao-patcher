"""Generate an experimental early-VM repair from a captured kernel layout.

The capture describes the built collection, not the standalone kernel: the KC
builder moves DATA and KLDDATA independently of TEXT. All emitted references are
RIP-relative so OpenCore's common slide remains valid.
"""
import hashlib
import json
import runpy
import struct
from pathlib import Path


def generate(capture_path, collection_path):
    capture = json.loads(Path(capture_path).read_text())
    collection = Path(collection_path).read_bytes()
    if hashlib.sha256(collection).hexdigest() != capture['collection_sha256']:
        raise ValueError('Early-VM capture belongs to another kernel collection')
    site = int(capture['site_address'], 0)
    offset = int(capture['site_offset'], 0)
    original = bytes.fromhex(capture['original'])
    if collection[offset:offset+len(original)] != original:
        raise ValueError('Captured initializer no longer matches the collection')
    addresses = {k: int(v, 0) for k, v in capture['addresses'].items()}
    code = bytearray()

    def relative(prefix, destination, suffix=b''):
        displacement = destination - (site + len(code) + len(prefix) + 4 + len(suffix))
        code.extend(prefix + struct.pack('<i', displacement) + suffix)

    # A startup callback enters with rsp % 16 == 8. Preserve call alignment.
    code.extend(b'\x50')                        # push rax
    relative(b'\x48\x8d\x3d', addresses['virtual_space_start'])
    relative(b'\x48\x8d\x35', addresses['virtual_space_end'])
    relative(b'\xe8', addresses['pmap_virtual_space'])
    code.extend(b'\x58')                        # pop rax
    for name, size in capture['reservations'].items():
        relative(b'\x48\xc7\x05', addresses[name], struct.pack('<I', size))
    code.extend(b'\xb8\xff\x0f\x00\x00')      # eax = page mask
    relative(b'\x48\x03\x05', addresses['virtual_space_start'])
    overflow_branch = len(code)
    code.extend(b'\x72\x00')                    # jc overflow
    code.extend(b'\x48\x25\x00\xf0\xff\xff')  # round up to 4 KiB
    relative(b'\x48\x89\x05', addresses['map_stolen_data'])
    code.extend(b'\x48\x05' + struct.pack('<I', sum(capture['reservations'].values())))
    relative(b'\x48\x89\x05', addresses['virtual_space_start'])
    code.extend(b'\xc3')                        # ret
    code[overflow_branch+1] = len(code) - (overflow_branch+2)
    code.extend(b'\x0f\x0b')                    # overflow remains fatal
    if len(code) > len(original):
        raise ValueError('Repair does not fit the captured initializer')
    code.extend(b'\x90' * (len(original)-len(code)))
    patch = dict(Arch='x86_64', Base='', Comment='Initialize early VM range before reservation',
        Count=1, Enabled=True, Find=original, Identifier='kernel', Limit=0,
        Mask=b'', MaxKernel=capture['darwin_version'], MinKernel=capture['darwin_version'],
        Replace=bytes(code), ReplaceMask=b'', Skip=0)
    patches = [patch]
    if 'range_call' in capture:
        call = capture['range_call']
        call_offset = int(call['offset'], 0)
        before = bytes.fromhex(call['original'])
        if collection[call_offset:call_offset+len(before)] != before:
            raise ValueError('Captured range call no longer matches the collection')

        def mov(source, destination):
            registers = dict(rax=0, rcx=1, rdx=2, rbx=3, rsp=4, rbp=5,
                             rsi=6, rdi=7, r8=8, r9=9, r10=10, r11=11,
                             r12=12, r13=13, r14=14, r15=15)
            src, dst = registers[source], registers[destination]
            return bytes([0x48 | ((src >> 3) << 2) | (dst >> 3), 0x89,
                          0xc0 | ((src & 7) << 3) | (dst & 7)])

        # vm_map insertion expects (map, lower bound, upper bound, flags).
        # The diagnosed Intel kmem_init call supplied these two bounds reversed.
        wrong = mov(call['upper'], 'rsi') + mov(call['lower'], 'rdx')
        right = mov(call['lower'], 'rsi') + mov(call['upper'], 'rdx')
        if before[:len(wrong)] != wrong:
            raise ValueError('Range-call capture does not describe reversed bounds')
        patches.append(dict(patch, Comment='Pass early kernel-map reservation bounds in ascending order',
                            Find=before, Replace=right + before[len(wrong):]))
    if 'pv_bootstrap_start' in capture:
        raise ValueError('Legacy PV range expansion loses aliases; use a pv_aliases capture')
    if 'pv_aliases' in capture:
        generate_aliases = runpy.run_path(str(Path(__file__).with_name('pv_bootstrap.py')))['generate']
        for comment, before, after in generate_aliases(capture['pv_aliases'], collection):
            patches.append(dict(patch, Comment=comment, Find=before, Replace=after))
    return patches, dict(capture=capture, capture_path=str(Path(capture_path).resolve()),
        capture_sha256=hashlib.sha256(Path(capture_path).read_bytes()).hexdigest(),
        collection_path=str(Path(collection_path).resolve()), replacements=[dict(comment=p['Comment'], find=p['Find'].hex(), replace=p['Replace'].hex()) for p in patches])
