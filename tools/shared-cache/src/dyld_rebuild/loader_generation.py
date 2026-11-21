"""Offline dyld1378 loader serialization with explicit, pinned inputs.

The compiled Apple serializers emit bytes. This module independently reads
those bytes and resolves program bindings; it never executes target code.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import struct
import subprocess
from .cache import CacheFile, CacheFamily
from .cache_exports import CacheDependency, FinalGraph, path_trie, export_trie
from .exports import Image, cstring, require, span, uleb
from .imports import Import, chained_imports, classic_imports, provider, sleb
from .pbl import VERSION, main_validation
from .provider_identity import x86_slice

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_NAMES = ('__OBJC_RO', '__OBJC_RW')
TREAT_AS_WEAK = (
    '__Znwm', '__ZnwmRKSt9nothrow_t', '__Znam', '__ZnamRKSt9nothrow_t',
    '__ZdlPv', '__ZdlPvRKSt9nothrow_t', '__ZdlPvm', '__ZdaPv', '__ZdaPvRKSt9nothrow_t', '__ZdaPvm',
    '__ZnwmSt11align_val_t', '__ZnwmSt11align_val_tRKSt9nothrow_t',
    '__ZnamSt11align_val_t', '__ZnamSt11align_val_tRKSt9nothrow_t',
    '__ZdlPvSt11align_val_t', '__ZdlPvSt11align_val_tRKSt9nothrow_t', '__ZdlPvmSt11align_val_t',
    '__ZdaPvSt11align_val_t', '__ZdaPvSt11align_val_tRKSt9nothrow_t', '__ZdaPvmSt11align_val_t',
    '__ZnwmSt19__type_descriptor_t', '__ZnamSt19__type_descriptor_t')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_file(path, record):
    path = Path(path)
    require(path.is_file() and path.stat().st_size == record['size'] and sha(path) == record['sha256'],
            f'missing or changed required input: {path}')
    return path


class LoaderFamily(CacheFamily):
    """Same bounded mapping reader, with explicitly checked variable family size."""
    def __init__(self, path, expected_files=None, expected_images=None):
        self.files = []
        try:
            self.main = CacheFile(path)
            self.files.append(self.main)
            data = self.main.data
            off, count = struct.unpack_from('<II', data, 392)
            require(1 <= count <= 63, 'unsupported loader family count')
            if expected_files is not None:
                require(count + 1 == expected_files, 'cache family count differs from manifest')
            for index in range(count):
                entry = span(data, off + 56 * index, 56)
                suffix, _ = cstring(entry, 24)
                require(suffix == f'.{index + 1:02d}', 'unexpected subcache suffix/order')
                file = CacheFile(str(path) + suffix)
                self.files.append(file)
                require(entry[:16] == file.data[88:104], 'subcache UUID mismatch')
                require(struct.unpack_from('<Q', entry, 16)[0] == file.mappings[0].address - self.main.mappings[0].address,
                        'subcache VM offset mismatch')
            ranges = sorted((m.address, m.address + m.size) for f in self.files for m in f.mappings)
            require(all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])), 'cache VM mappings overlap')
            off, count = struct.unpack_from('<II', data, 448)
            require(0 < count < 0x7fff, 'unsupported loader image count')
            if expected_images is not None:
                require(count == expected_images, 'cache image count differs from manifest')
            self.images = []
            for index in range(count):
                address, _, _, pathoff, pad = struct.unpack('<QQQII', span(data, off + index * 32, 32))
                name, _ = cstring(span(data, pathoff, min(4096, len(data) - pathoff)), 0)
                # 0 on 552-byte (26) headers; 27's 568-byte headers use the row's last word (1, or 2 on 26B131).
                require(name.startswith('/') and (not pad or self.main.mapping_offset == 568), 'invalid image identity')
                self.images.append((name, address))
            require(len({p for p, _ in self.images}) == count, 'duplicate cache image path')
        except BaseException:
            self.close()
            raise


def cache_manifest(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    if 'files' in data and 'main' in data:
        records = [(ROOT / r['path'], r) for r in data['files']]
        main = ROOT / data['main']
        expected_images = data.get('images', 3608)
    else:
        require(data.get('schema') == 1 and 'outputs' in data and 'original_files' in data, 'unknown cache pin manifest')
        records = [(path.parent / r['file'], r) for r in data['original_files'] + data['outputs']]
        main = path.parent / 'dyld_shared_cache_x86_64'
        expected_images = data['images']
    require(len({str(p) for p, _ in records}) == len(records), 'duplicate cache pin')
    for file, record in records:
        verify_file(file, record)
    expected_files = len([p for p, _ in records if p.name == main.name or p.name[len(main.name):].lstrip('.').isdigit()])
    require(expected_files > 1 and main in [p for p, _ in records], 'missing required cache pins')
    for index in range(expected_files):
        name = Path(str(main) + (f'.{index:02d}' if index else ''))
        require(name in [p for p, _ in records], f'missing required cache file: {name}')
    return main, expected_files, expected_images, [{'path': str(p), 'size': r['size'], 'sha256': r['sha256']} for p, r in records]


def load_root_manifest(path):
    data = json.loads(Path(path).read_text())
    require(data.get('schema') == 1 and data.get('coverage') == 'explicit-inputs', 'unknown explicit root manifest')
    files = {}
    for record in data['files']:
        virtual = record['virtual_path']
        require(virtual.startswith('/') and str(PurePosixPath(virtual)) == virtual and '..' not in PurePosixPath(virtual).parts,
                'noncanonical root path')
        require('\n' not in virtual and '\t' not in virtual and virtual not in files, 'duplicate/unsafe root path')
        physical = (ROOT / record['path']).resolve()
        require('\n' not in str(physical) and '\t' not in str(physical), 'unsafe physical root path')
        verify_file(physical, record)
        files[virtual] = {**record, 'physical_path': str(physical)}
    programs = data['programs']
    require(programs and len(set(programs)) == len(programs) and all(p in files for p in programs), 'required program input missing/duplicated')
    return files, programs


def required_programs(family):
    address, size = struct.unpack_from('<QI', family.main.data, 352)
    _, capacity = struct.unpack_from('<QQ', family.main.data, 336)
    entries = path_trie(family.read(address, size), capacity)
    paths = sorted(p for p in entries if not p.startswith('/cdhash/'))
    return entries, paths


def classic_targets(data, image, segments, stream_kind):
    """Decode target changes and VM locations with uint64 address arithmetic."""
    require(stream_kind in ('bind', 'lazy', 'weak'), 'unknown opcode stream')
    pos, ordinal, symbol, addend, weak = 0, None, None, 0, False
    token, last_token, rows, segment, delta, changed, kind, last_addend = 0, -1, [], None, None, False, 1, None
    if stream_kind == 'weak':
        ordinal = -3
    def emit():
        nonlocal last_token, delta, changed, last_addend
        require(ordinal is not None and symbol and segment is not None and delta is not None, 'incomplete opcode target state')
        require(0 <= segment < len(segments) and delta + 8 <= segments[segment][1], 'opcode bind outside mapped segment')
        require(kind == 1, 'nonpointer opcode target requires oracle')
        if changed or stream_kind == 'lazy' or (stream_kind == 'weak' and (token != last_token or addend != last_addend)):
            rows.append(Import(symbol, ordinal, provider(image, ordinal), weak,
                               stream_kind == 'lazy', addend, 1, stream_kind))
            last_token = token
            last_addend = addend
        changed = False
        delta = (delta + 8) & ((1 << 64) - 1)
    while pos < len(data):
        byte = data[pos]
        pos += 1
        op, imm = byte & 0xf0, byte & 15
        if op == 0:
            if stream_kind != 'lazy':
                require(not any(data[pos:]), 'nonzero opcode bytes after DONE')
                break
        elif op == 0x10:
            ordinal = imm
        elif op == 0x20:
            ordinal, pos = uleb(data, pos)
        elif op == 0x30:
            ordinal = imm - 16 if imm else 0
        elif op == 0x40:
            require(not imm & ~9 and (not imm & 8 or stream_kind == 'weak'), 'invalid opcode symbol flags')
            symbol, pos = cstring(data, pos)
            require(symbol, 'empty opcode symbol')
            weak = bool(imm & 1)
            token += 1
            changed = True
        elif op == 0x50:
            kind = imm
        elif op == 0x60:
            addend, pos = sleb(data, pos)
            changed = True
        elif op == 0x70:
            segment = imm
            require(segment < len(segments), 'opcode segment index outside image')
            delta, pos = uleb(data, pos)
        elif op == 0x80:
            value, pos = uleb(data, pos)
            require(delta is not None, 'opcode address advance lacks segment')
            delta = (delta + value) & ((1 << 64) - 1)
        elif op in (0x90, 0xb0):
            emit()
            if op == 0xb0:
                delta = (delta + imm * 8) & ((1 << 64) - 1)
        elif op == 0xa0:
            emit()
            value, pos = uleb(data, pos)
            delta = (delta + value) & ((1 << 64) - 1)
        elif op == 0xc0:
            count, pos = uleb(data, pos)
            skip, pos = uleb(data, pos)
            require(count <= 2_000_000, 'opcode bind repeat limit')
            for _ in range(count):
                emit()
                delta = (delta + skip) & ((1 << 64) - 1)
        else:
            require(False, f'unsupported opcode {op:#x}')
    # dyld stops at the stream extent as well as DONE. Old libgnsdk_submit
    # ends immediately after its final complete bind instruction.
    return rows


def disk_image(data, virtual_path, *, main=False):
    start, length, _ = x86_slice(data)
    data = span(data, start, length)
    magic, cpu, subtype, filetype = struct.unpack_from('<4I', span(data, 0, 32))
    require((magic, cpu, subtype) == (0xfeedfacf, 0x1000007, 3), 'unexpected disk architecture')
    require(filetype == 2 if main else filetype in (2, 6, 8), 'unexpected disk image type')
    count, size = struct.unpack_from('<II', data, 16)
    body = span(data, 32, size)
    pos, dependencies, segments, streams, chained, tries = 0, [], [], [], [], []
    for _ in range(count):
        command, width = struct.unpack('<II', span(body, pos, 8))
        require(width >= 8 and width % 8 == 0, 'invalid executable command')
        raw = span(body, pos, width)
        if command in (0xc, 0x80000018, 0x8000001f, 0x80000023, 0x20):
            nameoff, timestamp = struct.unpack_from('<II', raw, 8)
            flags = None
            if timestamp == 0x1a741800:
                flags, = struct.unpack('<I', span(raw, 24, 4))
                require(command in (0xc, 0x80000018) and nameoff == 28 and not flags & ~15, 'invalid disk dylib-use command')
            else:
                require(nameoff >= 24, 'disk dependency name overlaps header')
            name, _ = cstring(raw, nameoff)
            dependencies.append(CacheDependency(name, command, flags))
        elif command == 0x19:
            segments.append(struct.unpack_from('<4Q', raw, 24))
        elif command == 0x80000034:
            chained.append(span(data, *struct.unpack_from('<II', raw, 8)))
        elif command == 0x80000033:
            tries.append(struct.unpack_from('<II', raw, 8))
        elif command in (0x22, 0x80000022):
            export = struct.unpack_from('<II', raw, 40)
            if export[1]:
                tries.append(export)
            for at, kind in ((16, 'bind'), (24, 'weak'), (32, 'lazy')):
                offset, extent = struct.unpack_from('<II', raw, at)
                if extent:
                    streams.append((kind, span(data, offset, extent)))
        pos += width
    require(pos == len(body) and len(chained) <= 1 and not (chained and streams), 'conflicting disk import tables')
    require(len(set(tries)) <= 1, 'conflicting disk export tries')
    image = Image(virtual_path, dependencies, export_trie(span(data, *tries[0])) if tries else {})
    imports = chained_imports(chained[0], image) if chained else [row for kind, stream in streams if kind != 'weak' for row in classic_targets(stream, image, segments, kind)]
    if chained and filetype == 2 and struct.unpack_from('<I', data, 24)[0] & 0x8000:
        imports += [Import(symbol, -3, 'SPECIAL:weak-lookup', False, False, 0, 1, 'treat-as-weak') for symbol in TREAT_AS_WEAK]
    overrides = [row for kind, stream in streams if kind == 'weak' for row in classic_targets(stream, image, segments, kind)]
    return image, imports, overrides


def executable(data, virtual_path):
    image, imports, _ = disk_image(data, virtual_path, main=True)
    return image, imports


def bind_target(word, cached_count, app_count):
    kind = word >> 62
    require(kind in (0, 1, 2), 'unknown bind target kind')
    if kind == 1:
        value = (word >> 8) & ((1 << 54) - 1)
        if value & (1 << 53):
            value |= 0xc0000000000000
        value |= (word & 255) << 56
        return {'kind': 'absolute', 'value': value}
    ref = word & 65535
    app, index = bool(ref & 0x8000), ref & 0x7fff
    require(index < (app_count if app else cached_count), 'bind loader reference outside set')
    if kind == 2:
        return {'kind': 'function_variant', 'app': app, 'index': index, 'variant': (word >> 16) & 1023, 'table_offset': (word >> 26) & ((1 << 36) - 1)}
    offset = (word >> 24) & ((1 << 38) - 1)
    if offset & (1 << 37):
        offset |= 0x00ffffc000000000
    offset |= ((word >> 16) & 255) << 56
    return {'kind': 'image_offset', 'app': app, 'index': index, 'offset': offset}


def read_set(data, cached_count, *, app):
    magic, version, length, count, array = struct.unpack('<5I', span(data, 0, 20))
    require((magic, version) == (0x73703464, VERSION) and length == len(data), 'unsupported/truncated PBL set')
    require(0 < count < 0x7fff and (app or count == cached_count), 'invalid loader set count')
    require(array == 80, 'unexpected loader array/header size')
    offsets = struct.unpack(f'<{count}I', span(data, array, 4 * count))
    require(all(a < b for a, b in zip(offsets, offsets[1:])) and offsets[0] >= array + count * 4, 'overlapping/unordered loaders')
    for at in (24, 28, 36, 40, 44, 48, 64, 68, 72):
        value, = struct.unpack_from('<I', data, at)
        require(not value or 80 <= value < length, 'PBL set metadata offset outside set')
    rows = []
    for index, offset in enumerate(offsets):
        boundaries = [len(data)] + [value for value in offsets if value > offset]
        boundaries += [struct.unpack_from('<I', data, at)[0] for at in (24, 28, 36, 40, 44, 48, 64, 68, 72) if struct.unpack_from('<I', data, at)[0] > offset]
        loader = span(data, offset, min(boundaries) - offset)
        magic, flags, ref = struct.unpack_from('<IHH', span(loader, 0, 448))
        require(magic == 0x6c347964 and flags & 1 and bool(flags & 2) != app and ref == index | (0x8000 if app else 0), 'loader identity/ref mismatch')
        pathoff, depsoff, kindoff, fixupoff, altoff, fvioff, bits, regionsoff, depcount, bindoff, bindcount = struct.unpack_from('<10HI', loader, 32)
        require(pathoff >= 448, 'loader path overlaps fixed header')
        for start, extent in ((depsoff, depcount * 2), (kindoff, depcount if kindoff else 0),
                              (fvioff, 56 if fvioff else 0), (regionsoff, (bits >> 5) * 16), (bindoff, bindcount * 8)):
            require(not extent or start >= 448, 'loader array overlaps fixed header')
            span(loader, start, extent)
        path, _ = cstring(loader, pathoff)
        require(path.startswith('/'), 'nonabsolute loader path')
        deps = struct.unpack(f'<{depcount}H', span(loader, depsoff, depcount * 2))
        for dep in deps:
            require(dep == 0x7fff or dep & 0x7fff < (count if dep & 0x8000 else cached_count), 'dependent loader ref out of range')
        kinds = list(span(loader, kindoff, depcount)) if kindoff else [0] * depcount
        require(all(value <= 15 for value in kinds), 'unknown dependency attributes')
        region_count = bits >> 5          # 27: 11 bits after five flags (bit 4: TLVs pre-keyed)
        regions = []
        for number in range(region_count):
            word, fileoff, filesize = struct.unpack('<QII', span(loader, regionsoff + number * 16, 16))
            regions.append({'vm_offset': word & ((1 << 59) - 1), 'protection': (word >> 59) & 7,
                            'zero_fill': bool(word & (1 << 62)), 'read_only_data': bool(word & (1 << 63)), 'file_offset': fileoff, 'file_size': filesize})
        binds = [bind_target(struct.unpack_from('<Q', span(loader, bindoff, bindcount * 8), number * 8)[0], cached_count, count) for number in range(bindcount)]
        overrideoff, overridecount = struct.unpack_from('<II', loader, 92)
        span(loader, overrideoff, overridecount * 8)
        overrides = [bind_target(struct.unpack_from('<Q', loader, overrideoff + number * 8)[0], cached_count, count) for number in range(overridecount)]
        row = {'path': path, 'index': index, 'offset': offset, 'flags': flags, 'uuid': loader[8:24].hex(), 'cpu_subtype': struct.unpack_from('<I', loader, 24)[0],
               'properties': bits & 31, 'dependencies': list(deps), 'dependency_attributes': kinds, 'regions': regions, 'binds': binds,
               'exports_offset': struct.unpack_from('<Q', loader, 64)[0], 'exports_size': struct.unpack_from('<I', loader, 72)[0],
               'objc_info_offset': struct.unpack_from('<I', loader, 56)[0], 'twin_index': struct.unpack_from('<H', loader, 60)[0],
               'file_validation_offset': fvioff, 'override_bind_count': overridecount, 'override_binds': overrides,
               'section_locations': loader[104:448].hex()}
        if fvioff:
            fvi = span(loader, fvioff, 56)
            require(fvi[52] in (0, 1) and fvi[53] in (0, 1), 'invalid file validation flags')
            row['validation'] = {'slice_offset': struct.unpack_from('<Q', fvi)[0], 'cdhash': fvi[32:52].hex(), 'check_inode_mtime': bool(fvi[52]), 'check_cdhash': bool(fvi[53])}
        rows.append(row)
    missing_count, missing_offset = struct.unpack_from('<II', data, 32)
    require(not missing_count or 80 <= missing_offset < length, 'missing-path array outside set')
    missing_paths = []
    for _ in range(missing_count):
        path, missing_offset = cstring(data, missing_offset)
        # Genuine MissingPaths serialization preserves repeated search attempts.
        # The reader consumes packed strings without a uniqueness requirement.
        require(path.startswith('/'), 'nonabsolute missing path')
        missing_paths.append(path)
    return {'length': length, 'version': hex(version), 'loaders': rows,
            'objc_maps': [struct.unpack_from('<I', data, at)[0] for at in (40, 44, 48)],
            'swift_maps': [struct.unpack_from('<I', data, at)[0] for at in (64, 68, 72)],
            'must_be_missing_count': missing_count, 'must_be_missing_paths': missing_paths}


def validate_program(data, family, executable_bytes, virtual_path):
    parsed = read_set(data, len(family.images), app=True)
    validation = main_validation(data)
    require(validation['path'] == virtual_path, 'main executable path differs')
    image, imports = executable(executable_bytes, virtual_path)
    main = parsed['loaders'][0]
    require(len(main['binds']) == len(imports), 'generated bind count differs from import table')
    graph = FinalGraph(family, {})
    comparisons = []
    for index, (row, binding) in enumerate(zip(imports, main['binds'])):
        require(row.ordinal > 0, 'process-special import requires independent lookup oracle')
        resolution = graph.resolve(row.provider, row.symbol)
        if resolution['status'] != 'resolved':
            require(row.weak and resolution['status'] == 'missing_symbol' and binding == {'kind': 'absolute', 'value': 0}, 'unresolved non-nullable program bind')
        elif resolution['kind'] == 'absolute':
            require(binding['kind'] == 'absolute' and binding['value'] == int(resolution['input_offset'], 16) + row.addend, 'absolute program target differs')
        else:
            require(not binding.get('app', True), 'unexpected app binding in cache-only control')
            target_path, _ = family.images[binding['index']]
            target_offset = int(resolution['input_offset'], 16) + row.addend
            require(target_path == resolution['provider'], 'program bind provider differs from independent resolver')
            if binding['kind'] == 'function_variant':
                from .cache import commands
                target_address = family.images[binding['index']][1]
                rows = commands(family, target_address)
                tables = [struct.unpack_from('<II', raw, 8) for _, command, raw in rows if command == 0x37]
                linkedit = [struct.unpack_from('<4Q', raw, 24) for _, command, raw in rows if command == 0x19 and raw[8:24].rstrip(b'\0') == b'__LINKEDIT']
                require(resolution['flags'] & 0x20 and not row.addend and len(tables) == len(linkedit) == 1,
                        'unsupported function variant bind shape')
                table_offset, table_size = tables[0]
                vm, vm_size, file_offset, file_size = linkedit[0]
                require(file_offset <= table_offset and table_offset + table_size <= file_offset + file_size,
                        'function variant table outside LINKEDIT')
                require(binding['table_offset'] == vm + table_offset - file_offset - target_address and
                        binding['variant'] == int(resolution['extra_offset'], 16), 'function variant table/index differs')
            else:
                require(binding['offset'] == target_offset, 'program bind offset differs from independent resolver')
        comparisons.append({'index': index, 'symbol': row.symbol, 'requested_provider': row.provider, 'binding': binding, 'resolution': resolution})
    require(parsed['must_be_missing_count'] == 0, 'unprovided filesystem absence assumptions require complete root evidence')
    return {'validation': validation, 'set': parsed, 'bind_comparisons': comparisons}


def prepare_adapter(source):
    needle = '    hdr->forEachSegment(^(const UnsafeHeader::SegmentInfo& segInfo, bool& stop) {\n        Region region;\n'
    replacement = '''    hdr->forEachSegment(^(const UnsafeHeader::SegmentInfo& segInfo, bool& stop) {
        // Global optimization segments appended to final libobjc after its
        // factory PBL serialization are not per-image ownership regions.
        if (hdr->isDylib() && strcmp(hdr->installName(), "/usr/lib/libobjc.A.dylib") == 0 &&
            (segInfo.segmentName == "__OBJC_RO" || segInfo.segmentName == "__OBJC_RW"))
            return;
        Region region;
'''
    require(source.count(needle) == 1, 'unexpected withRegions source; adapter refused')
    return source.replace(needle, replacement)


def prepare_cache_path_adapter(source):
    """Use real cache path identity in the host builder, as runtime dyld does."""
    prefix = '#include <stdint.h>\nextern bool dyldOfflineCacheIndexOfPath(const char*, uint32_t&);\nextern const char* dyldOfflineCachePath(uint32_t);\n'
    signatures = [
        ('bool ProcessConfig::DyldCache::indexOfPath(const char* dylibPath, uint32_t& dylibIndex) const',
         '    return ::dyldOfflineCacheIndexOfPath(dylibPath, dylibIndex);\n'),
        ('const char* ProcessConfig::DyldCache::getIndexedImagePath(uint32_t dylibIndex) const',
         '    return ::dyldOfflineCachePath(dylibIndex);\n')]
    for signature, replacement in signatures:
        start=signature+'\n{\n#if BUILDING_CACHE_BUILDER || BUILDING_CACHE_BUILDER_UNIT_TESTS\n'
        require(source.count(start)==1,'cache path adapter source signature differs')
        begin=source.index(start)+len(start);end=source.index('#else',begin)
        original=source[begin:end]
        require('installName()' in original and 'cacheBuilderDylibs' in original,
                'cache path adapter source body differs')
        source=source[:begin]+replacement+source[end:]
    return prefix+source


def build_helper(directory):
    from .host_tools import loader
    binary = loader(directory)
    report = {'classification': 'host serializer', 'generator_sha256': sha(binary)}
    (Path(directory) / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    return binary, report


def check_target_schema(path):
    """Read target1387's own symbol and decode its header acceptance comparisons."""
    from .x86 import Disassembler
    data = Path(path).read_bytes()
    start, length, _ = x86_slice(data)
    data = span(data, start, length)
    require(struct.unpack_from('<I', data, 12)[0] == 7, 'expected target MH_DYLINKER')
    count, extent = struct.unpack_from('<II', data, 16)
    commands = span(data, 32, extent)
    pos, segments, tables = 0, [], []
    for _ in range(count):
        command, size = struct.unpack('<II', span(commands, pos, 8))
        require(size >= 8 and size % 8 == 0, 'invalid target load command')
        raw = span(commands, pos, size)
        if command == 0x19:
            segments.append(struct.unpack_from('<4Q', raw, 24))
        elif command == 2:
            tables.append(struct.unpack_from('<4I', raw, 8))
        pos += size
    require(pos == extent and len(tables) == 1, 'target symbol table ambiguity')
    symoff, count, stroff, strsize = tables[0]
    strings = span(data, stroff, strsize)
    matches = []
    for index in range(count):
        nameoff, kind, section, desc, value = struct.unpack('<IBBHQ', span(data, symoff + index * 16, 16))
        if nameoff < strsize:
            name, _ = cstring(strings, nameoff)
            if name == '__ZNK5dyld417PrebuiltLoaderSet11validHeaderERNS_12RuntimeStateE':
                matches.append(value)
    require(len(matches) == 1, 'target validHeader symbol missing/ambiguous')
    address = matches[0]
    locations = [off + address - vm for vm, vs, off, size in segments if vm <= address and address + 0x72 <= vm + size]
    require(len(locations) == 1, 'target schema function not file-backed')
    code = span(data, locations[0], 0x72)
    decoder = Disassembler()
    try:
        instructions, failure = decoder.interval(code, address)
    finally:
        decoder.close()
    require(failure is None and instructions[-1].data == b'\xc3', 'target schema function shape changed')
    require(any(i.data == bytes.fromhex('813f64347073') for i in instructions) and
            any(i.data == bytes.fromhex('81fa01c4ff1b') for i in instructions), 'target rejects expected loader magic/version')
    return {'classification': 'target1387 validHeader compares the serialized magic and version. Full runtime acceptance not checked here.',
            'path': str(path), 'sha256': sha(path), 'symbol': hex(address), 'code_sha256': hashlib.sha256(code).hexdigest(),
            'instructions': [{'address': hex(i.address), 'bytes': i.data.hex(), 'text': i.text.strip()} for i in instructions]}


def control_source_paths(contract_path=None):
    return [ROOT / ('src/dyld_rebuild/' + name + '.py') for name in
            ('loader_generation', 'cache', 'cache_exports', 'exports', 'imports', 'provider_identity', 'pbl', 'patches', 'x86')] + [
            ROOT / 'tools/loader-generator/generator.cpp', ROOT / 'tools/loader-generator/build-inputs.json',
            Path(contract_path) if contract_path is not None else ROOT / 'tools/loader-generator/control-contract.json']


def validate_control_report(control, contract, *, contract_path=None):
    require(control['cached_set']['original_cached_bytes_equal'] and
            control['cached_set']['sha256'] == contract['original_cached_set_sha256'] and
            control['cached_set']['loader_count'] == (contract['original_image_count'] if contract_path is not None else 3608) and
            control['cached_set']['size'] == (contract['original_cached_set_size'] if contract_path is not None else 2794512),
            'original-cache control did not match factory bytes')
    if contract_path is not None:
        require(control.get('mode') == 'program-control' and len(control.get('programs', [])) == 1,
                'current factory control lacks independently validated executable')
        require(contract.get('schema') == 2 and control['cache_inputs'] == contract['original_cache_inputs'],
                'factory control did not use exact current pristine inputs')
        require(control.get('control_contract') == {'path': str(Path(contract_path).resolve()), 'sha256': sha(contract_path)},
                'factory control contract provenance differs')
    expected = {str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
                for path in control_source_paths(contract_path)}
    require(set(control.get('source_hashes', {})) == expected, 'control source-key inventory is incomplete or unknown')
    for name, digest in control['source_hashes'].items():
        require(sha(ROOT / name) == digest, 'control report was generated with different source')


def run_generation(cache_pins, root_pins, output, *, mode, target_dyld, control_report=None,
                   control_contract=None, control_contract_sha256=None, workspace_root=None):
    from .patches import PatchTableV4
    output = Path(output).resolve()
    workspace = (ROOT/'work') if workspace_root is None else Path(workspace_root).resolve()
    require(workspace != Path('/') and output != workspace and output.is_relative_to(workspace) and not output.exists(),
            'output must be a fresh directory under the explicit workspace')
    require(mode in ('cached', 'program-control', 'required-programs'), 'unknown generation mode')
    files, programs = load_root_manifest(root_pins)
    main_path, file_count, image_count, cache_inputs = cache_manifest(cache_pins)
    require((control_contract is None) == (control_contract_sha256 is None), 'explicit control requires independent contract hash')
    contract_path = ROOT/'tools/loader-generator/control-contract.json' if control_contract is None else Path(control_contract).resolve()
    if control_contract is not None:
        require(sha(contract_path) == control_contract_sha256, 'explicit control contract hash differs')
    contract = json.loads(contract_path.read_text())
    if control_contract is not None:
        require(contract.get('schema') == 2 and all(type(contract.get(k)) is int and contract[k]>0
                for k in ('original_image_count','original_file_count','original_cached_set_size')),
                'unknown/incomplete explicit control contract')
        require(sha(contract['source_manifest']['path']) == contract['source_manifest']['sha256'],
                'explicit control source manifest changed')
        verify_file(contract['deriver']['path'], contract['deriver'])
        original_source = cache_inputs == contract['original_cache_inputs']
    else:
        original_source = (image_count, file_count) == (3608, 7)
    verify_file(target_dyld, contract['target_dyld'])
    target_contract = check_target_schema(target_dyld)
    family = LoaderFamily(main_path, file_count, image_count)
    try:
        # This refuses the current intermediate final family until its metadata is
        # coherent; image names are never matched to stale array positions.
        graph = FinalGraph(family, {})
        from .cache import commands
        for image_path, image_address in family.images:
            rows = commands(family, image_address)
            segments = [struct.unpack_from('<4Q', raw, 24) for _, command, raw in rows if command == 0x19]
            require(segments, f'missing image segments: {image_path}')
            for address, vm_size, offset, file_size in segments:
                require(file_size <= vm_size, f'file-backed bytes exceed VM extent: {image_path}')
                if file_size:
                    family.locate(address, file_size)
        objc_address = next(address for path, address in family.images if path == '/usr/lib/libobjc.A.dylib')
        synthetic = []
        for _, command, raw in commands(family, objc_address):
            if command == 0x19 and raw[8:24].rstrip(b'\0').decode('ascii') in SYNTHETIC_NAMES:
                address, size = struct.unpack_from('<QQ', raw, 24)
                maximum, initial = struct.unpack_from('<II', raw, 56)
                synthetic.append({'name': raw[8:24].rstrip(b'\0').decode('ascii'), 'address': address, 'size': size, 'maximum': maximum, 'initial': initial})
        require(synthetic == contract['libobjc_synthetic_segments'], 'uncertified synthetic libobjc regions')
        if not original_source:
            require(control_report is not None, 'final family requires a matching original-cache control report')
            control = json.loads(Path(control_report).read_text())
            validate_control_report(control, contract, contract_path=contract_path if control_contract is not None else None)
        patch_address, patch_size = struct.unpack_from('<QQ', family.main.data, 152)
        patches = PatchTableV4(family.read(patch_address, patch_size), patch_address, family.images, family.main.mappings[0].address)
        _, needed = required_programs(family)
        missing = [p for p in needed if p not in files]
        if mode == 'required-programs':
            require(not missing, f'missing {len(missing)} required program inputs; no sets dropped: {missing[:10]}')
            require(set(programs) == set(needed), 'program manifest does not exactly cover required paths')
        if mode == 'program-control':
            require(len(programs) == 1, 'program-control requires exactly one executable')
        # Pure-Python structural checks before the host helper sees input bytes.
        for program in programs:
            executable(Path(files[program]['physical_path']).read_bytes(), program)
        output.mkdir()
        helper, build_report = build_helper(output / 'helper')
        mapping = output / 'root.tsv'
        mapping.write_text(''.join(f"{virtual}\t{record['physical_path']}\n" for virtual, record in sorted(files.items())))
        bootstrap = programs[0]
        command_base = [str(helper), str(main_path), files[bootstrap]['physical_path'], bootstrap]
        cached_output = output / 'cached-dylibs.pbl'
        cached_command = [*command_base, str(cached_output), 'cached', str(image_count), str(file_count), str(mapping), '-']
        process = subprocess.run(cached_command, capture_output=True, text=True)
        (output / 'cached-generation.txt').write_text(process.stdout + process.stderr)
        require(process.returncode == 0, 'cached serializer refused; see cached-generation.txt')
        generated_cached = cached_output.read_bytes()
        parsed_cached = read_set(generated_cached, image_count, app=False)
        require([r['path'] for r in parsed_cached['loaders']] == [p for p, _ in family.images], 'cached loader/image order differs')
        original_address, = struct.unpack_from('<Q', family.main.data, 328)
        original_header = family.read(original_address, 80)
        original_size, = struct.unpack_from('<I', original_header, 8)
        original_bytes = family.read(original_address, original_size)
        original_same = original_bytes == generated_cached
        if original_source:
            require(original_same, 'original cached PBL control differs; adapter not certified')
        generated = []
        for number, program in enumerate(programs if mode != 'cached' else []):
            data = Path(files[program]['physical_path']).read_bytes()
            path = output / f'program-{number:04d}.pbl'
            command = [str(helper), str(main_path), files[program]['physical_path'], program, str(path), 'program',
                       str(image_count), str(file_count), str(mapping), str(cached_output)]
            process = subprocess.run(command, capture_output=True, text=True)
            (output / f'program-{number:04d}.txt').write_text(process.stdout + process.stderr)
            require(process.returncode == 0, f'program serializer refused {program}')
            check = validate_program(path.read_bytes(), family, data, program)
            display = subprocess.run(['/usr/bin/codesign', '-dvvv', '--arch', 'x86_64', files[program]['physical_path']], capture_output=True, text=True)
            import re
            match = re.search(r'^CDHash=([0-9a-f]{40})$', display.stderr, re.M)
            require(display.returncode == 0 and match and match[1] == check['validation']['cdhash'], 'generated executable CDHash differs from Apple codesign')
            (output / f'program-{number:04d}-codesign.txt').write_text(display.stdout + display.stderr)
            (output / f'program-{number:04d}-validation.json').write_text(json.dumps(check, indent=2) + '\n')
            generated.append({'path': program, 'file': path.name, 'size': path.stat().st_size, 'sha256': sha(path),
                              'cdhash': check['validation']['cdhash'], 'bind_count': len(check['bind_comparisons']),
                              'objc_maps': check['set']['objc_maps'], 'swift_maps': check['set']['swift_maps'], 'command': command})
        report = {'schema': 1, 'classification': 'actual Apple serializer output with independent structural and bind checks; target acceptance not checked here.',
                  'mode': mode, 'cache_inputs': cache_inputs, 'root_inputs': files, 'required_program_path_count': len(needed),
                  'unprovided_required_programs': missing, 'complete_candidate': False,
                  'cached_set': {'file': cached_output.name, 'size': len(generated_cached), 'sha256': sha(cached_output),
                                 'loader_count': image_count, 'alignment': 8, 'loader_offsets': [{'index': row['index'], 'path': row['path'], 'offset': row['offset']} for row in parsed_cached['loaders']], 'original_cached_bytes_equal': original_same, 'command': cached_command},
                  'programs': generated, 'target_schema': target_contract, 'helper_build': build_report,
                  'source_hashes': {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p): sha(p)
                                    for p in control_source_paths(contract_path if control_contract is not None else None)},
                  'open': ['Full final filesystem inventory and all required program sets remain required.',
                           'ObjC/Swift serialized maps are emitted, but a changed-input table oracle and target runtime acceptance remain OPEN.',
                           'No target code was executed, cache installed, shared region operation performed, or security setting changed.']}
        if control_contract is not None:
            report['control_contract'] = {'path': str(contract_path), 'sha256': sha(contract_path)}
        (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        return report
    finally:
        family.close()
