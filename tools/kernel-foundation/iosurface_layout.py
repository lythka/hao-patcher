"""Bind the IOSurface property snapshot adapter to its donor and built collection."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import runpy
import struct
import subprocess

HERE = Path(__file__).resolve().parent
MACHO = runpy.run_path(str(HERE/'commpage_build.py'))
SYMBOL = '__ZN23IOSurfaceRootUserClient11s_set_valueEPS_PvP25IOExternalMethodArguments'
IDENTIFIER = b'com.apple.iokit.IOSurface'
PREFIX = bytes.fromhex('554889e541574156415541545350')
DESCRIPTOR_SLOTS = {
    0x1c8: '__ZNK18IOMemoryDescriptor9getLengthEv',
    0x1e0: '__ZN25IOGeneralMemoryDescriptor9readBytesEyPvy',
    0x1f0: '__ZN25IOGeneralMemoryDescriptor7prepareEj',
    0x1f8: '__ZN25IOGeneralMemoryDescriptor8completeEj',
}


def thin(data):
    """Select x86_64 from an ordinary fat KDK kext; thin inputs are unchanged."""
    if data[:4] == bytes.fromhex('cafebabe'):
        count = struct.unpack_from('>I', data, 4)[0]
        slices = []
        for i in range(count):
            cpu, _, off, size, _ = struct.unpack_from('>5I', data, 8+20*i)
            if cpu == 0x1000007:
                slices.append(data[off:off+size])
        if len(slices) != 1:
            raise ValueError('Expected one Intel IOSurface slice')
        data = slices[0]
    list(MACHO['commands'](data))  # also verifies thin Intel identity
    return data


def file_offset(data, address, header=0):
    for base, _, off, size in MACHO['segments'](data, header).values():
        if base <= address < base+size:
            return off+address-base
    raise ValueError('IOSurface address is not backed by collection bytes')


def uuid(data, header=0):
    return next(data[p+8:p+24].hex() for cmd, p in MACHO['commands'](data, header) if cmd == 0x1b)


def validate_descriptor_abi(kernel):
    """The MacKernelSDK virtual slots used by the compiled descriptor calls."""
    names = MACHO['symbols'](kernel)
    table = file_offset(kernel, names['__ZTV25IOGeneralMemoryDescriptor'])+16
    for slot, symbol in DESCRIPTOR_SLOTS.items():
        if struct.unpack_from('<Q', kernel, table+slot)[0] != names[symbol]:
            raise ValueError(f'IOMemoryDescriptor virtual slot {slot:#x} differs: {symbol}')


def capture(donor, collection):
    donor = thin(donor)
    entries = []
    for cmd, p in MACHO['commands'](collection):
        if cmd == 0x80000035:
            name = collection[p+struct.unpack_from('<I', collection, p+24)[0]:].split(b'\0', 1)[0]
            if name == IDENTIFIER:
                entries.append(struct.unpack_from('<QQ', collection, p+8))
    if len(entries) != 1:
        raise ValueError('Expected one IOSurface member in BootKC')
    header_address, header_offset = entries[0]
    if uuid(donor) != uuid(collection, header_offset):
        raise ValueError('IOSurface donor and BootKC UUID differ')
    symbols = MACHO['symbols'](donor)
    start = symbols[SYMBOL]
    end = min(value for value in symbols.values() if value > start)
    code = donor[file_offset(donor, start):file_offset(donor, start)+end-start]
    # This entry's whole 14-byte prologue has no PC-relative instructions.
    # The observed dual input path is the contract used by the adapter.
    shapes = (PREFIX, bytes.fromhex('488b7a404885ff'),
              bytes.fromhex('4d8b6530418b453831db'), bytes.fromhex('83f80d'))
    if not code.startswith(PREFIX) or any(shape not in code for shape in shapes[1:]):
        raise ValueError('IOSurface s_set_value no longer has the captured descriptor/inline input ABI')
    old = MACHO['segments'](donor)
    new = MACHO['segments'](collection, header_offset)
    for name, (base, size, _, _) in old.items():
        if base <= start < base+size:
            address = new[name][0]+start-base
            break
    else:
        raise ValueError('IOSurface entry is outside donor segments')
    offset = file_offset(collection, address, header_offset)
    if collection[offset:offset+len(code)] != code:
        raise ValueError('IOSurface s_set_value differs in BootKC')
    return dict(collection_sha256=hashlib.sha256(collection).hexdigest(),
                donor_sha256=hashlib.sha256(donor).hexdigest(), donor_uuid=uuid(donor),
                identifier=IDENTIFIER.decode(), symbol=SYMBOL,
                header_address=hex(header_address), header_offset=hex(header_offset),
                entry_address=hex(address), entry_offset=hex(offset),
                prefix_hex=PREFIX.hex(), function_hex=code.hex())


def definitions(layout, collection):
    if layout['collection_sha256'] != hashlib.sha256(collection).hexdigest():
        raise ValueError('IOSurface layout belongs to another collection')
    header, offset = int(layout['header_offset'], 0), int(layout['entry_offset'], 0)
    code = bytes.fromhex(layout['function_hex'])
    prefix = bytes.fromhex(layout['prefix_hex'])
    if prefix != PREFIX or not code.startswith(prefix):
        raise ValueError('IOSurface layout has an unsafe trampoline prefix')
    if (uuid(collection, header) != layout['donor_uuid'] or
            collection[offset:offset+len(code)] != code or
            file_offset(collection, int(layout['header_address'], 0), header) != header or
            file_offset(collection, int(layout['entry_address'], 0), header) != offset):
        raise ValueError('IOSurface layout does not describe its collection member')
    return [f'static constexpr uintptr_t addr_iosurface_header = {layout["header_address"]}ULL;',
            f'static constexpr uintptr_t addr_iosurface_set_value = {layout["entry_address"]}ULL;',
            'static const unsigned char iosurface_uuid[] = {'+','.join(str(v) for v in bytes.fromhex(layout['donor_uuid']))+'};',
            'static const unsigned char iosurface_prefix[] = {'+','.join(str(v) for v in prefix)+'};']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--donor-iosurface', type=Path, required=True)
    parser.add_argument('--boot-kc', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    donor = args.donor_iosurface.read_bytes()
    result = capture(donor, args.boot_kc.read_bytes())
    result.update(unit='tools/kernel-foundation', revision=runpy.run_path(str(HERE/'unit.py'))['REVISION'],
                  operation='iosurface-layout', date=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  commit=subprocess.check_output(['git', '-C', str(HERE), 'rev-parse', 'HEAD'], text=True).strip(),
                  dirty=bool(subprocess.check_output(['git', '-C', str(HERE), 'status', '--porcelain'], text=True)),
                  inputs=dict(donor_iosurface=str(args.donor_iosurface.resolve()),
                              donor_file_sha256=hashlib.sha256(donor).hexdigest(),
                              boot_kc=str(args.boot_kc.resolve())))
    with args.out.open('x') as out:
        out.write(json.dumps(result, indent=2)+'\n')
    print(args.out)


if __name__ == '__main__':
    main()
