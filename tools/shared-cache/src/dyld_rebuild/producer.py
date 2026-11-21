"""Build one shared-cache artifact from explicit mounted filesystem views."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from . import atlas, sidecars
from .cache import interpose_sections
from .candidate import CandidateFamily
from .exports import require
from .host_tools import builder_objects, loader
from .loader_generation import cache_manifest, load_root_manifest, read_set, sha
from .program_pool import main_identity
from .run_assembly import emit_structural, prepare_global_objc, assemble_bootstrap, _run
from .run_inputs import prepare_inputs, pin
from .run_loaders import FilesystemViews, collect_program_inputs
from .run_metadata import prepare_metadata
from .run_patch import prepare_patch
from .run_prepare import prepare_producer
from .optimization_report import inspect as inspect_optimizations
from .swift_metadata import provenance

BASE = 'dyld_shared_cache_x86_64'


def read(record):
    return json.loads(Path(record['path']).read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    return pin(path)


def x86_64_uuid(path):
    """LC_UUID of the x86_64 slice of a Mach-O file (thin or fat), or None."""
    import struct
    data = Path(path).read_bytes()
    off = None
    magic, = struct.unpack_from('>I', data, 0)
    if magic in (0xcafebabe, 0xcafebabf):
        count, = struct.unpack_from('>I', data, 4)
        for i in range(min(count, 64)):
            if magic == 0xcafebabe:
                cpu, _, o, _, _ = struct.unpack_from('>IIIII', data, 8 + i * 20)
            else:
                cpu, _, o, _, _, _ = struct.unpack_from('>IIQQII', data, 8 + i * 32)
            if cpu == 0x01000007:
                off = o
    elif struct.unpack_from('<II', data, 0) == (0xfeedfacf, 0x01000007):
        off = 0
    if off is None:
        return None
    _, _, _, ncmds, _, _ = struct.unpack_from('<IiiIII', data, off + 4)
    at = off + 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, at)
        if cmd == 0x1b:
            return data[at + 8:at + 24].hex()
        at += size
    return None


def clear_same_uuid_overrides(blob, views, images):
    """27's cached loaders are all isOverridable with no file validation, so any on-disk file at a cached path counts as
    a root and drops every dependent to JIT loaders. 27's x86_64 OS cryptex ships libsystem_kernel/platform/pthread as
    files with the cache images' own UUIDs, so stock's set is never used either. A cached loader stays overridable unless
    every on-disk copy (System, OS and App cryptex views) carries the cache image's x86_64 LC_UUID; a real root still wins."""
    data = bytearray(Path(blob).read_bytes())
    cleared = []
    for row in read_set(bytes(data), images, app=False)['loaders']:
        copies = [views.roots[v] / row['path'].lstrip('/') for v in ('system', 'os', 'app') if views.roots.get(v) is not None]
        copies = [c for c in copies if c.is_file()]
        if not copies:
            continue
        try:
            uuids = {x86_64_uuid(c) for c in copies}
        except Exception:   # unreadable or not Mach-O: keep it overridable
            continue
        if uuids == {row['uuid']}:
            at = row['offset'] + 0x2c
            bits = int.from_bytes(data[at:at + 2], 'little')
            if bits & 2:
                data[at:at + 2] = (bits & ~2).to_bytes(2, 'little')
                cleared.append(row['path'])
    Path(blob).write_bytes(bytes(data))
    return cleared


def serialize(helper, manifest, collection, directory, *, programs=False, cached=None, views=None):
    directory.mkdir()
    main, count, images, inputs = cache_manifest(manifest)
    files, paths = load_root_manifest(collection['root']['path'])
    mapping = directory / 'root.tsv'
    mapping.write_text(''.join(f"{path}\t{row['physical_path']}\n" for path, row in sorted(files.items())))
    command = [str(helper), str(main), files[paths[0]]['physical_path'], paths[0]]
    if not programs:
        blob = directory / 'cached-dylibs.pbl'
        subprocess.run([*command, str(blob), 'cached', str(images), str(count), str(mapping), '-'], check=True)
        cleared = clear_same_uuid_overrides(blob, views, images) if views is not None else []
        if cleared:
            print('cached loaders not overridable (on-disk copies are the cache images): ' + ', '.join(cleared), flush=True)
        return write(directory / 'result.json', dict(mode='cached', cache_inputs=inputs, not_overridable=cleared,
                     cached_set=dict(file=blob.name, size=blob.stat().st_size, sha256=sha(blob))))
    batch = directory / 'batch.tsv'
    batch.write_text(''.join(f'{path}\t{directory}/program-{i:04d}.pbl\t{directory}/program-{i:04d}.log\n'
                             for i, path in enumerate(paths)))
    subprocess.run([*command, str(directory / 'batch-output'), 'program-batch', str(images), str(count),
                    str(mapping), str(cached), str(batch)], check=True)
    rows = []
    jit = []
    for index, path in enumerate(paths):
        blob = directory / f'program-{index:04d}.pbl'
        if not blob.exists():
            log = (directory / f'program-{index:04d}.log').read_text(errors='replace')
            if 'generation skipped (interposing)' in log:
                jit.append((path, 'interposing closure: dyld uses JIT loaders'))
            elif 'generation skipped (unlaunchable)' in log:
                why = log.split('generation skipped (unlaunchable): ', 1)[1].splitlines()[0]
                print(f'program not launchable on this image, no set: {path}: {why}', flush=True)
                jit.append((path, 'unlaunchable: ' + why))
            else:
                require(False, 'program serializer refused ' + path)
            continue
        data = blob.read_bytes()
        parsed = read_set(data, images, app=True)
        rows.append(dict(main_identity(data), file=blob.name, size=len(data), sha256=sha(blob),
                         loader_count=len(parsed['loaders'])))
    inventory = read(collection['inventory'])
    kept = set(paths) - {path for path, _ in jit}
    entries = {key: path for key, path in inventory['original_keys'].items()
               if not key.startswith('/cdhash/') and path in kept}
    retired = list(inventory['retired_programs']) + [
        dict(path=path, reason=reason,
             retired_keys=sorted(k for k, v in inventory['original_keys'].items() if v == path)) for path, reason in jit]
    for row in sorted(rows, key=lambda r: r['path'].encode()):
        if row['check_cdhash']:
            entries['/cdhash/' + row['cdhash']] = row['path']
    return write(directory / 'result.json', dict(schema=1, programs=rows,
                 required_program_paths=inventory['required_program_paths'],
                 retired_programs=retired, program_trie_entries=entries))


def _loaders(system, os_root, app, data, program_files, intake, assembly, meta, work):
    """Cached and program prebuilt loaders (dyld-1378 serializer); returns the final program-input collection."""
    views = FilesystemViews(system=system, os_root=os_root, app=app, data=data, program_files=program_files)
    collection = collect_program_inputs(intake['capture-manifest.json']['path'], assembly['path'],
                                        views, work / 'program-inputs')
    print('Build host loader serializer', flush=True)
    helper = loader(work / 'loader')
    cached = serialize(helper, assembly['path'], collection, work / 'cached', views=views)
    md = read(meta)
    allocation = md['outputs']['metadata-layout.json']
    layout = read(allocation)
    main, files, images, _ = cache_manifest(assembly['path'])
    blob = work / 'cached/cached-dylibs.pbl'
    decoded = read_set(blob.read_bytes(), images, app=False)
    addresses = work / 'cached/addresses.json'
    write(addresses, dict(base=layout['cached_pbl']['address'], alignment=8,
                         size=blob.stat().st_size, sha256=sha(blob),
                         loaders=[dict(index=i, path=row['path'], offset=row['offset'],
                                       address=layout['cached_pbl']['address'] + row['offset'])
                                  for i, row in enumerate(decoded['loaders'])]))
    source = intake['capture-manifest.json']
    _run(work, 'headers', 'tools/build-objc-tables.py', 'headers', '--candidate', main,
         '--subcache-count', files - 1, '--metadata-plan', md['outputs']['provider-objc/tables-input.json']['path'],
         '--allocation', allocation['path'], '--pbl-addresses', addresses, '--cached-pbl', blob,
         '--output-dir', work / 'headers', '--source-manifest', source['path'],
         '--source-manifest-sha256', source['sha256'])
    _run(work, 'cached-finalize', 'tools/finalize-cached-loaders.py', '--bootstrap-manifest', assembly['path'],
         '--header-report', work / 'headers/report.json', '--pbl-report', cached['path'], '--output', work / 'cached-family')
    cached_manifest = work / 'cached-family/build-report.json'
    final_collection = collect_program_inputs(source['path'], cached_manifest, views, work / 'final-program-inputs')
    serialize(helper, cached_manifest, final_collection, work / 'cached-final', views=views)
    programs = serialize(helper, cached_manifest, final_collection, work / 'programs', programs=True,
                         cached=work / 'cached-final/cached-dylibs.pbl')
    _run(work, 'program-finalize', 'tools/finalize-program-loaders.py', '--cache-manifest', cached_manifest,
         '--program-report', programs['path'], '--program-report-sha256', programs['sha256'],
         '--source-manifest', source['path'], '--source-manifest-sha256', source['sha256'],
         '--output', work / 'final')
    return final_collection


def build(system, os_root, app, data, build_id, selection, work, output, program_files=None, swift_catalog=None,
          swift_runtime_toolchain=None, prebuilt_loaders=True):
    work.mkdir(parents=True)
    print('Read cache and selected providers', flush=True)
    intake = prepare_inputs(os_root, system, build_id, work / 'intake', provider_profile=selection)
    prepared = prepare_producer(work / 'intake', work / 'prepare', intake_pins=intake, build=build_id)
    prep = prepared['report']
    structure = emit_structural(prep['path'], work / 'structural', preparation_sha256=prep['sha256'])['structural_manifest']
    meta = prepare_metadata(prep['path'], structure['path'], work / 'metadata',
                            preparation_sha256=prep['sha256'], structural_sha256=structure['sha256'],
                            swift_catalog=swift_catalog,swift_runtime_toolchain=swift_runtime_toolchain)['report']
    patch = prepare_patch(prep['path'], meta['path'], structure['path'], work / 'patch',
                          preparation_sha256=prep['sha256'], metadata_sha256=meta['sha256'],
                          structural_sha256=structure['sha256'])
    global_ = prepare_global_objc(prep['path'], meta['path'], structure['path'], work / 'global',
                                 preparation_sha256=prep['sha256'], metadata_sha256=meta['sha256'],
                                 structural_sha256=structure['sha256'])['report']
    assembly = assemble_bootstrap(prep['path'], meta['path'], global_['path'], structure['path'],
                                 patch['patch_report']['path'], patch['lookup_report']['path'], work / 'assembled',
                                 preparation_sha256=prep['sha256'], metadata_sha256=meta['sha256'],
                                 global_sha256=global_['sha256'], structural_sha256=structure['sha256'],
                                 patch_sha256=patch['patch_report']['sha256'],
                                 lookup_sha256=patch['lookup_report']['sha256'])['assembled_manifest']
    from .swift_conformance import regenerate
    print('Rebuild Swift conformance lookups', flush=True)
    assembly = regenerate(assembly['path'], work / 'conformance')
    md = read(meta)
    allocation = md['outputs']['metadata-layout.json']
    layout = read(allocation)
    source = intake['capture-manifest.json']
    if prebuilt_loaders:
        final_collection = _loaders(system, os_root, app, data, program_files, intake, assembly, meta, work)
    else:
        # fold-in v1 (pm 2026-11-14): no loader sets. Nothing from the source cache's sets is carried; dyld uses
        # JIT loaders. The program-file views are not needed.
        print('Finish without prebuilt loaders', flush=True)
        _run(work, 'no-loaders', 'tools/finalize-no-loaders.py', '--bootstrap-manifest', assembly['path'],
             '--output', work / 'final')
        final_collection = None
    main, files, images, _ = cache_manifest(work / 'final/build-report.json')
    source_info = read(source)
    original = CandidateFamily(source_info['main'], image_count=source_info['images'], subcache_count=source_info['cache_files'] - 1)
    try:
        exclusions = sidecars.calibrate(original, atlas.read(Path(source_info['main'] + '.atlas'), bytes(original.main.data[88:104])),
                                        Path(source_info['main'] + '.map').read_text())
    finally:
        original.close()
    family = CandidateFamily(main, image_count=images, subcache_count=files - 1)
    try:
        left = interpose_sections(family)
        require(not left, f'interpose section left in cache images (dyld ignores it there): {left[:4]}')
        value, text, _ = sidecars.derive(family, exclusions)
        swift_recipe = read(md['outputs']['swift/recipe.json']) if swift_catalog else None
        optimizations = inspect_optimizations(family, layout, swift_recipe, prebuilt_loaders=prebuilt_loaders)
    finally:
        family.close()
    output.mkdir(parents=True)
    for path in main.parent.glob(BASE + '*'):
        subprocess.run(['cp', '-c', str(path), str(output / path.name)], check=True)
    atlas.write(output / (BASE + '.atlas'), value)
    (output / (BASE + '.map')).write_text(text)
    selected = json.loads(Path(selection).read_text())['providers']
    (output / 'retired-roots.txt').write_text(''.join(path.lstrip('/') + '\n' for path in selected.values()))
    optimizations['swift']['conformance'] = read(assembly)['conformance']
    write(output / 'optimizations.json', optimizations)
    run = read(intake['run-provenance.json'])
    write(output / 'provider-provenance.json', dict(schema=1, build=build_id, provider_sources=run['provider_sources']))
    # Include the inputs actually used for the family and executable loaders,
    # not just paths to a build machine's mounted volumes.
    plan = read(read(prep)['artifacts']['provider-plan.json'])
    root_files = load_root_manifest(final_collection['root']['path'])[0] if final_collection else {}
    inputs = {str(Path(row['path']).resolve()) for row in source_info['files']}
    inputs.update(str(Path(row['input_path']).resolve()) for row in plan['providers'])
    inputs.update(str(Path(row['physical_path']).resolve()) for row in root_files.values())
    inputs.update((str(Path(selection).resolve()), str(Path(system) / 'System/Library/CoreServices/SystemVersion.plist')))
    if swift_catalog:
        inputs.add(str(Path(swift_catalog).resolve()))
        capture_record = Path(swift_catalog).parent / 'build.json'
        if capture_record.is_file():
            inputs.add(str(capture_record.resolve()))
    if swift_runtime_toolchain:
        inputs.update(str((Path(swift_runtime_toolchain)/name).resolve()) for name in ('runtime.o','relocate','build.json'))
    # These are external, source-built linker inputs, not products of this cache run.
    from .artifacts import helper
    for name in ('generator', 'objc-writer', 'swift-table-writer'):
        artifact = helper(name)
        if artifact is not None:
            inputs.add(str(artifact.resolve()))

    record = provenance(sorted(inputs), build=build_id, architecture='x86_64', providers=selected,
          images=images, cache_files=files, system=str(system), os=str(os_root),
          program_files=str(program_files) if program_files else None, native_acceptance='OPEN', optimizations=optimizations,
          prebuilt_loaders=prebuilt_loaders)
    write(output / 'build.json', record)
    return output
