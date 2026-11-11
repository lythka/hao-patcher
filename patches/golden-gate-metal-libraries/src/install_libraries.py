"""install_libraries.py SYSTEM ARTIFACT: install the repaired metallibs of ARTIFACT (System-rooted, made by
cache/metallib/make) into the System mount SYSTEM, only if each library on SYSTEM is the one the repair was made from.

ARTIFACT/SOURCES lists the sha256 of the System library each output was made from. A library still matching it is
replaced. One OCLP installed (listed in SYSTEM's OpenCore-Legacy-Patcher.plist, such as the Tahoe RenderBox and
Tungsten libraries) cannot be compared; it is replaced only when SYSTEM's build is the build the artifact was made
from (build.json system_build). Anything else stops before a file is written."""
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def listing(path):
    return {line.split(None, 1)[1]: line.split(None, 1)[0] for line in path.read_text().splitlines() if line.strip()}


def oclp_paths(system):
    record = system/'System/Library/CoreServices/OpenCore-Legacy-Patcher.plist'
    paths = set()
    if record.is_file():
        for patch in plistlib.loads(record.read_bytes()).values():
            if isinstance(patch, dict):
                for operation, folders in patch.items():
                    if operation in ('Merge System Volume', 'Overwrite System Volume'):
                        for folder, entries in folders.items():
                            paths.update(str(Path(folder.lstrip('/'))/name) for name in entries)
    return paths


def plan(system, artifact):
    """[(relative, note)] to install, or SystemExit naming what does not match."""
    if not (artifact/'SOURCES').is_file():
        sys.exit(f'MISSING/unsupported: {artifact} has no SOURCES (made before cache/metallib r53); rebuild it')
    sources, outputs = listing(artifact/'SOURCES'), listing(artifact/'SHA256SUMS')
    shipped = {str(p.relative_to(artifact)) for p in (artifact/'System').rglob('*.metallib')}
    if not (set(sources) == set(outputs) == shipped):
        sys.exit(f'MISSING/unsupported: {artifact}: SOURCES, SHA256SUMS and the shipped libraries differ')
    made_from = json.loads((artifact/'build.json').read_text()).get('system_build')
    build = plistlib.loads((system/'System/Library/CoreServices/SystemVersion.plist').read_bytes())['ProductBuildVersion']
    oclp, steps, errors = oclp_paths(system), [], []
    for relative, expected in sorted(sources.items()):
        if sha256(artifact/relative) != outputs[relative]:
            errors.append(f'MISSING/unsupported: {artifact/relative} does not match SHA256SUMS')
        elif not (system/relative).is_file():
            errors.append(f'MISSING/unsupported: /{relative} is not on the System')
        elif (found := sha256(system/relative)) == expected:
            steps.append((relative, 'source matches'))
        elif relative in oclp and made_from == build:
            steps.append((relative, f'replaces OCLP\'s copy; made from {build}'))
        else:
            errors.append(f'MISSING/unsupported: /{relative} is {found}; the repair was made from {expected} '
                          f'(build {made_from}, System {build})')
    if errors:
        sys.exit('\n'.join(errors))
    return steps


def main(system, artifact):
    for relative, note in plan(system, artifact):
        target = system/relative
        subprocess.run(['install', '-d', '-m', '755', str(target.parent)], check=True)
        subprocess.run(['install', '-o', '0', '-g', '0', '-m', '644', str(artifact/relative), str(target)], check=True)
        print(f'golden-gate-metal-libraries: installed /{relative} ({note})', flush=True)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]).resolve())
