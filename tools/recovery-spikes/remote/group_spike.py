"""Spike (d) and the fl entitlement: volume grouping on x86_64 in the Recovery. Runs ON THE LAPTOP (spike.sh starts it).
   group_spike.py T
Two fresh APFS containers, each in a raw image file on the RAM disk T, built the way golden-image.py does it
(partitionDisk GPT APFS, a System-role volume replacing the placeholder):
  A: golden-layout.py seed + group, i.e. the private APFSContainerVolumeGroupAdd call, exactly the build's code;
     then the Data volume is mounted and `fl` (signed ad hoc with com.apple.private.apfs.set-firmlink, the build's
     golden-firmlinks tool) sets and reads back one firmlink; `fl-noent` (same tool, no entitlement) tries another;
     then Preboot, Recovery and VM volumes are added as golden-image.py does.
  B: the fallback, `diskutil apfs addVolume ... -role D -groupWith SYSTEM`.
Nothing outside the two image files is touched."""
import json
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spikelib import Spike, CommandFailed, container_on, main_guard   # noqa: E402

IMAGE_MB = 512
# What golden-pristine's copy-altlink writes from Golden Gate's own filesystem (paths, not Apple bytes).
LINK_TARGET = '../../System/Volumes/Preboot/Cryptexes/OS'
ALTLINK_HEX = '/System/Volumes/Preboot/Cryptexes/Incoming/OS'.encode().hex()


def container(s, reference):
    rows = s.plist('diskutil', 'apfs', 'list', '-plist', reference)['Containers']
    if len(rows) != 1:
        raise CommandFailed('ambiguous container %s' % reference)
    return rows[0]


def add_volume(s, reference, name, role, *extra):
    before = {v['DeviceIdentifier'] for v in container(s, reference)['Volumes']}
    s.run('diskutil', 'apfs', 'addVolume', reference, 'APFS', name, '-role', role, *extra, '-nomount')
    added = [v['DeviceIdentifier'] for v in container(s, reference)['Volumes'] if v['DeviceIdentifier'] not in before]
    if len(added) != 1:
        raise CommandFailed('addVolume %s made %d volumes' % (role, len(added)))
    return added[0]


def mount(s, device, folder):
    folder.mkdir(parents=True, exist_ok=True)
    s.remember('mount', folder)
    s.run('diskutil', 'mount', '-mountOptions', 'nobrowse', '-mountPoint', folder, device)
    s.run('diskutil', 'enableOwnership', folder)
    return folder


def new_system(s, label):
    """golden-image.py ImageTarget.prepare + partition + create: raw file, GPT APFS, System volume mounted."""
    image = s.scratch/('group-%s.img' % label)
    with image.open('xb') as f:
        f.truncate(IMAGE_MB << 20)
    whole = s.attach_raw(image)
    name = 'Spike' + label.upper()
    s.run('diskutil', 'partitionDisk', whole, 'GPT', 'APFS', name, '100%')
    info = container_on(s.plist('diskutil', 'apfs', 'list', '-plist'), whole)
    reference = info['ContainerReference']
    if len(info['Volumes']) != 1:
        raise CommandFailed('new container has %d volumes' % len(info['Volumes']))
    placeholder = info['Volumes'][0]['DeviceIdentifier']
    s.run('diskutil', 'unmount', placeholder)
    system = add_volume(s, reference, name, 'S')
    s.run('diskutil', 'apfs', 'deleteVolume', placeholder)
    folder = mount(s, system, s.scratch/'mnt'/('%s-system' % label))
    return image, whole, reference, system, folder, name


def group_id(s, device):
    return s.plist('diskutil', 'info', '-plist', device).get('APFSVolumeGroupID')


def fl(s, tool, *args):
    p = s.run(tool, *args, check=False)
    out = (p.stdout or b'').decode(errors='replace').strip()
    return p.returncode, out


def fl_outcome(rc, out):
    if rc < 0:
        return 'killed by signal %d (AMFI refused the entitlement?)' % -rc
    return 'rc=%d %s' % (rc, out)


def path_a(s, layout, inputs):
    image, whole, reference, system, sysmount, name = new_system(s, 'a')
    p = s.run(sys.executable, '-I', layout, 'seed', '--image', image, '--system', sysmount,
              '--link-target', LINK_TARGET, '--altlink-hex', ALTLINK_HEX, check=False)
    s.check('seed-altlink', p.returncode == 0, 'golden-layout.py seed rc=%d' % p.returncode)
    data = add_volume(s, reference, name+' - Data', 'D')
    with s.timed('group-add'):
        p = s.run(sys.executable, '-I', layout, 'group', '--image', image, '--system', sysmount, '--data', data,
                  check=False)
    detail = (p.stdout or p.stderr or b'').decode(errors='replace').strip().splitlines()
    group = None
    if p.returncode == 0:
        group = json.loads(p.stdout)['group_uuid']
    s.check('group-add', p.returncode == 0, 'APFSContainerVolumeGroupAdd via golden-layout.py group: %s'
            % (group or (detail[-1] if detail else 'rc=%d' % p.returncode)))
    if group:
        ids = {group_id(s, system), group_id(s, data)}
        s.check('group-add-readback', ids == {group}, 'System %s and Data %s report group %s'
                % (system, data, ', '.join(sorted(str(i) for i in ids))))
    else:
        s.check('group-add-readback', None, 'not grouped')

    # firmlinks need the grouped pair with Data mounted, as golden-pristine.sh finalize has it
    datamount = mount(s, data, s.scratch/'mnt'/'a-data')
    for tool in ('fl', 'fl-noent'):
        os.chmod(inputs/tool, 0o755)
    for leaf, tool in (('SpikeFirm', 'fl'), ('SpikeFirmNoEnt', 'fl-noent')):
        (sysmount/leaf).mkdir(mode=0o755)
        (datamount/leaf).mkdir(mode=0o755)
        rc, out = fl(s, inputs/tool, 'set', sysmount/leaf, leaf)
        if tool == 'fl':
            ok = rc == 0 and 'rc=0' in out
            s.check('fl-entitled', ok if group else None, fl_outcome(rc, out))
            grc, gout = fl(s, inputs/tool, 'get', sysmount/leaf)
            s.check('fl-readback', (grc == 0 and "target='%s'" % leaf in gout) if ok else None, fl_outcome(grc, gout))
        else:
            s.info('fl-unentitled', ('accepted: the entitlement is not enforced here; ' if rc == 0 else
                                     'refused, as expected without the entitlement; ') + fl_outcome(rc, out))

    added = {}
    for role, label in (('B', 'Preboot'), ('R', 'Recovery'), ('V', 'VM')):
        added[label] = add_volume(s, reference, label, role)
    roles = sorted(r for v in container(s, reference)['Volumes'] for r in v.get('Roles', []))
    s.check('other-roles', roles == sorted(['System', 'Data', 'Preboot', 'Recovery', 'VM']),
            'roles %s' % ' '.join(roles))
    s.run('diskutil', 'apfs', 'listVolumeGroups', reference, check=False)
    for folder in (datamount, sysmount):
        s.run('diskutil', 'unmount', folder, check=False)
    s.detach(whole)


def path_b(s):
    image, whole, reference, system, sysmount, name = new_system(s, 'b')
    try:
        data = add_volume(s, reference, name+' - Data', 'D', '-groupWith', system)
    except CommandFailed as error:
        s.check('group-with', False, 'diskutil apfs addVolume -groupWith: %s' % error)
    else:
        ids = {group_id(s, system), group_id(s, data)}
        one = len(ids) == 1 and None not in ids
        s.check('group-with', one, 'diskutil apfs addVolume -role D -groupWith %s: System and Data report %s'
                % (system, ', '.join(sorted(str(i) for i in ids))))
    s.run('diskutil', 'unmount', sysmount, check=False)
    s.detach(whole)


def body(s):
    layout = s.scratch/'code/pipeline/stages/golden-layout.py'
    inputs = s.scratch/'inputs'
    with s.timed('path-a'):
        path_a(s, layout, inputs)
    with s.timed('path-b'):
        path_b(s)


if __name__ == '__main__':
    main_guard(Spike('group', sys.argv[1]), body)
