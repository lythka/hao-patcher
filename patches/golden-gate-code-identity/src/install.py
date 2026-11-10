"""Install generated native observers only over the originals they were built for."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def install(system, artifact):
    system, artifact = Path(system), Path(artifact)
    record = json.loads((artifact/'build.json').read_text())
    for entry in record['replacements']:
        path = Path(entry['path'])
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('non-relative replacement path')
        if sha(system/path) != entry['source_sha256'] or sha(artifact/'replace'/path) != entry['sha256']:
            raise ValueError(f'code identity observer changed: {path}')
    binary = artifact/'gg-code-identity.dylib'
    if sha(binary) != record['sha256']:
        raise ValueError('identity shim changed')
    for entry in record['replacements']:
        target = system/entry['path']
        subprocess.run(['chflags', 'noschg,nouchg,norestricted', target], check=True)
        shutil.copyfile(artifact/'replace'/entry['path'], target)
        target.chmod(0o755)
        subprocess.run(['chflags', 'restricted', target], check=True)
    target = system/'usr/lib/gg-code-identity.dylib'
    shutil.copyfile(binary, target); target.chmod(0o755)
    subprocess.run(['chflags', 'restricted', target], check=True)

if __name__ == '__main__':
    install(*sys.argv[1:])
