"""Spike (c): build and seal a cryptex image in the Recovery as golden-gate-userspace does. Runs ON THE LAPTOP.
   cryptex_spike.py T [FILL_MB]
Calls patches/golden-gate-userspace/src/install.py itself (new_image, image_mount, replace_tree, seal_image), the
steps of its App cryptex rebuild (install_app_cryptex without the hao stubs): Golden Gate's app.dmg mounted
read-only, a fresh raw APFS image (newfs_apfs -i) sized from it, its files copied whole, the root's mode and flags
kept, then apfs_prepare_cryptex and the Sealed check. FILL_MB MB of random files are added before sealing so the
seal time can be scaled to the ~4-5 GB combined OS cryptex. Two runs: with the Recovery's own APFS tools
("recovery"), then with Tahoe 25G332's newfs_apfs + apfs_prepare_cryptex carried as inputs ("carried"). Each
sealed image is mounted read-only again and compared with what was written. All on the RAM disk T."""
import importlib.util
import os
from pathlib import Path
import stat
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spikelib import Spike, main_guard, sha256   # noqa: E402

FILL_BLOCK = 16 << 20


def load_install(code):
    path = code/'patches/golden-gate-userspace/src/install.py'
    spec = importlib.util.spec_from_file_location('gg_install', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def listing(root, skip):
    """relative path -> (kind, size) for everything under ROOT except the top-level names in SKIP."""
    out = {}
    for folder, dirs, names in os.walk(root):
        rel = os.path.relpath(folder, root)
        if rel == '.':
            dirs[:] = [d for d in dirs if d not in skip]
            names = [n for n in names if n not in skip]
        for name in dirs+names:
            p = Path(folder)/name
            info = p.lstat()
            kind = 'l' if stat.S_ISLNK(info.st_mode) else 'd' if stat.S_ISDIR(info.st_mode) else 'f'
            out[os.path.normpath(os.path.join(rel, name))] = (kind, info.st_size if kind == 'f' else 0,
                                                             stat.S_IMODE(info.st_mode), info.st_uid)
    return out


def variant(s, install, label, app, fill_mb):
    resources = Path(install.APFS_RESOURCES)
    absent = [t for t in ('newfs_apfs', 'apfs_prepare_cryptex') if not (resources/t).exists()]
    if absent:
        s.check('seal:'+label, False, '%s absent from %s' % (' '.join(absent), resources))
        s.check('readback:'+label, None, 'not sealed')
        return
    s.info('tools:'+label, 'newfs_apfs %s, apfs_prepare_cryptex %s' % (
        sha256(resources/'newfs_apfs')[:16], sha256(resources/'apfs_prepare_cryptex')[:16]))
    mnt = s.scratch/'mnt'
    work = s.scratch/('cryptex-%s.dmg' % label)
    fills = {}
    with install.image_mount(app, mnt/('golden-app-%s' % label)) as source:
        size = install.logical_size(source)
        s.result('app_cryptex_bytes', size)
        with s.timed('new-image:'+label):
            install.new_image(work, size+(256 << 20)+(fill_mb << 20), source)
        metadata = source.stat()
        with install.image_mount(work, mnt/('combined-%s' % label), writable=True) as mount:
            with s.timed('copy-tree:'+label):
                install.replace_tree(source, mount, install.CRYPTEX_KEEP)
            if fill_mb:
                folder = mount/'hao-spike-filler'
                folder.mkdir(mode=0o755)
                for i in range(fill_mb*(1 << 20)//FILL_BLOCK):
                    p = folder/('fill-%02d' % i)
                    p.write_bytes(os.urandom(FILL_BLOCK))
                    fills['hao-spike-filler/'+p.name] = sha256(p)
            os.chmod(mount, stat.S_IMODE(metadata.st_mode))
            os.chflags(mount, metadata.st_flags)
        start = time.monotonic()
        try:
            seal = install.seal_image(work)
        except Exception as error:
            s.check('seal:'+label, False, '%s: %s' % (type(error).__name__, error))
            s.check('readback:'+label, None, 'not sealed')
            work.unlink(missing_ok=True)
            return
        seconds = time.monotonic()-start
        s.emit('TIME', 'seal:'+label, '%.1f' % seconds)
        total = size+len(fills)*FILL_BLOCK
        s.result('seal_mb_s:'+label, '%.0f' % (total/(1 << 20)/seconds if seconds else 0))
        s.check('seal:'+label, True, 'Sealed after apfs_prepare_cryptex; %s' % seal)
        want = listing(source, install.CRYPTEX_KEEP)
        with install.image_mount(work, mnt/('readback-%s' % label)) as sealed:
            have = listing(sealed, install.CRYPTEX_KEEP | {'hao-spike-filler'})
            bad = [k for k, v in fills.items() if sha256(sealed/k) != v]
        missing = sorted(set(want) ^ set(have))[:5]
        changed = sorted(k for k in set(want) & set(have) if want[k] != have[k])[:5]
        s.check('readback:'+label, not (missing or changed or bad),
                '%d entries as copied, %d filler files intact' % (len(want), len(fills)-len(bad)) if not
                (missing or changed or bad) else 'missing/extra %s changed %s bad filler %s' % (missing, changed, bad))
    work.unlink(missing_ok=True)


def body(s):
    fill_mb = int(sys.argv[2]) if len(sys.argv) > 2 else 256
    install = load_install(s.scratch/'code')
    inputs = s.scratch/'inputs'
    app = inputs/'golden_app_cryptex.dmg'
    with s.timed('variant:recovery'):
        variant(s, install, 'recovery', app, fill_mb)
    for tool in ('newfs_apfs', 'apfs_prepare_cryptex'):
        os.chmod(inputs/tool, 0o755)
    install.APFS_RESOURCES = str(inputs)
    with s.timed('variant:carried'):
        variant(s, install, 'carried', app, fill_mb)


if __name__ == '__main__':
    main_guard(Spike('cryptex', sys.argv[1]), body)
