"""Install only the recorded kernel repairs into an explicit target OpenCore config."""
import hashlib
import json
from pathlib import Path
import plistlib
import sys


def apply(config_path, artifact, collection, trace_artifact=None):
    record = json.loads((artifact / 'build.json').read_text())
    repair = record['inputs']['early_vm_repair']
    # These RIP-relative repairs depend on the built collection's segment layout.
    # Reject another layout instead of transplanting offsets from a different KC.
    if hashlib.sha256(collection.read_bytes()).hexdigest() != repair['capture']['collection_sha256']:
        raise ValueError('Target BootKC differs from the recorded kernel collection')
    donor = plistlib.loads((artifact / 'EFI/OC/config.plist').read_bytes())
    config = plistlib.loads(config_path.read_bytes())
    patches = []
    for replacement in repair['replacements']:
        matches = [p for p in donor['Kernel']['Patch']
                   if p['Comment'] == replacement['comment']
                   and p['Find'].hex() == replacement['find']
                   and p['Replace'].hex() == replacement['replace']]
        if len(matches) != 1:
            raise ValueError('Artifact has no unique recorded kernel repair')
        patches.extend(matches)
    comments = {p['Comment'] for p in patches}
    config['Kernel']['Patch'] = [p for p in config['Kernel']['Patch']
                                 if p['Comment'] not in comments] + patches
    config['Kernel']['Patch'] = [p for p in config['Kernel']['Patch']
                                 if not p['Comment'].startswith('Startup trace ')]
    if trace_artifact:
        trace = json.loads((trace_artifact / 'build.json').read_text())
        if trace['inputs']['collection_sha256'] != repair['capture']['collection_sha256']:
            raise ValueError('Startup trace belongs to another kernel collection')
        config['Kernel']['Patch'].extend(plistlib.loads((trace_artifact / 'patches.plist').read_bytes()))
        print(f'Installed {len(trace["capture"]["calls"])} framebuffer startup markers')
    bundle = 'IntelCommpage.kext'
    entry, = [e for e in donor['Kernel']['Add'] if e['BundlePath'] == bundle]
    config['Kernel']['Add'] = [e for e in config['Kernel']['Add']
                               if e['BundlePath'] != bundle] + [entry]
    config_path.write_bytes(plistlib.dumps(config))
    print(f'Installed {len(patches)} recorded kernel repairs and {bundle}; retained target OCLP settings')


if __name__ == '__main__':
    apply(*(Path(p) for p in sys.argv[1:]))
