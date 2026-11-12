"""A synthetic Boot kernel collection with the shapes the binder reads: a kernel fileset member (__TEXT, __DATA,
__DATA_CONST, __KLDDATA), an IOSurface member, the early-VM code points with RIP-relative fields, zero padding
before the first function, IntelCommpage hooks, user_trap and csr_get_active_config. Every segment can be moved,
as kmutil moves them, and the code's relocated fields follow.

`Fixture(**layout).write(root)` writes kc-recipe.json, the PV blob and a binder recipe made from this layout, and
returns their paths. No Apple bytes are involved."""
import hashlib
import json
from pathlib import Path
import shutil
import struct

HERE = Path(__file__).resolve().parent
UNIT = HERE.parent
BLOB = UNIT/'blobs/pv-alias.json'
BASE = 0xffffff8000000000
KERNEL_UUID = bytes(range(0x10, 0x20))
IOSURFACE_UUID = bytes(range(0x40, 0x50))
IOSURFACE_PREFIX = bytes.fromhex('554889e541574156415541545350')

# segment: (vm offset from BASE, vmsize, file offset)
SEGMENTS = {'__TEXT': (0x100000, 0x4000, 0x1000), '__DATA': (0x400000, 0x1000, 0x5000),
            '__DATA_CONST': (0x200000, 0x1000, 0x6000), '__KLDDATA': (0x600000, 0x1000, 0x7000)}
IOSURFACE = (0x900000, 0x1000, 0x9000)
SIZE = 0xa000

DATA = dict(virtual_space_start=('__DATA', 0x100), virtual_space_end=('__DATA', 0x108),
            pv_hash_mask=('__DATA', 0x200), pv_hash_table=('__DATA', 0x208), kernel_pmap=('__DATA', 0x300),
            avail_start=('__DATA', 0x400), last_managed_page=('__DATA', 0x408), phys_attributes=('__DATA', 0x410),
            pv_head_table=('__DATA', 0x418), virtual_avail=('__DATA', 0x420), vm_pages_end=('__DATA', 0x428),
            object_lock_data=('__DATA', 0x500), timer_data=('__DATA', 0x508), _PE_state=('__DATA', 0x800),
            _commPagePtr32=('__DATA', 0x900), pv_zone=('__DATA_CONST', 0x10), _commpage32_map=('__DATA_CONST', 0x20),
            map_stolen_data=('__KLDDATA', 0x80), map_data_size=('__KLDDATA', 0x88),
            kentry_data_size=('__KLDDATA', 0x90), map_nodes_data_size=('__KLDDATA', 0x98),
            # __TEXT functions
            vm_map_helper=('__TEXT', 0x2000), pmap_remove_options=('__TEXT', 0x2100),
            vm_object_lock=('__TEXT', 0x2200), pmap_virtual_space=('__TEXT', 0x2300), zalloc_flags=('__TEXT', 0x2400),
            pmap_find_pa=('__TEXT', 0x2500), pmap_query_pagesize=('__TEXT', 0x2600), panic=('__TEXT', 0x2700),
            panic_string=('__TEXT', 0x3000), _printf=('__TEXT', 0x2c00))
CODE = dict(cave=0x400, text=0x800, site=0x1000, range_call=0x1100, pv_hook=0x1200, hook_a=0x2800, hook_b=0x2900,
            _user_trap=0x2a00, _csr_get_active_config=0x2b00, version=0x3800)
PV_SYMBOLS = ['pv_zone', 'zalloc_flags', 'pv_hash_mask', 'pv_hash_table', 'kernel_pmap', 'avail_start',
              'pmap_find_pa', 'last_managed_page', 'phys_attributes', 'pv_head_table', 'pmap_query_pagesize',
              'virtual_avail', 'vm_pages_end', 'pmap_remove_options']
ADDRESSES = ['pmap_virtual_space', 'virtual_space_start', 'virtual_space_end', 'map_stolen_data', 'map_data_size',
             'kentry_data_size', 'map_nodes_data_size']


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def command(cmd, body):
    return struct.pack('<II', cmd, 8+len(body))+body


def segment(name, vmaddr, vmsize, fileoff, filesize):
    return command(0x19, name.encode().ljust(16, b'\0')+struct.pack('<QQQQiiII', vmaddr, vmsize, fileoff,
                                                                      filesize, 7, 5, 0, 0))


def header(commands):
    blob = b''.join(commands)
    return struct.pack('<IIIIIIII', 0xfeedfacf, 0x1000007, 3, 12, len(commands), len(blob), 0, 0)+blob


class Fixture:
    def __init__(self, moves=None, kernel_uuid=KERNEL_UUID, version='27.2.0', reversed_bounds=True):
        """moves: {segment: added vm offset}, as a different kmutil link would place it."""
        self.vm = {name: BASE+base+(moves or {}).get(name, 0) for name, (base, _, _) in SEGMENTS.items()}
        self.kernel_uuid, self.version, self.reversed_bounds = kernel_uuid, version, reversed_bounds
        self.data = bytearray(SIZE)
        self.fields = {}
        self._build()

    def va(self, name):
        segment, offset = DATA[name] if name in DATA else ('__TEXT', CODE[name])
        return self.vm[segment]+offset

    def fileoff(self, segment, offset):
        return SEGMENTS[segment][2]+offset

    def emit(self, item, offset, parts):
        """Write instructions at __TEXT+offset; parts are (prefix, target name or None, suffix)."""
        start, code, fields = self.vm['__TEXT']+offset, bytearray(), []
        for prefix, target, suffix in parts:
            if target is None:
                code += prefix
                continue
            end = len(code)+len(prefix)+4+len(suffix)
            fields.append(dict(at=len(code)+len(prefix), end=end, name=target))
            code += prefix+struct.pack('<i', self.va(target)-(start+end))+suffix
        position = self.fileoff('__TEXT', offset)
        self.data[position:position+len(code)] = code
        self.fields[item] = fields
        return len(code)

    def _build(self):
        kernel = [segment(name, self.vm[name], size, fileoff, size) for name, (_, size, fileoff) in SEGMENTS.items()]
        kernel.append(command(0x1b, self.kernel_uuid))
        member = header(kernel)
        self.data[0x1000:0x1000+len(member)] = member
        io = header([segment('__TEXT', BASE+IOSURFACE[0], IOSURFACE[1], IOSURFACE[2], IOSURFACE[1]),
                     command(0x1b, IOSURFACE_UUID)])
        self.data[IOSURFACE[2]:IOSURFACE[2]+len(io)] = io
        entry = IOSURFACE[2]+0x200
        self.data[entry:entry+len(IOSURFACE_PREFIX)] = IOSURFACE_PREFIX
        fileset = []
        for name, vm, off in (('com.apple.kernel', self.vm['__TEXT'], 0x1000),
                              ('com.apple.iokit.IOSurface', BASE+IOSURFACE[0], IOSURFACE[2])):
            fileset.append(command(0x80000035, struct.pack('<QQII', vm, off, 32, 0)+name.encode()+b'\0'*4))
        kc = header(fileset)
        self.data[:len(kc)] = kc
        version = f'Darwin Kernel Version {self.version}: fixture; root:xnu-0/RELEASE_X86_64\0'.encode()
        position = self.fileoff('__TEXT', CODE['version'])
        self.data[position:position+len(version)] = version
        imm = lambda v: struct.pack('<I', v)  # noqa: E731
        n = self.emit('site', CODE['site'], [
            (b'\x48\xc7\x05', 'map_data_size', imm(0x4000)), (b'\x48\xc7\x05', 'kentry_data_size', imm(0x2000)),
            (b'\x48\xc7\x05', 'map_nodes_data_size', imm(0x1000)), (b'\xb8\xff\x0f\x00\x00', None, b''),
            (b'\x48\x03\x05', 'virtual_space_start', b''), (b'\x72\x1b', None, b''),
            (b'\x48\x25\x00\xf0\xff\xff', None, b''), (b'\x48\x89\x05', 'map_stolen_data', b''),
            (b'\x48\x05\x00\x70\x00\x00', None, b''), (b'\x48\x89\x05', 'virtual_space_start', b''),
            (b'\xc3\x55\x48\x89\xe5', None, b''), (b'\x48\x8d\x3d', 'panic_string', b''),
            (b'\x48\x8d\x35', 'panic_string', b''), (b'\xba\xdd\x01\x00\x00\x31\xc0', None, b''),
            (b'\xe8', 'panic', b'')])
        assert n == 104, n
        moves = b'\x48\x89\xde\x4c\x89\xf2' if self.reversed_bounds else b'\x4c\x89\xf6\x48\x89\xda'
        assert self.emit('range_call', CODE['range_call'], [(moves+b'\xe8', 'vm_map_helper', b'')]) == 11
        self.emit('pv_hook', CODE['pv_hook'], [
            (b'\xe8', 'pmap_remove_options', b''), (b'\x4c\x8d\x2d', 'object_lock_data', b''),
            (b'\x4c\x89\xef\xe8', 'vm_object_lock', b''), (b'\x4c\x8b\x05', 'timer_data', b'')])
        self.emit('pv_cave_tail', CODE['text'], [(b'\x55\x48\x89\xe5\x48\x8d\x05', 'kernel_pmap', b''),
                                                 (b'\x5d\xc3'+bytes(range(0x60, 0x71)), None, b'')])
        self.emit('hook_a', CODE['hook_a'], [(b'\x55\x48\x89\xe5\x83\x3d', 'timer_data', b'\x03'),
                                             (b'\x7c\x40\xf6', None, b'')])
        self.emit('hook_b', CODE['hook_b'], [(bytes.fromhex('554889e55dc3662e0f1f84000000'), None, b'')])
        user_trap = (bytes.fromhex('554889e541574156415541545348' '83ec68') + bytes.fromhex('41833c240f')
                     + bytes.fromhex('498d8424a0000000418b9c24b0000000498b8c24b8000000'))
        self.emit('_user_trap', CODE['_user_trap'], [(user_trap.ljust(0x70, b'\xcc'), None, b'')])
        csr = self.emit('_csr_get_active_config', CODE['_csr_get_active_config'], [
            (b'\x55\x48\x89\xe5\x48\x8b\x0d', '_PE_state', b''),
            (bytes.fromhex('31c0f64106087405' 'b8ff1f0000' '2381' '98040000'), None, b'')])
        assert csr == 30, csr
        # csr_get_active_config reads PE_state+0xa0 (bootArgs): move the field's target.
        position = self.fileoff('__TEXT', CODE['_csr_get_active_config'])+7
        value = struct.unpack_from('<i', self.data, position)[0]+0xa0
        struct.pack_into('<i', self.data, position, value)
        self.fields['_csr_get_active_config'] = []

    # Recipes ---------------------------------------------------------------------------------------------------

    def point(self, name):
        segment, offset = DATA[name] if name in DATA else ('__TEXT', CODE[name])
        return dict(segment=segment, offset=hex(offset), kernel_address=hex(BASE+SEGMENTS[segment][0]+offset))

    def check(self, item, offset, length):
        position = self.fileoff('__TEXT', offset)
        raw = bytes(self.data[position:position+length])
        mask = bytearray(b'\xff'*length)
        fields = []
        for f in self.fields[item]:
            mask[f['at']:min(f['at']+4, length)] = bytes(len(mask[f['at']:f['at']+4]))
            segment, target = DATA[f['name']]
            fields.append(dict(at=f['at'], end=f['end'], target=dict(segment=segment, offset=hex(target)),
                               name=f['name']))
        masked = bytes(b & m for b, m in zip(raw, mask))
        return dict(length=length, mask=bytes(mask).hex(), masked_sha256=sha256(masked), fields=fields)

    def kc_recipe(self):
        cave = dict(self.point('cave'), capacity=1024, tail=32)
        early = dict(site=dict(self.point('site'), length=104), range_call=dict(self.point('range_call'), length=11),
                     pv_hook=dict(self.point('pv_hook'), length=24), pv_cave=cave,
                     pv_addresses={k: dict(self.point(k), symbol='_'+k) for k in PV_SYMBOLS},
                     addresses={k: dict(self.point(k), symbol='_'+k) for k in ADDRESSES})
        template = dict(kernel_uuid='00'*16, collection_sha256='00'*32, darwin_version='0.0.0',
                        site_address='0x0', site_offset='0x0', original='00'*104, addresses={},
                        reservations=dict(map_data_size=16384, kentry_data_size=8192, map_nodes_data_size=4096),
                        evidence=dict(source='fixture'),
                        range_call=dict(offset='0x0', address='0x0', original='00'*11, lower='r14', upper='rbx',
                                        evidence='fixture'),
                        pv_aliases=dict(cave=dict(address='0x0', offset='0x0', capacity=1024, original='00'*1056),
                                        hook=dict(address='0x0', offset='0x0', original='00'*24, overwrite_size=5),
                                        addresses={}, evidence='fixture'))
        return dict(kernel_uuid=self.kernel_uuid.hex(), kernel_sha256='00'*32,
                    definitions={'early-vm.json': early}, early_vm_template=template)

    def binder_recipe(self, kc_recipe, blob):
        code = dict(site=self.check('site', CODE['site'], 104), range_call=self.check('range_call', CODE['range_call'], 11),
                    pv_hook=self.check('pv_hook', CODE['pv_hook'], 24),
                    pv_cave_tail=self.check('pv_cave_tail', CODE['text'], 32))
        hooks = [dict(name=name, segment='__TEXT', offset=hex(CODE[name]), **self.check(name, CODE[name], 14))
                 for name in ('hook_a', 'hook_b')]
        symbols = {n: dict(segment=DATA[n][0], offset=hex(DATA[n][1])) for n in
                   ('_printf', '_PE_state', '_commPagePtr32', '_commpage32_map')}
        symbols.update({n: dict(segment='__TEXT', offset=hex(CODE[n])) for n in ('_user_trap', '_csr_get_active_config')})
        return dict(format=1, kc_recipe=dict(path=str(kc_recipe), sha256=sha256(kc_recipe.read_bytes())),
                    pv_blob=dict(path=str(blob), sha256=sha256(blob.read_bytes())), darwin_version=self.version,
                    code=code,
                    commpage=dict(required=['_printf', '_PE_state', '_user_trap'], alias_slots=['_commPagePtr32'],
                                  map_slots=['_commpage32_map'], symbols=symbols, hooks=hooks,
                                  reads=dict(user_trap=dict(symbol='_user_trap', length=0x70),
                                             csr_get_active_config=dict(symbol='_csr_get_active_config', length=32)),
                                  iosurface=dict(member='com.apple.iokit.IOSurface', entry=dict(
                                      segment='__TEXT', offset='0x200', length=14, mask='ff'*14,
                                      masked_sha256=sha256(IOSURFACE_PREFIX), fields=[]))))

    def write(self, root):
        """(kc path, binder recipe path) in root, with absolute pins to kc-recipe.json and the PV blob."""
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        kc, kc_recipe, blob, recipe = root/'BootKernelExtensions.kc', root/'kc-recipe.json', root/'pv-alias.json', root/'recipe.json'
        kc.write_bytes(bytes(self.data))
        kc_recipe.write_text(json.dumps(self.kc_recipe(), indent=1))
        shutil.copyfile(BLOB, blob)
        recipe.write_text(json.dumps(self.binder_recipe(kc_recipe, blob), indent=1))
        return kc, recipe
