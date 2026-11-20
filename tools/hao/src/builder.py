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

build-disk and install assemble from the relocatable kit (pipeline/kit.py, workflow plan step 16) on the Mac they
run on: build-disk stages the kit first (golden-run --stage-kit), install takes the one on the stick (--kit). Both
run pipeline/stages/golden-assemble.sh onto the confirmed disk, which golden-image.py's DiskTarget checks again
(policy external for build-disk, internal for install). They need what the builder VM gives the assembly: root,
System Integrity Protection's filesystem protection off and AMFI relaxed (the build makes firmlinks with an ad hoc
entitled tool). The installer's Recovery starts that way; a Mac's everyday macOS does not, and hao never changes
it: the command stops before writing. create-installer writes the stick (installer.py, stick.py).
"""
import json
import os
import plistlib
from pathlib import Path
import runpy
import subprocess
import sys
import contextlib
import datetime
import io
import re

from console import Stopped
import detect as DETECT
import disks as DISKS
import macos as MAC
import assets as ASSETS
import safety as SAFE

TOP = Path(__file__).resolve().parents[3]
ASSEMBLIES = TOP / 'pipeline' / 'assemblies'
PROFILES = TOP / 'pipeline' / 'profiles'      # image profiles: what an image is (target, packs, recipes)
TARGETS = TOP / 'pipeline' / 'targets'

# The step lists of the console mockups (Hao Console Variations), per command.
HOST_STEPS = ('Download', 'Unpack', 'Prepare packs')
DISK_STEPS = ('Lay out the disk', 'Copy macOS', 'Add Intel support from Tahoe', 'Apply packs', 'Link the kernel', 'Seal',
              'Make it bootable')
STEPS = {
    'build-image': ('Prepare packs', 'Assemble in the builder VM', 'Commit the image'),
    'build-image-here': ('Prepare the kit', 'Assemble the image on this Mac', 'Keep the reusable parts'),
    'build-disk': HOST_STEPS + DISK_STEPS,
    'create-installer': HOST_STEPS + ('Write the USB installer',),
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
HERE_STEPS = ('Prepare the kit', 'Assemble the image on this Mac', 'Keep the reusable parts')
BUILDS = Path.home() / 'Library' / 'Caches' / 'hao' / 'builds'
AMFI_RELAXED = re.compile(r'(^|\s)(amfi_get_out_of_my_way=(1|0x1)|amfi=0x80)(\s|$)')


def environment_refusal(host):
    """Why this Mac cannot assemble onto a disk, or None. The assembly formats APFS, makes firmlinks with an ad hoc
    entitled tool and sets restricted flags, as it does in the builder VM: root, SIP's filesystem protection off
    (CSR_ALLOW_UNRESTRICTED_FS) and AMFI relaxed (amfi_get_out_of_my_way=1, or the NetBoot Recovery's amfi=0x80)."""
    if host.euid() != 0:
        return 'not running as root'
    csr = host.csr_config()
    if csr is None or not csr & MAC.CSR_ALLOW_UNRESTRICTED_FS:
        return 'System Integrity Protection\'s filesystem protection is on'
    r = host.run(['nvram', 'boot-args'], check=False)
    args = r.stdout.decode(errors='replace').partition('\t')[2].strip() if r.returncode == 0 else ''
    if not AMFI_RELAXED.search(args):
        return 'AMFI is not relaxed (no amfi_get_out_of_my_way=1 boot argument)'
    return None


def assemble_onto(a, c, host, path, info, policy, run=subprocess.call):
    """Stage the kit (unless --kit names one), assemble it onto the confirmed disk with golden-assemble.sh, keep the
    sealed cryptex images it made. Returns the build folder."""
    why = environment_refusal(host)
    if why:
        raise Stopped('this Mac cannot build onto a disk: %s' % why,
                      why='the build formats APFS and makes firmlinks, which macOS allows only with System Integrity '
                          'Protection and AMFI relaxed, as in the installer\'s Recovery',
                      options=['start the Mac from the hao installer and run hao install there',
                               'or build an image file instead: hao build-image REPO --model MODEL'],
                      untouched='nothing was written to any disk')
    name = DISKS.normalize(a.disk)
    work = Path(getattr(a, 'work', None) or BUILDS / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')).resolve()
    record = None
    kit = Path(a.kit).resolve() if getattr(a, 'kit', None) else None
    # what root reads and writes from here on (the kit, the work folder, this checkout's store) is no one else's to
    # change: checked before anything is staged, and again for the folders staging made
    on_stick = bool(os.environ.get('HAO_STICK'))

    def no_one_else(*places, tree=()):
        for where in places:
            check = SAFE.others_can_change_tree if where in tree else SAFE.others_can_change
            why = check(where, owners=not on_stick)
            if why:
                raise Stopped('nothing was erased: %s' % why, untouched='nothing was written to any disk',
                              options=['make that folder yours and not writable by others (chmod go-w), or use another'])
    no_one_else(work, TOP / 'store', *([kit] if kit else []), tree=[kit] if kit else [])
    if kit is None:
        record = work / 'record'
        cmd = [sys.executable, '-I', str(TOP / 'pipeline' / 'golden-run'), str(path), '--stage-kit',
               '--work', str(work / 'stage'), '--record', str(record)]
        c.event('command', argv=cmd)
        c.item('prepare the kit  ' + c.paint('dim', str(work / 'stage' / 'share')), 'files')
        if run(cmd):
            raise Stopped('preparing the kit failed', untouched='nothing was written to the disk',
                          options=['the record is in %s' % record])
        kit = work / 'stage' / 'share'
    if not (kit / 'kit.json').is_file():
        raise Stopped('%s is no kit (no kit.json)' % kit, untouched='nothing was written to the disk')
    local = assembly_local(a, work)
    prior = resumed_work(a)
    # A kit staged here is collected afterwards (golden-run --collect-kit keeps its sealed cryptex images); a kit
    # named with --kit (hao install from the stick) is not, so storing them would only fill the stick.
    cmd = ['sh', str(kit / 'assembly-source' / 'golden-assemble.sh'), str(kit), str(local)] + (
           [] if record is not None else ['--no-cryptex-cache']) + ['--disk', name,
           '--confirm-disk', name] + (['--external'] if policy == 'external' else []) + (
           ['--resume', str(prior)] if prior else [])
    no_one_else(kit, local, tree=[kit])
    if local.exists() and (not local.is_dir() or local.is_symlink() or any(local.iterdir())):
        # the assembler's own folder is new (its configuration and copies are made there as root, never over old files)
        raise Stopped('nothing was erased: %s is not a new, empty folder' % local, untouched='nothing was written to any disk',
                      options=['remove it or name another work folder'])
    expect = getattr(a, 'disk_expect', None)
    if expect:                      # the assembler checks it again right before it erases (golden-image.py DiskTarget)
        import atexit
        import shutil
        import tempfile
        private = tempfile.mkdtemp(prefix='hao-disk-')     # this user's, 0700: no one else can replace the file
        atexit.register(shutil.rmtree, private, True)
        ident = Path(private) / 'disk-identity.json'
        fd = os.open(ident, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(expect['identity'], indent=1) + '\n')
        cmd[cmd.index('--confirm-disk') + 2:cmd.index('--confirm-disk') + 2] = ['--disk-identity', str(ident)]
    if expect:                      # staging can take long: the confirmed disk must still be the one at this number
        why = DISKS.recheck(host, name, expect['identity'], expect['policy'], expect['minimum'], expect['sources'])
        if why:
            raise Stopped('/dev/%s was not erased: %s' % (name, why), untouched='nothing was written to any disk',
                          options=['check the disks (diskutil list) and run the command again'])
    c.event('command', argv=cmd)
    c.item('assemble onto /dev/%s  %s' % (name, DISKS.label(info)), 'steps')
    rc = run(cmd)
    if rc:
        options = ['the assembler\'s record and logs: %s' % (local / 'work'),
                   'run the command again: it starts from an empty disk']
        step = failed_step(local / 'work')
        if step in RESUMABLE:
            options.append('or continue at %s once its cause is fixed: the same command with --resume %s'
                           % (step, work))
        raise Stopped('the build onto /dev/%s failed (exit %d)%s' % (name, rc, ' in step ' + step if step else ''),
                      why='the disk is erased and incomplete; it will not start', options=options)
    if record is not None and run([sys.executable, '-I', str(TOP / 'pipeline' / 'golden-run'), '--collect-kit',
                                   str(record)]):
        c.warning('the sealed cryptex images were not kept for reuse; the next build makes them again')
    return work


# golden-image.py's RESUMABLE: the steps a failed install can continue at (the steps before them completed)
RESUMABLE = ('boot-and-esp', 'hardware', 'posts')


def failed_step(work):
    """The step a failed assembly (its work folder) stopped in, or None."""
    try:
        steps = json.loads((Path(work) / 'build.json').read_text()).get('steps', [])
    except (OSError, ValueError):
        return None
    left = [row['name'] for row in steps if not row.get('complete')]
    return left[0] if left else None


def assembly_local(a, work=None):
    """The assembler's LOCAL folder (--local, else WORK/local), resolved as assemble_onto uses it."""
    if getattr(a, 'local', None):
        return Path(a.local).resolve()
    return (Path(work if work is not None else a.work).resolve() / 'local').resolve()


def resumed_work(a):
    """--resume WORK: the failed run's assembler work folder (WORK/local/work, or WORK itself), or None."""
    if not getattr(a, 'resume', None):
        return None
    for folder in (Path(a.resume) / 'local' / 'work', Path(a.resume)):
        if (folder / 'build.json').is_file():
            return folder.resolve()
    raise Stopped('%s holds no failed install (no local/work/build.json)' % a.resume, untouched='nothing was written')


def _quiet(fn, *args, **kw):
    """Call FN with its stderr chatter (patches.py prints skipped recipes) kept out of the console."""
    with contextlib.redirect_stderr(io.StringIO()):
        return fn(*args, **kw)


def target_data(name):
    """pipeline/targets/NAME.json, or {} when there is none."""
    try:
        return json.loads((TARGETS / (name + '.json')).read_text()) if name else {}
    except (OSError, ValueError):
        return {}


def is_generic_target(target):
    """Whether TARGET is the generic one (software graphics, "generic": true): the image for Macs without their own."""
    return bool(target_data(target).get('generic'))


def image_profiles():
    """{name: profile} of pipeline/profiles."""
    return {p.stem: json.loads(p.read_text()) for p in sorted(PROFILES.glob('*.json'))}


def generic_profiles(release=None):
    """The image profiles whose target is generic: those RELEASE carries ("profiles"), or with no release this
    checkout's. A release without "profiles" carries none."""
    if release is not None:
        return [n for n, p in sorted((release.get('profiles') or {}).items()) if is_generic_target(p.get('target'))]
    return [n for n, p in image_profiles().items() if is_generic_target(p.get('target'))]


def image_profile(a):
    """--profile NAME when NAME is an image profile (pipeline/profiles/NAME.json, e.g. generic), kept in a.image_profile;
    None when --profile is a hao detect profile file (a path to an existing file, or a name ending in .json)."""
    if getattr(a, 'image_profile', None):
        return a.image_profile
    value = getattr(a, 'profile', None)
    if not value or value.endswith('.json') or '/' in value or Path(value).expanduser().is_file():
        return None
    if not (PROFILES / (value + '.json')).is_file():
        raise Stopped('no image profile %s' % value,
                      why='--profile takes an image profile by name or a profile file from hao detect',
                      options=['one of: ' + (', '.join(image_profiles()) or 'none in this checkout'),
                               'or a file: --profile mac.json (hao detect --out mac.json on that Mac)'],
                      untouched='nothing was downloaded or written')
    a.image_profile = value
    return value


def mac(a):
    """The profile a command works for: --profile FILE (hao detect), --model MODEL, or this Mac (install); with
    --profile NAME (an image profile) and no Mac, the model of that profile's target."""
    if getattr(a, 'profile_data', None) is not None:
        return a.profile_data
    name = image_profile(a)
    if name:
        target = image_profiles()[name].get('target')
        model = target_data(target).get('model')
        try:
            p = DETECT.for_model(model)
        except DETECT.DetectError as x:
            raise Stopped('image profile %s: its target %s has no recorded profile' % (name, target), cause=str(x))
        p['target'] = target
        return p
    if getattr(a, 'profile', None):
        return DETECT.load(a.profile)
    if getattr(a, 'model', None):
        return DETECT.for_model(a.model)
    raise Stopped('no Mac named', why='packs and the OpenCore configuration follow from the Mac',
                  options=['name it: --model MacBookPro9,2', 'or run `hao detect --out mac.json` on it and pass '
                           '--profile mac.json'])


def assembly(name, profile, golden=None, image=None):
    """(path, assembly) for --assembly NAME, else the one that builds image profile IMAGE (its patch_set), else the one
    for the profile's target (TARGET-GOLDEN, no dev recipes)."""
    if name:
        p = Path(name) if Path(name).is_file() else ASSEMBLIES / (name + '.json')
        if not p.is_file():
            raise Stopped('no assembly %s' % name, options=['one of: ' + ', '.join(sorted(x.stem for x in
                                                                                         ASSEMBLIES.glob('*.json')))])
        a = json.loads(p.read_text())
        if profile.get('target') and a.get('target') != profile['target']:
            raise Stopped('assembly %s builds for target %s, the Mac is %s' % (p.stem, a.get('target'),
                                                                               profile['target']))
        if image and a.get('patch_set') != image:
            raise Stopped('assembly %s builds set %s, not image profile %s' % (p.stem, a.get('patch_set'), image))
        return p, a
    found = []
    if image:
        for p in sorted(ASSEMBLIES.glob('*.json')):
            a = json.loads(p.read_text())
            if a.get('runner', 'golden') == 'golden' and a.get('patch_set') == image and \
                    (golden is None or a.get('golden_build') == golden):
                found.append((p, a))
        if len(found) != 1:
            raise Stopped('%s assemblies build image profile %s' % (len(found) or 'no', image),
                          options=['name one: --assembly NAME (pipeline/assemblies)'])
        return found[0]
    for p in sorted(ASSEMBLIES.glob('*.json')):
        a = json.loads(p.read_text())
        if a.get('runner', 'golden') == 'golden' and a.get('target') == profile.get('target') and \
                p.stem == '%s-%s' % (a['target'], golden or a['golden_build']):
            found.append((p, a))
    if len(found) != 1:
        raise Stopped('no single release assembly for %s' % profile['model'],
                      options=['name one: --assembly NAME (pipeline/assemblies)'])
    return found[0]


def set_file(patches, name):
    """The set NAME: its image profile in PROFILES, else what patches.py set_path finds (a profile or a set file)."""
    p = PROFILES / (name + '.json')
    return p if p.is_file() else patches['set_path'](name)


def recipes_by_pack(asm, profile):
    """{pack: [recipe, ...]} of the assembly's set with the profile's packs, plus "(no pack)" for the rest."""
    patches = runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))
    defs = patches['load_packs']()
    owner = {r: n for n, d in defs.items() for r in d.get('recipes', [])}
    rows = _quiet(lambda: list(patches['plan'](set_file(patches, asm['patch_set']),
                                               TOP / 'pipeline/targets' / (asm['target'] + '.json'),
                                               '%s/%s' % (asm['golden_build'], asm['donor_build']),
                                               packs=profile['packs'])))
    out = {n: [] for n in profile['packs']}
    for stage, name, _ in rows:
        out.setdefault(owner.get(name, '(no pack)'), []).append(name)
    return out


# Commands that put macOS on a Mac (or the drive that will): they refuse an unknown or unproven Mac unless --unproven
GATED = ('install', 'create-installer', 'build-disk')


GENERIC = 'generic'     # the generic image's model pack / release profile (owner, v0.0.1: a software-graphics baseline)
GENERIC_NOTE = ('The generic image is an unsupported baseline for testing: it runs with software graphics on hardware '
                'hao does not know, and nothing about it is proven.')
GENERIC_LINE = 'This generic image is for virtual machines and non-Apple PCs, not for Mac hardware.'


def is_generic(profile):
    return GENERIC in (profile.get('packs') or []) or GENERIC in (profile.get('target'), profile.get('profile'))


def unproven(profile):
    """Why PROFILE is not a proven Mac (PROTOCOL S10): the generic image (always), an unknown GPU, a pack with no
    proof on this model, a model that is not proven. Empty when it is."""
    out = ['the generic image is an unsupported testing baseline'] if is_generic(profile) else []
    if is_generic(profile) and (profile.get('firmware_vendor') or '').startswith('Apple'):
        out.append('this is a Mac (Apple firmware): the generic image is for virtual machines and non-Apple PCs')
    out += ['unknown GPU %s%s: no rule picks its packs' % (d['device'], ' (%s)' % d['name'] if d.get('name') else '')
           for d in profile.get('unknown') or [] if d.get('role') == 'gpu']
    packs = [n for n, ok in (profile.get('proven') or {}).items() if not ok]
    if packs:
        out.append('packs not proven on %s: %s' % (profile.get('model'), ', '.join(packs)))
    if not profile.get('proven_model') and not packs:
        out.append('%s is not a proven model' % profile.get('model'))
    return out


def proof_gate(a, c, profile, command):
    """Stop COMMAND (exit 5) on an unknown or unproven Mac unless --unproven; with it, warn and keep the reasons for
    the install record (a.unproven_reasons)."""
    reasons = unproven(profile) if command in GATED else []
    image = getattr(a, 'image_profile', None)
    if command in GATED and image and is_generic_target(profile.get('target')):
        # the generic image, chosen by name (--profile generic or its question), is for virtual machines and non-Apple
        # PCs (its VirtualSMC conflicts with a Mac's SMC): for a Mac (Apple firmware, or a Mac model named) it needs
        # --unproven. Otherwise the choice is the consent; the install record and the stick's launcher keep it.
        if (profile.get('firmware_vendor') or '').startswith('Apple') or getattr(a, 'model', None):
            if not getattr(a, 'unproven', False):
                raise Stopped('the generic image is for virtual machines and non-Apple PCs, not a Mac',
                              why=GENERIC_LINE, code=5, untouched='nothing was downloaded or written',
                              options=['choose your Mac model instead', 'or add --unproven to make it anyway (kept in '
                                       'the install record)'])
        reasons = ['the generic image %s (software graphics) for %s, not a proven Mac'
                   % (image, profile.get('model') or 'a virtual machine or non-Apple PC')] + reasons
        a.unproven_reasons = reasons
        c.event('unproven', reasons=reasons, allowed=True, image_profile=image)
        c.warning('making the generic image (software graphics, a test baseline): %s' % '; '.join(reasons))
        return
    a.unproven_reasons = reasons
    if command in GATED and is_generic(profile):
        c.print(c.paint('warn', GENERIC_LINE), 'quiet')     # before any confirmation (patcher GEN1, pm)
    if not reasons:
        return
    c.event('unproven', reasons=reasons, allowed=bool(getattr(a, 'unproven', False)))
    if getattr(a, 'unproven', False):
        if is_generic(profile):
            c.print(c.paint('warn', GENERIC_NOTE), 'quiet')
        c.warning('installing on a Mac hao has not proven (--unproven, kept in the install record): %s'
                  % '; '.join(reasons))
        return
    if is_generic(profile):
        raise Stopped('the generic image needs --unproven', why=GENERIC_NOTE, code=5,
                      untouched='nothing was downloaded or written',
                      options=['to install it for testing, run the same command with --unproven: the install record '
                               'keeps it'])
    raise Stopped('%s is not a Mac hao has proven' % profile.get('model'), why='; '.join(reasons), code=5,
                  untouched='nothing was downloaded or written',
                  options=['use a proven Mac (hao detect shows what it finds)'] + (
                      ['or make the generic image (software graphics, a test baseline; for a Mac only with '
                       '--unproven): the same command with --profile %s --unproven' % a.generic_offer]
                      if getattr(a, 'generic_offer', None) else []) +
                          ['or, to try it anyway, run the same command with --unproven: the install record keeps it'])


def resolve(a, c, command):
    """Profile, assembly and recipes, shown on C; the common head of every builder command."""
    profile = mac(a)
    image = image_profile(a)
    target = image_profiles()[image].get('target') if image else None
    if image and is_generic_target(target):
        # the generic image is the same on every Mac: its target's packs (the OS layer), not what this Mac detects
        patches = runpy.run_path(str(TOP / 'pipeline' / 'patches.py'))
        profile = dict(profile, target=target,
                       packs=_quiet(patches['detect_packs'], str(TARGETS / (target + '.json'))))
    elif image and profile.get('target') != target:
        raise Stopped('image profile %s is for target %s, the Mac is %s' % (image, target, profile.get('target')))
    proof_gate(a, c, profile, command)
    path, asm = assembly(getattr(a, 'assembly', None), profile, image=image)
    groups = recipes_by_pack(asm, profile)
    proven = 'proven model %s' % c.ok if profile.get('proven_model') else 'not proven on this model'
    c.banner('hao %s  macOS 27 %s for %s  (%s)' % (command, asm['golden_build'], profile['model'], proven),
             command=command, assembly=path.stem, model=profile['model'], packs=profile['packs'])
    DETECT.show(c, profile)
    c.print('Recipes  %d in %d packs, set %s, donors %s' % (sum(len(v) for v in groups.values()), len(groups),
                                                            asm['patch_set'], c.paint('tahoe', 'Tahoe ' + asm['donor_build'])),
            'files')
    for pack, names in groups.items():
        c.print('  %s %s' % (c.paint('bold', '%-22s' % pack), ', '.join(names) or '-'), 'edits')
    return profile, path, asm, groups


def gate(c, host, a, policy, minimum):
    """The disk checks; returns the accepted disk's description, or stops."""
    if not a.disk:
        raise Stopped('no disk named', options=['name the whole disk: /dev/diskN (diskutil list %s)' % policy])
    why = DISKS.refusal(host, a.disk, policy, minimum, sources=[s for s in getattr(a, 'sources', []) if s])
    if why:
        raise Stopped('/dev/%s may not be erased: %s' % (DISKS.normalize(a.disk), why),
                      why='hao erases only a disk it can tell is the one you mean',
                      untouched='nothing was written to any disk')
    info, _ = DISKS.describe(host, a.disk)
    a.disk_expect = dict(identity=DISKS.identity(host, a.disk), policy=policy, minimum=minimum,
                         sources=[s for s in getattr(a, 'sources', []) if s])
    c.event('disk', disk=DISKS.normalize(a.disk), policy=policy, size=info.get('TotalSize') or info.get('Size'))
    return info


def show_steps(c, command, groups):
    steps = STEPS[command]
    c.steps(len(steps))
    for title in steps:
        c.step(title, plan=True)
        if title == 'Prepare packs':
            for pack, names in groups.items():
                c.item('%s %d recipes' % (c.paint('bold', '%-22s' % pack), len(names)), 'steps', pack=pack,
                       recipes=names)
        elif title in DISK_DETAIL:
            c.item(DISK_DETAIL[title], 'files')


def golden_run_command(path, repo, overlay_system=None, store=None):
    cmd = [sys.executable, '-I', str(TOP / 'pipeline' / 'golden-run'), str(path), '--repo', str(repo)]
    if overlay_system:
        cmd += ['--from-overlays', '--overlay-system', str(overlay_system)]
        if store:
            cmd += ['--overlay-store', str(store)]
    return cmd


def use_assets(a, c, path):
    """--assets DIR: the clone's store/ is DIR (a link, made when the clone has none), and every row the build
    reads must be there with its pinned hash before anything runs."""
    folder = Path(a.assets).expanduser().resolve()
    if not folder.is_dir():
        raise Stopped('no assets folder at %s' % folder, untouched='nothing was built')
    store = TOP / 'store'
    if store.is_symlink() or store.exists():
        if store.resolve() != folder:
            raise Stopped('this checkout already has a store (%s), not %s' % (store.resolve(), folder),
                          why='a build reads its inputs from one place',
                          options=['use a fresh clone', 'or leave out --assets to build from the existing store'])
    else:
        store.symlink_to(folder)
        c.item('store  -> %s' % c.paint('dim', str(folder)), 'files')
    problems = ASSETS.check(path, folder, here=bool(getattr(a, 'here', False)))
    for p in problems:
        c.item(p, 'quiet', ev='asset.problem', problem=p)
    if problems:
        raise Stopped('%d inputs are missing or wrong in %s' % (len(problems), folder),
                      why='every input is pinned by hash; the build reads nothing else',
                      untouched='nothing was built')
    c.item('inputs  every row present, hashes checked', 'steps', ok=True)


def export_image(a, c, repo, run=subprocess.call):
    """--export FILE: the committed image as one raw disk image file (ssdimg export)."""
    out = Path(a.export).expanduser().resolve()
    cmd = [str(TOP / 'tools' / 'bin' / 'ssdimg'), '-C', str(repo), 'export', str(out)]
    c.event('command', argv=cmd)
    if run(cmd):
        raise Stopped('exporting the image to %s failed' % out, options=['the image is committed in %s' % repo])
    c.item('raw image  %s' % out, 'steps', ok=True)


def build_image_here(a, c, host, path, groups, run=subprocess.call):
    """build-image --here: no builder VM. Stage the kit, assemble it on this Mac into a raw image file, keep the sealed
    cryptex images for the next build; the image file is the result (REPO is then the .img path). The Mac needs what
    the VM gives the assembly (root, SIP's filesystem protection off, AMFI relaxed): an Intel Mac booted from the hao
    installer or a hao image, as the North Star's "same assembler against a raw image"."""
    out = Path(a.out).expanduser().resolve()
    if out.exists():
        raise Stopped('%s exists' % out, options=['name a new image file'], untouched='nothing was built')
    why = environment_refusal(host)
    if why:
        raise Stopped('this Mac cannot assemble an image: %s' % why,
                      why='the build formats APFS and makes firmlinks, which macOS allows only with System Integrity '
                          'Protection and AMFI relaxed, as in the installer\'s Recovery or a hao image',
                      options=['run it as root on a Mac started from the hao installer or a hao image',
                               'or build in the builder VM on an Apple silicon Mac (without --here)'],
                      untouched='nothing was built')
    steps = HERE_STEPS
    c.steps(len(steps))
    work = Path(getattr(a, 'work', None) or BUILDS / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')).resolve()
    # the build's scripts call python3: this Python (the bundled CPython on a Mac without developer tools)
    os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')
    record = work / 'record'
    golden = str(TOP / 'pipeline' / 'golden-run')
    c.step(steps[0])
    cmd = [sys.executable, '-I', golden, str(path), '--stage-kit', '--work', str(work / 'stage'), '--record', str(record)]
    c.event('command', argv=cmd)
    if run(cmd):
        raise Stopped('preparing the kit failed', options=['the record is in %s' % record], untouched='nothing was built')
    kit = work / 'stage' / 'share'
    local = work / 'local'
    # the assembly writes the whole image file (the assembly's size) next to the staged kit (golden-run checked that)
    disk = runpy.run_path(str(TOP / 'pipeline' / 'diskguard.py'))
    try:
        size = json.loads(Path(path).read_text())['size_bytes'] if Path(path).is_file() else 0   # staging read it
        disk['check'](size / 1e9, 'build-image', out.name, str(work if work.exists() else TOP),
                      out=lambda m: c.item(m, 'steps'))
    except disk['Refused'] as x:
        raise Stopped('not enough disk space to assemble the image: %s' % x,
                      options=['free space on this Mac, then build again'], untouched='nothing was assembled')
    c.step(steps[1])
    cmd = ['sh', str(kit / 'assembly-source' / 'golden-assemble.sh'), str(kit), str(local)]
    c.event('command', argv=cmd)
    rc = run(cmd)
    if rc:
        raise Stopped('assembling the image failed (exit %d)' % rc,
                      options=['the assembler\'s record and logs: %s' % (local / 'work')])
    c.step(steps[2])
    if run([sys.executable, '-I', golden, '--collect-kit', str(record)]):
        c.warning('the sealed cryptex images were not kept for reuse; the next build makes them again')
    out.parent.mkdir(parents=True, exist_ok=True)
    if run(['mv', str(local / 'target.img'), str(out)]):
        raise Stopped('moving the image to %s failed' % out, options=['it is at %s' % (local / 'target.img')])
    c.done('image built: %s' % out, image=str(out), record=str(record))
    return 0


def build_image(a, c, run=subprocess.call, host=None):
    profile, path, asm, groups = resolve(a, c, 'build-image')
    if getattr(a, 'assets', None):
        use_assets(a, c, path)
    if getattr(a, 'here', False):
        if a.dry_run:
            show_steps(c, 'build-image-here', groups)
            c.done('dry run: nothing built')
            return 0
        return build_image_here(a, c, host or MAC.SystemHost(), path, groups, run)
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
    if getattr(a, 'export', None):
        export_image(a, c, repo, run)
    c.done('image committed to %s' % repo, repo=str(repo))
    return 0


def build_disk(a, c, host, run=subprocess.call):
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
    work = assemble_onto(a, c, host, path, info, 'external', run)
    write_install_record(c, host, DISKS.normalize(a.disk),
                         install_record(a, c, profile, path, asm, groups, DISKS.normalize(a.disk), getattr(a, 'kit', None)))
    c.done('macOS 27 built on /dev/%s; start the Intel Mac from it (hold Option)' % DISKS.normalize(a.disk),
           work=str(work))
    return 0


STATEMENT = TOP / 'tools' / 'hao' / 'data' / 'security-statement.txt'   # releases' one statement (O-2), verbatim


def show_statement(c):
    """Releases' reduced-security statement (owner O-2), shown before the user confirms an erase."""
    try:
        text = ' '.join(STATEMENT.read_text().split())
        if not text:
            raise OSError('empty')
    except OSError:
        raise Stopped('the reduced-security statement is missing (%s)' % STATEMENT, code=4,
                      why='hao shows it before anything is erased', untouched='nothing was written')
    c.event('statement', text=text)
    c.print('', 'quiet')
    c.print(c.paint('warn', text), 'quiet')
    return text


def check_trace(top):
    """The manifest check's trace: TOP/logs/manifest-check-TIME.txt (logs/ is outside the manifest), one line per file
    before it is read, and Python's fault handler writing a traceback there on SIGBUS/SIGSEGV (a page-in failure on
    the drive the check reads). None when the folder cannot be written; the check runs either way."""
    import faulthandler
    import time
    try:
        (top / 'logs').mkdir(exist_ok=True)
        f = open(top / 'logs' / ('manifest-check-%s.txt' % time.strftime('%Y%m%d-%H%M%S')), 'w')
    except OSError:
        return None
    faulthandler.enable(file=f, all_threads=True)
    return f


def new_file_owner(folder):
    """The owner a file made now in FOLDER reads as (a volume mounted without ownership reports uid 99 for it)."""
    import tempfile
    with tempfile.NamedTemporaryFile(dir=folder, prefix='.hao-owner-') as f:
        return os.fstat(f.fileno()).st_uid


def mount_point(path):
    """The mount point of the volume PATH (an existing folder) is on."""
    p = Path(os.path.abspath(path))
    while not os.path.ismount(p) and p != p.parent:
        p = p.parent
    return str(p)


def kit_ownership(c, host, place, owner=None, euid=os.geteuid):
    """Make files the install creates under PLACE (its work folder, or the nearest folder of it that exists) read as
    owned by this user. A Recovery mounts the USB installer's HAO "noowners", so every file there reads as owned by an
    unknown user (99), and the install's own checks of its private copies (made under its work folder, as root) refuse
    them after the shared cache is built (~3 h in). Decided by what a new file there reads as, not by `diskutil info`
    (given a folder rather than a mount point it answers only {Error: Could not find disk}). When the owner is wrong:
    `diskutil enableOwnership` on PLACE's volume, then the same test again; still wrong stops the install before
    anything is erased, and so does a test that cannot run. Returns True when ownership was turned on, False when it was already right."""
    place = Path(place).resolve()               # as assembly resolves it: a link to another volume is followed
    while not place.is_dir() and place != place.parent:
        place = place.parent
    owner = owner or new_file_owner
    want = euid()
    try:
        got = owner(place)
    except OSError as x:
        raise Stopped('could not test who owns files made in %s (%s)' % (place, x.strerror or x),
                      why='the install checks who owns its working copies there and would refuse them hours in',
                      options=['make that folder writable, or name another work folder'],
                      untouched='nothing was written to any disk') from None
    if got == want:
        return False
    mount = mount_point(place)
    r = host.run(['diskutil', 'enableOwnership', mount], check=False)
    try:
        after = owner(place)
    except OSError:
        after = None
    ok = after == want
    c.event('kit.ownership', mount=mount, before=got, after=after, enabled=ok, rc=r.returncode)
    if not ok:
        raise Stopped('files made in %s read as owned by uid %s, not %d, even after diskutil enableOwnership %s (rc %d)'
                      % (place, after if after is not None else got, want, mount, r.returncode),
                      why='the install checks who owns its working copies there and would refuse them hours in',
                      options=['turn ownership on for that volume (diskutil enableOwnership %s) and run the command '
                               'again' % mount], untouched='nothing was written to any disk')
    c.item('ownership on for %s (the install checks who owns its working copies)' % mount, 'files', ok=True)
    return True


def check_drive(a, c):
    """Before the install erases anything: the USB installer the kit is on must match the manifest create-installer
    wrote (owner O-1: custody plus an integrity check). A kit not on a USB installer (build-disk, --kit elsewhere) has
    none to check."""
    import stick as STICK
    kit = Path(a.kit).resolve()
    if os.environ.get('HAO_STICK'):             # run by the USB installer's launcher: the check is not optional
        top = Path(os.environ['HAO_STICK']).resolve()
        if kit != top / 'kit':                  # only the kit the manifest covers
            raise Stopped('the kit %s is not on the USB installer %s' % (kit, top), code=4,
                          untouched='nothing was erased')
    else:
        top = kit.parent
        if not any(os.path.lexists(top / n) for n in ('stick.json', STICK.MANIFEST)):
            return None                         # a kit that is not on a USB installer (build-disk, a developer's --kit)
    keep_awake()
    total = _manifest_bytes(top / STICK.MANIFEST)
    done = [0]
    trace = check_trace(top)
    with c.task('check the USB installer against its manifest', total=total or None, unit='bytes') as t:
        def seen(rel, size):
            done[0] += size
            t.update(done=done[0])
        def start(rel, size):
            if trace:
                trace.write('%d %s\n' % (size, rel))
                trace.flush()
        try:
            problems = STICK.check_manifest(top, on_file=seen, on_start=start)
        finally:
            if trace:
                import faulthandler
                faulthandler.disable()
                trace.write('end\n')
                trace.close()
    c.event('drive.check', problems=len(problems), files=None)
    if problems:
        path, what = problems[0]
        raise Stopped('the USB installer differs from what hao wrote: %s (%s)%s' % (
                          path, what, ' and %d more' % (len(problems) - 1) if len(problems) > 1 else ''),
                      why='a changed or damaged USB installer could install something else', code=4,
                      untouched='nothing was erased',
                      options=['make the USB installer again with hao create-installer, and keep it with you until '
                               'the install'])
    return True


def keep_awake():
    """The check reads the whole drive (minutes): keep the Mac awake while this process runs, where caffeinate exists."""
    import shutil
    if shutil.which('caffeinate'):
        subprocess.Popen(['caffeinate', '-dimsu', '-w', str(os.getpid())], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def _manifest_bytes(path):
    """The bytes the check reads (each hard-linked file once), for its progress bar."""
    try:
        m = json.loads(Path(path).read_text())
        return int(m.get('bytes') or sum(int(f.get('size') or 0) for f in m.get('files', {}).values()))
    except Exception:                       # only the progress bar's total: the check itself judges the manifest
        return None


def create_installer(a, c, host):
    """The USB stick: asks its questions and runs its steps in installer.py (the user's one command)."""
    import installer
    return installer.create_installer(a, c, host)


def install(a, c, host, run=subprocess.call):
    if getattr(a, 'kit', None) and not a.dry_run:
        check_drive(a, c)                       # first: before any planning code from the drive runs (owner O-1)
    if not getattr(a, 'profile', None) and not getattr(a, 'model', None):
        profile = DETECT.profile(DETECT.facts(host))
        name, _ = DETECT.target_for_model(profile['model'])
        profile['target'] = name
        if not name:
            raise Stopped('this Mac (%s) has no target in this release' % profile['model'],
                          why='its OpenCore configuration and kernel collections are not described yet',
                          untouched='nothing was written')
        a.profile_data = profile
    profile, path, asm, groups = resolve(a, c, 'install')
    if getattr(a, 'kit', None) and not getattr(a, 'sources', None):
        a.sources = [str(Path(a.kit).resolve())]       # never the disk the kit (the stick) is on
    if not a.disk:
        import installer
        a.disk = installer.ask_internal_disk(a, c, host, asm['size_bytes'], getattr(a, 'sources', []))
    info = gate(c, host, a, 'internal', asm['size_bytes'])
    prior = resumed_work(a)
    if prior:
        step = failed_step(prior)
        if step not in RESUMABLE:
            raise Stopped('the install in %s cannot be continued (it stopped in %s)' % (a.resume, step or 'no step'),
                          why='only %s can run again over what a failed attempt left' % ' and '.join(RESUMABLE),
                          options=['run it without --resume: it starts from an empty disk'], untouched='nothing was written')
        c.print('Disk   %s  continues the install in %s at %s' % (DISKS.label(info), a.resume, step))
    else:
        c.print('Disk   %s  will be erased' % DISKS.label(info))
    show_steps(c, 'install', groups)
    if a.dry_run:
        c.done('dry run: the disk passed every check; nothing was written')
        return 0
    if not getattr(a, 'kit', None):
        raise Stopped('no kit named', why='install assembles the kit the USB installer carries',
                      options=['name it: hao install /dev/diskN --kit /Volumes/HAO/kit'], untouched='nothing was written')
    show_statement(c)
    question, word = (('Continue the install on %s? The steps it completed are kept.' % DISKS.label(info), 'RESUME')
                      if prior else
                      ('Install macOS 27 on %s? Everything on it will be erased.' % DISKS.label(info), 'ERASE'))
    if not c.confirm(question, word, a.confirm):
        raise Stopped('not confirmed', untouched='nothing was written')
    if not getattr(a, 'local', None) and not getattr(a, 'work', None):
        a.work = str(Path(a.kit).resolve().parent / ('hao-install-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S')))
    kit_ownership(c, host, assembly_local(a))
    work = assemble_onto(a, c, host, path, info, 'internal', run)
    write_install_record(c, host, DISKS.normalize(a.disk),
                         install_record(a, c, profile, path, asm, groups, DISKS.normalize(a.disk), a.kit))
    blessed = start_up(c, host, profile, DISKS.normalize(a.disk))
    # no automatic restart when the start-up disk was not set: the Mac could start something else
    wait = None if getattr(a, 'no_restart', False) or not blessed else (key_within if sys.stdin.isatty() else None)
    if wait is None:
        c.done('Installed macOS 27 on /dev/%s; restart to use it' % DISKS.normalize(a.disk), work=str(work))
        return 0
    c.done('Installed macOS 27 on /dev/%s' % DISKS.normalize(a.disk), work=str(work))
    restart(c, host, wait)
    return 0


RECORD = Path('private/var/db/hao/install-record.json')     # on the installed Data volume; hao status, verify, update
MANIFESTS = Path('private/var/db/hao/manifests')            # verify.py's INSTALLED: per-pack file manifests


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def release_of():
    """(release, hao_patcher commit, hao_runtime commit) of the hao that runs: the USB installer's release.json
    ($HAO_STICK), else release.json beside hao, else the checkout's commit as "dev COMMIT"."""
    stick = Path(os.environ['HAO_STICK']) if os.environ.get('HAO_STICK') else None
    for rel in ([stick / 'release.json'] if stick else []) + [TOP / 'tools' / 'hao' / 'release.json']:
        try:
            r = json.loads(rel.read_text())
            return r.get('version') or r.get('release'), r.get('hao_patcher'), r.get('hao_runtime')
        except (OSError, ValueError):
            continue
    try:                                            # a development USB installer: the commit create-installer recorded
        commit = json.loads((stick / 'stick.json').read_text()).get('hao_commit') if stick else None
        if commit:
            return 'dev %s' % commit[:8], commit, None
    except (OSError, ValueError):
        pass
    try:
        commit = subprocess.run(['git', '-c', 'safe.directory=%s' % TOP, '-C', str(TOP), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True).stdout.strip()   # a user's checkout, under sudo
    except OSError:
        commit = ''
    return ('dev %s' % commit[:8]) if commit else None, commit or None, None


def install_record(a, c, profile, path, asm, groups, disk, kit=None, now=None):
    """The install record (schema 1) hao status, hao verify and hao update read: what was installed, from what."""
    release, patcher, runtime = release_of()
    recipes = {}
    for names in groups.values():
        for name in names:
            try:
                recipes[name] = json.loads((TOP / 'patches' / name / 'patch.json').read_text()).get('revision')
            except (OSError, ValueError):
                recipes[name] = None
    kit_json = Path(kit) / 'kit.json' if kit else None
    return dict(schema=1, date=(now or datetime.datetime.now().astimezone()).isoformat(timespec='seconds'),
                release=release, hao_patcher=patcher, hao_runtime=runtime, assembly=path.stem,
                patch_set=asm.get('patch_set'), golden_build=asm.get('golden_build'), donor_build=asm.get('donor_build'),
                model=profile.get('model'), board_id=profile.get('board_id'), packs=list(profile.get('packs') or []),
                recipes=recipes, recipes_by_pack={k: list(v) for k, v in groups.items()},
                kit=dict(kit_json_sha256=_sha256(kit_json)) if kit_json and kit_json.is_file() else None,
                disk=disk, command='install' if getattr(a, 'kit', None) else 'build-disk',
                manifests=str(MANIFESTS), unproven=list(getattr(a, 'unproven_reasons', None) or []))


def data_volume(host, disk):
    """The device of the Data volume (APFS role Data) in the container on DISK, or None."""
    try:
        listing = plistlib.loads(host.run(['diskutil', 'apfs', 'list', '-plist'], check=False).stdout or b'')
    except ValueError:
        return None
    for row in listing.get('Containers', []):
        stores = [s.get('DeviceIdentifier', '') for s in row.get('PhysicalStores', [])]
        if any(s == disk or s.startswith(disk + 's') for s in stores):
            for v in row.get('Volumes', []):
                if 'Data' in (v.get('Roles') or []):
                    return v.get('DeviceIdentifier')
    return None


def write_install_record(c, host, disk, record):
    """RECORD on the installed Data volume (root:wheel 0644), mounted for it when it is not; a warning, not a stop,
    when that fails (the system is installed). Returns the path written, or None."""
    dev = data_volume(host, disk)
    mount, mounted_here = None, False
    if dev:
        info = lambda: plistlib.loads(host.run(['diskutil', 'info', '-plist', dev], check=False).stdout or b'{}')
        try:
            mount = info().get('MountPoint')
            if not mount:
                host.run(['diskutil', 'mount', dev], check=False)
                mount, mounted_here = info().get('MountPoint'), True
        except ValueError:
            mount = None
    if not mount:
        c.warning('the install record was not written (no Data volume found on /dev/%s): hao status will not know '
                  'this install' % disk)
        return None
    out = Path(mount) / RECORD
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix('.tmp')
        tmp.write_text(json.dumps(record, indent=1, sort_keys=True) + '\n')
        tmp.chmod(0o644)
        if os.geteuid() == 0:
            os.chown(tmp, 0, 0)
        os.replace(tmp, out)
    except OSError as x:
        c.warning('the install record was not written (%s)' % x)
        return None
    finally:
        if mounted_here:
            host.run(['diskutil', 'unmount', dev], check=False)
    c.event('install-record', path=str(out), device=dev, release=record.get('release'))
    c.item('install record  %s' % c.paint('dim', '/' + str(RECORD)), 'files', ok=True)
    return out


RESTART_SECONDS = 10
BOOTSTRAP = 'System/Library/CoreServices/boot.efi'     # OpenCore's Bootstrap on the EFI partition (as OCLP installs it)


def start_up(c, host, profile, disk):
    """Make DISK the one the machine starts: bless OpenCore's Bootstrap on a Mac; on a VM or PC (the generic image) the
    firmware starts EFI/BOOT/BOOTx64.efi by itself, and bless (Apple firmware only) is not run. True when set."""
    if is_generic_target(profile.get('target')):
        c.item('start-up disk  the firmware starts EFI/BOOT/BOOTx64.efi (no bless on a VM or PC)', 'files', ok=True)
        return True
    return set_startup_disk(c, host, disk)


def set_startup_disk(c, host, disk):
    """Bless the OpenCore Bootstrap on DISK's EFI partition as the startup disk, so a Mac with another bootable disk
    starts hao's macOS. A Mac with only this disk finds the Bootstrap by itself (owner, laptop 1 after an NVRAM reset),
    so a failure here is a warning, not a stop. Returns True when blessed."""
    esp = None
    try:
        listing = plistlib.loads(host.run(['diskutil', 'list', '-plist', disk], check=False).stdout or b'')
        parts = (listing.get('AllDisksAndPartitions') or [{}])[0].get('Partitions', [])
        esp = next((p['DeviceIdentifier'] for p in parts if p.get('Content') == 'EFI'), None)
    except (ValueError, KeyError, IndexError):
        pass
    if esp is None:
        c.warning('the start-up disk was not set (no EFI partition found on /dev/%s): choose it in the Option picker' % disk)
        return False
    host.run(['diskutil', 'mount', esp], check=False)
    try:
        mount = plistlib.loads(host.run(['diskutil', 'info', '-plist', esp], check=False).stdout or b'').get('MountPoint')
    except ValueError:
        mount = None
    target = '%s/%s' % (mount, BOOTSTRAP)
    if mount and not os.path.exists(target) and os.path.exists('%s/EFI/BOOT/BOOTX64.EFI' % mount):
        target = '%s/EFI/BOOT/BOOTX64.EFI' % mount      # the generic layout (VMs, PCs): no Apple-style Bootstrap
    r = host.run(['bless', '--mount', mount, '--file', target, '--setBoot'], check=False) if mount else None
    c.event('startup-disk', disk=disk, esp=esp, mount=mount, ok=bool(r and r.returncode == 0))
    if not (r and r.returncode == 0):
        c.warning('the start-up disk was not set (bless on /dev/%s failed): choose it in the Option picker' % esp)
        return False
    c.item('start-up disk  OpenCore on /dev/%s' % esp, 'steps', ok=True)
    return True


def key_within(seconds):
    """True when a key is pressed on the terminal within SECONDS (read without Return; the terminal is restored)."""
    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        if select.select([fd], [], [], seconds)[0]:
            os.read(fd, 64)
            return True
        return False
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def restart(c, host, wait, seconds=RESTART_SECONDS):
    """Restart the Mac after a visible countdown that any key cancels (events restart.countdown, restart.tick,
    restart.cancelled or restart.now: cli renders them). WAIT(1) is True when a key was pressed in that second."""
    c.event('restart.countdown', seconds=seconds)
    c.print('Restarting in %d s. Press any key to stay in the Recovery.' % seconds, 'quiet')
    for left in range(seconds, 0, -1):
        c.event('restart.tick', left=left)
        if wait(1):
            c.event('restart.cancelled', left=left)
            c.print('Not restarting. Restart from the Apple menu when you are ready.', 'quiet')
            return False
    c.event('restart.now')
    if host.run(['/sbin/reboot'], check=False).returncode:
        c.warning('the Mac did not restart: restart it from the Apple menu')
        return False
    return True
