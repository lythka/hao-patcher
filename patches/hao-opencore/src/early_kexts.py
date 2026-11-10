"""Inject Tahoe display/network kexts absent from both hybrid collections."""
import hashlib
from pathlib import Path
import plistlib
import shutil
import struct
import sys


def members(collection):
    data = collection.read_bytes()
    pos = 32
    result = set()
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, size = struct.unpack_from('<II', data, pos)
        if command == 0x80000035:
            name = pos+struct.unpack_from('<I', data, pos+24)[0]
            result.add(data[name:].split(b'\0', 1)[0].decode())
        pos += size
    return result


def bundle_info(bundle):
    info = bundle/'Contents/Info.plist'
    if not info.exists():
        info = bundle/'Info.plist'
    return info, plistlib.loads(info.read_bytes())


def dependencies(roots, available, bundles):
    ordered = []
    visited = set(available)
    visiting = set()

    def add(identifier):
        if identifier in visited or identifier.startswith('com.apple.kpi.'):
            return
        if identifier in visiting:
            raise ValueError('Cyclic kext dependency: '+identifier)
        visiting.add(identifier)
        _, info = bundle_info(bundles[identifier])
        for required in sorted(info.get('OSBundleLibraries', {})):
            add(required)
        visiting.remove(identifier)
        visited.add(identifier)
        ordered.append(identifier)

    for root in roots:
        add(root)
    return ordered


def discover_bundles(extensions):
    bundles = {}
    # Some .kext directories are only containers for plug-ins (iPodDriver).
    # Index real bundle plists instead of assuming every directory is loadable.
    for info_path in sorted(extensions.rglob('Info.plist')):
        parent = info_path.parent
        bundle = parent.parent if parent.name == 'Contents' else parent
        if bundle.suffix == '.kext':
            info = plistlib.loads(info_path.read_bytes())
            bundles[info['CFBundleIdentifier']] = bundle
    return bundles


def copy_to_esp(source, destination):
    # FAT cannot preserve APFS flags/xattrs or symlinks. OpenCore needs bytes.
    if source.is_dir():
        destination.mkdir(mode=0o755, parents=True, exist_ok=True)
        for child in sorted(source.iterdir()):
            copy_to_esp(child, destination/child.name)
    else:
        shutil.copyfile(source, destination)


def apply(system, esp, roots_file):
    config_path = esp/'EFI/OC/config.plist'
    config = plistlib.loads(config_path.read_bytes())
    kexts = esp/'EFI/OC/Kexts'
    collections = system/'System/Library/KernelCollections'
    # A SystemKC client retains its linked addresses for dependencies in that
    # collection. Injecting a second copy into BootKC makes the loader discard
    # the SystemKC dependency without redirecting those clients' calls.
    available = members(collections/'BootKernelExtensions.kc')
    available.update(members(collections/'SystemKernelExtensions.kc'))
    for entry in config['Kernel']['Add']:
        if entry['Enabled']:
            _, info = bundle_info(kexts/entry['BundlePath'])
            available.add(info['CFBundleIdentifier'])
    bundles = discover_bundles(system/'System/Library/Extensions')
    roots = [line.strip() for line in roots_file.read_text().splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    for identifier in roots:
        if identifier in available:
            print('Tahoe driver already supplied by collection or EFI: '+identifier)
    for identifier in dependencies(roots, available, bundles):
        bundle = bundles[identifier]
        info_path, info = bundle_info(bundle)
        contents = info_path.parent.relative_to(bundle)
        executable = contents/'MacOS'/info['CFBundleExecutable'] if str(contents) == 'Contents' else contents/info['CFBundleExecutable']
        destination = kexts/bundle.name
        copy_to_esp(bundle, destination)
        config['Kernel']['Add'].append(dict(
            Arch='x86_64', BundlePath=bundle.name,
            Comment='Early Tahoe display/network: '+identifier, Enabled=True,
            ExecutablePath=str(executable), PlistPath=str(info_path.relative_to(bundle)),
            MinKernel='27.0.0', MaxKernel='27.99.99'))
        digest = hashlib.sha256((bundle/executable).read_bytes()).hexdigest()
        print(f'Early Tahoe injection: {identifier} {info["CFBundleVersion"]} sha256={digest}')
    config_path.write_bytes(plistlib.dumps(config))


if __name__ == '__main__':
    apply(*(Path(arg) for arg in sys.argv[1:]))
