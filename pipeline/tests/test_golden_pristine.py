"""Golden-first stage orchestration on regular directories with mocked macOS tools.

No images, APFS mounts, native binaries or privileged host tools are used.
"""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'stages/golden-pristine.sh'


class GoldenPristineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='golden-pristine-test-')
        self.root = Path(self.temp.name).resolve()
        self.source = self.root/'Golden filesystem'
        self.system, self.data = self.root/'fresh System', self.root/'fresh Data'
        self.extensions, self.kdk = self.root/'Tahoe SLE', self.root/'Intel KDK'
        self.bin = self.root/'mock tools'
        for path in (self.source, self.system, self.data, self.extensions, self.kdk, self.bin): path.mkdir()
        def put(root, name, value):
            path=root/name;path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value if isinstance(value, bytes) else value.encode())
            return path
        put(self.source, 'System/Library/CoreServices/SystemVersion.plist', plistlib.dumps(dict(ProductBuildVersion='26A428')))
        put(self.source, 'System/Library/Extensions/GoldenArm.kext/Contents/Info.plist', 'Golden ARM extension')
        put(self.source, 'System/Library/Templates/Data/Library/golden-data', 'Golden data, not Tahoe')
        put(self.source, 'System/Library/Templates/Data/private/etc/golden.conf', 'Golden settings')
        (self.source/'etc').symlink_to('private/etc')
        (self.source/'System/Cryptexes').mkdir(parents=True)
        (self.source/'System/Cryptexes/OS').symlink_to('../../System/Volumes/Preboot/Cryptexes/OS')
        for name in ('Library', 'private', 'pkg', 'usr/local'):
            (self.source/name).mkdir(parents=True, exist_ok=True)
        put(self.source, 'usr/share/firmlinks', '/AppleInternal\tAppleInternal\n/Library\tLibrary\n/private\tprivate\n/pkg\tpkg\n/usr/local\tusr/local\n')
        put(self.extensions, 'StockOnly.kext/resource', 'stock-only required resource')
        put(self.extensions, 'Common.kext/binary', 'stock implementation')
        put(self.kdk, 'System/Library/Extensions/Common.kext/binary', 'KDK implementation')
        put(self.kdk, 'System/Library/Extensions/KDKOnly.kext/binary', 'KDK-only driver')
        self.kernel=put(self.root, 'Golden x86 kernel', 'Darwin27 kernel')
        self.log=self.root/'commands.jsonl'
        fake=self.bin/'fake'
        fake.write_text('#!'+sys.executable+'\n'+'''
import json, os, pathlib, shutil, subprocess, sys, runpy
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
with pathlib.Path(os.environ['STAGE_LOG']).open('a') as stream:
    stream.write(json.dumps([name]+args)+'\\n')
if name == os.environ.get('STAGE_FAIL'):
    raise SystemExit(19)
if name == 'hdiutil':
    if args[0]=='attach':
        destination=pathlib.Path(args[args.index('-mountpoint')+1])
        shutil.copytree(os.environ['GOLDEN_FIXTURE'], destination, dirs_exist_ok=True, symlinks=True)
    elif args[0]=='detach':
        shutil.rmtree(args[1]);pathlib.Path(args[1]).mkdir()
elif name == 'ditto':
    source,target=map(pathlib.Path,args[-2:])
    if source.is_dir(): shutil.copytree(source,target,dirs_exist_ok=True,symlinks=True)
    else: shutil.copy2(source,target,follow_symlinks=False)
elif name == 'rsync':
    source,target=map(pathlib.Path,args[-2:])
    assert source.with_suffix('.prepared').is_file(), 'KDK must be prepared before rsync'
    assert args[:4]==['-r','-i','-a','--checksum']
    shutil.copytree(source,target,dirs_exist_ok=True,symlinks=True)
elif name == 'install':
    if args[0]=='-d':
        for value in args[3:]: pathlib.Path(value).mkdir(parents=True,exist_ok=True)
    else:
        source,target=map(pathlib.Path,args[-2:]);shutil.copyfile(source,target);target.chmod(int(args[args.index('-m')+1],8))
elif name == 'fl':
    assert args[0]=='set'
    assert pathlib.Path(args[1]).is_dir()
elif name == 'python-stub':
    assert args[0]=='-I'
    if args[1]=='-c':
        raise SystemExit(subprocess.run([sys.executable,*args]).returncode)
    elif args[2] in ('check-system','copy-altlink'): pass
    elif args[2]=='copy-system':
        source=pathlib.Path(args[args.index('--source')+1]);target=pathlib.Path(args[args.index('--system')+1])
        helper=runpy.run_path(args[1])
        try: helper['copy_system_tree'](source,target,copy=lambda *a:None)
        except subprocess.CalledProcessError as error: raise SystemExit(error.returncode)
    elif args[2]=='install-extensions':
        target=pathlib.Path(args[args.index('--system')+1])/'System/Library/Extensions'
        shutil.rmtree(target)
        shutil.copytree(os.environ['TAHOE_EXTENSIONS'],target)
    elif args[2]=='install-native':
        target=pathlib.Path(args[args.index('--system')+1])
        assert (target/'System/Library/Kernels/kernel').read_text()=='Darwin27 kernel'
        assert (target/'System/Library/Extensions/StockOnly.kext/resource').exists()
        (target/'native-donor-applied').write_text('explicit donor')
    else: raise SystemExit('unexpected API '+repr(args))
''')
        fake.chmod(0o755)
        for name in ('hdiutil','diskutil','ditto','install','chflags','fl','python-stub','sync','chown','rsync'):
            (self.bin/name).symlink_to('fake')
        self.env=dict(os.environ, PATH=str(self.bin)+os.pathsep+os.environ['PATH'],
                      TMPDIR=str(self.root), GOLDEN_FIXTURE=str(self.source), STAGE_LOG=str(self.log), TAHOE_EXTENSIONS=str(self.extensions))
        self.preparer=put(self.root,'pinned OCLP/kdk_intel_payload.py',"""import os,shutil
from pathlib import Path
def prepare(source,destination):
    if os.environ.get('KDK_PREPARE_FAIL'): raise ValueError('fixture preparation refusal')
    source,destination=Path(source),Path(destination)
    assert source.name=='Extensions'
    assert not destination.exists()
    shutil.copytree(source,destination,symlinks=True)
    destination.with_suffix('.prepared').write_text('selected preparer invoked')
    return {'selected_preparer':'fixture','thinned':[],'excluded_bundles':[]}
""")
        self.common=['--system' ,str(self.system),'--python',str(self.bin/'python-stub')]
        self.system_command=['sh',str(SCRIPT),'system',*self.common,
            '--filesystem',str(self.root/'Golden filesystem.dmg'), '--image',str(self.root/'output.img'),
            '--layout-helper',str(SCRIPT.with_name('golden-layout.py'))]
        self.command=['sh',str(SCRIPT),'finalize',*self.common,'--data',str(self.data),
            '--fl',str(self.bin/'fl'),'--kernel',str(self.kernel),'--kdk',str(self.kdk),
            '--kdk-preparer',str(self.preparer),
            '--donor-cli',str(self.root/'selected Tahoe donors/make'),
            '--extensions-artifact',str(self.root/'extensions artifact')]

    def tearDown(self): self.temp.cleanup()

    def run_stage(self, extra=(), environment=None):
        return subprocess.run(self.command+list(extra), env=environment or self.env, text=True, capture_output=True)

    def commands(self): return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_golden_first_then_stock_extensions_kdk_kernel_and_explicit_native_overlay(self):
        system_copy=subprocess.run(self.system_command,env=self.env,text=True,capture_output=True)
        self.assertEqual(system_copy.returncode,0,system_copy.stdout+system_copy.stderr)
        before=(self.system/'System/Cryptexes/OS').lstat().st_ino
        completed=self.run_stage(['--native-artifact',str(self.root/'native artifact')])
        self.assertEqual((self.system/'System/Cryptexes/OS').lstat().st_ino,before)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
        self.assertIn('RESULT rc=0',completed.stdout)
        self.assertEqual(plistlib.loads((self.system/'System/Library/CoreServices/SystemVersion.plist').read_bytes())['ProductBuildVersion'],'26A428')
        self.assertEqual((self.data/'Library/golden-data').read_text(),'Golden data, not Tahoe')
        self.assertEqual(os.readlink(self.system/'etc'),'private/etc')
        self.assertFalse((self.system/'System/Library/Extensions/GoldenArm.kext').exists())
        self.assertEqual((self.system/'System/Library/Extensions/Common.kext/binary').read_text(),'KDK implementation')
        self.assertTrue((self.system/'System/Library/Extensions/StockOnly.kext/resource').is_file())
        self.assertTrue((self.system/'System/Library/Extensions/KDKOnly.kext/binary').is_file())
        self.assertEqual((self.system/'System/Library/Kernels/kernel').read_bytes(),self.kernel.read_bytes())
        self.assertTrue((self.system/'native-donor-applied').is_file())
        commands=self.commands()
        attach=next(row for row in commands if row[:2]==['hdiutil','attach'])
        for value in ('-readonly','-owners','on'):self.assertIn(value,attach)
        copies=[row for row in commands if row[0]=='ditto']
        for row in copies:self.assertEqual(row[1:4],['--rsrc','--extattr','--acl'])
        self.assertTrue(copies[0][-2].endswith('/filesystem/System'))
        self.assertTrue(any(row[-2].endswith('/System/Library/Templates/Data') for row in copies))
        self.assertFalse(any(row[-2].endswith('/filesystem') for row in copies))
        calls=[row for row in commands if row[0]=='python-stub']
        self.assertEqual([row[3] for row in calls if row[2]!='-c'],['check-system','copy-system','copy-altlink','install-extensions','install-native'])
        prepared=next(row for row in calls if row[2]=='-c')
        self.assertEqual(prepared[4],str(self.preparer))
        self.assertEqual(Path(prepared[5]),self.kdk/'System/Library/Extensions')
        self.assertEqual(Path(prepared[6]).name,'kdk-intel')
        owners=[row for row in commands if row[0]=='chown']
        self.assertEqual(owners,[['chown','-R','root:wheel',prepared[6]]])
        self.assertFalse(any(row[0]=='ditto' and row[-2]==prepared[5] for row in commands))
        self.assertIn('selected_preparer',completed.stdout)
        self.assertEqual((self.kdk/'System/Library/Extensions/Common.kext/binary').read_text(),'KDK implementation')
        links=[row for row in commands if row[0]=='fl']
        self.assertEqual([row[-1] for row in links],['Library','private','pkg','usr/local'])
        self.assertTrue((self.data/'pkg').is_dir())
        self.assertEqual(len([row for row in commands if row[:2]==['hdiutil','detach']]),1)
        self.assertFalse(any(row[0] in ('newfs_apfs','bless','kmutil') for row in commands))

    def test_system_copy_failure_detaches_and_never_copies_data(self):
        completed=subprocess.run(self.system_command,env=dict(self.env,STAGE_FAIL='ditto'),text=True,capture_output=True)
        self.assertEqual(completed.returncode,19,completed.stdout+completed.stderr)
        self.assertIn('RESULT rc=19',completed.stdout)
        commands=self.commands()
        self.assertEqual(commands[-1][:2],['hdiutil','detach'])
        self.assertFalse(any('install-extensions' in row for row in commands))
        self.assertEqual(list(self.data.iterdir()),[])

    def test_firmlink_failure_stops_before_donor_or_kernel(self):
        shutil.copytree(self.source,self.system,dirs_exist_ok=True,symlinks=True)
        completed=self.run_stage(environment=dict(self.env,STAGE_FAIL='fl'))
        self.assertEqual(completed.returncode,19,completed.stdout+completed.stderr)
        self.assertFalse(any('install-extensions' in row for row in self.commands()))
        self.assertFalse((self.system/'System/Library/Kernels/kernel').exists())

    def test_kdk_preparation_failure_stops_before_rsync_or_kernel(self):
        shutil.copytree(self.source,self.system,dirs_exist_ok=True,symlinks=True)
        completed=self.run_stage(environment=dict(self.env,KDK_PREPARE_FAIL='1'))
        self.assertNotEqual(completed.returncode,0)
        self.assertIn('fixture preparation refusal',completed.stderr)
        self.assertFalse(any(row[0]=='rsync' for row in self.commands()))
        self.assertFalse((self.system/'System/Library/Kernels/kernel').exists())

    def test_running_system_or_overlapping_roots_refused_before_mount(self):
        for system,data in [('/',self.data),(self.system,self.system),(self.system,self.system/'nested')]:
            with self.subTest(system=system,data=data):
                Path(data).mkdir(parents=True,exist_ok=True)
                completed=self.run_stage(['--system',str(system),'--data',str(data)])
                self.assertNotEqual(completed.returncode,0)
                self.assertFalse(self.log.exists())

    def test_firmlink_traversal_refused_and_source_detached(self):
        (self.source/'usr/share/firmlinks').write_text('/Library\t../escape\n')
        shutil.copytree(self.source,self.system,dirs_exist_ok=True,symlinks=True)
        completed=self.run_stage()
        self.assertEqual(completed.returncode,2,completed.stdout+completed.stderr)
        self.assertIn('invalid firmlink target',completed.stderr)
        self.assertFalse((self.root/'escape').exists())
        self.assertFalse(any('install-extensions' in row for row in self.commands()))


if __name__=='__main__':unittest.main()
