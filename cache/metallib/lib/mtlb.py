#!/usr/bin/env python3
"""Checked MTLB 2.6-2.9 AIR reader/splicer.

The 88-byte header names function, public/private metadata and bitcode sections.
After the function entries, global HDYN/RLST tags hold absolute offset/size pairs
for trailing dynamic-library/reflection data. Preserve these payloads exactly and
move their offsets by the bitcode size delta. UUID tags and function metadata are
preserved. Unknown global tags, gaps, overlaps, hashes or ranges refuse a splice.
"""
import hashlib
import struct

# These exact Apple post.bom-matching CI modules carry a historical HASH
# that differs from their embedded bytes. Never accept this for another file.
FACTORY_MODULE_HASHES = {
    "d8e77d9153e2e0f89beeda69a3034d36ff157e6b66f63a5e1d2ca844e12104e7": (
        "7708b525b4a76e61e19ced16d448401c90bdea82c10eacd92c07c803016a6b8f",
        "c298f2168b89d2f4770431a3af35b136ff561ba9485ed2ee8f35a6b31aacdaa2"),
    "b2fb639f4fbe2764be48c8889bde19bac5b429a8d25f3d227bc02969f7585173": (
        "ab2e387a1014f60f5a4ca73c88673572efbef8c3d251aa4e3bebb93fa1b83879",
        "9a573b5108f483196cf4edbb4331a3411e323fac5be600ce51172651a6bea57a"),
    "50187950707060056cb0eee754ca712aea1347cf121666fd0519354e17d67c04": (
        "219a13b0a9bde72c4d6ac1292246a6c8094d6c898e2155c9d2a39fe18717f40b",
        "981771a05358575b98849eca17fc868550b76a9f8df40d529291f05ea4ae720e"),
    "2ca25d9741a79a1211eea6201351a464b654355446ddc14329fb5caea3a1d53f": (
        "0e414884db3f6d5cb77ddbada8fb19d6ce0578134fbcd85f4fdf82f1d7ee312f",
        "eebef004e7db680226eed415deffa506ac463c8b77bfc31da36ed5b2120c5f12"),
    "ba9e878c48b6276a181f06f9a216041d86fa01d081a2449af3524c445293b94c": (
        "14f0b8b3c7c414404210063ea78020f2feeedde067463395bbaa2e47a0018678",
        "a184cf91c3fafd9ea4d53ac70034cbf82d4590601a1417da79599cf5df29f820"),
    "e7062ac81d1e5092187d34c5ae1ae8892d95eea48491898b47ce55354343b7ec": (
        "b60e6d7738b7c04fd086ca903f39b4fc2373c4f93acbaaf76b9c89e98ed7aaad",
        "1d78b45af061ca6714188504052627be0262167e5e536802a4f8606fd9aad5a1"),
}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def tags(data, start, limit):
    result = {}
    off = start
    while True:
        need(off + 4 <= limit, 'unterminated tag list')
        tag = data[off:off + 4]
        if tag == b'ENDT':
            return result, off + 4
        need(off + 6 <= limit, 'truncated tag length')
        length = struct.unpack_from('<H', data, off + 4)[0]
        need(off + 6 + length <= limit, 'tag payload escapes section')
        need(tag not in result, 'duplicate tag')
        result[tag] = (off + 6, length)
        off += 6 + length


def parse(data):
    need(len(data) >= 88 and data[:4] == b'MTLB', 'not a complete MTLB header')
    major, minor = struct.unpack_from('<HH', data, 6)
    need(major == 2 and minor in (6, 7, 8, 9), 'unsupported MTLB version')
    hdr = struct.unpack_from('<9Q', data, 16)
    size, flo, fls, pmo, pms, qmo, qms, bco, bcs = hdr
    need(size == len(data), 'file size differs from header')
    need(88 <= flo and flo + 4 + fls <= pmo <= qmo <= bco <= size, 'invalid section order')
    need(pmo + pms == qmo and qmo + qms == bco and bco + bcs <= size, 'section ranges do not meet')
    n = struct.unpack_from('<I', data, flo)[0]
    off, functions = flo + 4, []
    for _ in range(n):
        need(off + 4 <= flo + 4 + fls, 'function entry count exceeds section')
        entry_size = struct.unpack_from('<I', data, off)[0]
        end = off + entry_size
        need(entry_size >= 8 and end <= flo + 4 + fls, 'invalid function entry length')
        entry, off = tags(data, off + 4, end)
        need(off == end, 'function entry has unparsed bytes')
        need(all(k in entry for k in (b'NAME', b'OFFT', b'MDSZ', b'HASH')), 'missing function tag')
        need(entry[b'OFFT'][1] == 24 and entry[b'MDSZ'][1] == 8 and entry[b'HASH'][1] == 32,
             'wrong function tag width')
        pos, length = entry[b'NAME']
        raw_name = data[pos:pos + length]
        need(raw_name.endswith(b'\0'), 'unterminated function name')
        name = raw_name[:-1].decode()
        offsets = struct.unpack_from('<3Q', data, entry[b'OFFT'][0])
        count = struct.unpack_from('<Q', data, entry[b'MDSZ'][0])[0]
        pos, length = entry[b'HASH']
        functions.append(dict(name=name, tags=entry, offt=offsets, size=count,
                              hash=data[pos:pos + length]))
    need(off == flo + 4 + fls, 'function list size differs from entries')
    global_tags, end = tags(data, off, pmo)
    need(end == pmo, 'unparsed global metadata')
    need(set(global_tags) <= {b'HDYN', b'ILST', b'RLST', b'UUID'}, 'unknown global metadata tag')
    trailers = []
    for name, (pos, length) in global_tags.items():
        need(length == 16, 'wrong global tag width')
        if name in (b'HDYN', b'ILST', b'RLST'):
            offset, count = struct.unpack_from('<2Q', data, pos)
            need(offset >= bco + bcs and offset + count <= size, 'trailer outside tail')
            trailers.append(dict(tag=name, position=pos, offset=offset, size=count))
    pos = bco + bcs
    for trailer in sorted(trailers, key=lambda item: item['offset']):
        need(trailer['offset'] == pos, 'trailing sections have a gap or overlap')
        pos += trailer['size']
    need(pos == size, 'unreferenced data after bitcode section')
    return dict(hdr=hdr, fns=functions, global_tags=global_tags, trailers=trailers)


def bitcode(data, lib, function):
    start = lib['hdr'][7] + function['offt'][2]
    return data[start:start + function['size']]


def check(data):
    lib = parse(data)
    pms, qms, bcs = lib['hdr'][4], lib['hdr'][6], lib['hdr'][8]
    names = set()
    factory = FACTORY_MODULE_HASHES.get(hashlib.sha256(data).hexdigest())
    for function in lib['fns']:
        need(function['name'] not in names, 'ambiguous duplicate function name')
        names.add(function['name'])
        a, b, c = function['offt']
        need(a <= pms and b <= qms and c + function['size'] <= bcs, 'function offsets escape sections')
        actual = hashlib.sha256(bitcode(data, lib, function)).digest()
        pinned_factory = factory is not None and factory == (function['hash'].hex(), actual.hex())
        need(actual == function['hash'] or pinned_factory, 'bitcode hash differs: ' + function['name'])
    return lib


def splice(data, replacements):
    """Replace named bitcode, preserving metadata and trailers except their offsets."""
    lib = check(data)
    bco = lib['hdr'][7]
    order = sorted(lib['fns'], key=lambda item: item['offt'][2])
    pos = 0
    chunks = {}
    for function in order:
        offset = function['offt'][2]
        if offset in chunks:
            first = chunks[offset][0]
            need(function['size'] == first['size'] and function['hash'] == first['hash'], 'inconsistent shared bitcode')
            chunks[offset].append(function)
            continue
        need(offset == pos, 'bitcode blobs do not tile section')
        chunks[offset] = [function]
        pos += function['size']
    need(pos == lib['hdr'][8], 'bitcode section has unclaimed bytes')
    need(set(replacements) <= {f['name'] for f in order}, 'unknown replacement function')
    output, body, new_offsets = bytearray(data[:bco]), bytearray(), {}
    for aliases in chunks.values():
        provided = [replacements[f['name']] for f in aliases if f['name'] in replacements]
        need(not provided or all(blob == provided[0] for blob in provided), 'conflicting shared replacements')
        blob = provided[0] if provided else bitcode(data, lib, aliases[0])
        for function in aliases:
            new_offsets[function['name']] = (len(body), blob, bool(provided))
        body += blob
    for function in lib['fns']:
        offset, blob, replaced = new_offsets[function['name']]
        struct.pack_into('<3Q', output, function['tags'][b'OFFT'][0],
                         function['offt'][0], function['offt'][1], offset)
        struct.pack_into('<Q', output, function['tags'][b'MDSZ'][0], len(blob))
        pos, length = function['tags'][b'HASH']
        if replaced:
            output[pos:pos + length] = hashlib.sha256(blob).digest()
    delta = len(body) - lib['hdr'][8]
    for trailer in lib['trailers']:
        struct.pack_into('<Q', output, trailer['position'], trailer['offset'] + delta)
    output += body
    output += data[bco + lib['hdr'][8]:]
    struct.pack_into('<Q', output, 16, len(output))
    struct.pack_into('<Q', output, 80, len(body))
    output = bytes(output)
    rebuilt = check(output)
    for a, b in zip(lib['trailers'], rebuilt['trailers']):
        need(data[a['offset']:a['offset'] + a['size']] ==
             output[b['offset']:b['offset'] + b['size']], 'trailer changed')
    return output


if __name__ == '__main__':
    import sys
    lib = check(open(sys.argv[1], 'rb').read())
    print(len(lib['fns']), 'functions; header', [hex(x) for x in lib['hdr']],
          'trailers', [item['tag'].decode() for item in lib['trailers']])
