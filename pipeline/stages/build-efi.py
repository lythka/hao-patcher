#!/usr/bin/env python3
"""Build a model's base EFI from pinned OCLP resources, without its application."""
import argparse
import hashlib
import json
import logging
from pathlib import Path
import plistlib
import re
import zipfile
import shutil
import sys
import tempfile
import types

SETTINGS = {'sip_status', 'secure_status', 'serial_settings', 'opencore_debug',
            'showpicker', 'oc_timeout', 'disable_mediaanalysisd', 'boot_efi'}


def build(source, site, target, revision, opencore, out):
    source, target, out = Path(source).resolve(), Path(target), Path(out).resolve()
    opencore=Path(opencore)
    selected=re.fullmatch(r'OpenCore-([0-9.]+)-(DEBUG|RELEASE)\.zip',opencore.name)
    if not selected:raise ValueError('expected a versioned OpenCore release archive')
    recipe = json.loads(target.read_text())
    settings = recipe['efi_build']['settings']
    if set(settings) - SETTINGS:
        raise ValueError('unsupported EFI setting: '+str(sorted(set(settings)-SETTINGS)))
    if out.exists():
        raise ValueError('EFI output must be new')
    if site:
        sys.path.insert(0, str(Path(site).resolve()))
    # Upstream __init__ imports the application entry point. Import only the
    # source package namespace: configuration generation needs no GUI or updater.
    package = types.ModuleType('opencore_legacy_patcher')
    package.__path__ = [str(source/'opencore_legacy_patcher')]
    sys.modules[package.__name__] = package
    from opencore_legacy_patcher import constants
    from opencore_legacy_patcher.detections import device_probe
    from opencore_legacy_patcher.efi_builder.build import BuildOpenCore
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='efi-build-', dir=out.parent) as temporary:
        configuration = constants.Constants()
        configuration.current_path = Path(temporary)
        configuration.payload_path = source/'payloads'
        configuration.custom_model = recipe['model']
        # External-model generation uses OCLP's model dataset. Never probe the
        # build host, read its preferences, or generate replacement serials.
        configuration.computer = device_probe.Computer()
        for name, value in settings.items():
            if type(value) is not type(getattr(configuration,name)):
                raise ValueError('wrong type for EFI setting: '+name)
            setattr(configuration,name,value)
        BuildOpenCore(recipe['model'], configuration)
        generated = configuration.opencore_release_folder
        config_path = generated/'EFI/OC/config.plist'
        config = plistlib.loads(config_path.read_bytes())
        config['#Revision']['Build-Version'] = configuration.patcher_version+' source '+revision
        config['#Revision']['OpenCore-Version'] = selected[1]+' - '+selected[2]
        # The model builder's bundled core can differ from our selected release.
        # Replace the complete selected OpenCore binary family, not just its core.
        with zipfile.ZipFile(opencore) as archive:
            members=set(archive.namelist())
            for path in generated.rglob('*.efi'):
                relative=str(path.relative_to(generated))
                name=('X64/EFI/BOOT/BOOTx64.efi' if relative=='System/Library/CoreServices/boot.efi'
                      else 'X64/'+relative)
                if name in members:path.write_bytes(archive.read(name))
        # OpenCore discovers this standard Windows path without BlessOverride.
        # Both pinned validators reject the redundant upstream entry.
        config['Misc']['BlessOverride']=[p for p in config['Misc']['BlessOverride']
                                         if p!='\\EFI\\Microsoft\\Boot\\bootmgfw.efi']
        config_path.write_bytes(plistlib.dumps(config))
        shutil.copytree(generated,out)
        record = dict(schema=1, operation='oclp-base-efi', model=recipe['model'],
                      source='https://github.com/dortania/OpenCore-Legacy-Patcher',
                      source_revision=revision, builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      oclp_version=configuration.patcher_version,
                      opencore_version=selected[1], opencore_variant=selected[2],
                      opencore_archive_sha256=hashlib.sha256(opencore.read_bytes()).hexdigest(), settings=settings,
                      target_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                      files={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in sorted(out.rglob('*')) if p.is_file()},
                      scope='base EFI; image-specific hao additions are applied during image assembly')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','target','opencore','out','record'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--site',type=Path)
    parser.add_argument('--revision',required=True)
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    # OCLP's terminal helper parses process arguments even through its build API.
    # Our CLI has already been parsed; do not expose it to upstream's parser.
    arguments=sys.argv
    try:
        sys.argv=sys.argv[:1]
        result=build(args.source,args.site,args.target,args.revision,args.opencore,args.out)
    finally:
        sys.argv=arguments
    args.record.write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
