"""Reconstruct the final Photos identity without modifying any image or input."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re

PHOTOS = 'System/Applications/Photos.app/Contents/MacOS/Photos'
ROOT = '/Library/hao-live-text/r1'
DEPENDENCY = ROOT + '/libLiveText.dylib'


def package_root(build):
    root = build.get('install_root')
    if (not isinstance(root, str)
            or re.fullmatch(r'/Library/hao-live-text/r[1-9][0-9]*', root) is None
            or build.get('load_paths') != [root + '/libLiveText.dylib']):
        raise ValueError('unsupported Live Text identity package root')
    return root


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    # Artifact inputs must remain immutable, including no __pycache__ writes.
    exec(compile(path.read_bytes(), str(path), 'exec'), result.__dict__)
    return result


def relative(root, name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('non-relative Live Text identity input')
    result = root / path
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError('Live Text identity input escapes root')
    return result


def reconstruct(source, stub, entry, fat, editor, root=ROOT):
    dependency = package_root({'install_root': root,
                               'load_paths': [root + '/libLiveText.dylib']}) + '/libLiveText.dylib'
    if digest(source) != entry['source_sha256']:
        raise ValueError('Photos original source hash differs')
    if digest(stub) != entry['stub_sha256']:
        raise ValueError('Photos payload stub hash differs')
    embedded = fat.combine(source, stub, drop_x86_64=entry.get('mode') == 'translate')
    if digest(embedded) != entry['result_sha256']:
        raise ValueError('Photos reconstructed payload result differs')
    original_arm = [part for part in fat.slices(source) if part[0] == fat.CPU_ARM64]
    if not original_arm:
        raise ValueError('Photos has no original ARM slices')
    final, report = editor.append_dylibs(embedded, [dependency])
    for data in (embedded, final):
        if [part for part in fat.slices(data) if part[0] == fat.CPU_ARM64] != original_arm:
            raise ValueError('Photos original ARM slices changed')
    return final, report, [{'cpu': cpu, 'subtype': sub, 'size': len(body),
                            'sha256': digest(body)} for cpu, sub, body in original_arm]


def transform(manifest, payload, original, runtime):
    """Return a copied manifest with only Photos' final result hash replaced."""
    payload, original, runtime = map(Path, (payload, original, runtime))
    build_bytes = (runtime / 'build.json').read_bytes()
    build = json.loads(build_bytes)
    root = package_root(build)
    if (build.get('tool') != 'tools/live-text-runtime'
            or build.get('revision', 0) < 3):
        raise ValueError('unsupported Live Text identity artifact')
    editor_name = 'install-tools/macho_dependencies.py'
    editor_path = relative(runtime, editor_name)
    editor_hash = digest(editor_path.read_bytes())
    if build['files'].get(editor_name) != editor_hash:
        raise ValueError('Live Text artifact editor hash differs')
    matches = [entry for entry in manifest['files'] if entry['path'] == PHOTOS]
    if len(matches) != 1:
        raise ValueError('expected exactly one Photos entry in hao manifest')
    entry = matches[0]
    source = (original / PHOTOS).read_bytes()
    stub_path = relative(payload / 'embed', entry['stub'])
    stub = stub_path.read_bytes()
    for bound in entry.get('bound', []):
        if 'file' in bound and digest(relative(original, bound['file']).read_bytes()) != bound['sha256']:
            raise ValueError('Photos bound Info.plist/resources changed')
    fat_path = payload / 'hao_fat.py'
    fat = load(fat_path, 'identity_hao_fat')
    editor = load(editor_path, 'identity_live_text_editor')
    final, report, arms = reconstruct(source, stub, entry, fat, editor, root)
    final_hash = digest(final)
    updated = copy.deepcopy(manifest)
    next(row for row in updated['files'] if row['path'] == PHOTOS)['result_sha256'] = final_hash
    record = {'runtime': str(runtime.resolve()), 'runtime_build_sha256': digest(build_bytes),
              'runtime_commit': build.get('commit'), 'runtime_revision': build['revision'],
              'editor_sha256': editor_hash, 'hao_fat_sha256': digest(fat_path.read_bytes()),
              'path': PHOTOS, 'install_root': root, 'dependency': root + '/libLiveText.dylib',
              'source_sha256': entry['source_sha256'], 'stub_sha256': entry['stub_sha256'],
              'embedded_sha256': entry['result_sha256'], 'final_sha256': final_hash,
              'preserved_arm_slices': arms, 'signature_preservation': report}
    return updated, record
