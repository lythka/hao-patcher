"""Compare what the binder computes with a recorded repair artifact, binding profile and commpage ABI header.

Each check is one row (what, same, detail). Nothing is tolerated: a row that differs says which field and why.
"""
import hashlib
import json
from pathlib import Path
import plistlib

import kcbind

PATCH_FIELDS = ('Arch', 'Base', 'Comment', 'Count', 'Enabled', 'Find', 'Identifier', 'Limit', 'Mask',
                'MaxKernel', 'MinKernel', 'Replace', 'ReplaceMask', 'Skip')


def describe(value):
    if isinstance(value, bytes):
        return f'{len(value)} bytes sha256 {hashlib.sha256(value).hexdigest()[:16]}'
    return repr(value)


def repairs(patches, capture, kc, efi):
    """Rows for the repair artifact folder `efi` (EFI/OC/config.plist, build.json)."""
    rows = []
    recorded_bytes = (Path(efi)/'EFI/OC/config.plist').read_bytes()
    recorded = plistlib.loads(recorded_bytes)['Kernel']['Patch']
    rows.append(('Kernel.Patch count', len(recorded) == len(patches), f'{len(patches)} computed, {len(recorded)} recorded'))
    for computed, entry in zip(patches, recorded):
        name = entry.get('Comment', '?')
        keys = sorted(set(entry) | set(computed))
        for key in keys:
            a, b = computed.get(key), entry.get(key)
            same = a == b and type(a) is type(b)
            rows.append((f'{name}: {key}', same, describe(b) if same else f'computed {describe(a)}, recorded {describe(b)}'))
        if set(entry) - set(PATCH_FIELDS):
            rows.append((f'{name}: keys', False, 'recorded entry has keys the binder does not know'))
    rows.append(('config.plist bytes', kcbind.config_plist(patches) == recorded_bytes,
                 f'{len(recorded_bytes)} bytes sha256 {hashlib.sha256(recorded_bytes).hexdigest()[:16]}'))
    build = Path(efi)/'build.json'
    if build.exists():
        recorded_capture = json.loads(build.read_text())['inputs']['early_vm_repair']['capture']
        own = recorded_capture['collection_sha256'] == kc.sha256
        for key in sorted(set(capture) | set(recorded_capture)):
            a, b = capture.get(key), recorded_capture.get(key)
            if key == 'collection_sha256' and not own:
                rows.append(('capture: collection_sha256', a == kc.sha256,
                             'this collection (the recorded capture names the bootstrap collection)'))
                continue
            rows.append((f'capture: {key}', a == b, 'same' if a == b else f'computed {a!r}, recorded {b!r}'))
    return rows


def binding(patches, table, kc, profile):
    """Rows for a kernel_repair_binding profile.json: patch sites and the commpage ABI bindings."""
    rows = []
    sites = profile['patches']
    computed = [dict(comment=p['Comment'], identifier=p['Identifier'], offset=kc.data.index(p['Find']),
                     length=len(p['Find']), sha256=kcbind.sha256(p['Find']),
                     replacement_sha256=kcbind.sha256(p['Replace'])) for p in patches]
    own = profile['reference_collection_sha256'] == kc.sha256
    for a, b in zip(computed, sites):
        same = a == b
        rows.append((f'binding patch site: {b["comment"]}', same,
                     f'offset {b["offset"]:#x}' if same else f'computed {a}, recorded {b}'))
    rows.append(('binding patch site count', len(computed) == len(sites), f'{len(computed)} / {len(sites)}'))
    recorded = {int(b['address'], 0): dict(member=b['member'], segment=b['segment'], offset=b['offset'])
                for b in profile['abi_bindings']}
    mine = kcbind.table_addresses(table, kc)
    rows.append(('abi_bindings addresses', set(mine) == set(recorded),
                 f'{len(mine)} computed, {len(recorded)} recorded'))
    for address in sorted(set(mine) & set(recorded)):
        same = mine[address] == recorded[address]
        if not same:
            rows.append((f'abi_binding {address:#x}', False, f'computed {mine[address]}, recorded {recorded[address]}'))
    if own:
        rows.append(('binding reference collection', True, 'this collection is the profile reference'))
    return rows


def abi_header(table, text, foundation):
    """Rows for commpage_abi.hpp: every collection-dependent definition, line for line."""
    rows = []
    recorded = kcbind.header_definitions(text)
    for name, line in kcbind.abi_lines(table, foundation).items():
        same = recorded.get(name) == line
        rows.append((f'commpage_abi.hpp {name}', same,
                     'same line' if same else f'computed {line!r}, recorded {recorded.get(name)!r}'))
    return rows
