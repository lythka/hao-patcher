#!/usr/bin/env python3
"""Assemble a Golden Gate-first raw image inside disposable maintenance macOS.

Usage: python3 -I golden-image.py CONFIG.json
All image/APFS operations run in that guest, never on the host. CONFIG names a
new regular image, a new work directory, size_bytes, name, python, layout_helper,
pristine_script, kdk_preparer, payload (filesystem/os/app/rosetta.dmg), kernel, kdk, fl,
donor_tool, donors {native,extensions,boot,driverkit,recovery}, esp (tree),
hardware {tool,artifact}, hooks [{name,kc_hook}] in set order, posts
[{name,script,folder}] in set order, and environment. Staged hook folders already
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
from pathlib import Path
import plistlib
import re
import shlex
import traceback
import subprocess
import sys
import time


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


class Image:
    def __init__(self, config, commands):
        self.config = config
        self.commands = commands
        self.work = Path(config['work'])
        self.image = Path(config['image'])
        self.mounts = {name:self.work/'mnt'/name for name in ('system','data','preboot','recovery','esp')}
        self.devices = {}
        self.mounted = []
        self.whole = None
        self.container = None
        self.group = None
        self.environment = dict(os.environ, **config['environment'])
        self.environment['OCLP_PYTHON'] = config['python']
        self.steps = []

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
                    if any(store['DeviceIdentifier'] == self.whole.removeprefix('/dev/')+'s2'
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

    def refresh_mount(self, key):
        # updatePreboot/bless may move or unmount this image's Preboot volume.
        # Resolve the actual device mount before writing, never the stale folder.
        actual = self.plist('diskutil','info','-plist',self.devices[key]).get('MountPoint')
        if actual == str(self.mounts[key]):
            if key not in self.mounted:
                self.mounted.append(key)
            return
        if actual:
            self.run('diskutil','unmount',self.devices[key])
        if key in self.mounted:
            self.mounted.remove(key)
        self.mount(key)

    def create(self):
        self.image.parent.mkdir(parents=True, exist_ok=True)
        with self.image.open('xb') as stream:
            stream.truncate(self.config['size_bytes'])
        attached = self.plist('hdiutil','attach','-imagekey','diskimage-class=CRawDiskImage','-nomount','-plist',self.image)
        self.whole = next(row['dev-entry'] for row in attached['system-entities']
                          if re.fullmatch(r'/dev/disk[0-9]+',row.get('dev-entry','')))
        self.run('diskutil','partitionDisk',self.whole,'GPT','APFS',self.config['name'],'100%')
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
                 '--system',self.mounts['system'],'--image',self.image,
                 '--layout-helper',c['layout_helper'],'--python',c['python'])

    def group_volumes(self):
        c = self.config
        self.add_volume('data',c['name']+' - Data','D')
        result = json.loads(self.run(c['python'],'-I',c['layout_helper'],'group',
                                    '--image',self.image,'--system',self.mounts['system'],
                                    '--data',self.devices['data'],capture=True))
        self.group = result['group_uuid']
        self.mount('data')
        for key, name, role in [('preboot','Preboot','B'),('recovery','Recovery','R'),('vm','VM','V')]:
            self.add_volume(key,name,role)
        for key in ('preboot','recovery'):
            self.mount(key)
        self.devices['esp'] = self.whole+'s1'
        self.mount('esp')
        keys = dict(system='SYSU',data='DATU',preboot='PBU',recovery='RCU')
        for name, variable in keys.items():
            self.environment[variable] = self.plist('diskutil','info','-plist',self.devices[name])['VolumeUUID']
        self.environment['GRP'] = self.group
        values={key:self.environment[key] for key in ('SYSU','DATU','PBU','RCU','GRP')}
        values['NAME']=c['name']
        (self.work/'uuids.env').write_text(''.join(key+'='+shlex.quote(value)+'\n' for key,value in values.items()))

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
            destination.mkdir(mode=0o755)
            self.run(c['python'],'-I',c['donor_tool'],'install-role','--artifact',artifact,
                     '--role',role,'--destination',destination)
        self.run('ditto','--rsrc','--extattr','--acl',c['esp'],self.mounts['esp'])

    def hardware(self):
        c = self.config
        self.run(c['python'],'-I',c['hardware']['tool'],'apply','--artifact',c['hardware']['artifact'],
                 '--system-root',self.mounts['system'],'--data-root',self.mounts['data'],
                 '--report',self.work/'hardware.json')

    def recipes(self):
        """Every oclp-hook recipe of the set, in set order, each through its own src/kc-hook.sh SYSTEM.
        golden-run guarantees darwin27-kernel then golden-gate-userspace come first; the environment
        carries the runner-only facts those two need (volume UUIDs, donor DriverKit artifact and tool)."""
        c = self.config
        environment = dict(self.environment, TAHOE_DRIVERKIT_ARTIFACT=str(c['donors']['driverkit']),
                           TAHOE_DONOR_TOOL=str(c['donor_tool']))
        for hook in c['hooks']:
            if not hook.get('kc_hook'):
                raise ValueError('set entry has no src/kc-hook.sh: '+hook['name'])
        # A Spotlight index the builder may have started on the offline System is not image content;
        # drop it before recipes walk System (snapshot drops it again).
        self.run('rm','-rf',self.mounts['system']/'.Spotlight-V100')
        for hook in c['hooks']:
            self.step('recipe '+hook['name'],lambda h=hook:self.run('sh','-eu',h['kc_hook'],self.mounts['system'],env=environment))
            if hook['name'] == 'darwin27-kernel':
                # Keep the collection exactly as darwin27-kernel linked it, before later recipes:
                # the dev-golden-bootstrap assembly derives row darwin27_boot_kc (kernel repairs) from it.
                self.run('ditto',self.mounts['system']/'System/Library/KernelCollections/BootKernelExtensions.kc',
                         self.work/'BootKernelExtensions.kc')

    def snapshot(self):
        system = self.mounts['system']
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
        self.run('diskutil','unmount',self.mounts['preboot'])
        self.mounted.remove('preboot')
        self.run('bless','--mount',system,'--create-snapshot')

    def posts(self):
        self.refresh_mount('preboot')
        # Match post.sh: image post recipes receive read-only System.
        self.run('diskutil','unmount',self.mounts['system'])
        self.mounted.remove('system')
        self.run('mount','-o','nobrowse,rdonly','-t','apfs','/dev/'+self.devices['system'],self.mounts['system'])
        self.mounted.append('system')
        environment = dict(self.environment, SYS=str(self.mounts['system']),DATA=str(self.mounts['data']),
                           PRE=str(self.mounts['preboot']),REC=str(self.mounts['recovery']),ESP=str(self.mounts['esp']))
        for post in self.config['posts']:
            output = self.work/'post'/post['name']
            output.mkdir(mode=0o755,parents=True,exist_ok=True)
            self.step('post '+post['name'],lambda p=post,o=output:self.run('sh','-eu',p['script'],
                      env=dict(environment,P=p['folder'],PATCH_OUTPUT_DIR=str(o))))
        self.run('rm','-rf',self.mounts['data']/'.Spotlight-V100')
        self.run('sync')

    def export_efi(self):
        """Deliver the final ESP separately for installation after OS restoration."""
        destination = self.work/'boot-efi'
        destination.mkdir(mode=0o755)
        self.run('ditto',self.mounts['esp'],destination/'payload')
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
        if self.whole and not self.mounted:
            try:
                self.run('hdiutil','detach',self.whole)
                self.whole = None
            except Exception as error:
                errors.append(str(error))
        return errors


# The fixed order of every Golden Gate image: (step, Image method, why).
LAYERS = [
    ('layout', 'create', 'new raw image: GPT, one APFS container, its System volume'),
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


def _assemble(config, commands=None):
    work = Path(config['work']); work.mkdir(parents=True, exist_ok=False, mode=0o755)
    commands = commands or Commands(work)
    image = Image(config,commands)
    record = dict(unit='pipeline',revision=1,operation='golden-first-image',configuration=config,
                  date=datetime.now(timezone.utc).isoformat(),
                  configuration_sha256=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
                  source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  status='building',validation='BUILD ONLY; boot and complete product acceptance remain untested')
    error = None
    try:
        for name, method, _ in LAYERS:
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
                      attached_device=image.whole)
        (work/'build.json').write_text(json.dumps(record,indent=2)+'\n')
    if error is not None: raise error
    return record


def assemble(config, commands=None):
    old_umask=os.umask(0o022)
    try:
        return _assemble(config,commands)
    finally:
        os.umask(old_umask)


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('config',type=Path)
    args = parser.parse_args()
    try:
        result = assemble(json.loads(args.config.read_text()))
    except BaseException as error:
        traceback.print_exc()
        code=getattr(error,'returncode',1)
        code=code if isinstance(code,int) and code>0 else 1
        print('RESULT rc='+str(code),flush=True)
        return code
    print(json.dumps(dict(image=result['configuration']['image'],group_uuid=result['group_uuid'],status=result['status'])))
    print('RESULT rc=0',flush=True)
    return 0


if __name__ == '__main__': sys.exit(main())
