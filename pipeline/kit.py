#!/usr/bin/env python3
"""The relocatable kit (workflow plan step 16): the folder golden-run stages (its "share"), usable wherever the
assembly runs, with no builder VM: the arm64 builder VM, an Intel Mac building onto an external SSD (hao build-disk),
or the target's Recovery installing onto its internal disk (hao install).

KIT/kit.json holds the assembler configuration with two placeholders instead of absolute paths: ${KIT} (the kit
folder; the assembly writes only KIT/cryptex-cache/out, the sealed cryptex images it made for reuse:
`golden-run --collect-kit RECORD` keeps them in the store) and ${LOCAL} (a writable folder on a disk the assembly does not erase, holding its
local copies of the source, Python, hooks and posts, and its work folder). It names no target: `render` adds either
the raw image (LOCAL/target.img) or the whole disk to erase.

    kit.py render KIT LOCAL --out CONFIG [--disk diskN --confirm-disk diskN [--external]]
    kit.py python-dir [MACHINE]        # the kit folder holding the CPython for this Mac (py, py-x86_64)

Python 3.9+ only (it runs under the kit's own CPython, or a host's older python3)."""
import argparse
import json
import platform
import sys
from pathlib import Path

SCHEMA = 1
KIT = '${KIT}'
LOCAL = '${LOCAL}'
# The kit's CPython folder per machine; golden-assemble.sh copies the right one to LOCAL/py, so the configuration's
# python (LOCAL/py/bin/python3.12) is the same on every Mac.
PYTHON_DIRS = {'arm64': 'py', 'x86_64': 'py-x86_64'}


def python_dir(machine=None):
    machine = machine or platform.machine()
    if machine not in PYTHON_DIRS:
        raise ValueError('no kit Python for machine %r (kits carry %s)' % (machine, ', '.join(sorted(PYTHON_DIRS))))
    return PYTHON_DIRS[machine]


def template(config, assembly, kit_root, local):
    """kit.json's content: CONFIG (built with KIT_ROOT and LOCAL as its two roots) with the roots replaced by the
    placeholders and its target removed. ASSEMBLY names what it builds."""
    for key in ('image', 'disk', 'confirm_disk'):
        config = {k: v for k, v in config.items() if k != key}

    def swap(value):
        if isinstance(value, dict):
            return {k: swap(v) for k, v in value.items()}
        if isinstance(value, list):
            return [swap(v) for v in value]
        if isinstance(value, str):
            for root, token in ((local, LOCAL), (kit_root, KIT)):   # LOCAL first: it may lie inside the kit root
                if value == root or value.startswith(root + '/'):
                    return token + value[len(root):]
        return value
    rendered = swap(config)
    left = [v for v in strings(rendered) if v.startswith('/') and not v.startswith(('/usr/', '/bin/', '/System/'))]
    if left:
        raise ValueError('kit configuration keeps absolute paths outside the kit: ' + ', '.join(sorted(set(left))))
    return dict(schema=SCHEMA, assembly=assembly, python_dirs=PYTHON_DIRS, config=rendered)


def strings(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)
    elif isinstance(value, str):
        yield value


def render(kit_json, kit, local, disk=None, confirm_disk=None, disk_policy='internal'):
    """The assembler configuration for a kit at KIT assembling in LOCAL: the raw image LOCAL/target.img, or the
    whole DISK (CONFIRM_DISK must name it again; golden-image.py's DiskTarget runs the installer's disk checks under
    DISK_POLICY: internal for hao install, the Mac's own disk; external for hao build-disk, an external SSD)."""
    if kit_json.get('schema') != SCHEMA:
        raise ValueError('kit.json schema %r, this kit.py reads %d' % (kit_json.get('schema'), SCHEMA))
    kit, local = str(kit).rstrip('/') or '/', str(local).rstrip('/') or '/'
    if local == kit or local.startswith(kit + '/'):
        raise ValueError('LOCAL %s is inside the kit %s; the kit is read-only to the assembly' % (local, kit))

    def fill(value):
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v) for v in value]
        if isinstance(value, str):
            return value.replace(LOCAL, local).replace(KIT, kit)
        return value
    config = fill(kit_json['config'])
    if disk:
        if not confirm_disk:
            raise ValueError('a disk target needs --confirm-disk naming the same disk')
        if disk_policy not in ('internal', 'external'):
            raise ValueError('disk policy %r: internal or external' % disk_policy)
        config.update(disk=disk, confirm_disk=confirm_disk, disk_policy=disk_policy)
    else:
        config['image'] = local + '/target.img'
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    r = sub.add_parser('render', help='write the assembler configuration for KIT assembling in LOCAL')
    r.add_argument('kit', type=Path)
    r.add_argument('local', type=Path)
    r.add_argument('--out', type=Path, required=True)
    r.add_argument('--disk')
    r.add_argument('--confirm-disk')
    r.add_argument('--external', action='store_true', help='the disk is an external SSD (hao build-disk)')
    p = sub.add_parser('python-dir', help='the kit folder with the CPython for MACHINE (default: this Mac)')
    p.add_argument('machine', nargs='?')
    a = parser.parse_args(argv)
    try:
        if a.command == 'python-dir':
            print(python_dir(a.machine))
            return 0
        config = render(json.loads((a.kit / 'kit.json').read_text()), a.kit, a.local, a.disk, a.confirm_disk,
                        'external' if a.external else 'internal')
    except ValueError as error:
        print('kit.py: ' + str(error), file=sys.stderr)
        return 2
    a.out.write_text(json.dumps(config, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
