"""Make OCLP's legacy MTLCompilerService (3802, UUID 3716D20F) load cache/metallib's compat.dylib at startup.

compat.dylib repairs the AIR of every shader the service compiles for the HD 4000 (runtime-generated MPSGraph and
Espresso kernels included); it must be in the service's initial dependency closure, before MTLCompiler resolves its
plugin entry, so a load command is appended to the service executable. The service is re-signed ad hoc with its
identifier, entitlements and flags; its Info.plist and CodeResources stay byte-identical and stay bound in the new
signature's special slots. Client applications are unchanged.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import uuid

HERE = Path(__file__).resolve().parent
UNIT = HERE.parent
REPO = UNIT.parents[1]
SERVICE = 'System/Library/Frameworks/Metal.framework/Versions/A/XPCServices/MTLCompilerService.xpc/Contents/MacOS/MTLCompilerService'
LIBRARY = 'System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/3802/Libraries/libhaoMetalCompilerCompat.dylib'
UUID = uuid.UUID('3716D20F-B990-3906-B3E5-44E88AE63AF8').bytes
spec = importlib.util.spec_from_file_location('backend_editor', HERE/'macho_edit.py')
editor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor)
spec = importlib.util.spec_from_file_location('live_text_paths', HERE/'paths.py')
paths = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paths)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args):
    return subprocess.check_output(list(map(str, args)), text=True).strip()


def check_service(data):
    commands = editor.load_commands(data)
    identities = [data[at+8:at+24] for cmd, at, size in commands if cmd == 0x1b and size == 24]
    if identities != [UUID]:
        raise ValueError('unsupported legacy Metal compiler service UUID')
    return editor.signature_directories(data, commands, require_adhoc=False)


def signature_metadata(data):
    directories=editor.signature_directories(data,editor.load_commands(data),require_adhoc=False)
    result=[]
    for cd, hashes, _, _, _, kind, size, flags, special in directories:
        length=struct.unpack_from('>I',data,cd+4)[0]
        result.append({'cdhash':editor.digest(kind,data[cd:cd+length])[:20].hex(),
                       'flags':flags,'platform':data[cd+38],
                       'bundle_slots':{str(slot):data[cd+hashes-slot*size:cd+hashes-(slot-1)*size].hex()
                                       for slot in (1,3) if slot<=special}})
    return result


def bind_bundle(data, info, resources):
    """Bind existing bundle bytes after signing; no code pages are changed."""
    result = bytearray(data)
    for cd, hashes, _, _, _, kind, size, _, special in editor.signature_directories(data, editor.load_commands(data)):
        if special < 3:
            raise ValueError('new service signature lacks bundle special slots')
        for slot, content in ((1, info), (3, resources)):
            at = cd + hashes - slot * size
            result[at:at+size] = editor.digest(kind, content)
    return bytes(result)


def build(source, compat_artifact, output):
    source, compat_artifact, output = map(lambda p: Path(p).resolve(), (source, compat_artifact, output))
    original = (source/'MTLCompilerService').read_bytes()
    cds = check_service(original)
    info = (source/'Info.plist').read_bytes()
    resources = (source/'CodeResources').read_bytes()
    for cd, hashes, _, _, _, kind, size, _, special in cds:
        for slot, content in ((1, info), (3, resources)):
            if special < slot or original[cd+hashes-slot*size:cd+hashes-(slot-1)*size] != editor.digest(kind, content):
                raise ValueError('original service bundle binding differs')
    compat_record = json.loads((compat_artifact/'build.json').read_text())
    if compat_record.get('outputs', {}).get('compat.dylib') != sha(compat_artifact/'compat.dylib'):
        raise ValueError('compatibility artifact digest differs')
    changed, edit = editor.append_dylibs(original, ['/'+LIBRARY], resign_filetype=2)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='metal-backend-sign-') as tmp:
        bundle = Path(tmp)/'MTLCompilerService.xpc'
        contents = bundle/'Contents'
        executable = contents/'MacOS/MTLCompilerService'
        executable.parent.mkdir(parents=True)
        (contents/'_CodeSignature').mkdir()
        executable.write_bytes(changed)
        (contents/'Info.plist').write_bytes(info)
        (contents/'_CodeSignature/CodeResources').write_bytes(resources)
        run('codesign', '-f', '-s', '-', '--preserve-metadata=identifier,entitlements,flags,runtime', executable)
        # codesign regenerates the seal even when asked to sign the executable.
        # Keep the existing bundle untouched and bind its original two files.
        (contents/'_CodeSignature/CodeResources').write_bytes(resources)
        executable.write_bytes(bind_bundle(executable.read_bytes(), info, resources))
        # The captured legacy resource seal has custom omit rules which modern
        # host codesign rejects as obsolete. Verify code normally and separately
        # prove the unchanged target seal/Info hashes in every CodeDirectory.
        run('codesign', '--verify', '--ignore-resources', '--verbose=2', bundle)
        signed = executable.read_bytes()
        new_cds = editor.signature_directories(signed, editor.load_commands(signed))
        if [c[7] for c in new_cds] != [c[7] | 2 for c in cds]:
            raise ValueError('service signature policy flags changed')
        if ([m['bundle_slots'] for m in signature_metadata(original)] !=
                [m['bundle_slots'] for m in signature_metadata(signed)]):
            raise ValueError('service bundle signature slots changed')
        target = output/'replace'/SERVICE
        target.parent.mkdir(parents=True)
        target.write_bytes(signed)
        target.chmod(0o755)
    target_lib = output/'replace'/LIBRARY
    target_lib.parent.mkdir(parents=True)
    shutil.copyfile(compat_artifact/'compat.dylib', target_lib)
    target_lib.chmod(0o755)
    run('install_name_tool', '-id', '/'+LIBRARY, target_lib)
    run('codesign', '-f', '-s', '-', target_lib)
    run('codesign', '--verify', target_lib)
    record = {'unit':'tools/live-text', 'operation':'compiler-service',
              'commit':run('git','-C',REPO,'rev-parse','HEAD'),
              'dirty':bool(run('git','-C',REPO,'status','--porcelain','--',UNIT,REPO/'cache/metallib/lib/runtime')),
              'date':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'inputs':{paths.repo_path(p):sha(p) for p in (source/'MTLCompilerService',source/'Info.plist',source/'CodeResources',compat_artifact/'build.json',compat_artifact/'compat.dylib')},
              'source_sha256':{str(p.relative_to(REPO)):sha(p) for p in (HERE/'compiler_service.py',HERE/'macho_edit.py')},
              'compat_commit':compat_record.get('commit'), 'compat_revision':compat_record.get('revision'),
              'service':SERVICE, 'source_service_sha256':sha(source/'MTLCompilerService'),
              'source_info_sha256':sha(source/'Info.plist'), 'source_resources_sha256':sha(source/'CodeResources'),
              'files':{SERVICE:sha(output/'replace'/SERVICE),LIBRARY:sha(target_lib)},
              'native_signature':'ad-hoc; original Apple CMS identity replaced',
              'resource_validation':'original legacy envelope retained; host code verification uses --ignore-resources, with exact bundle-slot comparison',
              'original_signature':signature_metadata(original),
              'new_signature':signature_metadata(signed),
              'service_uuid':str(uuid.UUID(bytes=UUID)), 'edit':edit,
              'validation':'authored startup-interposition control (tests/test_compiler_service.py); 26A428 laptop trial 2026-11-09'}
    (output/'build.json').write_text(json.dumps(record,indent=2)+'\n')
    return record

