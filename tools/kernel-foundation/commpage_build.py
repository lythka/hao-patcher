"""Build the macOS 26.5 commpage source adaptation for a target Intel KC."""
import argparse
import re
import datetime
import hashlib
import json
from pathlib import Path
import plistlib
import runpy
import shutil
import struct
import subprocess


def dependency_identity(path):
    record = dict(path=str(path.resolve()))
    if (path/'source.json').exists():
        identity = json.loads((path/'source.json').read_text())
        record.update(commit=identity['commit'], source=identity)
    else:
        record['commit'] = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    return record


def commands(data, offset=0):
    if struct.unpack_from('<II', data, offset) != (0xfeedfacf, 0x1000007):
        raise ValueError('Expected thin Intel Mach-O')
    pos = offset+32
    for _ in range(struct.unpack_from('<I', data, offset+16)[0]):
        cmd, size = struct.unpack_from('<II', data, pos)
        yield cmd, pos
        pos += size


def segments(data, offset=0):
    return {data[p+8:p+24].split(b'\0')[0]: struct.unpack_from('<QQQQ', data, p+24)
            for cmd, p in commands(data, offset) if cmd == 0x19}


def symbols(data):
    p = next(p for cmd, p in commands(data) if cmd == 2)
    off, count, strings, _ = struct.unpack_from('<4I', data, p+8)
    result = {}
    for i in range(count):
        name, kind, _, _, address = struct.unpack_from('<IBBHQ', data, off+i*16)
        if name and kind & 0xe:
            name = data[strings+name:].split(b'\0', 1)[0].decode()
            result[name] = address
    return result


def policy_syscall_binding(sandbox, ops_offset, completion_offset, callback_symbol):
    """The donor Sandbox's _hook_policy_syscall: its __TEXT offset, its slot in the donor's own policy ops
    table (the one table that holds the rename completion callback at completion_offset and that the policy
    conf named "Sandbox" points to at ops_offset) and its first 16 bytes. Every value is checked to be unique."""
    seg = segments(sandbox)
    def vmaddr(offset):
        for base, size, fileoff, filesize in seg.values():
            if fileoff <= offset < fileoff+filesize:
                return base+offset-fileoff
        raise ValueError('Sandbox offset outside segments')
    def fileoff(address):
        for base, size, offset, filesize in seg.values():
            if base <= address < base+filesize:
                return offset+address-base
        return None
    def named_sandbox(conf):
        name = fileoff(struct.unpack_from('<Q', sandbox, conf)[0]) if conf >= 0 else None
        return name is not None and sandbox[name:name+8] == b'Sandbox\0'
    def qwords(value, lo=0, hi=None):
        key, found, pos = struct.pack('<Q', value), [], lo
        while (pos := sandbox.find(key, pos, len(sandbox) if hi is None else hi)) >= 0:
            found.append(pos); pos += 1
        return found
    names = symbols(sandbox)
    tables = [h-completion_offset for h in qwords(names[callback_symbol])
              if any(named_sandbox(r-ops_offset) for r in qwords(vmaddr(h-completion_offset)))]
    if len(tables) != 1:
        raise ValueError(f'expected one donor Sandbox policy ops table, found {len(tables)}')
    slots = [q-tables[0] for q in qwords(names['_hook_policy_syscall'], tables[0], tables[0]+4096)]
    if len(slots) != 1:
        raise ValueError(f'expected one mpo_policy_syscall slot, found {slots}')
    delta = names['_hook_policy_syscall']-seg[b'__TEXT'][0]
    return delta, slots[0], sandbox[seg[b'__TEXT'][2]+delta:][:16]


CSR_GET = re.compile(rb'\x55\x48\x89\xe5\x48\x8b\x0d(....)\x31\xc0\xf6\x41\x06\x08\x74.\xb8\xff\x1f\x00\x00'
                     rb'\x23\x81(....)', re.S)


def user_trap_binding(code):
    """user_trap's frame-setup prologue (whole instructions, >= 14 bytes, no PC-relative operand) for the SSE
    alignment hook's trampoline, after checking that this kernel's user_trap reads x86_saved_state_t with the
    offsets IntelCommpage uses (flavor 15 at 0, the interrupt frame at 0xa0, err at 0xb0, rip at 0xb8)."""
    for name, pattern in (('flavor 15', b'\x41\x83\x3c\x24\x0f'), ('isf at 0xa0', b'\x49\x8d\x84\x24\xa0\x00\x00\x00'),
                          ('err at 0xb0', b'\x41\x8b\x9c\x24\xb0\x00\x00\x00'),
                          ('rip at 0xb8', b'\x49\x8b\x8c\x24\xb8\x00\x00\x00')):
        if pattern not in code:
            raise ValueError(f'user_trap: saved-state access ({name}) not found')
    steps = [b'\x55', b'\x48\x89\xe5', b'\x41\x57', b'\x41\x56', b'\x41\x55', b'\x41\x54', b'\x53']
    pos = 0
    while pos < 14:
        step = next((len(s) for s in steps if code.startswith(s, pos)), 0)
        if not step and code.startswith(b'\x48\x83\xec', pos):
            step = 4
        if not step:
            raise ValueError(f'user_trap prologue byte {pos} is not a known frame-setup instruction')
        pos += step
    return code[:pos]


def csr_layout(code, address, pe_state):
    """(PE_state offset of bootArgs, bootArgs offset of csrActiveConfig) read from csr_get_active_config's own code
    (`movq PE_state+X(%rip),%rcx; ...; andl $0x1fff,Y(%rcx)`) at address. Another shape stops the build: a wrong
    offset would write into the kernel."""
    m = CSR_GET.match(code)
    if not m:
        raise ValueError('csr_get_active_config differs from the expected shape')
    bootargs = address+11+struct.unpack('<i', m.group(1))[0]-pe_state
    csr = struct.unpack('<I', m.group(2))[0]
    if not (0 <= bootargs < 0x1000 and 0 < csr < 0x1000):
        raise ValueError('csr_get_active_config offsets out of range')
    return bootargs, csr


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('kernel', 'boot-kc', 'donor-kernel', 'donor-source', 'lilu', 'sdk', 'donor-amfi', 'amfi-layout', 'exec-entry', 'fixed-entry', 'donor-sandbox', 'rename-layout', 'donor-endpoint', 'port-policy-layout', 'sandbox-op-layout', 'iosurface-layout', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--fixed-beyond-max', type=lambda s:int(s,0), required=True,
                        help='Target ABI kernel VM flags, verified against target binary')
    parser.add_argument('--map-jit-flag', type=lambda s:int(s,0), required=True)
    args = parser.parse_args()
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=False)
    build = out/'build'; build.mkdir()
    kernel = args.kernel.read_bytes(); kc = args.boot_kc.read_bytes()
    amfi_layout = json.loads(args.amfi_layout.read_text())
    amfi = args.donor_amfi.read_bytes()
    if (amfi_layout['kernel_sha256'] != hashlib.sha256(kernel).hexdigest()
            or amfi_layout['donor_sha256'] != hashlib.sha256(amfi).hexdigest()):
        raise ValueError('AMFI layout does not match target and donor')
    entry = next(p for cmd,p in commands(kc) if cmd == 0x80000035
                 and kc[p+struct.unpack_from('<I',kc,p+24)[0]:].split(b'\0',1)[0] in (b'com.apple.kernel',b'mach_kernel'))
    header_address, header_offset = struct.unpack_from('<QQ',kc,entry+8)
    old, new = segments(kernel), segments(kc,header_offset)
    def mapped(va):
        for name, (base,size,_,_) in old.items():
            if base <= va < base+size:
                return new[name][0]+va-base
        raise ValueError('Symbol outside mapped segments')
    def read(va,size):
        for base,length,off,_ in new.values():
            if base <= va < base+length:
                return kc[off+va-base:off+va-base+size]
        raise ValueError('Hook outside KC segments')
    names = symbols(kernel)
    donor_names = symbols(args.donor_kernel.read_bytes())
    hooks = ['_vm_commpage_init','_vm_commpage_text_init','_vm_commpage_enter']
    for hook in hooks:
        if hook not in donor_names: raise ValueError('Donor lacks '+hook)
    hooks.append('_amfi_interface_register')
    exec_entry = json.loads(args.exec_entry.read_text())
    if exec_entry['kernel_sha256'] != hashlib.sha256(kernel).hexdigest():
        raise ValueError('Exec entry belongs to another kernel')
    exec_prefix = bytes.fromhex(exec_entry['prefix_hex'])
    if read(mapped(names[exec_entry['symbol']]),len(exec_prefix)) != exec_prefix:
        raise ValueError('Exec entry prologue differs')
    hooks.append(exec_entry['symbol'])
    fixed_entry = json.loads(args.fixed_entry.read_text())
    fixed_address = mapped(int(fixed_entry['address'], 0))
    fixed_prefix = bytes.fromhex(fixed_entry['prefix_hex'])
    if (fixed_entry['kernel_sha256'] != hashlib.sha256(kernel).hexdigest()
            or read(fixed_address,len(fixed_prefix)) != fixed_prefix):
        raise ValueError('Fixed locator capture differs')
    hook_addresses = [mapped(names[n]) for n in hooks]+[fixed_address]
    rename_layout = json.loads(args.rename_layout.read_text())
    sandbox = args.donor_sandbox.read_bytes()
    if (rename_layout['kernel_sha256'] != hashlib.sha256(kernel).hexdigest()
            or rename_layout['donor_sha256'] != hashlib.sha256(sandbox).hexdigest()):
        raise ValueError('Rename layout does not match target and donor')
    register_prefix = bytes.fromhex(rename_layout['register_prefix_hex'])
    return_prefix = bytes.fromhex(rename_layout['return_prefix_hex'])
    register_address = mapped(names['_mac_policy_register'])
    return_address = mapped(int(rename_layout['return_address'],0))
    if (read(register_address,len(register_prefix)) != register_prefix
            or read(return_address,len(return_prefix)) != return_prefix):
        raise ValueError('Rename hook bytes differ')
    hook_addresses += [register_address,return_address]
    uuid = next(kernel[p+8:p+24] for cmd,p in commands(kernel) if cmd==0x1b)
    kcuuid = next(kc[p+8:p+24] for cmd,p in commands(kc,header_offset) if cmd==0x1b)
    if uuid != kcuuid: raise ValueError('Standalone kernel and KC UUID differ')
    required = ['_printf','_pmap_create_options','_vm_map_create_options',
                '_commpage_populate','_commpage_text_populate','_random',
                '_commpage_text32_location','_commpage_text64_location',
                '_mach_make_memory_entry','_vm_map_enter_mem_object',
                '_kernel_map','_vm_protect','_amfi',
                '_copyin','_copyinstr','_vfs_context_current','_vnode_getfromfd','_mac_vnop_getxattr','_vnode_put',
                '_PE_state','_PE_parse_boot_argn',
                '_copyout','_ml_set_interrupts_enabled','_current_thread','_user_trap',
                '_machine_thread_get_state','_fpu_get_fxstate','_fpu_set_fxstate']
    maps = ['_commpage32_map','_commpage64_map','_commpage_text32_map','_commpage_text64_map']
    aliases = ['_commPagePtr32','_commPagePtr64','_commPageTextPtr32','_commPageTextPtr64']
    definitions = [f'static constexpr uintptr_t addr{name} = {mapped(names[name]):#x}ULL;' for name in required]
    iosurface_layout = json.loads(args.iosurface_layout.read_text())
    iosurface = runpy.run_path(str(Path(__file__).with_name('iosurface_layout.py')))
    iosurface['validate_descriptor_abi'](kernel)
    definitions += iosurface['definitions'](iosurface_layout, kc)
    iosurface_prefix = bytes.fromhex(iosurface_layout['prefix_hex'])
    user_trap_prefix = user_trap_binding(read(mapped(names['_user_trap']), 0x70))
    definitions += [f'static constexpr unsigned user_trap_prefix_length = {len(user_trap_prefix)};',
                    'static const unsigned char user_trap_prefix[] = {'+','.join(str(v) for v in user_trap_prefix)+'};']
    csr_get = mapped(names['_csr_get_active_config'])
    bootargs_offset, csr_offset = csr_layout(read(csr_get, 32), csr_get, mapped(names['_PE_state']))
    definitions += [f'static constexpr uintptr_t pe_state_bootargs_offset = {bootargs_offset:#x};',
                    f'static constexpr uintptr_t boot_args_csr_offset = {csr_offset:#x};']
    amfi_uuid = next(amfi[p+8:p+24] for cmd,p in commands(amfi) if cmd==0x1b)
    anchor = symbols(amfi)[amfi_layout['anchor_symbol']]-segments(amfi)[b'__TEXT'][0]
    port_layout = json.loads(args.port_policy_layout.read_text())
    endpoint = args.donor_endpoint.read_bytes()
    if (port_layout['kernel_sha256'] != hashlib.sha256(kernel).hexdigest()
            or port_layout['donor_sha256'] != hashlib.sha256(endpoint).hexdigest()):
        raise ValueError('Port policy layout does not match target and donor')
    endpoint_uuid = next(endpoint[p+8:p+24] for cmd,p in commands(endpoint) if cmd==0x1b)
    endpoint_anchor = symbols(endpoint)[port_layout['callback_symbol']]-segments(endpoint)[b'__TEXT'][0]
    definitions += ['static const unsigned char endpoint_uuid[] = {'+','.join(str(v) for v in endpoint_uuid)+'};',
                    f'static constexpr uintptr_t endpoint_anchor_delta = {endpoint_anchor};',
                    f'static constexpr unsigned endpoint_port_offset = {port_layout["callback_offset"]};']
    sandbox_uuid = next(sandbox[p+8:p+24] for cmd,p in commands(sandbox) if cmd==0x1b)
    sandbox_anchor = symbols(sandbox)[rename_layout['callback_symbol']]-segments(sandbox)[b'__TEXT'][0]
    definitions += ['static const unsigned char sandbox_uuid[] = {'+','.join(str(v) for v in sandbox_uuid)+'};',
                    f'static constexpr uintptr_t sandbox_anchor_delta = {sandbox_anchor};',
                    f'static constexpr unsigned policy_ops_offset = {rename_layout["ops_offset"]};',
                    f'static constexpr unsigned policy_completion_offset = {rename_layout["completion_offset"]};',
                    f'static constexpr unsigned register_prefix_length = {len(register_prefix)};']
    policy_syscall_delta, syscall_slot, policy_syscall_prefix = policy_syscall_binding(
        sandbox, rename_layout['ops_offset'], rename_layout['completion_offset'], rename_layout['callback_symbol'])
    definitions += [f'static constexpr uintptr_t sandbox_policy_syscall_delta = {policy_syscall_delta:#x};',
                    f'static constexpr unsigned sandbox_policy_syscall_offset = {syscall_slot};',
                    'static const unsigned char sandbox_policy_syscall_prefix[] = {'+','.join(str(v) for v in policy_syscall_prefix)+'};']
    # Sandbox profile operation layout (sandbox_layout.py): the donor's numbering for the target's profiles.
    op_layout = json.loads(args.sandbox_op_layout.read_text())
    if op_layout['donor_sha256'] != hashlib.sha256(sandbox).hexdigest():
        raise ValueError('Sandbox operation layout belongs to another donor')
    drop = op_layout['drop']
    if (op_layout['target_operation_count']-len(drop) != op_layout['donor_operation_count']
            or drop != sorted(set(drop)) or any(i >= op_layout['target_operation_count'] for i in drop)):
        raise ValueError('Sandbox operation layout is not a removal of target operations')
    # kernel-mig-routine bitmaps (sandbox_layout.py mig): the target's routine index -> the donor's, by name.
    mig = op_layout.get('mig')
    if not mig:
        raise ValueError('Sandbox operation layout has no kernel-mig-routine map (make sandbox-layout r30+)')
    if (len(mig['map']) != mig['target_count'] or any(d is not None and not 0 <= d < mig['donor_count'] for d in mig['map'])
            or mig['donor_count'] > 8*64):
        raise ValueError('Sandbox kernel-mig-routine map is out of range')
    profile_init = op_layout['donor']['profile_init']
    profile_init_delta = symbols(sandbox)[profile_init['symbol']]-segments(sandbox)[b'__TEXT'][0]
    profile_init_prefix = bytes.fromhex(profile_init['prefix_hex'])
    text = segments(sandbox)[b'__TEXT']
    if (profile_init_delta != int(profile_init['offset'], 0)
            or sandbox[text[2]+profile_init_delta:text[2]+profile_init_delta+len(profile_init_prefix)] != profile_init_prefix):
        raise ValueError('Sandbox profile_init entry differs from the layout')
    definitions += [f'static constexpr unsigned sandbox_target_ops = {op_layout["target_operation_count"]};',
                    f'static constexpr unsigned sandbox_donor_ops = {op_layout["donor_operation_count"]};',
                    f'static constexpr unsigned sandbox_target_names = {op_layout["target_name_count"]};',
                    f'static constexpr unsigned sandbox_filter_max = {op_layout["donor"]["filter_max"]};',
                    'static const unsigned short sandbox_drop[] = {'+','.join(str(i) for i in drop)+'};',
                    f'static constexpr unsigned sandbox_mig_filter = {mig["filter"]};',
                    'static const unsigned short sandbox_mig_map[] = {'+','.join(str(0xffff if d is None else d) for d in mig['map'])+'};',
                    f'static constexpr uintptr_t sandbox_profile_init_delta = {profile_init_delta:#x};',
                    f'static constexpr unsigned sandbox_profile_init_prefix_length = {len(profile_init_prefix)};',
                    'static const unsigned char sandbox_profile_init_prefix[] = {'+','.join(str(v) for v in profile_init_prefix)+'};']
    definitions += ['static const unsigned char amfi_uuid[] = {'+','.join(str(v) for v in amfi_uuid)+'};',
                    f'static constexpr uintptr_t amfi_anchor_delta = {anchor};']
    definitions += [f'static constexpr unsigned amfi_{key} = {amfi_layout[key]};' for key in
                    ('insert_offset','donor_size','trust_version_offset','trust_version',
                     'entitlements_version_offset','entitlements_version','anchor_offset')]
    definitions += [f'static constexpr uintptr_t addr_kernel_header = {header_address:#x}ULL;',
                    f'static constexpr uint64_t map_jit_flag = {args.map_jit_flag:#x}ULL;',
                    'static const uintptr_t alias_slots[] = {'+','.join(f'{mapped(names[n]):#x}ULL' for n in aliases)+'};',
                    f'static constexpr unsigned exec_prefix_length = {len(exec_prefix)};',
                    f'static constexpr unsigned fixed_prefix_length = {len(fixed_prefix)};',
                    f'static constexpr uint64_t map_fixed_beyond_max = {args.fixed_beyond_max:#x}ULL;',
                    'static const unsigned char target_uuid[] = {'+','.join(str(v) for v in uuid)+'};',
                    'static const uintptr_t map_slots[] = {'+','.join(f'{mapped(names[n]):#x}ULL' for n in maps)+'};',
                    'static const uintptr_t hook_sites[] = {'+','.join(f'{a:#x}ULL' for a in hook_addresses)+'};',
                    'static const unsigned char hook_original[][14] = {'+','.join('{'+','.join(str(v) for v in read(a,14))+'}' for a in hook_addresses)+'};']
    (build/'commpage_abi.hpp').write_text('\n'.join(definitions)+'\n')
    here = Path(__file__).resolve().parent
    def run(*a): subprocess.run([str(v) for v in a], check=True)
    run('xcrun','clang++','-target','x86_64-apple-macos10.13','-std=c++17','-O2',
        '-mkernel','-fapple-kext','-fno-exceptions','-fno-rtti','-fno-stack-protector',
        '-mno-red-zone','-mcmodel=kernel','-fno-builtin','-nostdinc','-DKERNEL','-DKERNEL_PRIVATE',
        '-I'+str(args.sdk/'Headers'),'-I'+str(args.lilu/'Lilu'),'-I'+str(build),'-I'+str(here/'src'),
        '-Wno-deprecated-declarations','-Wno-unknown-warning-option','-Wno-stdlibcxx-not-found',
        '-c',here/'src/commpage.cpp','-o',build/'commpage.o')
    # The captured prefix has no PC-relative instructions. All original stack
    # arguments remain in place when the target function resumes.
    assembly = '''.text
.globl _IntelCommpage_map_exec
_IntelCommpage_map_exec:
    pushq %rdi
    pushq %rsi
    pushq %rdx
    pushq %rcx
    pushq %r8
    pushq %r9
    subq $8, %rsp
    callq _IntelCommpage_enter_for_exec
    addq $8, %rsp
    popq %r9
    popq %r8
    popq %rcx
    popq %rdx
    popq %rsi
    popq %rdi
    .byte '''+','.join(hex(v) for v in exec_prefix)+'''
    jmpq *_IntelCommpage_exec_resume(%rip)
'''
    # Only this extension requests the restored exception. Match exact pages;
    # leave the map's actual bounds and every remaining locator check intact.
    assembly += '''
.globl _IntelCommpage_fixed_locator
_IntelCommpage_fixed_locator:
    .byte '''+','.join(hex(v) for v in fixed_prefix)+'''
    movabsq $'''+hex(args.fixed_beyond_max & ~1)+''', %rax
    testq %rax, %r8
    jz 2f
    cmpq $4096, %rdx
    jne 2f
    movl $0xffff0000, %eax
    cmpq %rax, %rsi
    je 1f
    movabsq $0x7fffffe00000, %rax
    cmpq %rax, %rsi
    je 1f
    movq _IntelCommpage_text32_slot(%rip), %rax
    movl (%rax), %eax
    cmpq %rax, %rsi
    je 1f
    movq _IntelCommpage_text64_slot(%rip), %rax
    cmpq (%rax), %rsi
    jne 2f
1:
    leaq 4096(%rsi), %rax
    cmpq %rax, %r10
    cmovbq %rax, %r10
2:
    jmpq *_IntelCommpage_fixed_resume(%rip)
'''
    assembly += '''
.globl _IntelCommpage_policy_register
_IntelCommpage_policy_register:
    pushq %rdi
    pushq %rsi
    pushq %rdx
    pushq %rcx
    pushq %r8
    pushq %r9
    subq $8, %rsp
    callq _IntelCommpage_observe_policy
    addq $8, %rsp
    popq %r9
    popq %r8
    popq %rcx
    popq %rdx
    popq %rsi
    popq %rdi
    .byte '''+','.join(hex(v) for v in register_prefix)+'''
    jmpq *_IntelCommpage_policy_resume(%rip)
.globl _IntelCommpage_rename_return
_IntelCommpage_rename_return:
    callq _IntelCommpage_rename_complete
    .byte '''+','.join(hex(v) for v in return_prefix)+'''
'''
    # Donor profile_init's frame-setup prologue (no PC-relative operand), then
    # the rest of the donor function: the adapter's call to the original.
    assembly += '''
.globl _IntelCommpage_profile_init_original
_IntelCommpage_profile_init_original:
    .byte '''+','.join(hex(v) for v in profile_init_prefix)+'''
    jmpq *_IntelCommpage_profile_init_resume(%rip)
'''
    # user_trap's frame-setup prologue, then the rest of it: the SSE alignment hook's call to the original.
    assembly += '''
.globl _IntelCommpage_user_trap_original
_IntelCommpage_user_trap_original:
    .byte '''+','.join(hex(v) for v in user_trap_prefix)+'''
    jmpq *_IntelCommpage_user_trap_resume(%rip)
'''
    assembly += '''
.globl _IntelCommpage_iosurface_set_value_original
_IntelCommpage_iosurface_set_value_original:
    .byte '''+','.join(hex(v) for v in iosurface_prefix)+'''
    jmpq *_IntelCommpage_iosurface_set_value_resume(%rip)
'''
    (build/'exec_entry.S').write_text(assembly)
    run('xcrun','clang','-target','x86_64-apple-macos10.13','-c',
        build/'exec_entry.S','-o',build/'exec_entry.o')
    sse_assembly = runpy.run_path(str(here/'sse_codegen.py'))['assembly']()
    (build/'sse_exec.S').write_text(sse_assembly)
    run('xcrun','clang','-target','x86_64-apple-macos10.13','-DKERNEL','-c',build/'sse_exec.S','-o',build/'sse_exec.o')
    contents = out/'IntelCommpage.kext/Contents'; (contents/'MacOS').mkdir(parents=True)
    run('xcrun','ld','-arch','x86_64','-kext','-undefined','dynamic_lookup',
        '-o',contents/'MacOS/IntelCommpage',build/'commpage.o',build/'exec_entry.o',build/'sse_exec.o')
    info = dict(CFBundleIdentifier='com.apple.kec.IntelCommpage',CFBundleName='IntelCommpage',
                CFBundleExecutable='IntelCommpage',CFBundlePackageType='KEXT',
                CFBundleVersion='0.1.0',CFBundleShortVersionString='0.1.0',OSBundleRequired='Root',
                OSBundleLibraries={'as.vit9696.Lilu':'1.7.1','com.apple.kpi.libkern':'8.0.0',
                                   'com.apple.kpi.mach':'8.0.0','com.apple.kpi.iokit':'8.0.0'})
    # XNU loads KECs synchronously before publishing IOKit personalities.
    info['AppleKernelExternalComponent'] = True
    (contents/'Info.plist').write_bytes(plistlib.dumps(info))
    run('codesign','--force','--sign','-',contents.parent)
    shutil.copy2(args.donor_source/'APPLE_LICENSE',out/'APPLE_LICENSE')
    inputs = {n:dict(path=str(getattr(args,n).resolve()),sha256=hashlib.sha256(getattr(args,n).read_bytes()).hexdigest())
              for n in ('kernel','boot_kc','donor_kernel','donor_amfi','amfi_layout','exec_entry','fixed_entry','donor_sandbox','rename_layout','donor_endpoint','port_policy_layout','sandbox_op_layout','iosurface_layout')}
    source = args.donor_source/'osfmk/vm/vm_shared_region.c'
    inputs['donor_source'] = dict(path=str(args.donor_source.resolve()),
        identity=json.loads((args.donor_source/'source.json').read_text()),
        file_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    record = dict(unit='tools/kernel-foundation',revision=runpy.run_path(str(Path(__file__).resolve().parent / 'unit.py'))['REVISION'],operation='commpage-kext',
        date=datetime.datetime.now(datetime.timezone.utc).isoformat(),kernel_uuid=uuid.hex(),
        commit=subprocess.check_output(['git','-C',str(here),'rev-parse','HEAD'],text=True).strip(),
        dirty=bool(subprocess.check_output(['git','-C',str(here),'status','--porcelain'],text=True)),
        dependencies={n:dependency_identity(getattr(args,n)) for n in ('lilu','sdk')},
        inputs=inputs,fixed_beyond_max=hex(args.fixed_beyond_max),map_jit_flag=hex(args.map_jit_flag),
        files={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file()})
    (out/'build.json').write_text(json.dumps(record,indent=2)+'\n'); print(out)


if __name__ == '__main__': main()
