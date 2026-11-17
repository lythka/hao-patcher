#!/usr/bin/env python3
"""Make a model's hardware profile (profiles/NAME.json) from OCLP's own patch-set detection, not by hand.

  profile.py --source OCLP_SOURCE [--site SITE] --target pipeline/targets/NAME.json --like profiles/mbp92.json OUT

OCLP_SOURCE is the pinned OCLP source tree (row oclp_source, unpacked); SITE its Python packages (the kit's site,
for the kit's x86_64 Python 3.12). The target's "oclp_hardware_profile" (model, board_id, cpu, pci, pcie_webcam,
t1_chip) becomes OCLP's device_probe.Computer; OCLP's HardwarePatchsetDetection runs for the donor's Darwin version
(from --like's "donor_policy") and its patch sets become "patchset_order", "patches" and "entries" in the layout
tools/oclp-hardware reads. Every other key (OCLP and PSP versions, donor policy) is copied from --like. The 9,2's
recorded profile is reproduced exactly from pipeline/targets/mbp92.json (tests/test_profile.py, with OCLP_SOURCE).
"""
import argparse
import json
from pathlib import Path
import sys
import types


def computer(device_probe, hw):
    """OCLP's Computer for the target's hardware profile: GPUs (PCI class 0x03xxxx) and the Wi-Fi card (0x0280xx)
    from its PCI list, its CPU flags and leafs, the PCIe camera and T1 flags. Nothing is probed on this host."""
    def device(cls, d):
        return cls(vendor_id=int(d['vendor_id'], 16), device_id=int(d['device_id'], 16),
                   class_code=int(d['class_code'], 16), name=d.get('name'), model=d.get('model'),
                   pci_path=d.get('pci_path'), acpi_path=d.get('acpi_path'))
    kinds = {0x8086: device_probe.Intel, 0x10de: device_probe.NVIDIA, 0x1002: device_probe.AMD}
    gpus = [device(kinds[int(d['vendor_id'], 16)], d) for d in hw['pci']
            if d['class_code'].startswith('0x03') and int(d['vendor_id'], 16) in kinds]
    wifi = [device(device_probe.Broadcom, d) for d in hw['pci']
            if d['class_code'].startswith('0x0280') and int(d['vendor_id'], 16) == 0x14e4]
    igpu = next((g for g in gpus if (g.pci_path or '') == 'PciRoot(0x0)/Pci(0x2,0x0)'), None)
    return device_probe.Computer(
        real_model=hw['model'], real_board_id=hw['board_id'], reported_model=hw['model'],
        reported_board_id=hw['board_id'], gpus=gpus, igpu=igpu, dgpu=None, wifi=wifi[0] if wifi else None,
        cpu=device_probe.CPU(name=hw['cpu']['name'], flags=hw['cpu']['flags'], leafs=hw['cpu']['leafs']),
        pcie_webcam=bool(hw.get('pcie_webcam')), t1_chip=bool(hw.get('t1_chip')),
        third_party_sata_ssd=bool(hw.get('third_party_sata_ssd')))


def make(source, site, target, like):
    if site:
        sys.path.insert(0, str(Path(site).resolve()))
    package = types.ModuleType('opencore_legacy_patcher')           # as pipeline/stages/build-efi.py: no GUI import
    package.__path__ = [str(Path(source).resolve()/'opencore_legacy_patcher')]
    sys.modules[package.__name__] = package
    from opencore_legacy_patcher import constants
    from opencore_legacy_patcher.detections import device_probe
    from opencore_legacy_patcher.sys_patch.patchsets import detect
    t, base = json.loads(Path(target).read_text()), json.loads(Path(like).read_text())
    policy = base['donor_policy']
    c = constants.Constants()
    c.computer = computer(device_probe, t['oclp_hardware_profile'])
    found = detect.HardwarePatchsetDetection(
        c, xnu_major=policy['darwin_major'], xnu_minor=policy['darwin_minor'], os_build=policy['build'],
        os_version='%d.%d' % (policy['darwin_major'] + 1, policy['darwin_minor']))
    patches, entries = {}, []
    for name, ops in found.patches.items():
        kept = {}
        for op, folders in ops.items():
            op = str(getattr(op, 'value', op))
            if op not in ('Overwrite System Volume', 'Merge System Volume'):
                raise ValueError('%s: operation %r is not one tools/oclp-hardware applies' % (name, op))
            kept[op] = folders
            for folder, files in folders.items():
                for item, version in files.items():
                    entries.append(dict(patch=name, operation=op, destination=folder + '/' + item,
                                        source_version=version,
                                        support_package_relative_path=version + folder + '/' + item))
        patches[name] = kept
    stem = Path(target).stem
    out = dict(base, patchset_order=list(found.patches), patches=patches, entries=entries, model=t['model'],
               name='%s-legacy-hardware-darwin%d' % (stem, policy['darwin_major']))
    return {k: out[k] for k in base}                                  # the --like profile's key order


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--site', type=Path)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--like', type=Path, required=True)
    parser.add_argument('out', type=Path)
    a = parser.parse_args()
    argv, sys.argv = sys.argv, sys.argv[:1]                           # OCLP parses process arguments on import
    try:
        body = json.dumps(make(a.source, a.site, a.target, a.like), indent=2) + '\n'
    finally:
        sys.argv = argv
    a.out.write_text(body)


if __name__ == '__main__':
    main()
