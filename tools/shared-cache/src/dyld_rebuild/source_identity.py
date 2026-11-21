"""Identify installed producer source without requiring Git or developer tools."""
import hashlib
from pathlib import Path
import re


def head(root):
    """Best-effort checkout label, never a claim that files equal this commit."""
    root = Path(root).resolve()
    for folder in (root, *root.parents):
        gitdir = folder/'.git'
        if gitdir.is_file():
            text = gitdir.read_text().strip()
            if not text.startswith('gitdir: '):
                return None
            gitdir = (folder/text[8:]).resolve()
        if not gitdir.is_dir():
            continue
        common = gitdir
        if (gitdir/'commondir').is_file():
            common = (gitdir/(gitdir/'commondir').read_text().strip()).resolve()
        try:
            value = (gitdir/'HEAD').read_text().strip()
            if value.startswith('ref: '):
                ref = value[5:]
                value = ''
                for base in (gitdir, common):
                    path = base/ref
                    if path.is_file():
                        value = path.read_text().strip()
                        break
                if not value and (common/'packed-refs').is_file():
                    for line in (common/'packed-refs').read_text().splitlines():
                        if line.endswith(' '+ref):
                            value = line.split()[0]
                            break
            return value if re.fullmatch('[0-9a-f]{40,64}', value) else None
        except OSError:
            return None
    return None


def content_hash(root):
    """Hash relative paths and bytes; checkout location and Python caches do not count."""
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if not path.is_file() or '__pycache__' in relative.parts or path.suffix == '.pyc':
            continue
        digest.update(relative.as_posix().encode()+b'\0')
        with path.open('rb') as stream:
            digest.update(hashlib.file_digest(stream, 'sha256').digest())
    return digest.hexdigest()
