"""hao detect: the Mac's hardware, the OCLP patch sets it needs and the packs they select, as a profile file.

Runs on the target (its Recovery or an installed macOS), read-only: sysctl for the model and CPU, ioreg for the board
id and the PCI devices (GPU, Wi-Fi, audio). Patch sets come from, in order:

  1. a recorded OCLP profile for the model (tools/oclp-hardware/profiles/*.json, made by OCLP's own detection on that
     model: patchsets_source "recorded OCLP profile ..."), else
  2. data/detect-rules.json: PCI vendor/device -> patch sets, covering the proven laptop's devices only
     (patchsets_source "device rules"); a device no rule knows is listed under "unknown" and named in a note.

Packs then follow from packs/*.json (pipeline/patches.py select_packs). A profile also answers `--model MODEL` without
the Mac attached (hao build-disk / build-image): the recorded profile of that model and its target's board id.
"""
import json
from pathlib import Path
import plistlib
import runpy

TOP = Path(__file__).resolve().parents[3]
PROFILES = TOP / 'tools' / 'oclp-hardware' / 'profiles'
TARGETS = TOP / 'pipeline' / 'targets'
RULES = Path(__file__).resolve().parents[1] / 'data' / 'detect-rules.json'
ROLES = {'0300': 'gpu', '0380': 'gpu', '0280': 'wifi', '0403': 'audio', '0200': 'ethernet'}


class DetectError(RuntimeError):
    pass


def _patches():
    return runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))


def _str(v):
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).split(b'\0', 1)[0].decode(errors='replace')
    return v if isinstance(v, str) else None


def _le(v):
    """An ioreg data property (little-endian, as vendor-id and class-code are) as an int."""
    if isinstance(v, (bytes, bytearray)):
        return int.from_bytes(bytes(v)[:4], 'little')
    return int(v) if isinstance(v, int) else None


def _plist(host, *argv):
    r = host.run(list(argv))
    try:
        return plistlib.loads(r.stdout)
    except Exception:
        raise DetectError('%s: output is not a plist' % ' '.join(argv)) from None


def _text(host, *argv):
    return host.run(list(argv)).stdout.decode(errors='replace').strip()


def facts(host):
    """What the running Mac reports: model, board id, CPU, memory and its PCI devices."""
    model = _text(host, 'sysctl', '-n', 'hw.model')
    cpu = _text(host, 'sysctl', '-n', 'machdep.cpu.brand_string')
    mem = _text(host, 'sysctl', '-n', 'hw.memsize')
    pe = _plist(host, 'ioreg', '-a', '-r', '-d', '1', '-c', 'IOPlatformExpertDevice')
    pe = pe[0] if isinstance(pe, list) and pe else pe if isinstance(pe, dict) else {}
    devices = []
    for d in _plist(host, 'ioreg', '-a', '-r', '-c', 'IOPCIDevice') or []:
        vendor, device, cls = _le(d.get('vendor-id')), _le(d.get('device-id')), _le(d.get('class-code'))
        if vendor is None or device is None or cls is None:
            continue
        cls4 = '%04x' % ((cls >> 8) & 0xffff)
        devices.append({'vendor': '%04x' % (vendor & 0xffff), 'device': '%04x' % (device & 0xffff), 'class': cls4,
                        'role': ROLES.get(cls4), 'name': _str(d.get('model')) or _str(d.get('IOName')) or
                        _str(d.get('name'))})
    return {'model': model, 'board_id': _str(pe.get('board-id')), 'cpu': cpu,
            'memory_bytes': int(mem) if mem.isdigit() else None, 'machine': _text(host, 'uname', '-m'),
            'devices': devices}


def recorded_profile(model):
    """(name, profile) of the recorded OCLP profile for MODEL, or (None, None)."""
    for p in sorted(PROFILES.glob('*.json')):
        d = json.loads(p.read_text())
        if d.get('model') == model:
            return p.stem, d
    return None, None


def target_for_model(model):
    """The pipeline target (targets/*.json) of MODEL: its board id and the name the assemblies use."""
    for p in sorted(TARGETS.glob('*.json')):
        d = json.loads(p.read_text())
        if d.get('model') == model:
            return p.stem, d
    return None, None


def rule_patchsets(devices, rules=None):
    """(patch sets in rule order, [(device, rule)], [unknown devices of a role that matters])."""
    rules = rules if rules is not None else json.loads(RULES.read_text())['rules']
    out, matched, unknown = [], [], []
    for dev in devices:
        hit = next((r for r in rules if r['class'] == dev['class'] and r['vendor'] == dev['vendor']
                    and dev['device'] in r['devices']), None)
        if hit:
            matched.append((dev, hit))
            out += [s for s in hit['patchsets'] if s not in out]
        elif dev['role'] in ('gpu', 'wifi', 'audio'):
            unknown.append(dev)
    return out, matched, unknown


def profile(f, packs=None, rules=None):
    """The profile hao writes: FACTS plus patch sets, their source, packs and whether the model is proven."""
    packs = packs if packs is not None else _patches()['load_packs']()
    name, rec = recorded_profile(f['model'])
    notes = []
    _, matched, unknown = rule_patchsets(f.get('devices', []), rules)
    if rec:
        patchsets = list(rec['patchset_order'])
        source = 'recorded OCLP profile %s (OCLP %s)' % (name, rec.get('oclp_version'))
    else:
        patchsets, _, _ = rule_patchsets(f.get('devices', []), rules)
        source = 'device rules (tools/hao/data/detect-rules.json)'
        notes.append('no recorded OCLP profile for %s: patch sets come from device rules, not OCLP' % f['model'])
    for dev in unknown:
        notes.append('no rule for %s %s:%s (%s): its hardware gets no pack' % (dev['role'], dev['vendor'],
                                                                              dev['device'], dev.get('name')))
    if f.get('machine') not in (None, 'x86_64'):
        notes.append('this Mac is %s, not an Intel Mac: the profile is only useful for testing' % f['machine'])
    chosen = _patches()['select_packs'](patchsets, f.get('board_id'), packs)
    if f.get('machine') not in (None, 'x86_64'):     # "any_intel" means an Intel Mac
        chosen = [n for n in chosen if not packs[n].get('detect', {}).get('any_intel')]
    proven = {n: f['model'] in packs[n].get('proven_on', []) for n in chosen}
    if chosen and not all(proven.values()):
        notes.append('packs not proven on %s: %s' % (f['model'], ', '.join(n for n, ok in proven.items() if not ok)))
    return {'format': 1, 'model': f['model'], 'board_id': f.get('board_id'), 'cpu': f.get('cpu'),
            'memory_bytes': f.get('memory_bytes'), 'machine': f.get('machine'),
            'gpu': [d for d in f.get('devices', []) if d['role'] == 'gpu'],
            'wifi': [d for d in f.get('devices', []) if d['role'] == 'wifi'],
            'audio': [d for d in f.get('devices', []) if d['role'] == 'audio'],
            'matched': [{'device': '%s:%s' % (d['vendor'], d['device']), 'role': d['role'], 'family': r['family'],
                         'patchsets': r['patchsets']} for d, r in matched],
            'oclp_patchsets': patchsets, 'patchsets_source': source, 'packs': chosen, 'proven': proven,
            'proven_model': bool(chosen) and all(proven.values()), 'notes': notes}


def for_model(model, packs=None):
    """A profile for MODEL without the Mac: its recorded OCLP profile and its target's board id (build-disk --model)."""
    name, rec = recorded_profile(model)
    tname, target = target_for_model(model)
    if not rec or not target:
        raise DetectError('no recorded profile for %s: run `hao detect` on that Mac and pass --profile' % model)
    f = {'model': model, 'board_id': target.get('board_id'), 'cpu': None, 'memory_bytes': None, 'machine': 'x86_64',
         'devices': []}
    p = profile(f, packs)
    p['target'] = tname
    p['from'] = 'model'
    p['gpu_name'] = target.get('gpu')
    return p


def load(path):
    p = json.loads(Path(path).read_text())
    if p.get('format') != 1 or not p.get('model') or not isinstance(p.get('packs'), list):
        raise DetectError('%s is not a hao detect profile' % path)
    if not p.get('target'):
        p['target'] = target_for_model(p['model'])[0]
    return p


def show(c, p):
    """Print a profile on console C at its level (the mockup's detection table at --show-edits)."""
    c.event('detect', profile=p)
    gpu = ', '.join(d.get('name') or d['device'] for d in p['gpu']) or p.get('gpu_name') or 'GPU unknown'
    c.print('Mac    %s  %s  board %s' % (p['model'], gpu,
                                         p.get('board_id')))
    c.print('Packs  %s' % ' · '.join(p['packs']))
    for m in p.get('matched', []):
        c.print('  %-6s %-10s -> %-34s -> %s' % (m['role'], m['device'], m['family'], ', '.join(m['patchsets'])),
                'edits')
    c.print('  patch sets from %s' % p['patchsets_source'], 'files')
    for n in p['notes']:
        c.note(n)
