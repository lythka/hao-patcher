"""Artifact store: every edited file kept by content, inspectable next to its original, reused when hashes match.

  STORE/objects/<sha256>                    content-addressed bytes (originals and results, written once)
  STORE/results/<key>.json                  one apply: key, input/output sha256, the full record
  STORE/view/<path>/original, patched       links to the objects, side by side for inspection
  STORE/view/<path>/record.json, explain.txt

key = sha256 over the engine version, the canonical spec, the input sha256 and the source of every matcher
function the spec names. A result is reused only when its output object still hashes to its recorded sha256;
anything else is applied again (the earlier object stays in objects/ for comparison).
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile

ENGINE_VERSION = 1


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def key(spec, input_sha, repo):
    parts = ['edit-engine/%d' % ENGINE_VERSION, sha256(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()),
             input_sha]
    for e in spec['edits']:
        if e.get('kind') == 'matcher' and 'fn' in e:
            source = Path(repo) / e['fn'].rsplit(':', 1)[0]
            parts.append('%s=%s' % (e['fn'], sha256(source.read_bytes()) if source.is_file() else 'missing'))
    return sha256('\n'.join(parts).encode())


def _write_once(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


class Store:
    def __init__(self, root):
        self.root = Path(root)

    def object_path(self, digest):
        return self.root / 'objects' / digest

    def put(self, data):
        digest = sha256(data)
        path = self.object_path(digest)
        if not (path.is_file() and sha256(path.read_bytes()) == digest):
            _write_once(path, data)
        return digest

    def get(self, digest):
        """The object's bytes, or None when it is missing or no longer hashes to its name."""
        path = self.object_path(digest)
        if not path.is_file():
            return None
        data = path.read_bytes()
        return data if sha256(data) == digest else None

    def lookup(self, k):
        """(output bytes, record) of an earlier apply with key K whose output is intact, else None."""
        path = self.root / 'results' / ('%s.json' % k)
        if not path.is_file():
            return None
        try:
            result = json.loads(path.read_text())
        except ValueError:
            return None
        data = self.get(result.get('output_sha256', ''))
        if data is None:
            return None
        return data, result['record']

    def save(self, k, spec, original, output, record, explain_lines):
        self.put(original)
        self.put(output)
        result = {'key': k, 'engine_version': ENGINE_VERSION, 'path': spec['path'],
                  'input_sha256': record['input_sha256'], 'output_sha256': record['output_sha256'], 'record': record}
        _write_once(self.root / 'results' / ('%s.json' % k), (json.dumps(result, indent=1) + '\n').encode())
        self.view(spec['path'], record, explain_lines)

    def view(self, path, record, explain_lines):
        """STORE/view/PATH/{original,patched,record.json,explain.txt}: the newest apply for that path."""
        folder = self.root / 'view' / path
        folder.mkdir(parents=True, exist_ok=True)
        for name, digest in (('original', record['input_sha256']), ('patched', record['output_sha256'])):
            link = folder / name
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(os.path.relpath(self.object_path(digest), folder))
        _write_once(folder / 'record.json', (json.dumps(record, indent=1) + '\n').encode())
        _write_once(folder / 'explain.txt', ('\n'.join(explain_lines) + '\n').encode())


def changed_ranges(a, b, block=4096):
    """[(start, end)] where A and B differ, merged; past the shorter file everything counts as changed."""
    out = []
    n = min(len(a), len(b))
    for base in range(0, n, block):
        if a[base:base + block] == b[base:base + block]:
            continue
        for i in range(base, min(base + block, n)):
            if a[i] != b[i]:
                if out and out[-1][1] == i:
                    out[-1] = (out[-1][0], i + 1)
                else:
                    out.append((i, i + 1))
    if len(a) != len(b):
        if out and out[-1][1] == n:
            out[-1] = (out[-1][0], max(len(a), len(b)))
        else:
            out.append((n, max(len(a), len(b))))
    return out
