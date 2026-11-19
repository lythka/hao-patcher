#!/usr/bin/env python3
"""Assemble a Golden Gate-first raw image inside disposable maintenance macOS.

Usage: python3 -I golden-image.py CONFIG.json
All image/APFS operations run in that guest, never on the host. CONFIG names a
target (see Target below: image, a new regular file, or disk, a whole disk that
passes DiskTarget's checks, with confirm_disk), a new work directory, size_bytes
(the image size, or a disk's minimum), name, python, layout_helper,
pristine_script, kdk_preparer, payload (filesystem/os/app/rosetta.dmg), kernel, kdk, fl,
donor_tool, donors {native,extensions,boot,driverkit,recovery}, esp (tree),
hardware {tool,artifact} (or null: the target selects no OCLP patch sets, so
the step hardware installs nothing), hooks [{name,kc_hook}] in set order, posts
[{name,script,folder}] in set order, environment (every step, hook and post
runs with it; golden-run puts the target there: HAO_TARGET_MODEL, the target's
model, and HAO_TARGET_DATA, the kit's JSON file of its platform_data), and optionally overlays
{plan,files,installer,livepatch} (golden-run --from-overlays: a recipe the plan
marks "overlay" has its files installed by golden-overlays.py at its turn in
set order instead of running its hook or apply.sh), and optionally cryptex_cache
(the share's folder golden-gate-userspace reuses its sealed cryptex images from,
GOLDEN_CRYPTEX_CACHE in its environment). Staged hook folders already
contain their declared input files. The host runner owns input selection/staging,
source identities and the set-order rules (see pipeline/golden-run).

Order (LAYERS below, then the set's recipes, then snapshot/posts/EFI):
the base layers are fixed and the same for every set; everything after them is a
set entry, run strictly in set-file order. Tahoe appears only through explicit
donor/build-tool roles. There is no Tahoe pristine installation or SystemVersion
substitution.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import stat
from pathlib import Path
import plistlib
import re
import shlex
import traceback
import subprocess
import sys
import time

# Copies onto the FAT ESP carry data only. A Recovery runs no doubleagentd, the daemon that stores a FAT volume's
# extended attributes and ACLs, so ditto's --rsrc/--extattr/--acl (and its defaults) fail there with "Attribute not
# found" (bench2 rerun3: boot-and-esp, 90 min into a QEMU install). A full macOS writes them as ._ files instead.
FAT_COPY = ('ditto', '--norsrc', '--noextattr', '--noacl')
def snapshot_command(system, machine=None):
    """bless that snapshots SYSTEM and makes the snapshot the boot root. Intel bless (a stick's Recovery) refuses
    --create-snapshot without --bootefi ("Can't use snapshot options without --bootefi"), so it takes OCLP's form,
    which also finds the Preboot volume itself; the arm64 builder VM keeps --mount."""
    if (machine or os.uname().machine) == 'x86_64':
        return ('bless', '--folder', Path(system)/'System/Library/CoreServices', '--bootefi', '--create-snapshot')
    return ('bless', '--mount', system, '--create-snapshot')


# The steps a failed run can be resumed at (golden-image.py --resume), and why running them again over what the
# failed attempt left is safe. The steps before one must all have completed. Every other step edits files in place
# (the recipes, the snapshot, the posts) or makes the volumes (layout, group-volumes): those start from an empty disk.
RESUMABLE = {
    'boot-and-esp': 'install-role writes into a group folder removed first; the ESP copy writes the same files again',
    'hardware': 'OCLP\'s install_new_file replaces each destination; RSRRepair and the record plist are rewritten',
    'posts': 'only when no post recipe started: the step remounts System read-only and runs every recipe from the first '
             '(laptop 1, 11-16: the System unmount at its start was refused by a process of the Recovery)',
}
# Attempts of a volume unmount before it is forced: a Recovery's mds or fseventsd may hold a volume for a moment.
UNMOUNT_TRIES = 4
UNMOUNT_WAIT = 3

# The uid and gid of _unknown: a file read from a volume mounted noowners (the stick's HAO volume in the Recovery) has
# them, and ditto keeps them. No file of a macOS system belongs to them.
UNKNOWN = 99


def fixed_owners(root, lstat=os.lstat, lchown=os.lchown):
    """Give root (0) every uid and wheel (0) every gid that is _unknown (99) under ROOT, on ROOT's device, symlinks
    themselves. Returns the number of entries changed."""
    root = str(root); device = lstat(root).st_dev; changed = 0
    def fix(path):
        nonlocal changed
        st = lstat(path)
        if st.st_uid == UNKNOWN or st.st_gid == UNKNOWN:
            lchown(path, 0 if st.st_uid == UNKNOWN else -1, 0 if st.st_gid == UNKNOWN else -1)
            changed += 1
        return st
    fix(root)
    for folder, dirs, files in os.walk(root):
        keep = []
        for name in dirs:
            st = fix(os.path.join(folder, name))
            if st.st_dev == device and not os.path.islink(os.path.join(folder, name)):
                keep.append(name)
        dirs[:] = keep
        for name in files:
            fix(os.path.join(folder, name))
    return changed


class Commands:
    def __init__(self, work):
        self.work = work
        self.entries = []

    def run(self, command, env=None, capture=False):
        command = list(map(str, command))
        index = len(self.entries)
        log = self.work/f'command-{index:03d}.log'
        row = dict(command=command, log=str(log), started=datetime.now(timezone.utc).isoformat())
        self.entries.append(row)
        result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        log.write_bytes(result.stdout+b'\n--- stderr ---\n'+result.stderr)
        row['returncode'] = result.returncode
        if not capture:
            for data in (result.stdout, result.stderr):
                if data:
                    print(data.decode(errors='replace'), end='', flush=True)
        elif result.stderr:
            print(result.stderr.decode(errors='replace'), end='', file=sys.stderr, flush=True)
        result.check_returncode()
        return result.stdout


class Target:
    """What the layers are written to: everything that differs between a raw image file and a whole disk.

    The assembler never names a device or a mount point itself. It asks the target for the whole disk
    (prepare), the GPT/APFS layout (partition), the ESP and APFS store partitions, the mount point of
    each volume, the binding golden-layout.py checks each System mutation against (--image), and to give
    the disk back afterwards (release). prepare, partition and release issue their commands through the
    assembler's run/plist, so tests record them and nothing runs on the host."""
    kind = None

    def __init__(self, mount_root):
        self.mount_root = Path(mount_root)
        self.whole = None   # /dev/diskN once prepared

    def mount_point(self, key):
        return self.mount_root/key

    def prepare(self, run, plist):
        raise NotImplementedError

    def adopt(self, run, plist):
        """Take the disk a failed run left, without erasing it (golden-image.py --resume)."""
        raise NotImplementedError

    def partition(self, run, name):
        # One GPT: s1 the ESP diskutil adds, s2 the APFS container with its first volume.
        run('diskutil','partitionDisk',self.whole,'GPT','APFS',name,'100%')

    def esp_device(self):
        return self.whole+'s1'

    def store(self):
        return self.whole.removeprefix('/dev/')+'s2'

    @property
    def binding(self):
        raise NotImplementedError

    @property
    def attached(self):
        """A device the build attached and has not released (build.json attached_device)."""
        return None

    def release(self, run):
        """Called once every volume is unmounted."""

    def summary(self):
        raise NotImplementedError

    def record(self):
        """Extra build.json fields; the image target adds none, so its record is unchanged."""
        return {}


class ImageTarget(Target):
    """A new raw image file of size_bytes, attached without mounting; the development builder VM's target."""
    kind = 'image'

    def __init__(self, image, size_bytes, mount_root):
        super().__init__(mount_root)
        self.name = image   # as configured: main() reports it unchanged
        self.image = Path(image)
        self.size_bytes = size_bytes

    def prepare(self, run, plist):
        self.image.parent.mkdir(parents=True, exist_ok=True)
        with self.image.open('xb') as stream:
            stream.truncate(self.size_bytes)
        self.adopt(run, plist)

    def adopt(self, run, plist):
        if not self.image.is_file():
            raise FileNotFoundError('no image to resume: '+str(self.image))
        attached = plist('hdiutil','attach','-imagekey','diskimage-class=CRawDiskImage','-nomount','-plist',self.image)
        self.whole = next(row['dev-entry'] for row in attached['system-entities']
                          if re.fullmatch(r'/dev/disk[0-9]+',row.get('dev-entry','')))

    @property
    def binding(self):
        return self.image

    @property
    def attached(self):
        return self.whole

    def release(self, run):
        run('hdiutil','detach',self.whole)
        self.whole = None

    def summary(self):
        return dict(image=self.name)


class DiskTarget(Target):
    """A whole disk, erased, for the USB installer on the target Mac or a running Mac installing to another disk.

    prepare() refuses the disk unless it passes the checks tools/ssdpush/ssdinstall.c makes before it erases a
    disk (disk_check, path_on_disk, the typed confirmation, unmount_disk; the size check is agent.c's), in its
    order. They use only diskutil, df and mount reads until the disk is accepted; then it unmounts everything on
    the disk and fails if anything stays mounted. The container fills the disk, so size_bytes is a minimum."""
    kind = 'disk'

    POLICIES = ('internal', 'external')

    def __init__(self, disk, minimum_bytes, mount_root, confirm=None, sources=(), policy='internal', identity=None):
        super().__init__(mount_root)
        self.identity = identity    # the identity the user confirmed (tools/hao disks.identity), or None
        if policy not in self.POLICIES:
            raise ValueError('disk policy %r: one of %s' % (policy, ', '.join(self.POLICIES)))
        self.policy = policy
        disk = str(disk)
        self.name = disk[6:] if disk.startswith('/dev/r') else disk[5:] if disk.startswith('/dev/') else disk
        self.minimum_bytes = minimum_bytes
        self.confirm = confirm
        self.sources = [str(path) for path in sources]

    @staticmethod
    def on(device, names):
        """ssdinstall: a device is on NAME when it is NAME or one of its slices (NAMEsN...)."""
        device = device.removeprefix('/dev/')
        return any(device == name or device.startswith(name+'s') for name in names)

    def containers(self, plist):
        """ssdinstall containers_of: the APFS containers whose physical store is a partition of this disk."""
        return [row['ContainerReference'] for row in plist('diskutil','apfs','list','-plist')['Containers']
                if any(store['DeviceIdentifier'].startswith(self.name+'s') for store in row.get('PhysicalStores',[]))]

    def refusal(self, run, plist):
        """The first reason this disk may not be erased, or None (ssdinstall disk_check, then install's checks)."""
        if not re.fullmatch(r'disk[0-9]+',self.name):
            return 'not a whole disk name (diskN)'
        try:
            info = plist('diskutil','info','-plist',self.name)
        except (subprocess.CalledProcessError, plistlib.InvalidFileException):
            return 'diskutil info failed'
        if not info.get('WholeDisk'):
            return 'not a whole disk'
        if info.get('VirtualOrPhysical') == 'Virtual':
            return 'virtual (disk image / RAM disk)'
        internal = bool(info.get('Internal'))
        removable = bool(info.get('Removable') or info.get('RemovableMedia') or info.get('Ejectable'))
        if self.policy == 'internal':
            # hao install: the Mac's own disk, from the installer's Recovery
            if not internal:
                return 'not internal'
            if removable:
                return 'removable'
            if info.get('BusProtocol') == 'USB':
                return 'USB'
        elif internal and not removable:
            # hao build-disk: the user's external SSD (USB, Thunderbolt), never the Mac's own disk
            return 'internal: this build writes only to an external disk'
        names = [self.name]+self.containers(plist)
        if self.on(plist('diskutil','info','-plist','/').get('DeviceIdentifier',''),names):
            return 'holds the running system'
        size = info.get('TotalSize') or info.get('Size') or 0
        if size < self.minimum_bytes:
            return 'too small: %d bytes, the build needs %d' % (size, self.minimum_bytes)
        if self.sources:
            lines = run('df','-P',*self.sources,capture=True).decode().splitlines()[1:]
            if any(self.on(line.split()[0],names) for line in lines if line.startswith('/dev/')):
                return 'holds the build\'s own work folder or inputs, the disk it would erase'
        if self.confirm != self.name:
            return 'not confirmed: confirm_disk must be '+self.name
        return self.identity_refusal(plist)

    def identity_refusal(self, plist):
        """Why the disk at this number is not the one the user confirmed (its disk_identity), or None. The copies before
        the assembler starts can take minutes, and another disk can take the number meanwhile."""
        if self.identity is None:
            return None
        try:
            now = disk_identity(plist, self.name)
        except (subprocess.CalledProcessError, plistlib.InvalidFileException, ValueError):
            return 'diskutil cannot identify it'
        if now != self.identity:
            changed = ', '.join(sorted(k for k in set(now)|set(self.identity) if now.get(k) != self.identity.get(k)))
            return 'it is not the disk that was confirmed (%s changed)' % changed
        return None

    def prepare(self, run, plist):
        why = self.refusal(run, plist)
        if why:
            raise ValueError('/dev/%s: %s; refusing' % (self.name, why))
        names = [self.name]+self.containers(plist)
        # ssdinstall unmount_disk: the synthesized APFS disks first (unmountDisk of the store may not), then
        # the disk; their status is not trusted, the mount table is.
        for name in names[1:]+names[:1]:
            try:
                run('diskutil','unmountDisk',name)
            except subprocess.CalledProcessError:
                pass
        for line in run('mount',capture=True).decode().splitlines():
            device, _, rest = line.partition(' on ')
            if self.on(device,names):
                raise ValueError('%s is still mounted at %s' % (device, rest.rsplit(' (',1)[0]))
        why = self.identity_refusal(plist)       # again, right before partitionDisk (or the resume) uses it
        if why:
            raise ValueError('/dev/%s: %s; refusing' % (self.name, why))
        self.whole = '/dev/'+self.name

    def adopt(self, run, plist):
        # The same checks and unmounting as prepare; the erase is partition's, which a resumed run skips.
        self.prepare(run, plist)

    @property
    def binding(self):
        return self.whole

    def summary(self):
        return dict(disk='/dev/'+self.name)

    def record(self):
        return dict(target=dict(kind=self.kind, disk='/dev/'+self.name, policy=self.policy, minimum_bytes=self.minimum_bytes,
                                accepted=self.whole is not None))


def disk_identity(plist, name):
    """tools/hao/src/disks.py identity(), the same fields from the same diskutil reads: media name, size, bus, device
    path and the partitions' UUIDs. ValueError when diskutil names no device path for it."""
    info = plist('diskutil','info','-plist',name)
    parts = (plist('diskutil','list','-plist',name).get('AllDisksAndPartitions') or [{}])[0].get('Partitions',[])
    if not (info.get('DeviceTreePath') or info.get('IORegistryEntryName')):
        raise ValueError('no device path for '+name)
    return {'media': (info.get('MediaName') or '').strip(), 'size': info.get('TotalSize') or info.get('Size'),
            'bus': info.get('BusProtocol'), 'path': info.get('DeviceTreePath') or info.get('IORegistryEntryName'),
            'partitions': sorted(p.get('DiskUUID') or p.get('VolumeUUID') or p.get('DeviceIdentifier','')[len(name):]
                                 for p in parts)}


def source_paths(config):
    """Folders and files the build reads or writes outside the target: none may live on a disk it erases."""
    paths = [config['work']]+[config[key] for key in ('python','payload','kernel','kdk','fl','esp','donor_tool')]
    paths += list(config.get('donors',{}).values())+([config['hardware']['artifact']] if config['hardware'] else [])
    return list(dict.fromkeys(map(str,paths)))


def target_for(config):
    mounts = Path(config['work'])/'mnt'
    if ('image' in config) == ('disk' in config):
        raise ValueError('configuration must name exactly one target: image or disk')
    if 'disk' in config:
        return DiskTarget(config['disk'],config['size_bytes'],mounts,confirm=config.get('confirm_disk'),
                          sources=source_paths(config),policy=config.get('disk_policy','internal'),
                          identity=config.get('disk_identity'))
    return ImageTarget(config['image'],config['size_bytes'],mounts)


class Assembler:
    def __init__(self, config, commands, target):
        self.config = config
        self.commands = commands
        self.target = target
        self.work = Path(config['work'])
        self.mounts = {name:target.mount_point(name) for name in ('system','data','preboot','recovery','esp')}
        self.devices = {}
        self.mounted = []
        self.container = None
        self.group = None
        self.environment = dict(os.environ, **config['environment'])
        self.environment['OCLP_PYTHON'] = config['python']
        self.steps = []
        self.owners = {}
        self.manifests = {}
        self.plan = None
        if config.get('overlays'):
            self.plan = {s['name']: s for s in json.loads(Path(config['overlays']['plan']).read_text())['steps']}

    def from_overlays(self, name):
        """Whether the overlay plan installs NAME's files instead of running its hook."""
        return bool(self.plan) and self.plan.get(name, {}).get('mode') == 'overlay'

    def install_overlay(self, name):
        o = self.config['overlays']
        record = self.work/'overlays'/(name+'.json')
        record.parent.mkdir(mode=0o755, exist_ok=True)
        roots = [a for key in ('system','data','esp') if key in self.mounted
                 for a in ('--root',key+'='+str(self.mounts[key]))]
        self.run(self.config['python'],'-I',o['installer'],'install',o['plan'],o['files'],'--recipe',name,
                 '--livepatch',o['livepatch'],*roots,'--record',record)

    def run(self, *args, capture=False, env=None):
        return self.commands.run(args, env=self.environment if env is None else env, capture=capture)

    def plist(self, *args):
        return plistlib.loads(self.run(*args, capture=True))

    def step(self, name, action):
        print('STEP '+name, flush=True)
        row = dict(name=name, started=time.time())
        self.steps.append(row)
        action()
        row['seconds'] = time.time()-row['started']
        row['complete'] = True

    def container_info(self):
        return next(row for row in self.plist('diskutil','apfs','list','-plist')['Containers']
                    if any(store['DeviceIdentifier'] == self.target.store()
                           for store in row['PhysicalStores']))

    def add_volume(self, key, name, role):
        before = {row['DeviceIdentifier'] for row in self.container_info()['Volumes']}
        self.run('diskutil','apfs','addVolume',self.container,'APFS',name,'-role',role,'-nomount')
        added = [row for row in self.container_info()['Volumes'] if row['DeviceIdentifier'] not in before]
        if len(added) != 1:
            raise ValueError('addVolume did not create exactly one '+key+' volume')
        self.devices[key] = added[0]['DeviceIdentifier']

    def mount(self, key):
        destination = self.mounts[key]
        destination.mkdir(parents=True, exist_ok=True, mode=0o755)
        if key == 'esp':
            # DiskArbitration's FSKit activation timed out on a clean FAT volume.
            # Use the direct FAT mount path already used by post.sh.
            self.run('/sbin/mount_msdos','-o','nobrowse',self.devices[key],destination)
        else:
            self.run('diskutil','mount','-mountOptions','nobrowse','-mountPoint',destination,self.devices[key])
        self.mounted.append(key)
        if key != 'esp':  # FAT has no Unix ownership; diskutil returns -69860.
            self.run('diskutil','enableOwnership',destination)

    def unmount(self, target):
        """diskutil unmount TARGET, again after a pause while a process holds it (diskutil names the dissenter), then
        forced: the callers mount the volume again themselves, so nothing is lost by forcing."""
        for attempt in range(UNMOUNT_TRIES):
            try:
                self.run('diskutil','unmount',target)
                return
            except subprocess.CalledProcessError:
                print('unmount of %s refused (attempt %d of %d): the dissenter is in the line above'
                      % (target, attempt+1, UNMOUNT_TRIES), flush=True)
                time.sleep(UNMOUNT_WAIT)
        self.run('diskutil','unmount','force',target)

    def refresh_mount(self, key):
        # updatePreboot/bless may move or unmount this image's Preboot volume.
        # Resolve the actual device mount before writing, never the stale folder.
        actual = self.plist('diskutil','info','-plist',self.devices[key]).get('MountPoint')
        if actual == str(self.mounts[key]):
            if key not in self.mounted:
                self.mounted.append(key)
            return
        if actual:
            self.unmount(self.devices[key])
        if key in self.mounted:
            self.mounted.remove(key)
        self.mount(key)

    def create(self):
        self.target.prepare(self.run, self.plist)
        self.target.partition(self.run, self.config['name'])
        info = self.container_info()
        self.container = info['ContainerReference']
        if len(info['Volumes']) != 1:
            raise ValueError('new image must initially contain only its System volume')
        placeholder = info['Volumes'][0]['DeviceIdentifier']
        self.run('diskutil','unmount',placeholder)
        # APFS rejects promoting an ordinary volume to System (-69599).
        # Use the creation sequence exercised by golden_layout_vm.py.
        self.add_volume('system',self.config['name'],'S')
        self.run('diskutil','apfs','deleteVolume',placeholder)
        self.mount('system')

    def system(self):
        c = self.config
        self.run('sh',c['pristine_script'],'system','--filesystem',Path(c['payload'])/'filesystem.dmg',
                 '--system',self.mounts['system'],'--image',self.target.binding,
                 '--layout-helper',c['layout_helper'],'--python',c['python'],
                 '--set-id-record',self.work/'set-id.json')

    def group_volumes(self):
        c = self.config
        self.add_volume('data',c['name']+' - Data','D')
        result = json.loads(self.run(c['python'],'-I',c['layout_helper'],'group',
                                    '--image',self.target.binding,'--system',self.mounts['system'],
                                    '--data',self.devices['data'],capture=True))
        self.group = result['group_uuid']
        self.mount('data')
        for key, name, role in [('preboot','Preboot','B'),('recovery','Recovery','R'),('vm','VM','V')]:
            self.add_volume(key,name,role)
        for key in ('preboot','recovery'):
            self.mount(key)
        self.devices['esp'] = self.target.esp_device()
        self.mount('esp')
        keys = dict(system='SYSU',data='DATU',preboot='PBU',recovery='RCU')
        for name, variable in keys.items():
            self.environment[variable] = self.plist('diskutil','info','-plist',self.devices[name])['VolumeUUID']
        self.environment['GRP'] = self.group
        values={key:self.environment[key] for key in ('SYSU','DATU','PBU','RCU','GRP')}
        values['NAME']=c['name']
        (self.work/'uuids.env').write_text(''.join(key+'='+shlex.quote(value)+'\n' for key,value in values.items()))

    def adopt(self, prior):
        """Instead of layout and group-volumes (golden-image.py --resume): the failed run's disk, its container and
        volumes found again by their VolumeUUIDs (device numbers change across restarts), mounted as group_volumes
        leaves them, and the environment the later steps read."""
        values = {key:value for key, _, value in (shlex.split(line)[0].partition('=') for line in
                  (Path(prior['work'])/'uuids.env').read_text().splitlines() if line.strip())}
        self.target.adopt(self.run, self.plist)
        info = self.container_info()
        self.container = info['ContainerReference']
        volumes = {row['DeviceIdentifier']:row for row in info['Volumes']}
        for key, variable in dict(system='SYSU',data='DATU',preboot='PBU',recovery='RCU').items():
            try:
                device = self.plist('diskutil','info','-plist',values[variable])['DeviceIdentifier']
            except subprocess.CalledProcessError:
                device = None    # no volume with that UUID on this Mac
            if device not in volumes:
                raise ValueError('%s volume %s is not in the container of %s: not the failed run\'s disk'
                                 % (key, values[variable], self.target.whole))
            self.devices[key] = device
            self.environment[variable] = values[variable]
        vm = [d for d, row in volumes.items() if 'VM' in row.get('Roles',[])]
        if vm:
            self.devices['vm'] = vm[0]
        self.devices['esp'] = self.target.esp_device()
        self.group = self.environment['GRP'] = values['GRP']
        for key in ('system','data','preboot','recovery','esp'):
            self.mount(key)
        (self.work/'uuids.env').write_text((Path(prior['work'])/'uuids.env').read_text())

    def finalize(self):
        c = self.config
        self.run('sh',c['pristine_script'],'finalize','--system',self.mounts['system'],
                 '--data',self.mounts['data'],'--fl',c['fl'],'--kernel',c['kernel'],'--kdk',c['kdk'],
                 '--donor-cli',c['donor_tool'],'--extensions-artifact',c['donors']['extensions'],
                 '--native-artifact',c['donors']['native'],'--kdk-preparer',c['kdk_preparer'],'--python',c['python'])

    def bootstrap(self):
        c = self.config
        for role in ('preboot','recovery'):
            artifact = Path(c['donors']['boot' if role=='preboot' else 'recovery'])
            destination = self.mounts[role]/self.group
            if destination.exists():    # a resumed run (RESUMABLE): what the failed attempt wrote goes first
                self.run('rm','-rf',destination)
            destination.mkdir(mode=0o755)
            self.run(c['python'],'-I',c['donor_tool'],'install-role','--artifact',artifact,
                     '--role',role,'--destination',destination)
        self.run(*FAT_COPY,c['esp'],self.mounts['esp'])

    def hardware(self):
        c = self.config
        if c['hardware'] is None:
            # The target selects no OCLP patch sets (golden-run's hardware_overlay): nothing to install, said so.
            (self.work/'hardware.json').write_text(json.dumps(dict(overlay=None,
                why='the target selects no OCLP patch sets'),indent=2)+'\n')
            print('golden-image: hardware: no OCLP hardware overlay (the target selects no patch sets)', flush=True)
            self.fix_owners('base layers',('system','data','preboot','recovery'))
            return
        self.run(c['python'],'-I',c['hardware']['tool'],'apply','--artifact',c['hardware']['artifact'],
                 '--system-root',self.mounts['system'],'--data-root',self.mounts['data'],
                 '--report',self.work/'hardware.json')
        self.fix_owners('base layers',('system','data','preboot','recovery'))

    def fix_owners(self, when, keys):
        """Files the kit gave the target with _unknown owners (a Recovery reads the stick noowners) get root:wheel, as
        the builder VM's build gives them. bench: 2639 Tahoe donor kexts were 99:99, so kmutil refused them all."""
        counts = {key:fixed_owners(self.mounts[key]) for key in keys if key in self.mounted}
        self.owners[when] = counts
        if any(counts.values()):
            print('golden-image: %s: _unknown (99) owners set to root:wheel: %s' % (when, ', '.join(
                  '%s %d' % item for item in counts.items() if item[1])), flush=True)

    def recipes(self):
        """Every oclp-hook recipe of the set, in set order, each through its own src/kc-hook.sh SYSTEM, or, when
        the overlay plan says so, by installing its files from the overlays (install_overlay). golden-run
        guarantees darwin27-kernel then golden-gate-userspace come first; the environment
        carries the runner-only facts those two need (volume UUIDs, donor DriverKit artifact and tool) and the native
        donor artifact golden-gate-native-identities reads Apple originals from."""
        c = self.config
        environment = dict(self.environment, TAHOE_DRIVERKIT_ARTIFACT=str(c['donors']['driverkit']),
                           TAHOE_DONOR_TOOL=str(c['donor_tool']), TAHOE_NATIVE_ARTIFACT=str(c['donors']['native']))
        if c.get('cryptex_cache'):
            # golden-gate-userspace reuses or stores its sealed cryptex images there (golden-run, cryptex_cache.py)
            environment['GOLDEN_CRYPTEX_CACHE'] = str(c['cryptex_cache'])
        for hook in c['hooks']:
            if not hook.get('kc_hook'):
                raise ValueError('set entry has no src/kc-hook.sh: '+hook['name'])
        # A Spotlight index the builder may have started on the offline System is not image content;
        # drop it before recipes walk System (snapshot drops it again).
        self.run('rm','-rf',self.mounts['system']/'.Spotlight-V100')
        for hook in c['hooks']:
            if self.from_overlays(hook['name']):
                self.step('overlay '+hook['name'],lambda h=hook:self.install_overlay(h['name']))
                continue
            self.step('recipe '+hook['name'],lambda h=hook:self.run('sh','-eu',h['kc_hook'],self.mounts['system'],env=environment))
            if hook['name'] == 'darwin27-kernel':
                # Keep the collection exactly as darwin27-kernel linked it, before later recipes:
                # the dev-golden-bootstrap assembly derives row darwin27_boot_kc (kernel repairs) from it.
                self.run('ditto',self.mounts['system']/'System/Library/KernelCollections/BootKernelExtensions.kc',
                         self.work/'BootKernelExtensions.kc')
                # And all three files as linked, for a kit whose install cannot link them (golden-run stores them
                # under their key, pipeline/prelinked_kc.py).
                (self.work/'kc').mkdir(mode=0o755,exist_ok=True)
                for name in ('BootKernelExtensions.kc','BootKernelExtensions.kc.elides','SystemKernelExtensions.kc'):
                    self.run('ditto',self.mounts['system']/'System/Library/KernelCollections'/name,self.work/'kc'/name)

    def snapshot(self):
        self.fix_owners('recipes',('system','data','preboot','recovery'))
        system = self.mounts['system']
        record = self.work/'set-id.json'
        if record.is_file():        # before the seal: a System that lost a set-ID mode is never snapshotted
            bad = check_set_id(json.loads(record.read_text()), system)
            if bad:
                raise RuntimeError('%d set-user-ID/set-group-ID files differ from the macOS 27 System (diag#0057), '
                                   'not sealed: %s' % (len(bad), '; '.join(bad[:8])))
            print('golden-image: %d set-ID files keep their mode and owner' % len(json.loads(record.read_text())),
                  flush=True)
        self.run('rm','-rf',system/'.Spotlight-V100')
        self.run('diskutil','apfs','updatePreboot',self.devices['system'])
        self.refresh_mount('preboot')
        version = system/'System/Library/CoreServices/SystemVersion.plist'
        preboot = self.mounts['preboot']/self.group
        for relative in ['System/Library/CoreServices/SystemVersion.plist',
                         'boot/System/Library/CoreServices/SystemVersion.plist',
                         'cryptex1/current/SystemVersion.plist']:
            destination = preboot/relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            self.run('ditto',version,destination)
        self.run('bless','--folder',system/'System/Library/CoreServices','--bootefi','--label',self.config['name'])
        for name in ('.disk_label','.disk_label_2x'):
            (system/'System/Library/CoreServices'/name).unlink(missing_ok=True)
        core=preboot/'System/Library/CoreServices'
        for name,value in [('.root_uuid',self.group),('.disk_label.contentDetails',self.config['name'])]:
            path=core/name
            path.write_text(value);path.chmod(0o644)
        self.unmount(self.mounts['preboot'])
        self.mounted.remove('preboot')
        self.run(*snapshot_command(system))

    def posts(self):
        self.refresh_mount('preboot')
        # Match post.sh: image post recipes receive read-only System.
        self.unmount(self.mounts['system'])
        self.mounted.remove('system')
        self.run('mount','-o','nobrowse,rdonly','-t','apfs','/dev/'+self.devices['system'],self.mounts['system'])
        self.mounted.append('system')
        environment = dict(self.environment, SYS=str(self.mounts['system']),DATA=str(self.mounts['data']),
                           PRE=str(self.mounts['preboot']),REC=str(self.mounts['recovery']),ESP=str(self.mounts['esp']))
        for post in self.config['posts']:
            if self.from_overlays(post['name']):
                self.step('overlay '+post['name'],lambda p=post:self.install_overlay(p['name']))
                continue
            output = self.work/'post'/post['name']
            output.mkdir(mode=0o755,parents=True,exist_ok=True)
            self.step('post '+post['name'],lambda p=post,o=output:self.run('sh','-eu',p['script'],
                      env=dict(environment,P=p['folder'],PATCH_OUTPUT_DIR=str(o))))
        self.run('rm','-rf',self.mounts['data']/'.Spotlight-V100')
        self.fix_owners('posts',('data','preboot','recovery'))
        current = self.mounts['preboot']/self.group/'cryptex1/current'
        if current.is_dir():        # after the last writer of the cryptex images (#90: the boot's graft maps no hole)
            print('golden-image: cryptex holes filled',
                  json.dumps(allocate_cryptex_images(current,run=lambda c:self.run(*c))),flush=True)
        self.write_manifests()
        self.run('sync')

    def write_manifests(self):
        """One manifest per recipe (verify.py's format 1) of the files it declares, as they are installed now that the
        System is sealed: Data/private/var/db/hao/manifests/RECIPE.json, root:wheel 0644. `hao verify` reads them. A
        declared seed file (bytes macOS changes after install) also carries "content": "seed" and its "reason"."""
        path = self.config.get('declared')
        if not path or not Path(path).is_file():
            return
        declared = json.loads(Path(path).read_text())
        folder = self.mounts['data']/MANIFESTS
        folder.mkdir(mode=0o755,parents=True,exist_ok=True)
        counts = {}
        for name, entry in sorted(declared.items()):
            files = {}
            for rel in entry.get('files', []):
                found = manifest_entry([self.mounts['system'], self.mounts['data']], rel)
                if found:
                    if rel in entry.get('seed', {}):
                        found.update(content='seed', reason=entry['seed'][rel])
                    files[rel] = found
            target = folder/(name+'.json')
            target.write_text(json.dumps(dict(format=1, recipe=name, pack=entry.get('pack'), files=files, dirs={}),
                                         indent=1, sort_keys=True)+'\n')
            if os.geteuid() == 0:
                os.chown(target, 0, 0)
            os.chmod(target, 0o644)
            counts[name] = len(files)
        if os.geteuid() == 0:
            os.chown(folder, 0, 0)
        os.chmod(folder, 0o755)
        self.manifests = counts
        print('golden-image: manifests for %d recipes (%d files) in %s' % (len(counts), sum(counts.values()), MANIFESTS),
              flush=True)

    def export_efi(self):
        """Deliver the final ESP separately for installation after OS restoration."""
        destination = self.work/'boot-efi'
        destination.mkdir(mode=0o755)
        self.run(*FAT_COPY,self.mounts['esp'],destination/'payload')
        # Every OpenCore setting needs a recorded reason, on the ESP as the last post left it (recipes after
        # hao-opencore change it too): hao-opencore's own check, run once more on the final copy.
        for post in self.config['posts']:
            if post['name']=='hao-opencore':
                self.run(self.config['python'],'-I','-B',str(Path(post['folder'])/'src/config_reasons.py'),'check',
                         destination/'payload/EFI/OC/config.plist','--kexts',destination/'payload/EFI/OC/Kexts')
        config = plistlib.loads((destination/'payload/EFI/OC/config.plist').read_bytes())
        system = self.mounts['system']
        version = plistlib.loads((system/'System/Library/CoreServices/SystemVersion.plist').read_bytes())
        collection = system/'System/Library/KernelCollections/BootKernelExtensions.kc'
        files = {}
        for path in sorted((destination/'payload').rglob('*')):
            if path.is_symlink():
                raise ValueError('unexpected symlink in FAT EFI payload: '+str(path))
            if path.is_file():
                files[str(path.relative_to(destination/'payload'))] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = dict(schema=1,kind='hao-prepared-efi',os_build=version['ProductBuildVersion'],
                        model=config.get('#Revision',{}).get('Original-Model'),
                        kernel_collection_sha256=hashlib.sha256(collection.read_bytes()).hexdigest(),
                        opencore_revision=config.get('#Revision',{}),
                        kernel_repairs_applied=any(p['name']=='hao-opencore' for p in self.config['posts']),
                        files=files,
                        installation='Install this complete payload after restoring its matching OS image. Do not regenerate a stock OCLP EFI over it.')
        # Some upstream configs embed binary hardware probes; keep provenance JSON-safe.
        manifest['opencore_revision'] = {k:v for k,v in manifest['opencore_revision'].items()
                                         if isinstance(v,(str,int,bool))}
        (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')

    def close(self):
        errors = []
        for name in list(reversed(self.mounted)):
            try:
                self.run('diskutil','unmount',self.mounts[name])
                self.mounted.remove(name)
            except Exception as error:
                errors.append(str(error))
        if self.target.whole and not self.mounted:
            try:
                self.target.release(self.run)
            except Exception as error:
                errors.append(str(error))
        return errors


def check_set_id(expected, system):
    """[problem] for each (path, mode, uid, gid) of the source System's set-ID files (golden-layout's set-id.json)
    whose file in SYSTEM is a regular file or folder with another mode or owner. A file a recipe removed is not a
    problem; one a recipe replaced (a hao stub, written in place) must keep the mode and owner too."""
    bad = []
    for rel, mode, uid, gid in expected:
        p = Path(system)/rel
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(st.st_mode):
            continue
        if (stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid) != (mode, uid, gid):
            bad.append('/%s is %04o %d:%d, not %04o %d:%d' % (rel, stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid,
                                                           mode, uid, gid))
    return bad


def hole_map(path):
    """[(start, end)] of PATH's holes (SEEK_HOLE/SEEK_DATA); [] when the file system cannot report them."""
    holes = []
    fd = os.open(str(path), os.O_RDONLY)
    try:
        size, at = os.fstat(fd).st_size, 0
        while at < size:
            try:
                start = min(os.lseek(fd, at, os.SEEK_HOLE), size)
            except OSError:
                return []
            if start >= size:
                break
            try:
                end = os.lseek(fd, start, os.SEEK_DATA)
            except OSError as error:
                if getattr(error, 'errno', None) != 6:      # ENXIO: only a hole is left
                    return []
                end = size
            if end <= start:
                return []
            holes.append((start, end))
            at = end
    finally:
        os.close(fd)
    return holes


CRYPTEX_IMAGES = ('os.dmg', 'app.dmg')
ZERO = bytes(1 << 20)


def allocate_cryptex_images(current, free=None, run=subprocess.check_call):
    """Leave Preboot's cryptex images (CURRENT = Preboot/GROUP/cryptex1/current) with no holes, after the last recipe
    that writes them (golden-gate-userspace, golden-gate-shared-cache's fold, posts with PRE). The boot grafts them,
    and the APFS graft cannot map a hole (#90: "_graft_dev_read: blockmap lut lookup failed", ENOENT, then SIGBUS in
    the x86_64 shared cache), although a hole reads as zeros, so contents and seal are unchanged. Zeros go over every
    hole, os.clone.dmg is cloned again from a filled os.dmg, and it refuses before writing when Preboot's container
    has less free space than the holes (FREE: bytes free, measured when not given), or when a hole is left.
    Returns {name: bytes filled}."""
    current = Path(current)
    holes = {name: hole_map(current/name) for name in CRYPTEX_IMAGES if (current/name).is_file()}
    need = sum(e - s for h in holes.values() for s, e in h)
    clone = current/'os.clone.dmg'
    reclone = clone.is_file() and (bool(holes.get('os.dmg')) or bool(hole_map(clone)))
    if need:
        if free is None:
            st = os.statvfs(str(current))
            free = st.f_bavail * st.f_frsize
        if free < need + (1 << 30):
            raise RuntimeError('%s: the cryptex images have %d bytes of holes to fill (#90) and Preboot has %d bytes '
                               'free; nothing was written' % (current, need, free))
    filled = {}
    for name, ranges in holes.items():
        fd = os.open(str(current/name), os.O_WRONLY)
        try:
            for start, end in ranges:
                at = start
                while at < end:
                    view = memoryview(ZERO)[:min(len(ZERO), end - at)]
                    while view:
                        n = os.pwrite(fd, view, at)
                        view, at = view[n:], at + n
            os.fsync(fd)
        finally:
            os.close(fd)
        filled[name] = sum(e - s for s, e in ranges)
    if reclone:
        clone.unlink()
        run(['cp', '-c', str(current/'os.dmg'), str(clone)])
    left = {name: hole_map(current/name) for name in CRYPTEX_IMAGES + ('os.clone.dmg',) if (current/name).is_file()}
    left = {name: h for name, h in left.items() if h}
    if left:
        raise RuntimeError('%s: holes left in %s (#90)' % (current, ', '.join(sorted(left))))
    return filled


MANIFESTS = 'private/var/db/hao/manifests'
FLAG_NAMES ={0x80000: 'restricted', 0x20000: 'schg', 0x100000: 'sunlnk', 0x2: 'uchg', 0x8000: 'hidden', 0x1: 'nodump'}


def manifest_entry(roots, rel):
    """{sha256, mode, owner, flags} of REL under the first of ROOTS that has it as a regular file, else None."""
    for root in roots:
        p = Path(root)/rel
        if p.is_file() and not p.is_symlink():
            st = os.lstat(p)
            h = hashlib.sha256()
            with open(p, 'rb') as f:
                for block in iter(lambda: f.read(1 << 20), b''):
                    h.update(block)
            return dict(sha256=h.hexdigest(), mode='%04o' % stat.S_IMODE(st.st_mode), owner='%d:%d' % (st.st_uid, st.st_gid),
                        flags=sorted(n for bit, n in FLAG_NAMES.items() if getattr(st, 'st_flags', 0) & bit))
    return None


# The fixed order of every Golden Gate image: (step, Assembler method, why).
LAYERS = [
    ('layout', 'create', 'the target (new raw image, or an eligible whole disk): GPT, one APFS container, its System volume'),
    ('golden-system', 'system', 'the macOS 27 System itself, copied from the build\'s filesystem.dmg while it is the only volume'),
    ('group-volumes', 'group_volumes', 'Data grouped with System, then Preboot, Recovery and VM volumes; the ESP mounted'),
    ('golden-finalize', 'finalize', 'Data template and firmlinks (27\'s own); Tahoe Extensions + KDK Intel kexts, because 27 '
     'has no Intel drivers; the Darwin 27 x86_64 kernel file, which the hardware overlay checks; Tahoe native roots '
     '(x86_64 code 27 lacks)'),
    ('boot-and-esp', 'bootstrap', 'Tahoe\'s Intel Preboot and Recovery files, because 27 has none; the generated OpenCore ESP'),
    ('hardware', 'hardware', 'OCLP\'s legacy hardware overlay (HD 4000 Metal/OpenGL, wireless, audio) over all of the above'),
    ('recipes', 'recipes', 'the set\'s oclp-hook recipes in set order: darwin27-kernel, golden-gate-userspace, then fixes'),
    ('snapshot', 'snapshot', 'Preboot update, bless and the System snapshot'),
    ('posts', 'posts', 'the set\'s post recipes in set order, System read-only (OpenCore config, dev access)'),
    ('efi-bundle', 'export_efi', 'the final ESP exported with a manifest for installation after restore'),
]


def resume_point(config, prior):
    """The step a failed run (its build.json PRIOR) resumes at, or ValueError: the configuration must be the same
    (paths compared below the LOCAL folder each run has), every step before it complete, the step in RESUMABLE."""
    if prior.get('status') != 'failed':
        raise ValueError('the run to resume did not fail (status %s)' % prior.get('status'))
    old, new = str(Path(prior['configuration']['work']).parent), str(Path(config['work']).parent)

    def same(value):
        return json.loads(json.dumps(value).replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1]))
    keys = set(config)|set(prior['configuration'])
    # Not compared: the work folder, the image (the failed run's is used) and the disk's name, which can change
    # between starts (bench 0745: disk0 in the failed run, disk1 after a restart). adopt() finds the failed run's
    # volumes by UUID in the named disk's container, and refuses a disk that does not hold them.
    differ = sorted(k for k in keys - {'work','image','disk','confirm_disk','disk_identity'}
                    if same(prior['configuration'].get(k)) != config.get(k))
    if differ:
        raise ValueError('the kit\'s configuration differs from the failed run\'s (%s): start from an empty disk' % ', '.join(differ))
    done = {row['name'] for row in prior.get('steps',[]) if row.get('complete')}
    start = next(i for i, (name, _, _) in enumerate(LAYERS) if name not in done)
    name = LAYERS[start][0]
    started = [row['name'] for row in prior.get('steps',[]) if row['name'].startswith(('post ','overlay '))]
    if name == 'posts' and started:
        raise ValueError('the failed run stopped in step posts after %s started, which cannot run again over its own '
                         'partial work: start from an empty disk' % started[0])
    if name not in RESUMABLE:
        raise ValueError('the failed run stopped in step %s, which cannot be run again over its own partial work; '
                         'resumable: %s' % (name, ', '.join(RESUMABLE)))
    return start


def _assemble(config, commands=None, target=None, prior=None):
    start = resume_point(config, prior) if prior else 0
    if prior and 'image' in config:
        config = dict(config, image=prior['configuration']['image'])
    target = target or target_for(config)
    work = Path(config['work']); work.mkdir(parents=True, exist_ok=False, mode=0o755)
    commands = commands or Commands(work)
    image = Assembler(config,commands,target)
    record = dict(unit='pipeline',revision=1,operation='golden-first-image',configuration=config,
                  date=datetime.now(timezone.utc).isoformat(),
                  configuration_sha256=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
                  source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  status='building',validation='BUILD ONLY; boot and complete product acceptance remain untested')
    if prior:
        record['resumed'] = dict(work=prior['work'],step=LAYERS[start][0],why=RESUMABLE[LAYERS[start][0]])
    error = None
    try:
        if prior:
            print('RESUME at %s: the steps before it completed in %s' % (LAYERS[start][0], prior['work']), flush=True)
            image.steps += [dict(row, reused=True) for row in prior['steps']
                            if row['name'] in {n for n, _, _ in LAYERS[:start]}]
            image.adopt(prior)
        for name, method, _ in LAYERS[start:]:
            image.step(name,getattr(image,method))
        record['status'] = 'assembled'
    except BaseException as caught:
        error = caught; record.update(status='failed',error=str(caught))
    finally:
        cleanup = image.close()
        if cleanup:
            record.update(status='failed',cleanup_errors=cleanup)
            if error is None: error=RuntimeError('; '.join(cleanup))
        record.update(volumes=image.devices,group_uuid=image.group,steps=image.steps,commands=commands.entries,
                      owners_fixed=image.owners, manifests=image.manifests,
                      attached_device=target.attached,**target.record())
        (work/'build.json').write_text(json.dumps(record,indent=2)+'\n')
    if error is not None: raise error
    return record


def assemble(config, commands=None, target=None, prior=None):
    old_umask=os.umask(0o022)
    try:
        return _assemble(config,commands,target,prior)
    finally:
        os.umask(old_umask)


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('config',type=Path)
    parser.add_argument('--resume',type=Path,metavar='WORK',
                        help='the work folder of a failed run on the same disk: continue at its failed step (RESUMABLE)')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        prior = None
        if args.resume:
            prior = json.loads((args.resume/'build.json').read_text())
            prior['work'] = str(args.resume)
            resume_point(config, prior)
            if 'image' in config:
                config['image'] = prior['configuration']['image']
        target = target_for(config)
        result = assemble(config,target=target,prior=prior)
    except BaseException as error:
        traceback.print_exc()
        code=getattr(error,'returncode',1)
        code=code if isinstance(code,int) and code>0 else 1
        print('RESULT rc='+str(code),flush=True)
        return code
    print(json.dumps(dict(target.summary(),group_uuid=result['group_uuid'],status=result['status'])))
    print('RESULT rc=0',flush=True)
    return 0


if __name__ == '__main__': sys.exit(main())
