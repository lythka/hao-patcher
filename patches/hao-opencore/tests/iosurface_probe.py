#!/usr/bin/env python3
"""Prepare native Intel IOSurface regression probes; never execute graphics on the host.

Usage: python3 iosurface_probe.py NEW_DIRECTORY [--sdk SDK_PATH]
Copy the four binaries to QEMU or the target Mac and run the controls in the patch README.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out',type=Path)
    parser.add_argument('--sdk',type=Path)
    args=parser.parse_args()
    here=Path(__file__).resolve().parent
    args.out.mkdir(parents=True,exist_ok=False)
    sdk=str(args.sdk) if args.sdk else subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip()
    binaries=[]
    sources={}
    for source, stem, frameworks in [('iosurface_value.c','iosurface-value',['-framework','CoreFoundation','-framework','IOSurface']), ('cow_guard_control.c','cow-guard-control',[])]:
      sources[source]=hashlib.sha256((here/source).read_bytes()).hexdigest()
      for hardened in (False,True):
        binary=args.out/(stem+'-cow' if hardened else stem)
        subprocess.run(['xcrun','clang','-arch','x86_64','-isysroot',sdk,'-mmacosx-version-min=14.0','-Wall','-Wextra','-Werror',str(here/source)]+frameworks+['-o',str(binary)],check=True)
        command=['codesign','-s','-','--force']
        if hardened:command+=['--entitlements',str(here/'cow-entitlements.plist')]
        subprocess.run(command+[str(binary)],check=True)
        binaries.append(dict(path=str(binary),sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),cow_defeatured=hardened))
    (args.out/'build.json').write_text(json.dumps(dict(sdk=sdk,sources=sources,binaries=binaries),indent=2)+'\n')
    print(args.out)

if __name__=='__main__':main()
