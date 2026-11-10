"""Bind unchanged repairs to a new KC only when every fileset member is identical.

The enclosing KC's UUID and prelink source paths vary with kmutil's build directory.
They are not addresses used by these repairs. Fileset headers, segment maps and
segment bytes include every kernel/kext target and every RIP-relative global.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import plistlib
import re
import shutil
import struct
import subprocess

REVISION = 39


def sha(data):
    return hashlib.sha256(data).hexdigest()


def commands(data, offset=0):
    if data[offset:offset+4] != b'\xcf\xfa\xed\xfe':
        raise ValueError('Expected a little-endian 64-bit Mach-O')
    count, size = struct.unpack_from('<II', data, offset+16)
    position, end = offset+32, offset+32+size
    if end > len(data):
        raise ValueError('Truncated Mach-O commands')
    for _ in range(count):
        command, length = struct.unpack_from('<II', data, position)
        if length < 8 or position+length > end:
            raise ValueError('Invalid Mach-O command size')
        yield command, position, length
        position += length
    if position != end:
        raise ValueError('Mach-O command count/size mismatch')


def members(data):
    result = {}
    for command, position, length in commands(data):
        if command != 0x80000035:  # LC_FILESET_ENTRY
            continue
        address, offset, name_offset = struct.unpack_from('<QQI', data, position+8)
        name = data[position+name_offset:position+length].split(b'\0')[0].decode()
        if not name or name in result:
            raise ValueError('Missing or duplicate fileset identifier')
        segments, uuid = [], None
        for kind, p, n in commands(data, offset):
            if kind == 0x1b:
                uuid = data[p+8:p+24].hex()
            if kind == 0x19:
                label, va, size, start, count = struct.unpack_from('<16sQQQQ', data, p+8)
                if start+count > len(data):
                    raise ValueError('Fileset segment exceeds collection')
                segments.append(dict(name=label.rstrip(b'\0').decode(), address=va,
                                     size=size, offset=start, length=count,
                                     command_sha256=sha(data[p:p+n]),
                                     sha256=sha(data[start:start+count])))
        header_length = 32 + struct.unpack_from('<I', data, offset+20)[0]
        result[name] = dict(address=address, offset=offset, uuid=uuid,
                            header_sha256=sha(data[offset:offset+header_length]), segments=segments)
    if 'com.apple.kernel' not in result:
        raise ValueError('Collection has no kernel fileset member')
    return result


def enclosing_identity(data):
    normalized = bytearray(data)
    for command, position, length in commands(data):
        if command == 0x1b:
            normalized[position+8:position+24] = bytes(16)
    segments = []
    for command, position, length in commands(data):
        if command == 0x19:
            label, address, size, offset, count = struct.unpack_from('<16sQQQQ', data, position+8)
            name = label.rstrip(b'\0').decode()
            if offset+count > len(data):
                raise ValueError('Enclosing segment exceeds collection')
            segments.append(dict(name=name, address=address, size=size, offset=offset, length=count,
                                 command_sha256=sha(data[position:position+length]),
                                 sha256=None if name == '__PRELINK_INFO' else sha(normalized[offset:offset+count])))
    header_length = 32 + struct.unpack_from('<I', data, 20)[0]
    return dict(header_sha256=sha(normalized[:header_length]), segments=segments)


def outer_uuid(data):
    uuids = [data[p+8:p+24] for command, p, size in commands(data) if command == 0x1b]
    if len(uuids) != 1:
        raise ValueError('Collection has no unique enclosing UUID')
    return uuids[0]


def prelink(data):
    regions = []
    for command, p, size in commands(data):
        if command != 0x19 or data[p+8:p+24].rstrip(b'\0') != b'__PRELINK_INFO':
            continue
        for i in range(struct.unpack_from('<I', data, p+64)[0]):
            q = p+72+i*80
            label, segment, address, length, offset = struct.unpack_from('<16s16sQQI', data, q)
            if label.rstrip(b'\0') == b'__info':
                regions.append(data[offset:offset+length])
    if len(regions) != 1:
        raise ValueError('Collection has no unique prelink info plist')
    # kmutil emits XML with section padding; binary plists use their exact section extent.
    blob = regions[0]
    return plistlib.loads(blob if blob.startswith(b'bplist') else blob.rstrip(b'\0'))


def normalized_prelink(data, expected_paths=None):
    info = prelink(data)
    if info['_PrelinkKCID'] != outer_uuid(data):
        raise ValueError('Prelink KC identity differs from enclosing UUID')
    info['_PrelinkKCID'] = bytes(16)
    paths = []
    for index, entry in enumerate(info['_PrelinkInfoDictionary']):
        path = entry.get('_CodelessKextBundlePath')
        if path is None:
            continue
        marker = '/System/Library/Extensions/'
        if not path.startswith('/') or marker not in path or '..' in Path(path).parts:
            raise ValueError('Unrecognized codeless bundle path')
        prefix, suffix = path.split(marker, 1)
        normalized = marker + suffix
        if entry['kOSBundleDextUniqueIdentifier'] != hashlib.sha256(path.encode('utf-8')).digest():
            raise ValueError('Codeless bundle ID is not the hash of its path')
        paths.append(dict(index=index, identifier=entry['CFBundleIdentifier'], path=normalized))
        entry['_CodelessKextBundlePath'] = normalized
        entry['kOSBundleDextUniqueIdentifier'] = hashlib.sha256(normalized.encode('utf-8')).digest()
    if expected_paths is not None and paths != expected_paths:
        raise ValueError('Codeless bundle path set differs from profile')
    return info, paths


def metadata_differences(reference, target, path=''):
    result = []
    if isinstance(reference, dict) and isinstance(target, dict):
        for key in sorted(set(reference) | set(target)):
            if key not in reference or key not in target:
                result.append(dict(path=path+'/'+key, reference=repr(reference.get(key)), target=repr(target.get(key))))
            else:
                result.extend(metadata_differences(reference[key], target[key], path+'/'+key))
    elif isinstance(reference, list) and isinstance(target, list) and len(reference) == len(target):
        for i, (a, b) in enumerate(zip(reference, target)):
            result.extend(metadata_differences(a, b, path+'/'+str(i)))
    elif reference != target:
        result.append(dict(path=path, reference=repr(reference), target=repr(target)))
    return result


def address_binding(address, identity):
    # The enclosing fileset regions can overlap; prefer the kernel for its own globals.
    for name, member in identity.items():
        for segment in member['segments']:
            if segment['address'] <= address < segment['address'] + segment['size']:
                return dict(member=name, segment=segment['name'], address=hex(address),
                            offset=segment['offset'] + address-segment['address'])
    raise ValueError(f'Target address {address:#x} is outside all fileset segments')


def payload_files(artifact):
    paths = [artifact/'build.json', artifact/'EFI/OC/config.plist']
    paths += sorted((artifact/'EFI/OC/Kexts/IntelCommpage.kext').rglob('*'))
    return {str(p.relative_to(artifact)): sha(p.read_bytes()) for p in paths if p.is_file()}


def patch_sites(data, artifact):
    record = json.loads((artifact/'build.json').read_text())
    repair = record['inputs']['early_vm_repair']
    patches = plistlib.loads((artifact/'EFI/OC/config.plist').read_bytes())['Kernel']['Patch']
    sites = []
    for replacement in repair['replacements']:
        matches = [p for p in patches if p['Comment'] == replacement['comment']
                   and p['Find'].hex() == replacement['find']
                   and p['Replace'].hex() == replacement['replace']]
        if len(matches) != 1:
            raise ValueError('Artifact has no unique recorded kernel repair')
        patch = matches[0]
        before = bytes.fromhex(replacement['find'])
        if not before or data.count(before) != 1:
            raise ValueError(f'Kernel repair Find is not unique: {patch["Comment"]}')
        sites.append(dict(comment=patch['Comment'], identifier=patch['Identifier'],
                          offset=data.index(before), length=len(before), sha256=sha(before),
                          replacement_sha256=sha(patch['Replace'])))
    return sites


def source_identity():
    source = Path(__file__).resolve()
    git = subprocess.run(['git', '-C', str(source.parent), 'rev-parse', 'HEAD'],
                         capture_output=True, text=True)
    status = subprocess.run(['git', '-C', str(source.parent), 'status', '--porcelain'],
                            capture_output=True, text=True)
    return dict(unit='tools/kernel-fixes', revision=REVISION,
                dirty=bool(status.stdout.strip()) if status.returncode == 0 else None,
                commit=git.stdout.strip() if git.returncode == 0 else None,
                source_sha256=sha(source.read_bytes()))


def generate_profile(artifact, collection, abi_header, metadata_collection=None):
    data = collection.read_bytes()
    repair = json.loads((artifact/'build.json').read_text())['inputs']['early_vm_repair']
    if sha(data) != repair['capture']['collection_sha256']:
        raise ValueError('Reference collection differs from the recorded capture')
    identity = members(data)
    metadata_data = (metadata_collection or collection).read_bytes()
    if members(metadata_data) != identity or enclosing_identity(metadata_data) != enclosing_identity(data):
        raise ValueError('Metadata baseline changes compiled collection bindings')
    metadata, paths = normalized_prelink(metadata_data)
    if identity['com.apple.kernel']['uuid'] != repair['capture']['kernel_uuid']:
        raise ValueError('Captured kernel UUID differs from reference member')
    header = abi_header.read_text()
    addresses = sorted(set(int(v, 16) for v in re.findall(r'0xffffff[0-9a-fA-F]+', header)))
    bindings = [address_binding(a, identity) for a in addresses]
    if not bindings:
        raise ValueError('Compiled ABI header has no target addresses')
    capture = repair['capture']
    globals_ = dict(capture['addresses'])
    globals_.update(capture.get('pv_aliases', {}).get('addresses', {}))
    capture_bindings = {k: address_binding(int(v, 0), identity) for k, v in globals_.items()}
    contexts = []
    for name, context in [('initializer', dict(address=capture['site_address'], offset=capture['site_offset'], original=capture['original'])),
                          ('range', capture.get('range_call')),
                          ('cave', capture.get('pv_aliases', {}).get('cave')),
                          ('hook', capture.get('pv_aliases', {}).get('hook'))]:
        if context is None:
            continue
        original = bytes.fromhex(context['original'])
        offset = int(context['offset'], 0)
        binding = address_binding(int(context['address'], 0), identity)
        if offset != binding['offset'] or data[offset:offset+len(original)] != original:
            raise ValueError(f'Captured {name} context differs from reference member')
        contexts.append(dict(name=name, **binding, length=len(original), sha256=sha(original)))
    return dict(schema=1, producer=source_identity(), reference_collection_sha256=sha(data),
                reference_capture_sha256=repair['capture_sha256'],
                reference_capture=repair['capture'], members=identity, enclosing=enclosing_identity(data),
                metadata_collection_sha256=sha(metadata_data),
                prelink_sha256=sha(plistlib.dumps(metadata, fmt=plistlib.FMT_BINARY)),
                codeless_paths=paths,
                payload_files=payload_files(artifact), patches=patch_sites(data, artifact),
                contexts=contexts, capture_globals=capture_bindings,
                abi_header_sha256=sha(abi_header.read_bytes()), abi_bindings=bindings)


def bind(profile_path, artifact, collection, output):
    profile = json.loads(profile_path.read_text())
    if profile['producer']['source_sha256'] != sha(Path(__file__).read_bytes()):
        raise ValueError('Binding profile was generated by another producer source')
    if profile['schema'] != 1:
        raise ValueError('Unsupported repair binding profile')
    if payload_files(artifact) != profile['payload_files']:
        raise ValueError('Repair artifact payload differs from the reference profile')
    reference_bytes = (artifact/'build.json').read_bytes()
    record = json.loads(reference_bytes)
    repair = record['inputs']['early_vm_repair']
    if repair['capture'] != profile['reference_capture']:
        raise ValueError('Repair capture differs from reference profile')
    data = collection.read_bytes()
    actual = members(data)
    if actual != profile['members']:
        changed = sorted(k for k in set(actual) | set(profile['members'])
                         if actual.get(k) != profile['members'].get(k))
        raise ValueError('KC fileset binding differs: ' + ', '.join(changed))
    if enclosing_identity(data) != profile['enclosing']:
        raise ValueError('Enclosing KC differs outside UUID/prelink metadata')
    metadata, _ = normalized_prelink(data, profile['codeless_paths'])
    target_sha = sha(data)
    binding_mode = 'exact-reference' if target_sha == profile['reference_collection_sha256'] else 'verified-metadata-baseline'
    metadata_sha = sha(plistlib.dumps(metadata, fmt=plistlib.FMT_BINARY))
    if binding_mode != 'exact-reference' and metadata_sha != profile['prelink_sha256']:
        raise ValueError('Non-path prelink metadata differs from reviewed baseline')
    if patch_sites(data, artifact) != profile['patches']:
        raise ValueError('Kernel repair patch sites differ from reference')
    for context in profile['contexts']:
        start = context['offset']
        if sha(data[start:start+context['length']]) != context['sha256']:
            raise ValueError('Captured repair context differs: ' + context['name'])
    output.mkdir(parents=True, exist_ok=False)
    # Only the closure consumed by repairs.apply is copied; no donor EFI or trace patches.
    for name in profile['payload_files']:
        target = output/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact/name, target)
    (output/'binding-profile.json').write_bytes(profile_path.read_bytes())
    repair['capture']['collection_sha256'] = target_sha
    repair['collection_path'] = str(collection.resolve())
    capture_bytes = (json.dumps(repair['capture'], indent=2)+'\n').encode()
    (output/'capture.json').write_bytes(capture_bytes)
    repair['capture_path'] = str((output/'capture.json').resolve())
    repair['capture_sha256'] = sha(capture_bytes)
    (output/'reference-build.json').write_bytes(reference_bytes)
    record = dict(inputs=dict(early_vm_repair=repair))
    record.update(unit='tools/kernel-fixes', revision=REVISION,
                  commit=profile['producer']['commit'], dirty=profile['producer']['dirty'],
                  operation='bind-kernel-repairs', date=datetime.now(timezone.utc).isoformat())
    # The original capture remains in the proof, rather than being represented as a new trace.
    record['inputs']['repair_binding'] = dict(
        metadata_collection_sha256=profile['metadata_collection_sha256'],
        normalized_prelink_sha256=metadata_sha,
        binding_mode=binding_mode,
        producer=profile['producer'], runtime_source_sha256=sha(Path(__file__).read_bytes()),
        profile_sha256=sha(profile_path.read_bytes()), reference_artifact=str(artifact.resolve()),
        reference_record_sha256=sha(reference_bytes),
        reference_capture_sha256=profile['reference_capture_sha256'],
        reference_collection_sha256=profile['reference_collection_sha256'],
        target_collection_sha256=target_sha, fileset_members=len(actual),
        patches=profile['patches'], unchanged_payload_files={
            k: v for k, v in profile['payload_files'].items() if k != 'build.json'})
    record['validation'] = 'Static binding verified; boot of this collection is not yet tested'
    record['files'] = {str(p.relative_to(output)): sha(p.read_bytes())
                       for p in sorted(output.rglob('*')) if p.is_file() and p != output/'build.json'}
    (output/'build.json').write_text(json.dumps(record, indent=2)+'\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('profile', 'bind'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--artifact', type=Path, required=True)
        cmd.add_argument('--boot-kc', type=Path, required=True)
        cmd.add_argument('--output', type=Path, required=True)
        if name == 'profile':
            cmd.add_argument('--abi-header', type=Path, required=True)
            cmd.add_argument('--metadata-kc', type=Path, required=True)
        else:
            cmd.add_argument('--profile', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'profile':
        profile = generate_profile(args.artifact, args.boot_kc, args.abi_header, args.metadata_kc)
        reference, _ = normalized_prelink(args.boot_kc.read_bytes())
        target, _ = normalized_prelink(args.metadata_kc.read_bytes())
        review = metadata_differences(reference, target)
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output/'profile.json').write_text(json.dumps(profile, indent=2)+'\n')
        (args.output/'metadata-review.json').write_text(json.dumps(review, indent=2)+'\n')
        record = dict(unit='tools/kernel-fixes', revision=REVISION, operation='kernel-repair-binding-profile',
                      **{k: v for k, v in profile['producer'].items() if k in ('commit', 'dirty')},
                      date=datetime.now(timezone.utc).isoformat(), producer=profile['producer'],
                      inputs={k: dict(path=str(v.resolve()), sha256=sha(v.read_bytes()))
                              for k, v in dict(reference_kc=args.boot_kc, metadata_kc=args.metadata_kc,
                                               abi_header=args.abi_header, reference_record=args.artifact/'build.json').items()},
                      files={p.name: sha(p.read_bytes()) for p in sorted(args.output.iterdir())},
                      validation='Static binding only; review metadata-review.json; boot is not yet tested')
        (args.output/'build.json').write_text(json.dumps(record, indent=2)+'\n')
    else:
        bind(args.profile, args.artifact, args.boot_kc, args.output)
    print(args.output)


if __name__ == '__main__':
    main()
