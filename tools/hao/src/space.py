"""The disk space making the USB installer needs, stage by stage (`hao create-installer --plan`; owner, 2026-11-16:
"figure out what the minimum required space at any given time is").

At each stage, what must exist on this Mac and on the USB drive: the downloads and our assets (sizes from release.json),
the IPSW's disk images, streamed straight onto the drive (release.json's IPSW pins), and the rows made on this Mac
(sizes measured, tools/hao/data/space-plan.json). The IPSW and the installer are read by range, never stored. Each
work tree is deleted once the rows made from it check. The peak is the largest stage; create-installer refuses before
writing anything when this Mac's free space or the drive is smaller.
"""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / 'data' / 'space-plan.json'
GB = 1e9


def load():
    return json.loads(DATA.read_text())


def plan(release, cache=None, data=None):
    """{'stages': [(name, mac GB, drive GB, what)], 'mac_peak', 'drive_need', 'download', 'read_by_range'}."""
    d = data or load()
    m = d['measured']
    downloads = [x for x in release.get('downloads', []) if not x.get('stream')]
    streamed = [x for x in release.get('downloads', []) if x.get('stream')]
    assets = release.get('assets', [])

    def here(x):
        return cache is not None and (Path(cache) / 'sha256' / x['sha256']).is_file()
    fetched = sum(x['size'] for x in downloads + assets if not here(x)) / GB
    kept = sum(x['size'] for x in downloads + assets) / GB
    images = 0.0
    for x in streamed:
        images += sum(i['size'] for i in (x.get('pins') or {}).get('images', {}).values()) / GB
    images = images or 23.7                      # a release without IPSW pins: the 26B131 images
    read = m['ipsw_read_gb'] + m['installer_read_gb'] if streamed else 0.0
    stages = []
    mac = kept
    stages.append(('download', mac, 0.0, 'Apple\'s small files, OCLP, OpenCore, Python, our assets (%.1f GB to fetch)'
                   % fetched))
    mac += m['place_unpack_gb']
    stages.append(('place', mac, 0.0, 'our assets unpacked where the rows read them; the rest APFS clones'))
    drive = images
    stages.append(('userspace', mac + m['scratch_gb'], drive, 'the IPSW\'s System, Rosetta and cryptex images; each '
                   'encrypted one is checked on the drive, then decrypted onto it'))
    mac += m['made_small_gb']
    stages.append(('IPSW rows', mac + m['metallib_work_gb'], drive, 'kernel, Metal libraries (from the images on the '
                   'drive), OCLP hardware, Python packages'))
    stages.append(('donors', mac + m['donor_tree_gb'] + m['donor_rows_gb'] + m['donor_scratch_gb'] + m['scratch_gb'],
                   drive,
                   'one x86_64 pass over the installer, read by range; its tree is deleted after'))
    mac += m['donor_rows_gb']
    stages.append(('kit', mac + m['kit_stage_mac_gb'], drive, 'the kit staged on this Mac (the images stay on the drive)'))
    drive += m['kit_rest_drive_gb'] + m['hao_folder_gb'] + d['boot_partition_gb']
    stages.append(('write', mac + m['kit_stage_mac_gb'], drive, 'OpenCore and the Recovery, hao and the kit onto the '
                   'drive; the images are moved into the kit, not copied'))
    stages.append(('after', mac, drive, 'the staged kit removed from this Mac (--keep-kit keeps it); downloads and '
                   'made rows kept for the next run'))
    return dict(stages=stages, mac_peak=max(s[1] for s in stages), drive_need=max(s[2] for s in stages),
                download=fetched, read_by_range=read, margin=d['margin_gb'], drive_fraction=d['drive_free_fraction'],
                install_cache_rebuild=bool(release.get('install_cache_rebuild')))


def problems(p, mac_free_bytes, drive_bytes=None):
    """What stops the run before it writes anything: too little free space here, or a drive too small."""
    out = []
    need = p['mac_peak'] + p['margin']
    if mac_free_bytes / GB < need:
        out.append('this Mac has %.1f GB free; making the USB installer needs %.1f GB at its peak (%.1f + %.0f margin)'
                   % (mac_free_bytes / GB, need, p['mac_peak'], p['margin']))
    if drive_bytes is not None and drive_bytes * p['drive_fraction'] / GB < p['drive_need']:
        out.append('the USB drive holds %.1f GB; the installer needs %.1f GB (a %d GB drive or larger)' % (
            drive_bytes / GB, p['drive_need'], drive_size_gb(p)))
    return out


MINIMUM_DRIVE_GB = 64      # what create-installer accepts (installer.STICK_GB; disks refuses under 60 * 10^9 bytes):
                           # up to 41 GB is written when the userspace images are copied from this Mac's store


def drive_size_gb(p):
    """The drive size to ask for: the smallest size sold (64, 128, ...) that holds the installer, never below what
    create-installer accepts."""
    size = MINIMUM_DRIVE_GB
    while size * p['drive_fraction'] < p['drive_need']:
        size *= 2
    return size


def lines(p):
    out = ['%-10s %8s %8s  %s' % ('stage', 'this Mac', 'drive', 'what is on disk')]
    for name, mac, drive, what in p['stages']:
        out.append('%-10s %6.1f GB %5.1f GB  %s' % (name, mac, drive, what))
    out.append('peak: %.1f GB on this Mac (plus a %.0f GB margin), %.1f GB on the USB drive (a %d GB drive or larger); '
               '%.1f GB downloaded, %.1f GB read by range from Apple\'s IPSW and installer'
               % (p['mac_peak'], p['margin'], p['drive_need'], drive_size_gb(p), p['download'], p['read_by_range']))
    if p.get('install_cache_rebuild'):
        out.append('On the target during install: shared-cache rebuild needs 55 GB free scratch space; '
                   'allow roughly 25–60 extra minutes on Ivy Bridge (estimated from host measurements).')
    return out
