"""hao create-installer: the one command a user runs to make the USB installer (`./hao create-installer`; it asks for
the password once, only to erase the USB drive).

It asks only what it must, one question at a time, with numbered choices, then runs without further questions:

  1. which Mac the installer is for: a proven model (the first is the default), this Mac (hao detect, offered on an
     Intel Mac), a profile file `hao detect --out` wrote on that Mac, or, when the release carries it, the generic
     image (software graphics, a test baseline for other Intel Macs). This Mac, when it is not a model the release
     can install on, is offered the generic image in a second question (stopping is the default); the generic image
     is only ever made when chosen, there or with --profile generic
  2. which disk is the USB stick: the external, physical whole disks diskutil lists, never an internal disk, the disk
     of the running system or a disk image; a stick that is too small, or that holds this hao folder or its downloads,
     is shown with the reason and cannot be chosen
  3. the confirmation: the disk's name typed, after saying that everything on it will be erased

--model, --profile, the disk argument and --confirm answer them ahead (scripts and tests). Then: Download (the files
release.json pins, beside `hao`, through tools/acquire, each checked by size and sha256), Prepare packs, Write the USB
stick (stick.py: OpenCore and the Recovery on a FAT32 partition, hao and the kit on a Mac OS Extended one).
--dry-run asks the questions and shows the plan; it downloads and writes nothing.

Until the kit is made from the downloads on the user's Mac (workflow plan steps 12/13), the kit is staged from the
checkout's store (golden-run --stage-kit) or named with --kit; the Recovery is the donor build's stock Tahoe
BaseSystem (row `tahoe_basesystem`), or --basesystem FILE. --image FILE (development) writes a raw stick image instead of a
disk: no root, no disk question, no confirmation; QEMU or `dd` take it from there.
"""
import datetime
import json
import os
from pathlib import Path
import plistlib
import runpy
import subprocess
import sys

from console import Stopped, human_size
import builder as BUILD
import detect as DETECT
import disks as DISKS
import doctor as DOCTOR
import frontdoor as FD
import livepatch as LP
import shutil
import space as SPACE
import stick as STICK

TOP = Path(__file__).resolve().parents[3]
ACQUIRE = TOP / 'tools' / 'acquire' / 'acquire'
STICK_GB = 64                      # what the README asks for: a stick sold as 64 GB
MINIMUM_BYTES = 60 * 10 ** 9       # sticks sold as 64 GB hold 60 to 64 * 10^9 bytes
DOCTOR_CHECKS = DOCTOR.checks    # hao doctor's survey; tests put a fake here
STEPS = ('Download', 'Prepare packs', 'Write the USB installer')
# the stick's launchers export HAO_STICK (the HAO volume): there the report command is the stick's own
REPORT = ('save a report for developers: %s/report (it writes the file into the USB installer\'s reports folder; attach it to '
          'a GitHub issue)' % os.environ['HAO_STICK'] if os.environ.get('HAO_STICK') else
          'save a report for developers: ./hao report (attach the file it writes to a GitHub issue)')


# -- questions

def _answer(c, prompt):
    try:
        return c.ask(prompt).strip()
    except EOFError:
        raise Stopped('hao needs an answer and got none',
                      why='it was started without a keyboard to answer its questions',
                      options=['run it in Terminal: ./hao create-installer'],
                      untouched='nothing was downloaded or written') from None


def choose(c, key, question, choices, default=1):
    """Ask QUESTION with CHOICES ([(label, value)], numbered from 1; a None value is listed but cannot be chosen) and
    return the chosen value. An empty answer takes DEFAULT; anything else not on the list asks again."""
    numbered = [(label, value) for label, value in choices if value is not None]
    c.event('question', key=key, question=question, choices=[label for label, _ in choices])
    c.print('', 'quiet')
    c.print(question, 'quiet')
    n = 0
    for label, value in choices:
        if value is None:
            c.print('     -  ' + label, 'quiet')
        else:
            n += 1
            c.print('  %3d  %s' % (n, label), 'quiet')
    for _ in range(3):
        text = _answer(c, 'Choice [%d]: ' % default if default else 'Choice: ')
        if not text and default:
            text = str(default)
        if text.isdigit() and 1 <= int(text) <= len(numbered):
            label, value = numbered[int(text) - 1]
            c.event('answer', key=key, choice=int(text), label=label)
            return value
        c.print('Type a number from 1 to %d.' % len(numbered), 'quiet')
    raise Stopped('no choice made', untouched='nothing was downloaded or written',
                  options=['run it again and type one of the numbers shown'])


def proven_models(release):
    """The models a release is proven on (release.json "proven_models"), else those of this checkout's targets whose
    packs are all proven on them."""
    if release and release.get('proven_models'):
        return list(release['proven_models'])
    out = []
    for p in sorted(DETECT.TARGETS.glob('*.json')):
        model = json.loads(p.read_text()).get('model')
        try:
            if model and DETECT.for_model(model)['proven_model']:
                out.append(model)
        except DETECT.DetectError:
            continue
    return out


GENERIC_LABEL = 'the generic image: software graphics, no GPU acceleration; a test baseline'


def ask_mac(a, c, host, release):
    """Fill a.model, a.profile or a.profile_data from the first question, unless a flag answered it. a.profile may be
    an image profile name (the generic image)."""
    generic = BUILD.generic_profiles(release)
    a.generic_offer = generic[0] if generic else None
    if getattr(a, 'model', None) or getattr(a, 'profile', None) or getattr(a, 'profile_data', None) is not None:
        return
    choices = []
    for model in proven_models(release):
        _, target = DETECT.target_for_model(model)
        choices.append(('%-14s %s  (proven)' % (model, (target or {}).get('gpu') or ''), ('model', model)))
    intel = host.run(['uname', '-m'], check=False).stdout.decode(errors='replace').strip() == 'x86_64'
    if intel:
        choices.append(('This Mac (you are running hao on the Mac you will install on)', ('detect', None)))
    choices.append(('Another Mac, described by a profile file (`hao detect --out mac.json` run on that Mac)',
                    ('profile', None)))
    if a.generic_offer:
        choices.append(('A virtual machine or a non-Apple PC (not a Mac): ' + GENERIC_LABEL, ('generic', a.generic_offer)))
    kind, model = choose(c, 'mac', 'Which Mac is the installer for?  (its Model Identifier: Apple menu > About This '
                                   'Mac > More Info > System Report)', choices)
    if kind == 'model':
        a.model = model
    elif kind == 'generic':
        a.profile = model
    elif kind == 'detect':
        profile = DETECT.profile(DETECT.facts(host))
        profile['target'], _ = DETECT.target_for_model(profile['model'])
        if not profile['target'] and a.generic_offer and getattr(a, 'unproven', False):
            # the generic image is for virtual machines and non-Apple PCs; on a Mac only with --unproven
            pick = choose(c, 'generic', 'This Mac (%s) is not a model this release can install on. Make the generic '
                                        'image (for virtual machines and non-Apple PCs) for it anyway?' % profile['model'],
                          [('No, stop here', 'stop'), ('Yes, ' + GENERIC_LABEL, 'generic')])
            if pick == 'generic':
                a.profile_data, a.profile = profile, a.generic_offer
                return
        if not profile['target']:
            raise Stopped('this Mac (%s) is not one this release can install on' % profile['model'],
                          why='its startup files and hardware support are not described yet',
                          options=['choose a proven model instead', REPORT],
                          untouched='nothing was downloaded or written')
        a.profile_data = profile
    else:
        path = _answer(c, 'Profile file (drag it into this window): ')
        path = path.strip('\'"').replace('\\ ', ' ').strip()
        c.event('answer', key='profile', path=path)
        a.profile = str(Path(path).expanduser())


def release_assembly(release, image=None):
    """The assembly RELEASE builds for image profile IMAGE (its "profiles"), else its top-level assembly (the first
    profile's: a release that knows no profiles has only that one)."""
    if not image:
        return release.get('assembly')
    entry = (release.get('profiles') or {}).get(image)
    if entry is None:
        raise Stopped('release %s has no image profile %s' % (release.get('version') or release.get('release'), image),
                      options=['one of: ' + (', '.join(release.get('profiles') or {}) or 'none (an older release)')],
                      untouched='nothing was downloaded or written')
    return entry['assembly']


PROFILE_FIELDS = ('assembly', 'target', 'patch_set', 'recipes', 'dev_only_recipes', 'proven_models')


def release_view(release, name):
    """RELEASE as one image profile NAME sees it: the profile's assembly, target, set and recipes at the top, only the
    downloads, assets and made-on-Mac rows it uses ("profiles"[NAME]["rows"]) and its own harvested rows."""
    entry = release['profiles'][name]
    rows = set(entry['rows'])
    out = {k: v for k, v in release.items() if k != 'profiles'}
    out.update({k: entry[k] for k in PROFILE_FIELDS if k in entry}, profile=name,
               harvested=list(entry.get('harvested', release.get('harvested', []))))
    out['install_cache_rebuild'] = bool(entry.get('install_cache_rebuild', False))
    for k in ('downloads', 'assets', 'made_on_mac'):
        out[k] = [x for x in release.get(k, []) if x['row'] in rows]
    return out


def signed_release(a, release_path):
    """The release file the USB installer carries: the signed one beside its .sig (hao install checks that signature),
    never profile_release's unsigned view, which only steers the downloads and the rows made here."""
    return getattr(a, 'signed_release_path', None) or release_path


def profile_release(a, path, release, cache):
    """(path, release) the rest of create-installer reads: RELEASE itself when it carries one image profile (or knows
    none), else its view for the chosen profile (release_view), written beside the downloads: the download, the rows
    made on this Mac and the USB installer then hold only what that image uses."""
    profiles = release.get('profiles') or {}
    if len(profiles) < 2:
        return path, release
    name = BUILD.image_profile(a) or next((n for n, p in profiles.items() if p.get('assembly') == a.assembly), None)
    if name is None:
        return path, release
    view = release_view(release, name)
    folder = Path(cache) / 'releases'
    if a.dry_run:
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='hao-release-'))
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / ('%s-%s.json' % (release.get('release', 'release'), name))
    out.write_text(json.dumps(view, indent=1) + '\n')
    files = len(view['downloads']) + len(view['assets'])
    a.release_view = dict(profile=name, path=str(out), files=files)
    return out, view


def plain_refusal(why):
    """disks.refusal's reason in a user's words."""
    for start, words in (('not a whole disk name', 'it is not a whole disk; name the whole disk, like disk4'),
                         ('diskutil info failed', 'there is no such disk'),
                         ('not a whole disk', 'it is part of a disk; name the whole disk, like disk4'),
                         ('virtual', 'it is a disk image, not a USB drive'),
                         ('internal', 'it is this Mac\'s internal disk'),
                         ('holds the running system', 'it holds the macOS you are running'),
                         ('too small', 'it is too small: a USB drive of at least %d GB is needed' % STICK_GB),
                         ('holds this command', 'it holds this hao folder or its downloads')):
        if why.startswith(start):
            return words
    return why


def sticks(host, minimum, sources):
    """[(disk, diskutil info, refusal or None)] of the external physical whole disks: the ones that may be erased,
    and the ones a user would recognise but that are refused (too small, holding hao's files). Internal disks, the
    running system's and disk images are left out."""
    r = host.run(['diskutil', 'list', '-plist', 'external', 'physical'], check=False)
    try:
        whole = plistlib.loads(r.stdout).get('WholeDisks', []) if r.returncode == 0 else []
    except Exception:
        whole = []
    out = []
    for disk in whole:
        why = DISKS.refusal(host, disk, 'external', minimum, sources=sources)
        if why and not why.startswith(('too small', 'holds this command')):
            continue
        info, _ = DISKS.describe(host, disk)
        out.append((disk, info, why))
    return out


def ask_disk(a, c, host, minimum, sources):
    """The stick: the disk argument checked, else the second question. Returns its diskutil info."""
    if a.disk:
        why = DISKS.refusal(host, a.disk, 'external', minimum, sources=sources)
        if why:
            raise Stopped('hao will not erase %s: %s' % (DISKS.normalize(a.disk), plain_refusal(why)),
                          why='hao erases only an external disk it can tell is the USB drive you mean',
                          untouched='nothing was downloaded or written',
                          options=['run it again and choose the USB drive from the list'])
        return DISKS.describe(host, a.disk)[0]
    while True:
        found = sticks(host, minimum, sources)
        usable = [(d, info) for d, info, why in found if not why]
        if usable:
            break
        for d, info, why in found:
            c.print('  %s: %s' % (DISKS.label(info), plain_refusal(why)), 'quiet')
        c.event('question', key='plug-in')
        c.print('', 'quiet')
        c.print('No USB drive found. Plug in a USB drive of at least %d GB.' % STICK_GB, 'quiet')
        if _answer(c, 'Press Return to look again, or type q to quit: ').lower().startswith('q'):
            raise Stopped('no USB drive chosen', untouched='nothing was downloaded or written',
                          options=['plug in a USB drive of at least %d GB and run it again' % STICK_GB])
    choices = [(DISKS.label(info) + ('  ' + plain_refusal(why) if why else ''), None if why else disk)
               for disk, info, why in found]
    a.disk = choose(c, 'disk', 'Which disk is the USB drive?', choices, default=None)
    return dict(usable)[a.disk]


def ask_internal_disk(a, c, host, minimum, sources):
    """hao install's question when no disk is named (the stick's Recovery): which internal disk to erase. Lists the
    internal physical whole disks; one refused (too small, holding the running system or the stick's files) is shown
    with the reason and cannot be chosen. Returns the disk name."""
    r = host.run(['diskutil', 'list', '-plist', 'internal', 'physical'], check=False)
    try:
        whole = plistlib.loads(r.stdout).get('WholeDisks', []) if r.returncode == 0 else []
    except Exception:
        whole = []
    choices = []
    for disk in whole:
        why = DISKS.refusal(host, disk, 'internal', minimum, sources=sources)
        try:
            info, _ = DISKS.describe(host, disk)
        except LP.PatchError:
            continue
        choices.append((DISKS.label(info) + ('  (%s)' % why if why else ''), None if why else disk))
    if not any(v for _, v in choices):
        for label, _ in choices:
            c.print('  ' + label, 'quiet')
        raise Stopped('no internal disk macOS 27 can be installed on',
                      why='hao installs only onto an internal disk of at least %s' % human_size(minimum),
                      options=['in Disk Utility, check that the internal disk is there', REPORT],
                      untouched='nothing was written')
    return choose(c, 'disk', 'Which disk should macOS 27 be installed on? Everything on it will be erased.', choices,
                  default=None)


# -- the steps

def load_release(path):
    if not path or not Path(path).is_file():
        return None
    return json.loads(Path(path).read_text())


def administrator(c, host, disk, run=subprocess.run):
    """As a user: ask for the password once, now, for the one step that needs it (erasing DISK); the erase commands
    then run through sudo -n (stick.as_admin). As root: nothing to ask."""
    if host.euid() == 0:
        return
    c.item('erasing the USB drive needs an administrator: macOS asks for your password (once, for that step only)',
           'quiet', ev='admin.ask', disk=disk)
    try:
        ok = run(['sudo', '-v', '-p', 'Password to erase /%s (hao asks once): ' % ('dev/' + disk)]).returncode == 0
    except OSError:
        ok = False
    if not ok:
        raise Stopped('the USB drive was not erased: no administrator password was given',
                      why='erasing a drive needs an administrator\'s password',
                      options=['run ./hao create-installer again and type your password when asked'],
                      untouched='nothing was written to the USB drive')


def no_release(path):
    return Stopped('there is no release.json beside hao (%s)' % path,
                   why='it lists every file to download, each with its size and sha256; hao downloads nothing else',
                   options=['download the release archive from the project\'s GitHub releases page, unpack it and '
                            'run ./hao create-installer in its folder'],
                   untouched='nothing was downloaded or written')


def origin_of(row):
    """The origin tag of a download row (the mockups' Apple / Tahoe / OCLP / hao), '' for other sources (Python). Our assets are hao's."""
    name, url = row.get('name', ''), row.get('url', '')
    if '25G' in name or row.get('row') in ('installer', 'kdk'):
        return 'tahoe'
    if 'apple.com' in url:
        return 'apple'
    if 'PatcherSupportPkg' in url or 'OpenCore' in url:
        return 'oclp'
    if row.get('kind') == 'asset' or 'hao-assets' in url:
        return 'hao'
    return ''


def download(c, release_path, cache, acquire, dry_run):
    """Step 1: every file release.json pins, into the cache, each checked by size and sha256."""
    try:
        rows = acquire['load'](release_path)
    except acquire['AcquireError'] as x:
        raise Stopped('release.json is damaged: %s' % x, why='every file must be pinned by URL, size and sha256',
                      options=['download the release archive again'], untouched='nothing was downloaded or written')
    total = sum(r['size'] for r in rows if not r.get('stream'))   # a streamed one is read by range, not downloaded
    have = [r for r in rows if acquire['final_path'](cache, r).is_file() and
            acquire['final_path'](cache, r).stat().st_size == r['size']] if dry_run else []
    c.event('download.plan', files=len(rows), bytes=total, cache=str(cache))
    if dry_run:
        c.item('%d files, %s, each checked by size and sha256%s' % (
            len(rows), human_size(total), ', %d already downloaded' % len(have) if have else ''))
        return rows
    done = {}
    by_name = {r['name']: r for r in rows}
    tty = hasattr(c.out, 'isatty') and c.out.isatty() and c.level == 'steps'

    def on(ev):
        k = ev['ev']
        if k == 'progress':
            done[ev['name']] = ev['done']
            if tty:
                print('\r  ' + c.paint('bar', '%3d%%  %s of %s ' % (100 * sum(done.values()) // max(total, 1),
                                                                 human_size(sum(done.values())), human_size(total))),
                      end='', file=c.out, flush=True)
            return
        c.event('fetch.' + k, **{x: y for x, y in ev.items() if x != 'ev'})
        if k == 'streamed':
            c.item('%s %-44s %s' % (c.paint.origin(origin_of(by_name.get(ev['name'], {})), 6), ev['name'],
                                    'read by range when the rows are made'), 'files', ok=True)
        if k in ('ok', 'reused'):
            done[ev['name']] = next(r['size'] for r in rows if r['name'] == ev['name'])
            c.item('%s %-44s %s' % (c.paint.origin(origin_of(by_name.get(ev['name'], {})), 6), ev['name'],
                                    'already here' if k == 'reused' else 'sha256'), 'files', ok=True)
        elif k == 'mirror-failed':
            c.item('%-44s %s: %s' % (ev['name'], ev['url'], ev['error']), 'files')
    try:
        acquire['fetch'](rows, cache, on_event=on)
    except acquire['AcquireError'] as x:
        if tty:
            print('', file=c.out)
        raise Stopped('a download failed', cause=str(x).splitlines()[0],
                      why='hao uses only the exact files the release pins',
                      options=['check the internet connection and run the same command again: finished files are '
                               'kept and a partial one resumes', REPORT],
                      untouched='the USB drive is erased and holds no installer yet')
    if tty:
        print('\r' + ' ' * 40 + '\r', end='', file=c.out, flush=True)
    c.item('%d files, %s' % (len(rows), human_size(total)), ok=True, ev='download.done', files=len(rows))
    return rows


def show_plan(c, release, cache):
    p = SPACE.plan(release, cache)
    for line in SPACE.lines(p):
        c.print(line, 'quiet')
    c.event('space.plan', mac_peak_gb=round(p['mac_peak'], 1), drive_need_gb=round(p['drive_need'], 1),
            drive_size_gb=SPACE.drive_size_gb(p), download_gb=round(p['download'], 1))
    return p


def check_space(c, release, cache, drive_bytes):
    """Before anything is written: this Mac's free space against the plan's peak, the drive against its need."""
    p = SPACE.plan(release, cache)
    where = cache if Path(cache).exists() else Path.home()
    bad = SPACE.problems(p, shutil.disk_usage(where).free, drive_bytes)
    c.event('space.check', mac_peak_gb=round(p['mac_peak'], 1), drive_need_gb=round(p['drive_need'], 1),
            problems=bad)
    if bad:
        raise Stopped(bad[0], why='hao checks the space every stage needs before it writes anything',
                      options=['see each stage: ./hao create-installer --plan'] + (
                          ['free space on this Mac and run it again'] if 'this Mac' in bad[0] else
                          ['use a USB drive of at least %d GB' % SPACE.drive_size_gb(p)]),
                      untouched='nothing was downloaded or written')
    c.item('space  %.1f GB at the peak on this Mac, %.1f GB on the USB drive' % (p['mac_peak'], p['drive_need']),
           ok=True)
    return p


def make_rows(a, c, release_path, cache, userspace_on, run=subprocess.call):
    """place + make-on-mac: the rows the release does not ship, made here from Apple's files (the IPSW and the
    installer read by range), the userspace images straight onto the drive. The plan's check has run, so the
    maintainers' disk floor (pipeline/diskguard.py) is not applied."""
    env = dict(os.environ, HAO_DISK_FLOOR_GB=os.environ.get('HAO_DISK_FLOOR_GB', '1'))
    (TOP / 'store').mkdir(exist_ok=True)
    import safety as SAFE
    for where in (cache, TOP / 'store', release_path):   # what root unpacks and runs from: no one else's to change
        why = SAFE.others_can_change(where)
        if why:
            raise Stopped('the inputs were not made: %s' % why, untouched='the USB drive is erased and holds no installer yet',
                          options=['make that folder yours and not writable by others (chmod go-w), or use another'])
    place = [sys.executable, '-I', str(TOP / 'pipeline' / 'place'), str(release_path), str(cache)]
    # place again after the producers: it completes the rows that are plain copies out of rows made here
    for cmd in (place, [sys.executable, '-I', str(TOP / 'pipeline' / 'make-on-mac'), str(release_path),
                        '--userspace-on', str(userspace_on), '--cache', str(cache)], place):
        c.event('command', argv=cmd)
        if run(cmd, env=env):
            raise Stopped('making the inputs failed (%s)' % Path(cmd[2]).name,
                          options=['run the same command again: rows already made are kept', REPORT],
                          untouched='the USB drive is erased and holds no installer yet')
    c.item('the rows made on this Mac all check (pipeline/make-on-mac)', ok=True)


def preflight(c, release, cache):
    """hao doctor --for create-installer, before any question: a failed check stops with doctor's own exit code."""
    failed = DOCTOR.show(c, DOCTOR_CHECKS('create-installer', FD.where(), release, cache, offline=False),
                         'create-installer')
    if failed:
        raise Stopped('%d check%s failed: %s' % (len(failed), '' if len(failed) == 1 else 's',
                                                 ', '.join(r['name'] for r in failed)),
                      why='hao create-installer would stop or make different bytes on this Mac',
                      untouched='nothing was downloaded or written',
                      options=[r['fix'] for r in failed if r['fix']] + ['see every check: ./hao doctor'], code=5)


def create_installer(a, c, host, acquire=None):
    release_path = Path(getattr(a, 'release', None) or TOP / 'tools' / 'hao' / 'release.json')
    cache = Path(getattr(a, 'cache', None) or BUILD.BUILDS.parent)
    minimum = getattr(a, 'minimum_bytes', None) or MINIMUM_BYTES
    release = load_release(release_path)
    if getattr(a, 'plan', False):
        if release is None:
            raise no_release(release_path)
        show_plan(c, release, cache)
        return 0
    preflight(c, release, cache)
    c.print('hao makes a USB installer for macOS 27 on an Intel Mac. It asks which Mac and which USB drive, then works on '
            'its own.', 'quiet')
    image = getattr(a, 'image', None)
    folder = Path(a.folder).expanduser().resolve() if getattr(a, 'folder', None) else None
    if folder is not None:      # HAO's contents only, for a volume made elsewhere (a NetBoot Recovery fetches it)
        if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
            raise Stopped('%s is not a new or empty folder' % folder, untouched='nothing was downloaded or written',
                          options=['name a new folder: --folder DIR'])
        image = str(folder)     # from here on it is written like an image: no root, no disk question, no erase
    if not a.dry_run and not image:
        if release is None and not getattr(a, 'kit', None):
            raise no_release(release_path)
    ask_mac(a, c, host, release)
    if release and not getattr(a, 'assembly', None):
        a.assembly = release_assembly(release, BUILD.image_profile(a))
    if getattr(a, 'kit', None) and not getattr(a, 'assembly', None):
        a.assembly = kit_assembly(a.kit)
    if release:
        a.signed_release_path = release_path   # the signed release (and its .sig) is what the USB installer carries
        release_path, release = profile_release(a, release_path, release, cache)
    a.sources = input_paths(a, cache, release_path)
    if folder is not None:
        info = dict(DeviceIdentifier='folder', MediaName=str(folder), TotalSize=None)
    elif image:
        info = dict(DeviceIdentifier='image', MediaName=str(image), TotalSize=a.image_size)
    else:
        info = ask_disk(a, c, host, minimum, a.sources)
    disk = DISKS.normalize(a.disk) if not image else None
    c.print('', 'quiet')
    profile, path, asm, groups = BUILD.resolve(a, c, 'create-installer')
    if folder is not None:
        c.print('Folder %s  (the USB installer\'s HAO volume only: hao, the kit, install and report)' % folder)
    elif image:
        c.print('Drive  image file %s (%s, sparse)' % (image, human_size(a.image_size)))
    else:
        c.print('Drive  %s  external, big enough %s' % (DISKS.label(info), c.ok))
        c.event('disk', disk=disk, policy='external', size=info.get('TotalSize') or info.get('Size'))
    BUILD.show_statement(c)                 # every run, before any confirmation (owner O-2)
    c.print(STICK.CUSTODY, 'quiet')
    if not a.dry_run and not image:
        c.print('')
        expect = dict(identity=DISKS.identity(host, disk), policy='external', minimum=minimum, sources=a.sources)
        if not c.confirm('Everything on %s will be erased.' % DISKS.label(info), disk, a.confirm):
            raise Stopped('not confirmed', untouched='nothing was downloaded or written')
        a.disk_expect = expect
    make_here = bool(release and release.get('made_on_mac') and not getattr(a, 'kit', None))
    if make_here and not a.dry_run:
        check_space(c, release, cache, info.get('TotalSize') or info.get('Size'))
    parts = None
    if not a.dry_run and folder is not None:
        (folder / STICK.BUILD_DIR).mkdir(parents=True, exist_ok=True)
        a.drive_build = folder / STICK.BUILD_DIR
    elif not a.dry_run:
        # erase the drive first: the userspace images are made straight onto it
        opencore = opencore_input(a, asm)
        if image:
            disk = STICK.attach_image(host, Path(image).expanduser().resolve(), a.image_size)
            c.item('USB installer image %s attached as /dev/%s' % (image, disk), 'files')
        if not image and not STICK.own_device(disk):
            administrator(c, host, disk)
        try:
            parts = STICK.prepare(host, disk, opencore, getattr(a, 'basesystem', None),
                                  on_step=lambda text: c.item(text, 'steps', ok=True),
                                  expect=getattr(a, 'disk_expect', None))
        except (STICK.StickError, OSError, LP.PatchError) as x:
            raise Stopped('erasing the USB drive failed', cause=str(x).splitlines()[0], options=[REPORT],
                          untouched='nothing was downloaded')
        a.drive_build = Path(parts[STICK.KIT_NAME][1]) / STICK.BUILD_DIR
    c.steps(len(STEPS))
    c.step(STEPS[0], plan=a.dry_run)
    if getattr(a, 'kit', None):
        c.item('the kit is given (--kit): it holds every input, nothing is downloaded', ev='download.plan', files=0)
    elif release is None:
        c.item('no release.json beside hao: the files to download are not known here', ev='download.plan', files=None)
    else:
        download(c, release_path, cache, acquire or runpy.run_path(str(ACQUIRE)), a.dry_run)
    c.step(STEPS[1], plan=a.dry_run)
    c.item('%d packs, %d recipes chosen for %s' % (len(profile['packs']), sum(len(v) for v in groups.values()),
                                             profile['model']), ok=not a.dry_run, ev='packs', packs=profile['packs'])
    for pack, names in groups.items():
        c.item('%s %d recipes' % (c.paint('bold', '%-22s' % pack), len(names)), 'files')
    if make_here and not a.dry_run:
        if parts is not None:
            try:
                STICK.check_mounts(host, disk, parts)    # the images go straight onto HAO: it must still be the drive
            except STICK.StickError as x:
                raise Stopped('the USB drive changed while hao was working', cause=str(x), options=[REPORT],
                              untouched='nothing more was written')
        make_rows(a, c, release_path, cache, a.drive_build)
    if a.dry_run:
        c.step(STEPS[2], plan=True)
        c.item('erase %s; partition 1: OpenCore and the Recovery; partition 2: hao, the kit and the install command'
               % (disk or 'the image'))
        c.done('dry run: nothing was downloaded or written')
        return 0
    kit = prepare_kit(a, c, path)
    a.kit_dir = kit
    basesystem, opencore = stick_inputs(a, asm)
    c.step(STEPS[2])
    record = dict(created=datetime.datetime.now().isoformat(timespec='seconds'), model=profile['model'],
                  target=asm['target'], assembly=path.stem, golden_build=asm['golden_build'],
                  donor_build=asm['donor_build'], basesystem=Path(basesystem).name,
                  hao_commit=BUILD.release_of()[1],      # the install record names it (a dev drive has no git)
                  unproven=list(getattr(a, 'unproven_reasons', None) or []))   # its install launcher passes --unproven
    if getattr(a, 'image_profile', None):
        record['profile'] = a.image_profile
    if folder is not None:
        try:
            rec = STICK.write_kit(folder, kit, TOP, signed_release(a, release_path) if release else None, record)
        except (STICK.StickError, OSError, LP.PatchError) as x:
            raise Stopped('writing the folder failed', cause=str(x).splitlines()[0], options=[REPORT])
        c.item('%s  hao, the kit (%.1f GB), install and report; checked file by file' % (folder, rec['kit_bytes'] / 1e9),
               'steps', ok=True)
        c.event('stick.written', stick=rec)
        left = finish_kit(a, c, kit) + [STICK.CUSTODY]
        c.done('USB installer contents written to %s (a volume with these files at its root installs as the USB '
               'installer does: ./install)' % folder, more=left)
        return 0
    try:
        rec = STICK.write(host, disk, basesystem, opencore, kit, TOP,
                          release=signed_release(a, release_path) if release else None,
                          record=record, on_step=lambda text: c.item(text, 'steps', ok=True), parts=parts)
    except (STICK.StickError, OSError, LP.PatchError) as x:
        raise Stopped('writing the USB installer failed', cause=str(x).splitlines()[0],
                      why='the USB drive is erased and incomplete; it will not start',
                      options=['run the same command again: it starts from an empty USB drive; the downloads are kept',
                               REPORT])
    finally:
        if image:
            host.run(['hdiutil', 'detach', '/dev/' + disk], check=False, timeout=300)
    c.event('stick.written', stick=rec)
    left = finish_kit(a, c, kit) + [STICK.CUSTODY]
    if image:
        c.done('USB installer image written: %s' % image, disk=disk, more=left)
        return 0
    # the writer leaves the stick unmounted; eject it, so the last line can say it may be unplugged
    ejected = host.run(['diskutil', 'eject', '/dev/' + disk], check=False, timeout=300).returncode == 0
    c.done('the USB installer is ready%s. On the Intel Mac: hold Option while it starts, choose EFI Boot, then in the '
           'Recovery open Terminal and run /Volumes/HAO/install' % (
               ' and ejected: you can unplug it' if ejected else
               ' (it could not be ejected: eject it in Finder before unplugging)'), disk=disk, ejected=ejected,
           more=left)
    return 0


def finish_kit(a, c, kit):
    """After the drive is written and its kit checked file by file (stick.verify_kit): remove the kit staged on this
    Mac (its folder WORK/stage; the record stays), unless --keep-kit or the kit was given with --kit. Returns the
    summary lines."""
    if getattr(a, 'kit', None):
        return []
    stage = Path(kit).parent
    if stage.name != 'stage' or not (stage / 'share' / 'kit.json').is_file():
        return []
    if getattr(a, 'keep_kit', False):
        c.event('kit.kept', path=str(stage))
        return ['The staged kit stays on this Mac (--keep-kit): %s' % stage]
    before = shutil.disk_usage(stage).free        # what removing it frees: clones and hard links of the store free little
    shutil.rmtree(stage, ignore_errors=True)
    if stage.exists():
        c.warning('the staged kit could not be removed: %s' % stage)
        return ['The staged kit is still on this Mac: %s' % stage]
    freed = max(0, shutil.disk_usage(stage.parent).free - before)
    c.event('kit.removed', path=str(stage), freed=freed)
    c.item('removed the staged kit from this Mac (the USB installer has it)', 'steps', ok=True)
    return ['%s on this Mac: the staged kit is on the USB installer now (--keep-kit keeps it)'
            % ('%s freed' % human_size(freed) if freed >= 1 << 20 else 'Space freed')]


def input_paths(a, cache, release_path):
    """Every place create-installer reads from or works in, as existing paths (a path not made yet: its nearest
    existing folder; a link: where it points too): the drive that holds any of them is never erased."""
    out = []
    for p in [TOP, cache, release_path] + [getattr(a, k, None) for k in ('kit', 'basesystem', 'opencore', 'work')]:
        if not p:
            continue
        p = Path(p).expanduser()
        for q in (p, p.resolve()):
            while not q.exists() and q != q.parent:
                q = q.parent
            if str(q) not in out:
                out.append(str(q))
    return out


def lock_path(*argv):
    """pipeline/lock.py path ...: an input row's file, hash-checked."""
    r = subprocess.run([sys.executable, '-I', str(TOP / 'pipeline' / 'lock.py'), 'path'] + [str(x) for x in argv],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode:
        raise Stopped('an input is missing or has the wrong sha256', cause=r.stderr.decode(errors='replace').strip()[:300],
                      untouched='the USB drive was not touched')
    return Path(r.stdout.decode().strip())


def opencore_input(a, asm):
    """--opencore, else the target's esp row when this store has it, else None (the kit's own ESP is used later)."""
    if getattr(a, 'opencore', None):
        return Path(a.opencore).expanduser().resolve()
    try:
        return lock_path(TOP / 'pipeline' / 'targets' / (asm['target'] + '.json'), 'esp')
    except Stopped:
        return None


def kit_esp_tar(kit, work):
    """The ESP the kit's install writes (golden-run's build-efi.py from pinned OCLP resources: EFI/OC and the
    Bootstrap), as a tar for the stick: a fresh Mac has no harvested esp row."""
    import tarfile
    esp = Path(kit) / 'assembly-source' / 'esp'
    if not (esp / 'EFI' / 'OC' / 'config.plist').is_file():
        return None
    out = Path(work) / 'kit-esp.tar'
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, 'w') as t:
        t.add(esp, arcname='.')
    return out


def stick_inputs(a, asm):
    """(BaseSystem.dmg, the target's ESP tar): --basesystem or the donor build's stock Tahoe BaseSystem (row
    tahoe_basesystem, made on the Mac by donor-inputs); the ESP row
    of targets/TARGET.json (OpenCore as OCLP installs it on that model)."""
    basesystem = getattr(a, 'basesystem', None)
    if basesystem:
        basesystem = Path(basesystem).expanduser().resolve()
    else:
        basesystem = lock_path(TOP / 'pipeline' / 'inputs' / asm['donor_build'], 'tahoe_basesystem')
    if not basesystem.is_file():
        raise Stopped('no BaseSystem.dmg at %s' % basesystem, untouched='the USB drive was not touched')
    if getattr(a, 'opencore', None):
        opencore = Path(a.opencore).expanduser().resolve()
        if not opencore.is_file():
            raise Stopped('no OpenCore ESP file at %s' % opencore, untouched='the USB drive was not touched')
        return basesystem, opencore
    opencore = opencore_input(a, asm)
    if opencore is None and getattr(a, 'kit_dir', None):
        opencore = kit_esp_tar(a.kit_dir, Path(a.kit_dir).parent)
    if opencore is None:
        raise Stopped('no OpenCore ESP: neither the target\'s esp row nor the kit\'s own ESP is there',
                      untouched='the USB drive is erased and holds no installer yet')
    return basesystem, opencore


def kit_assembly(kit):
    """The assembly a staged kit was made from (kit.json), or None."""
    try:
        return json.loads((Path(kit).expanduser() / 'kit.json').read_text()).get('assembly')
    except (OSError, ValueError):
        return None


def link_on_target(a):
    """Whether hao install links the kernel collections on the target (the default, the release path) rather than
    the kit carrying prelinked ones (--prelinked, development: they come from a builder-VM build)."""
    return not getattr(a, 'prelinked', False)


def prepare_kit(a, c, path, run=subprocess.call):
    """The kit the stick carries: --kit, else staged now (golden-run --stage-kit) from the checkout's store."""
    if getattr(a, 'kit', None):
        kit = Path(a.kit).expanduser().resolve()
    else:
        work = Path(getattr(a, 'work', None) or BUILD.BUILDS / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')).resolve()
        cmd = [sys.executable, '-I', str(TOP / 'pipeline' / 'golden-run'), str(path), '--stage-kit'] + (
            ['--link-on-target'] if link_on_target(a) else []) + (
            [] if getattr(a, 'cryptex_cache', False) else ['--no-cryptex-cache']) + [
               '--work', str(work / 'stage'), '--record', str(work / 'record')]
        c.event('command', argv=cmd)
        c.item('prepare the kit  ' + c.paint('dim', str(work / 'stage' / 'share')), 'files')
        drive = getattr(a, 'drive_build', None)
        env = dict(os.environ, KIT_ON_DRIVE=str(Path(drive).resolve()),
                   HAO_DISK_FLOOR_GB=os.environ.get('HAO_DISK_FLOOR_GB', '1')) if drive else None
        if (run(cmd, env=env) if env else run(cmd)):
            raise Stopped('preparing the kit failed', options=['the record is in %s' % (work / 'record'), REPORT],
                          untouched='the USB drive was not touched')
        kit = work / 'stage' / 'share'
    if not (kit / 'kit.json').is_file():
        raise Stopped('%s is no kit (no kit.json)' % kit, untouched='the USB drive was not touched')
    prelinked = prelinked_kc(kit)
    if prelinked is None and link_on_target(a):
        c.item('kit  %s  (kernel collections linked by the install, as root)' % c.paint('dim', str(kit)), 'files',
               ok=True)
        return kit
    if prelinked is None:
        raise Stopped('the kit has no prelinked kernel collections (--prelinked)',
                      why='--prelinked asks for collections linked in advance, and this kit has none',
                      options=['leave out --prelinked: hao install then links them on the target Mac',
                               'or build this image once in the builder VM (hao build-image, or golden-run ASSEMBLY '
                               '--repo REPO): it stores them, and a kit staged afterwards carries them',
                               'the kit record names the key it looked for: %s' % (kit.parent.parent / 'record')],
                      untouched='the USB drive was not touched')
    c.item('kit  %s  (kernel collections prelinked, key %s)' % (c.paint('dim', str(kit)), prelinked['key']),
           'files', ok=True)
    return kit


def prelinked_kc(kit):
    """The kit's prelinked/prelinked.json of darwin27-kernel (pipeline/prelinked_kc.py), or None."""
    for found in sorted(Path(kit).glob('hooks/*darwin27-kernel/prelinked/prelinked.json')):
        return json.loads(found.read_text())
    return None
