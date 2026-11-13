"""hao create-installer: the one command a user runs to make the USB installer (`sudo ./hao create-installer`).

It asks only what it must, one question at a time, with numbered choices, then runs without further questions:

  1. which Mac the installer is for: a proven model (the first is the default), this Mac (hao detect, offered on an
     Intel Mac), or a profile file `hao detect --out` wrote on that Mac
  2. which disk is the USB stick: the external, physical whole disks diskutil lists, never an internal disk, the disk
     of the running system or a disk image; a stick that is too small, or that holds this hao folder or its downloads,
     is shown with the reason and cannot be chosen
  3. the confirmation: the disk's name typed, after saying that everything on it will be erased

--model, --profile, the disk argument and --confirm answer them ahead (scripts and tests). Then: Download (the files
release.json pins, beside `hao`, through tools/acquire, each checked by size and sha256), Prepare packs, Write the USB
stick. Writing the stick is not built yet: the command stops there, says so in plain words and leaves the stick as it
was. --dry-run asks the questions and shows the plan; it downloads and writes nothing.
"""
import json
import os
from pathlib import Path
import plistlib
import runpy
import sys

from console import Stopped, human_size
import builder as BUILD
import detect as DETECT
import disks as DISKS

TOP = Path(__file__).resolve().parents[3]
ACQUIRE = TOP / 'tools' / 'acquire' / 'acquire'
STICK_GB = 64                      # what the README asks for: a stick sold as 64 GB
MINIMUM_BYTES = 60 * 10 ** 9       # sticks sold as 64 GB hold 60 to 64 * 10^9 bytes
STEPS = ('Download', 'Prepare packs', 'Write the USB stick')
REPORT = 'save a report for developers: ./hao report (attach the file it writes to a GitHub issue)'


# -- questions

def _answer(c, prompt):
    try:
        return c.ask(prompt).strip()
    except EOFError:
        raise Stopped('hao needs an answer and got none',
                      why='it was started without a keyboard to answer its questions',
                      options=['run it in Terminal: sudo ./hao create-installer'],
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


def ask_mac(a, c, host, release):
    """Fill a.model, a.profile or a.profile_data from the first question, unless a flag answered it."""
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
    kind, model = choose(c, 'mac', 'Which Mac is the installer for?  (its Model Identifier: Apple menu > About This '
                                   'Mac > More Info > System Report)', choices)
    if kind == 'model':
        a.model = model
    elif kind == 'detect':
        profile = DETECT.profile(DETECT.facts(host))
        profile['target'], _ = DETECT.target_for_model(profile['model'])
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


def plain_refusal(why):
    """disks.refusal's reason in a user's words."""
    for start, words in (('not a whole disk name', 'it is not a whole disk; name the whole disk, like disk4'),
                         ('diskutil info failed', 'there is no such disk'),
                         ('not a whole disk', 'it is part of a disk; name the whole disk, like disk4'),
                         ('virtual', 'it is a disk image, not a USB stick'),
                         ('internal', 'it is this Mac\'s internal disk'),
                         ('holds the running system', 'it holds the macOS you are running'),
                         ('too small', 'it is too small: a stick of at least %d GB is needed' % STICK_GB),
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
                          why='hao erases only an external disk it can tell is the USB stick you mean',
                          untouched='nothing was downloaded or written',
                          options=['run it again and choose the stick from the list'])
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
        c.print('No USB stick found. Plug in a stick of at least %d GB.' % STICK_GB, 'quiet')
        if _answer(c, 'Press Return to look again, or type q to quit: ').lower().startswith('q'):
            raise Stopped('no USB stick chosen', untouched='nothing was downloaded or written',
                          options=['plug in a USB stick of at least %d GB and run it again' % STICK_GB])
    choices = [(DISKS.label(info) + ('  ' + plain_refusal(why) if why else ''), None if why else disk)
               for disk, info, why in found]
    a.disk = choose(c, 'disk', 'Which disk is the USB stick?', choices, default=None)
    return dict(usable)[a.disk]


# -- the steps

def load_release(path):
    if not path or not Path(path).is_file():
        return None
    return json.loads(Path(path).read_text())


def no_release(path):
    return Stopped('there is no release.json beside hao (%s)' % path,
                   why='it lists every file to download, each with its size and sha256; hao downloads nothing else',
                   options=['download the release archive from the project\'s GitHub releases page, unpack it and '
                            'run sudo ./hao create-installer in its folder'],
                   untouched='nothing was downloaded or written')


def download(c, release_path, cache, acquire, dry_run):
    """Step 1: every file release.json pins, into the cache, each checked by size and sha256."""
    try:
        rows = acquire['load'](release_path)
    except acquire['AcquireError'] as x:
        raise Stopped('release.json is damaged: %s' % x, why='every file must be pinned by URL, size and sha256',
                      options=['download the release archive again'], untouched='nothing was downloaded or written')
    total = sum(r['size'] for r in rows)
    have = [r for r in rows if acquire['final_path'](cache, r).is_file() and
            acquire['final_path'](cache, r).stat().st_size == r['size']] if dry_run else []
    c.event('download.plan', files=len(rows), bytes=total, cache=str(cache))
    if dry_run:
        c.item('%d files, %s, each checked by size and sha256%s' % (
            len(rows), human_size(total), ', %d already downloaded' % len(have) if have else ''))
        return rows
    done = {}
    tty = hasattr(c.out, 'isatty') and c.out.isatty() and c.level == 'steps'

    def on(ev):
        k = ev['ev']
        if k == 'progress':
            done[ev['name']] = ev['done']
            if tty:
                print('\r  %3d%%  %s of %s ' % (100 * sum(done.values()) // max(total, 1),
                                               human_size(sum(done.values())), human_size(total)),
                      end='', file=c.out, flush=True)
            return
        c.event('fetch.' + k, **{x: y for x, y in ev.items() if x != 'ev'})
        if k in ('ok', 'reused'):
            done[ev['name']] = next(r['size'] for r in rows if r['name'] == ev['name'])
            c.item('%-44s %s' % (ev['name'], 'already here' if k == 'reused' else 'sha256'), 'files', ok=True)
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
                      untouched='the stick was not touched')
    if tty:
        print('\r' + ' ' * 40 + '\r', end='', file=c.out, flush=True)
    c.item('%d files, %s' % (len(rows), human_size(total)), ok=True, ev='download.done', files=len(rows))
    return rows


def create_installer(a, c, host, acquire=None):
    release_path = Path(getattr(a, 'release', None) or TOP / 'tools' / 'hao' / 'release.json')
    cache = Path(getattr(a, 'cache', None) or BUILD.BUILDS.parent)
    minimum = getattr(a, 'minimum_bytes', None) or MINIMUM_BYTES
    release = load_release(release_path)
    c.print('hao makes a USB installer for macOS 27 on an Intel Mac. It asks which Mac and which stick, then works on '
            'its own.', 'quiet')
    if not a.dry_run:
        if host.euid() != 0:
            raise Stopped('hao create-installer needs administrator rights to erase the USB stick',
                          options=['run it again as: sudo ./hao create-installer'],
                          untouched='nothing was downloaded or written')
        if release is None:
            raise no_release(release_path)
    if release and not getattr(a, 'assembly', None):
        a.assembly = release.get('assembly')
    ask_mac(a, c, host, release)
    a.sources = [str(p) for p in (TOP, cache) if p.exists()]
    info = ask_disk(a, c, host, minimum, a.sources)
    disk = DISKS.normalize(a.disk)
    c.print('', 'quiet')
    profile, path, asm, groups = BUILD.resolve(a, c, 'create-installer')
    c.print('Stick  %s  external, big enough %s' % (DISKS.label(info), c.ok))
    c.event('disk', disk=disk, policy='external', size=info.get('TotalSize') or info.get('Size'))
    if not a.dry_run:
        c.print('')
        if not c.confirm('Everything on %s will be erased.' % DISKS.label(info), disk, a.confirm):
            raise Stopped('not confirmed', untouched='nothing was downloaded or written')
    c.steps(len(STEPS))
    c.step(STEPS[0], plan=a.dry_run)
    if release is None:
        c.item('no release.json beside hao: the files to download are not known here', ev='download.plan', files=None)
    else:
        download(c, release_path, cache, acquire or runpy.run_path(str(ACQUIRE)), a.dry_run)
    c.step(STEPS[1], plan=a.dry_run)
    c.item('%d packs, %d recipes chosen for %s' % (len(profile['packs']), sum(len(v) for v in groups.values()),
                                             profile['model']), ok=not a.dry_run, ev='packs', packs=profile['packs'])
    for pack, names in groups.items():
        c.item('%-22s %d recipes' % (pack, len(names)), 'files')
    c.step(STEPS[2], plan=a.dry_run)
    c.item('erase %s; partition 1: OpenCore, Recovery and the installer; partition 2: the kit' % disk)
    if a.dry_run:
        c.done('dry run: nothing was downloaded or written')
        return 0
    raise Stopped('this release cannot write the USB stick yet',
                  why='making the stick (its Recovery, the installer and the macOS 27 files from Apple\'s downloads) '
                      'is still being built',
                  untouched='the stick was not touched; the downloads are kept and reused next time',
                  options=['wait for a release whose notes say it writes the stick, then run the same command',
                           'developers: build an image in the builder VM with hao build-image (tools/hao/README.md)'])
