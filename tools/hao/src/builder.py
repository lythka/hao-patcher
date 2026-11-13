"""hao build-image, build-disk, create-installer, install: one plan, four places it runs (workflow plan, "One assembler,
three contexts").

Each command resolves the same things first: the Mac (a hao detect profile, or --model and its recorded profile), its
packs, the assembly (pipeline/assemblies: target, Golden Gate and donor builds, set) and the recipes each pack brings
(pipeline/patches.py plan --packs). Then it shows its steps at the console's level and, without --dry-run, runs them:

  build-image       materialise the overlays and assemble an image in the builder VM (pipeline/golden-run
                    --from-overlays), committed to an image repository. Development and CI.
  build-disk        output (b): the same assembly onto an external SSD (disks.py "external" gate)
  create-installer  output (a): the USB stick, partition 1 OpenCore + Recovery with the launcher, partition 2 the kit
  install           on the target, from the stick: the assembly onto the internal disk (disks.py "internal" gate)

What runs for real today: build-image (through the VM), and every command's checks and plan (--dry-run). The disk
steps of build-disk, create-installer and install need the relocatable kit (golden-run's staged share and guest config
with paths relative to the kit, and an x86_64 Python) that step 16 makes; until then they stop after their checks and
say so, before anything is written.
"""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import contextlib
import io

from console import Stopped
import detect as DETECT
import disks as DISKS

TOP = Path(__file__).resolve().parents[3]
ASSEMBLIES = TOP / 'pipeline' / 'assemblies'

# The step lists of the console mockups (Hao Console Variations), per command.
HOST_STEPS = ('Download', 'Unpack', 'Prepare packs')
DISK_STEPS = ('Lay out the disk', 'Copy macOS', 'Add Intel support from Tahoe', 'Apply packs', 'Link the kernel', 'Seal',
              'Make it bootable')
STEPS = {
    'build-image': ('Prepare packs', 'Assemble in the builder VM', 'Commit the image'),
    'build-disk': HOST_STEPS + DISK_STEPS,
    'create-installer': HOST_STEPS + ('Write the USB stick',),
    'install': DISK_STEPS,
}
# What each disk step is, in golden-image.py's terms (LAYERS, then the set, then snapshot/posts/EFI).
DISK_DETAIL = {
    'Lay out the disk': 'APFS container, System + Data volume group, firmlinks, Preboot, Recovery',
    'Copy macOS': 'Golden Gate System and Data template',
    'Add Intel support from Tahoe': 'kernel, kexts, DriverKit and boot files (donors), OCLP hardware overlay',
    'Apply packs': 'each overlay file checked against its manifest',
    'Link the kernel': 'kernel collections with Tahoe\'s kmutil, then kc-binder binds the kernel repairs',
    'Seal': 'cryptex container and seal, snapshot',
    'Make it bootable': 'OpenCore to the EFI partition, bless',
}
NOT_WIRED = ('the disk steps need the relocatable kit (golden-run\'s staged inputs and guest config with paths '
             'relative to the kit, and an x86_64 Python for the target), workflow plan step 16')


def _quiet(fn, *args, **kw):
    """Call FN with its stderr chatter (patches.py prints skipped recipes) kept out of the console."""
    with contextlib.redirect_stderr(io.StringIO()):
        return fn(*args, **kw)


def mac(a):
    """The profile a command works for: --profile FILE (hao detect), --model MODEL, or this Mac (install)."""
    if getattr(a, 'profile_data', None) is not None:
        return a.profile_data
    if getattr(a, 'profile', None):
        return DETECT.load(a.profile)
    if getattr(a, 'model', None):
        return DETECT.for_model(a.model)
    raise Stopped('no Mac named', why='packs and the OpenCore configuration follow from the Mac',
                  options=['name it: --model MacBookPro9,2', 'or run `hao detect --out mac.json` on it and pass '
                           '--profile mac.json'])


def assembly(name, profile, golden=None):
    """(path, assembly) for --assembly NAME, else the one for the profile's target (TARGET-GOLDEN, no dev recipes)."""
    if name:
        p = Path(name) if Path(name).is_file() else ASSEMBLIES / (name + '.json')
        if not p.is_file():
            raise Stopped('no assembly %s' % name, options=['one of: ' + ', '.join(sorted(x.stem for x in
                                                                                         ASSEMBLIES.glob('*.json')))])
        a = json.loads(p.read_text())
        if profile.get('target') and a.get('target') != profile['target']:
            raise Stopped('assembly %s builds for target %s, the Mac is %s' % (p.stem, a.get('target'),
                                                                               profile['target']))
        return p, a
    found = []
    for p in sorted(ASSEMBLIES.glob('*.json')):
        a = json.loads(p.read_text())
        if a.get('runner', 'golden') == 'golden' and a.get('target') == profile.get('target') and \
                p.stem == '%s-%s' % (a['target'], golden or a['golden_build']):
            found.append((p, a))
    if len(found) != 1:
        raise Stopped('no single release assembly for %s' % profile['model'],
                      options=['name one: --assembly NAME (pipeline/assemblies)'])
    return found[0]


def recipes_by_pack(asm, profile):
    """{pack: [recipe, ...]} of the assembly's set with the profile's packs, plus "(no pack)" for the rest."""
    patches = runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))
    defs = patches['load_packs']()
    owner = {r: n for n, d in defs.items() for r in d.get('recipes', [])}
    rows = _quiet(lambda: list(patches['plan'](TOP / 'pipeline/sets' / (asm['patch_set'] + '.txt'),
                                               TOP / 'pipeline/targets' / (asm['target'] + '.json'),
                                               asm['donor_build'], packs=profile['packs'])))
    out = {n: [] for n in profile['packs']}
    for stage, name, _ in rows:
        out.setdefault(owner.get(name, '(no pack)'), []).append(name)
    return out


def resolve(a, c, command):
    """Profile, assembly and recipes, shown on C; the common head of every builder command."""
    profile = mac(a)
    path, asm = assembly(getattr(a, 'assembly', None), profile)
    groups = recipes_by_pack(asm, profile)
    proven = 'proven model %s' % c.ok if profile.get('proven_model') else 'not proven on this model'
    c.banner('hao %s  macOS 27 %s for %s  (%s)' % (command, asm['golden_build'], profile['model'], proven),
             command=command, assembly=path.stem, model=profile['model'], packs=profile['packs'])
    DETECT.show(c, profile)
    c.print('Recipes  %d in %d packs, set %s, donors %s' % (sum(len(v) for v in groups.values()), len(groups),
                                                            asm['patch_set'], asm['donor_build']), 'files')
    for pack, names in groups.items():
        c.print('  %-22s %s' % (pack, ', '.join(names) or '-'), 'edits')
    return profile, path, asm, groups


def gate(c, host, a, policy, minimum):
    """The disk checks; returns the accepted disk's description, or stops."""
    if not a.disk:
        raise Stopped('no disk named', options=['name the whole disk: /dev/diskN (diskutil list external)'])
    why = DISKS.refusal(host, a.disk, policy, minimum, sources=[s for s in getattr(a, 'sources', []) if s])
    if why:
        raise Stopped('/dev/%s may not be erased: %s' % (DISKS.normalize(a.disk), why),
                      why='hao erases only a disk it can tell is the one you mean',
                      untouched='nothing was written to any disk')
    info, _ = DISKS.describe(host, a.disk)
    c.event('disk', disk=DISKS.normalize(a.disk), policy=policy, size=info.get('TotalSize') or info.get('Size'))
    return info


def show_steps(c, command, groups):
    steps = STEPS[command]
    c.steps(len(steps))
    for title in steps:
        c.step(title, plan=True)
        if title == 'Prepare packs':
            for pack, names in groups.items():
                c.item('%-22s %d recipes' % (pack, len(names)), 'steps', pack=pack, recipes=names)
        elif title in DISK_DETAIL:
            c.item(DISK_DETAIL[title], 'files')


def golden_run_command(path, repo, overlay_system=None, store=None):
    cmd = [sys.executable, '-I', str(TOP / 'pipeline' / 'golden-run'), str(path), '--repo', str(repo)]
    if overlay_system:
        cmd += ['--from-overlays', '--overlay-system', str(overlay_system)]
        if store:
            cmd += ['--overlay-store', str(store)]
    return cmd


def build_image(a, c, run=subprocess.call):
    profile, path, asm, groups = resolve(a, c, 'build-image')
    patches = runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))
    full = _quiet(patches['detect_packs'], str(TOP / 'pipeline/targets' / (asm['target'] + '.json')))
    if sorted(full) != sorted(profile['packs']):
        raise Stopped('golden-run builds every pack its target detects (%s), not %s' % (
            ', '.join(full), ', '.join(profile['packs'])), why='choosing packs per build is not wired into golden-run',
            options=['build the detected packs (drop --profile, or use --model)'])
    repo = Path(a.out).resolve()
    show_steps(c, 'build-image', groups)
    cmd = golden_run_command(path, repo, a.overlay_system, a.overlay_store)
    c.event('command', argv=cmd)
    c.print('runs  ' + ' '.join(cmd), 'files')
    if a.dry_run:
        c.done('dry run: nothing built')
        return 0
    if not getattr(a, 'allow_unresolved_swaps', False):
        open_swaps = runpy.run_path(str(TOP / 'pipeline' / 'swaps.py'))['unresolved']()
        for r in open_swaps:
            c.item('%s  %s  swapped %s, only on the laptop' % (r['path'], r['sha256'][:12], r['date']), 'quiet',
                   ev='swap.open', path=r['path'], sha256=r['sha256'])
        if open_swaps:
            raise Stopped('%d live-swapped fixes are in no recipe' % len(open_swaps),
                          why='an image built now would lose them at the next push',
                          options=['put each into a recipe or row, or mark it dropped, in pipeline/live-swaps.tsv',
                                   'build anyway: --allow-unresolved-swaps'], untouched='nothing was built')
    rc = run(cmd)
    if rc:
        raise Stopped('golden-run failed (exit %d)' % rc, why='the image was not committed',
                      options=['the build record is in store/runs/ (golden-run prints its folder)'])
    c.done('image committed to %s' % repo, repo=str(repo))
    return 0


def build_disk(a, c, host):
    profile, path, asm, groups = resolve(a, c, 'build-disk')
    info = gate(c, host, a, 'external', asm['size_bytes'])
    show_steps(c, 'build-disk', groups)
    c.print('')
    c.print('disk  %s  everything on it will be erased' % DISKS.label(info))
    if a.dry_run:
        c.done('dry run: the disk passed every check; nothing was written')
        return 0
    if not c.confirm('Erase %s to build macOS 27 on it?' % DISKS.label(info), DISKS.normalize(a.disk), a.confirm):
        raise Stopped('not confirmed', untouched='nothing was written')
    raise Stopped('build-disk cannot write yet', why=NOT_WIRED, untouched='nothing was written to the disk',
                  options=['build an image instead: hao build-image REPO --model %s' % profile['model']])


def create_installer(a, c, host):
    profile, path, asm, groups = resolve(a, c, 'create-installer')
    info = gate(c, host, a, 'external', a.minimum_bytes)
    show_steps(c, 'create-installer', groups)
    c.item('partition 1  OpenCore (%s) + Recovery + launcher' % profile['model'], 'files')
    c.item('partition 2  kit: images, packs, Python, tools, release.json', 'files')
    c.print('')
    c.print('disk  %s  everything on it will be erased' % DISKS.label(info))
    if a.dry_run:
        c.done('dry run: the stick passed every check; nothing was written')
        return 0
    if not c.confirm('Erase %s to make the installer?' % DISKS.label(info), 'ERASE', a.confirm):
        raise Stopped('not confirmed', untouched='nothing was written')
    raise Stopped('create-installer cannot write yet', why=NOT_WIRED, untouched='nothing was written to the stick')


def install(a, c, host):
    if not getattr(a, 'profile', None) and not getattr(a, 'model', None):
        profile = DETECT.profile(DETECT.facts(host))
        name, _ = DETECT.target_for_model(profile['model'])
        profile['target'] = name
        if not name:
            raise Stopped('this Mac (%s) has no target in this release' % profile['model'],
                          why='its OpenCore configuration and kernel collections are not described yet',
                          untouched='nothing was written')
        a.profile_data = profile
    _, path, asm, groups = resolve(a, c, 'install')
    info = gate(c, host, a, 'internal', asm['size_bytes'])
    c.print('Disk   %s  will be erased' % DISKS.label(info))
    show_steps(c, 'install', groups)
    if a.dry_run:
        c.done('dry run: the disk passed every check; nothing was written')
        return 0
    if not c.confirm('Install macOS 27 on %s? Everything on it will be erased.' % DISKS.label(info), 'ERASE',
                     a.confirm):
        raise Stopped('not confirmed', untouched='nothing was written')
    raise Stopped('install cannot write yet', why=NOT_WIRED, untouched='nothing was written to the disk')
