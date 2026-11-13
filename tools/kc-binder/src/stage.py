"""Copy what `kc-binder bind` needs at run time into a new folder, at the same repository-relative paths.

  stage.py OUT

OUT/tools/kc-binder/{kc-binder, src/kcbind.py, src/compare.py, recipes/*.json} and every file a recipe pins
(kc-recipe.json, the PV blob), plus tools/kernel-foundation/commpage_build.py (the user_trap and csr shape checks).
OUT/stage.json lists each file with its sha256, so a consumer can check the copy before it runs it. The binder then
runs from OUT exactly as from the checkout: recipes name their pins relative to the repository root, which is OUT.

The image build stages the binder this way for the builder VM (patches/hao-opencore/make.sh). Standard library
only, and kept to Python 3.9 so the host's /usr/bin/python3 can run it.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
UNIT = HERE.parent
REPO = UNIT.parents[1]
RUNTIME = ('tools/kc-binder/kc-binder', 'tools/kc-binder/src/kcbind.py', 'tools/kc-binder/src/compare.py',
           'tools/kernel-foundation/commpage_build.py')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def closure(repo=REPO):
    """Repository-relative paths of every file the binder reads when it binds, in a fixed order."""
    paths = list(RUNTIME)
    for recipe in sorted((repo/'tools/kc-binder/recipes').glob('*.json')):
        paths.append(str(recipe.relative_to(repo)))
        binder = json.loads(recipe.read_text())
        for key in ('kc_recipe', 'pv_blob'):
            pin = binder[key]
            if sha256((repo/pin['path']).read_bytes()) != pin['sha256']:
                raise ValueError('%s: %s differs from the sha256 the recipe names' % (recipe.name, pin['path']))
            paths.append(pin['path'])
    return list(dict.fromkeys(paths))


def stage(out, repo=REPO):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    files = {}
    for relative in closure(repo):
        target = out/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo/relative, target)
        if relative == 'tools/kc-binder/kc-binder':
            target.chmod(0o755)
        files[relative] = sha256(target.read_bytes())
    record = dict(unit='tools/kc-binder', operation='stage', files=files)
    (out/'stage.json').write_text(json.dumps(record, indent=1, sort_keys=True)+'\n')
    return record


def verify(out):
    """The stage.json record of OUT, after checking every listed file against it (ValueError otherwise)."""
    out = Path(out)
    record = json.loads((out/'stage.json').read_text())
    for relative, digest in sorted(record['files'].items()):
        if sha256((out/relative).read_bytes()) != digest:
            raise ValueError('staged binder file %s differs from stage.json' % relative)
    return record


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    stage(sys.argv[1])
    print(sys.argv[1])
