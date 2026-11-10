#!/usr/bin/env python3
"""Model a valid low-memory bootstrap alias in a disposable, paused QEMU VM.

Copy one allocation page into conventional low RAM, reserve that RAM, and
change the allocation's PTE before bootstrap PV registration. PV metadata is
never edited. Verify both aliases and their hash links before identity cleanup,
then verify that cleanup removes only the temporary alias. Leave the VM paused.
"""
import argparse
import json
from pathlib import Path
import runpy
import struct

GDB = runpy.run_path(str(Path(__file__).with_name('startup_trace_vm.py')))['GDB']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('gdb', 'layout', 'out'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--slide', type=lambda s: int(s, 0), required=True)
    parser.add_argument('--page', type=lambda s: int(s, 0), default=3)
    parser.add_argument('--allocation', choices=('prefix', 'vm_pages'), default='prefix')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    layout = {k:int(v, 0)+args.slide for k,v in json.loads(args.layout.read_text()).items()}
    gdb = GDB(args.gdb)

    def read(address, size=8):
        return int.from_bytes(gdb.memory(address, size), 'little')

    def stop(address):
        assert gdb.command(f'Z1,{address:x},1') == 'OK'
        gdb.command('c')
        registers = gdb.registers()
        assert gdb.command(f'z1,{address:x},1') == 'OK'
        assert registers['rip'] == address, registers
        return registers

    print('Waiting before bootstrap PV registration', flush=True)
    stop(layout['before_bootstrap'])
    pmap = layout['_kernel_pmap_store']
    physmap = read(layout['_physmap_base'])
    cr3 = read(pmap+0x40) & 0xffffffffff000
    va = read(layout['_vm_page_buckets' if args.allocation == 'prefix' else '_vm_pages'])
    if args.allocation == 'prefix':
        assert read(layout['_virtual_avail']) <= va < read(layout['_vm_pages'])

    def pte_for(address):
        table = cr3
        for shift in (39, 30, 21, 12):
            pointer = physmap+table+((address >> shift) & 511)*8
            entry = read(pointer)
            if shift == 12 or not entry & 1:
                return pointer, entry
            assert not entry & 128, 'Fixture expects 4 KiB mappings'
            table = entry & 0xffffffffff000

    pointer, entry = pte_for(va)
    assert entry & 1
    last = read(layout['_vm_kernel_base_page'], 4)
    regions = layout['_pmap_memory_regions']
    count = read(layout['_pmap_memory_region_count'], 4)
    for index in range(count):
        address = regions+index*40
        region = struct.unpack('<7I4xQ', gdb.memory(address, 40))
        if region[0] <= args.page <= region[5] < last:
            assert region[6] == 7  # EFI conventional memory
            gdb.write(address+32, struct.pack('<Q', region[7] | (1 << 59)))
            region_index = index
            break
    else:
        raise AssertionError('No conventional low-memory region for the fixture')
    table = read(layout['_pv_head_table'])
    root = table+args.page*32
    untouched = gdb.memory(root, 32)
    page = gdb.memory(physmap+(entry & 0xffffffffff000), 4096)
    gdb.write(physmap+args.page*4096, page)
    gdb.write(pointer, struct.pack('<Q', (entry & ~0xffffffffff000) | args.page*4096))
    assert gdb.memory(root, 32) == untouched, 'Fixture must not change PV metadata'

    # Flush the changed mapping through the kernel, retaining the interrupted state.
    saved = bytes.fromhex(gdb.command('g'))
    registers = gdb.registers()
    stack = ((registers['rsp']-1024) & ~15)-8
    gdb.write(stack, struct.pack('<Q', registers['rip']))
    call = bytearray(saved)
    for index, value in {5:pmap, 4:va, 3:va+4096, 2:0, 8:0, 7:stack,
                         16:layout['_pmap_flush_tlbs']}.items():
        struct.pack_into('<Q', call, index*8, value)
    assert gdb.command('G'+call.hex()) == 'OK'
    stop(registers['rip'])
    assert gdb.command('G'+saved.hex()) == 'OK'
    print(f'Modeled allocation {va:#x} at low PFN {args.page:#x}; PV unchanged', flush=True)
    stop(layout['after_bootstrap'])
    errors = (layout['failure'], layout['_panic'])
    for address in errors:
        assert gdb.command(f'Z1,{address:x},1') == 'OK'
    stop(layout['before_unmap'])
    table = read(layout['_pv_head_table'])
    root = table+args.page*32
    identity = 0xffffff8000000000+args.page*4096
    mappings = []
    current = root
    for _ in range(32):
        following, previous, address, owner = struct.unpack('<4Q', gdb.memory(current, 32))
        assert read(following+8) == current and read(previous) == current
        mappings.append((address & ~4095, owner))
        if current != root:
            assert read(current+32, 4) == args.page
            mask = read(layout['_npvhashmask'], 4)
            bucket = ((address >> 12) ^ owner) & mask
            hashed = read(read(layout['_pv_hash_table'])+bucket*8)
            for _ in range(1000):
                if hashed == current:
                    break
                assert hashed
                hashed = read(hashed+40)
            assert hashed == current
        current = following
        if current == root:
            break
    assert sorted(mappings) == sorted([(identity, pmap), (va, pmap)]), mappings
    stop(layout['after_unmap'])
    following, previous, address, owner = struct.unpack('<4Q', gdb.memory(root, 32))
    assert (following, previous, address & ~4095, owner) == (root, root, va, pmap)
    assert pte_for(identity)[1] == 0
    assert pte_for(va)[1] & 0xffffffffff001 == args.page*4096+1
    report = dict(allocation=args.allocation, page=hex(args.page), va=hex(va),
                  reserved_region=region_index, original_pte=hex(entry),
                  both_aliases_and_hash_links_verified=True,
                  only_identity_alias_removed=True, live_allocation_preserved=True)
    (args.out/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    for address in errors:
        assert gdb.command(f'z1,{address:x},1') == 'OK'
    print('PASS: both records valid; only temporary identity mapping removed', flush=True)


if __name__ == '__main__':
    main()
