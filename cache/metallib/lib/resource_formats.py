"""Read checked MTLB, indexed LLVM wrappers and fat GPU/AIR resource containers.

Format validation is not compatibility acceptance. Native Mach-O slices remain
opaque coverage blockers. Return module bytes without modifying any resource.
"""
import hashlib
import struct
import mtlb


def need(test, message):
    if not test:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def llvm_wrapper(blob):
    need(len(blob) >= 20, 'short LLVM wrapper')
    magic, version, start, length, cpu = struct.unpack_from('<5I', blob)
    need(magic == 0x0b17c0de and version == 0 and start == 20, 'unsupported LLVM wrapper')
    need(cpu == 0xffffffff, 'unsupported indexed LLVM wrapper CPU')
    need(start + length <= len(blob) and length >= 4, 'LLVM bitcode range')
    need(blob[start:start + 4] == b'BC\xc0\xde', 'LLVM bitcode magic')
    need(not any(blob[start + length:]), 'LLVM wrapper nonzero padding')


def mtlb_modules(data):
    lib = mtlb.check(data)
    groups = {}
    for f in lib['fns']:
        blob = mtlb.bitcode(data, lib, f)
        key = digest(blob)
        item = groups.setdefault(key, dict(sha256=key, blob=blob, names=[]))
        item['names'].append(f['name'])
    return list(groups.values()), len(lib['fns'])


def decode(data):
    need(len(data) >= 4, 'short shader resource')
    if data[:4] == b'MTLB':
        modules, exports = mtlb_modules(data)
        return dict(kind='MTLB', modules=modules, exports=exports, native_slices=[])
    if data[:4] == b'\xca\xfe\xba\xbe':
        return decode_fat(data)
    return decode_indexed(data)


def decode_indexed(data):
    count = struct.unpack_from('<I', data)[0]
    need(0 < count <= 100000, 'invalid indexed module count')
    pos, names, entries = 4, set(), []
    for _ in range(count):
        end = data.find(b'\0', pos)
        need(end >= pos and end + 5 <= len(data), 'indexed name/range')
        try:
            name = data[pos:end].decode('utf-8')
        except UnicodeDecodeError as error:
            raise ValueError('indexed name encoding') from error
        need(name and name not in names, 'empty or duplicate indexed name')
        names.add(name)
        offset = struct.unpack_from('<I', data, end + 1)[0]
        entries.append((name, offset))
        pos = end + 5
    modules = []
    for name, offset in entries:
        need(offset == pos and offset + 4 <= len(data), 'indexed gap/overlap/range')
        size = struct.unpack_from('<I', data, offset)[0]
        need(offset + 4 + size <= len(data), 'indexed blob range')
        blob = data[offset + 4:offset + 4 + size]
        llvm_wrapper(blob)
        modules.append(dict(names=[name], sha256=digest(blob), blob=blob))
        pos = offset + 4 + size
    need(pos == len(data), 'unclaimed indexed tail')
    return dict(kind='indexed-LLVM', modules=modules, exports=0,
                symbols=count, native_slices=[])


def native_macho(blob, cpu, subtype):
    need(len(blob) >= 32 and blob[:4] == b'\xcf\xfa\xed\xfe', 'unsupported native slice')
    _, actual_cpu, actual_subtype, filetype, count, commands, _, _ = struct.unpack_from('<8I', blob)
    need((cpu, subtype) == (actual_cpu, actual_subtype), 'native slice CPU mismatch')
    need(32 + commands <= len(blob), 'native command range')
    pos = 32
    for _ in range(count):
        need(pos + 8 <= 32 + commands, 'short native command')
        _, size = struct.unpack_from('<2I', blob, pos)
        need(size >= 8 and size % 8 == 0 and pos + size <= 32 + commands,
             'invalid native command size')
        pos += size
    need(pos == 32 + commands, 'unclaimed native commands')
    return dict(cpu=cpu, subtype=subtype, filetype=filetype, sha256=digest(blob),
                size=len(blob), status='opaque-native', load_commands=count)


def decode_fat(data):
    need(len(data) >= 8, 'short fat header')
    count = struct.unpack_from('>I', data, 4)[0]
    need(0 < count <= 100 and 8 + 20 * count <= len(data), 'invalid fat table')
    pos, modules, native, slices, exports = 8 + 20 * count, [], [], [], 0
    for index in range(count):
        cpu, subtype, offset, size, alignment = struct.unpack_from('>5I', data, 8 + 20 * index)
        need(alignment < 32 and offset % (1 << alignment) == 0, 'fat slice alignment')
        need(offset >= pos and size > 0 and offset + size <= len(data), 'fat gap/overlap/range')
        need(not any(data[pos:offset]), 'nonzero fat alignment padding')
        blob = data[offset:offset + size]
        item = dict(index=index, cpu=cpu, subtype=subtype, offset=offset, size=size,
                    sha256=digest(blob))
        if blob[:4] == b'MTLB':
            found, exported = mtlb_modules(blob)
            for module in found:
                module = dict(module, slice_index=index, slice_cpu=cpu, slice_subtype=subtype)
                modules.append(module)
            exports += exported
            item['kind'] = 'MTLB'
        else:
            native.append(dict(native_macho(blob, cpu, subtype), slice_index=index))
            item['kind'] = 'opaque-native'
        slices.append(item)
        pos = offset + size
    need(pos == len(data), 'unclaimed fat tail')
    return dict(kind='fat', modules=modules, exports=exports, native_slices=native, slices=slices)
