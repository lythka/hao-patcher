/*
 * Copyright (c) 2007-2020 Apple Inc. All rights reserved.
 * Adapted 2026 from xnu-12377.121.6, osfmk/vm/vm_shared_region.c,
 * _vm_commpage_init, vm_commpage_init/text_init and vm_commpage_enter.
 *
 * @APPLE_LICENSE_HEADER_START@
 * This file contains Original Code and/or Modifications of Original Code
 * as defined in and that are subject to the Apple Public Source License
 * Version 2.0 (the 'License'). You may not use this file except in
 * compliance with the License. Please obtain a copy of the License at
 * http://www.opensource.apple.com/apsl/ and read it before using this file.
 * The Original Code and all software distributed under the License are
 * distributed on an 'AS IS' basis, WITHOUT WARRANTY OF ANY KIND, EITHER
 * EXPRESS OR IMPLIED, AND APPLE HEREBY DISCLAIMS ALL SUCH WARRANTIES,
 * INCLUDING WITHOUT LIMITATION, ANY WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE, QUIET ENJOYMENT OR NON-INFRINGEMENT.
 * Please see the License for the specific language governing rights and
 * limitations under the License.
 * @APPLE_LICENSE_HEADER_END@
 */
#include <Headers/kern_mach.hpp>
#include <mach-o/loader.h>
#include <mach/kmod.h>
#include <libkern/libkern.h>
#include <libkern/OSAtomic.h>
#include <string.h>
#include <IOKit/IOLib.h>
#include <IOKit/IOUserClient.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <IOKit/IODeviceTreeSupport.h>
#include <libkern/c++/OSData.h>
#include "commpage_abi.hpp"
#include "sandbox_profile.h"
#include "sandbox_storage_class.h"
#include "iosurface_snapshot.h"

// Darwin 27 changed vm_map and kernel-flag layouts. Keep them opaque; call
// its own constructors and memory-entry routines. Addresses are generated
// from the specific target KC; no standalone-kernel data offsets are used.
using Map = void *;
using Port = void *;
static uintptr_t slide;
static Port handles[4];
extern "C" {
uintptr_t IntelCommpage_exec_resume;
void IntelCommpage_map_exec();
uintptr_t IntelCommpage_fixed_resume;
uintptr_t IntelCommpage_text32_slot, IntelCommpage_text64_slot;
void IntelCommpage_fixed_locator();
uintptr_t IntelCommpage_policy_resume;
void IntelCommpage_policy_register();
void IntelCommpage_rename_return();
uintptr_t IntelCommpage_profile_init_resume;
int IntelCommpage_profile_init_original(void *,unsigned char *,size_t);
uintptr_t IntelCommpage_user_trap_resume;
void IntelCommpage_user_trap_original(unsigned char *);
void IntelCommpage_user_trap(unsigned char *);
uintptr_t IntelCommpage_iosurface_set_value_resume;
int IntelCommpage_iosurface_set_value_original(void *,void *,IOExternalMethodArguments *);
}
template<typename T> static T at(uintptr_t a) { return reinterpret_cast<T>(a+slide); }
static void fail(const char *operation, int error) {
    panic("IntelCommpage: %s failed (%d)", operation, error);
}
static void make_map(unsigned index) {
    auto pmap = at<void *(*)(void *,uint64_t,unsigned)>(addr_pmap_create_options)(nullptr,0,0);
    if (!pmap) fail("pmap_create",-1);
    Map map = at<Map(*)(void *,uint64_t,uint64_t,unsigned)>(addr_vm_map_create_options)(pmap,0,4096,0);
    if (!map) fail("vm_map_create",-1);
    *at<Map *>(map_slots[index])=map;
}
static void make_handle(unsigned index) {
    // Export the kernel alias, not the sealed submap. Darwin 27 no longer
    // accepts Tahoe's named-submap handle flag; that bit now denotes a copy.
    Map map=*at<Map *>(addr_kernel_map);
    uint64_t alias=*at<uint64_t *>(alias_slots[index]);
    uint64_t size=4096;
    bool text=index>=2;
    int protection=text ? 5 : 1;
    auto protect=at<int(*)(Map,uint64_t,uint64_t,int,int)>(addr_vm_protect);
    // Text's kernel alias is normally RW. Temporarily expose RX to construct
    // an RX handle, then restore RW before starting any userspace processes.
    if (text) {
        int kr=protect(map,alias,4096,0,5);
        if (kr) fail("text alias RX",kr);
    }
    auto make_entry=at<int(*)(Map,uint64_t *,uint64_t,int,Port *,Port)>(addr_mach_make_memory_entry);
    int kr=make_entry(map,&size,alias,protection,&handles[index],nullptr);
    if (text) {
        int restore=protect(map,alias,4096,0,3);
        if (restore) fail("text alias RW restore",restore);
    }
    if (kr || size!=4096 || !handles[index]) fail("kernel alias memory entry",kr);
}
static void data_init() {
    printf("IntelCommpage: entering donor data initialization\n");
    make_map(0); make_map(1);
    at<void(*)()>(addr_commpage_populate)();
    make_handle(0); make_handle(1);
    printf("IntelCommpage: donor data initialization complete\n");
}
static void text_init() {
    auto random=at<unsigned(*)()>(addr_random);
    make_map(2);
    *at<uint32_t *>(addr_commpage_text32_location)=0xffff1000U+(random()%14U)*4096U;
    make_map(3);
    *at<uint64_t *>(addr_commpage_text64_location)=0x7fffffe01000ULL+(random()%510U)*4096ULL;
    at<void(*)()>(addr_commpage_text_populate)();
    make_handle(2); make_handle(3);
    printf("IntelCommpage: donor text initialization complete\n");
}
static int enter(Map map, void *task, int is64) {
    (void)task;
    unsigned index=is64 ? 1 : 0;
    uint64_t data_addr=is64 ? 0x7fffffe00000ULL : 0xffff0000ULL;
    uint64_t text_addr=is64 ? *at<uint64_t *>(addr_commpage_text64_location)
                           : *at<uint32_t *>(addr_commpage_text32_location);
    using Enter=int(*)(Map,uint64_t *,uint64_t,uint64_t,uint64_t,Port,uint64_t,int,int,int,int,void *,unsigned);
    auto map_entry=at<Enter>(addr_vm_map_enter_mem_object);
    int kr=map_entry(map,&data_addr,4096,0,map_fixed_beyond_max,handles[index],0,0,1,1,0,nullptr,0);
    if (kr) { printf("IntelCommpage: data mapping failed %d\n",kr); return kr; }
    kr=map_entry(map,&text_addr,4096,0,map_fixed_beyond_max|map_jit_flag,handles[index+2],0,0,5,5,0,nullptr,0);
    printf("IntelCommpage: process commpage mapping %s (%d)\n",is64 ? "x86_64":"i386",kr);
    return kr;
}
extern "C" void IntelCommpage_enter_for_exec(Map map, void *task, int is64) {
    int kr=enter(map,task,is64);
    if (kr) fail("exec commpage mapping",kr);
}
static bool matches_uuid(const mach_header_64 *h, const unsigned char *uuid) {
    if(h->magic!=MH_MAGIC_64 || h->cputype!=CPU_TYPE_X86_64) return false;
    auto p=reinterpret_cast<const unsigned char *>(h+1), end=p+h->sizeofcmds;
    for(unsigned i=0;i<h->ncmds;i++) {
        if(p+sizeof(load_command)>end) return false;
        auto lc=reinterpret_cast<const load_command *>(p);
        if(lc->cmdsize<sizeof(load_command) || p+lc->cmdsize>end) return false;
        if(lc->cmd==LC_UUID && lc->cmdsize>=sizeof(uuid_command))
            return memcmp(reinterpret_cast<const uuid_command *>(lc)->uuid,uuid,16)==0;
        p+=lc->cmdsize;
    }
    return false;
}
// Tahoe's newer Sandbox uses paired begin/end callbacks. Darwin 27.0 still
// calls its begin callback through the old will-rename slot, but has no end
// dispatch. Preserve its policy; finish pending swaps after vn_rename returns.
static void (*sandbox_rename_end)();
static void (*endpoint_port_notify)(void *,void *);
static int endpoint_port_check(void *cred,void *info) {
    endpoint_port_notify(cred,info);
    return 0; // Tahoe's notification has no authorization result.
}
static void write_jump(uintptr_t address, uintptr_t destination) {
    // Indirect absolute jump preserves every argument register.
    unsigned char jump[14]={0xff,0x25,0,0,0,0};
    memcpy(jump+6,&destination,8);
    memcpy(reinterpret_cast<void *>(address),jump,sizeof(jump));
}
// Tahoe's IOSurface maps serialized OOL property values using CoW. Darwin 27's
// CoW-defeatured clients prohibit copying malloc's DELAY_FORK objects this way.
// Snapshot through the descriptor's ordinary read API, then reuse the donor's
// inline parser. Pixel storage, sharing, permissions and VM guards are untouched.
static_assert(__builtin_offsetof(IOExternalMethodArguments,structureInput)==0x30,"IOSurface input ABI");
static_assert(__builtin_offsetof(IOExternalMethodArguments,structureInputSize)==0x38,"IOSurface input size ABI");
static_assert(__builtin_offsetof(IOExternalMethodArguments,structureInputDescriptor)==0x40,"IOSurface descriptor ABI");
struct IOSurfaceSnapshot {
    void *client, *reference;
    uint64_t length(IOMemoryDescriptor *descriptor) { return descriptor->getLength(); }
    void *allocate(uint64_t size) {
        // Pageable, naturally bounded by IOKit's allocator; no untrusted-size
        // wired allocation or new application/property-specific size limit.
        return IOMallocPageable(size,1);
    }
    void release(void *buffer,uint64_t size) { IOFreePageable(buffer,size); }
    int prepare(IOMemoryDescriptor *descriptor) { return descriptor->prepare(kIODirectionOut); }
    int complete(IOMemoryDescriptor *descriptor) { return descriptor->complete(kIODirectionOut); }
    uint64_t read(IOMemoryDescriptor *descriptor,uint64_t offset,void *buffer,uint64_t size) {
        return descriptor->readBytes(offset,buffer,size);
    }
    int original(IOExternalMethodArguments *arguments) {
        return IntelCommpage_iosurface_set_value_original(client,reference,arguments);
    }
    int bad_argument() { return kIOReturnBadArgument; }
    int no_memory() { return kIOReturnNoMemory; }
    int copy_error() { return kIOReturnVMError; }
};
static int iosurface_set_value_adapted(void *client,void *reference,IOExternalMethodArguments *arguments) {
    IOSurfaceSnapshot host{client,reference};
    return iosurface_snapshot(host,arguments);
}
static void install_iosurface_snapshot() {
    auto header=at<const mach_header_64 *>(addr_iosurface_header);
    uintptr_t entry=addr_iosurface_set_value+slide;
    if(!matches_uuid(header,iosurface_uuid) ||
       memcmp(reinterpret_cast<const void *>(entry),iosurface_prefix,sizeof(iosurface_prefix))) {
        printf("IntelCommpage: IOSurface entry differs; property snapshot adapter not installed\n");
        return;
    }
    IntelCommpage_iosurface_set_value_resume=entry+sizeof(iosurface_prefix);
    write_jump(entry,reinterpret_cast<uintptr_t>(iosurface_set_value_adapted));
    printf("IntelCommpage: IOSurface descriptor property snapshot adapter installed\n");
}
// Darwin 27.0's libsandbox numbers operations differently from Tahoe's Sandbox
// (four inserted operations), and Tahoe's profile_init rejects its operation
// count. Rewrite each such profile to Tahoe's numbering in the kernel's own
// copy before profile_init reads it; anything else passes unchanged.
// Its kernel-mig-routine numbering differs as well (27.0 adds io_service_get_wait_info at 143 and others;
// Tahoe has vm_remap_new_external): syscall-mig mask bitmaps are moved to Tahoe's indices by routine name.
static const sandbox_op_layout sandbox_layout={sandbox_target_ops,sandbox_donor_ops,sandbox_target_names,
    sandbox_filter_max,sizeof(sandbox_drop)/sizeof(sandbox_drop[0]),sandbox_drop,
    sandbox_mig_filter,sizeof(sandbox_mig_map)/sizeof(sandbox_mig_map[0]),sandbox_mig_map};
static volatile UInt32 sandbox_logged;
static bool first(UInt32 bit) { return !(OSBitOrAtomic(bit,&sandbox_logged)&bit); }
static int profile_init_adapted(void *profile, unsigned char *data, size_t size) {
    size_t length; unsigned reason;
    int status=sandbox_profile_adapt(data,size,&sandbox_layout,&length,&reason);
    if (status==SANDBOX_ADAPTED && first(0x80000000U))
        printf("IntelCommpage: Sandbox profile renumbered from %u to %u operations, MIG routines remapped (%lu to %lu bytes)\n",
               sandbox_target_ops,sandbox_donor_ops,static_cast<unsigned long>(size),static_cast<unsigned long>(length));
    if (status==SANDBOX_REFUSED && first(reason))
        printf("IntelCommpage: Sandbox profile with %u operations left unchanged (reason %#x)\n",sandbox_target_ops,reason);
    return IntelCommpage_profile_init_original(profile,data,length);
}
static void install_profile_adapter(uintptr_t header) {
    // Only the donor's exact entry bytes are replaced; otherwise profiles
    // keep the target numbering and profile_init rejects them as before.
    uintptr_t entry=header+sandbox_profile_init_delta;
    if (memcmp(reinterpret_cast<const void *>(entry),sandbox_profile_init_prefix,sandbox_profile_init_prefix_length)) {
        printf("IntelCommpage: Sandbox profile_init differs; operation layout adapter not installed\n");
        return;
    }
    IntelCommpage_profile_init_resume=entry+sandbox_profile_init_prefix_length;
    if (MachInfo::setKernelWriting(true,nullptr)!=KERN_SUCCESS) {
        printf("IntelCommpage: Sandbox profile_init not writable; operation layout adapter not installed\n");
        return;
    }
    write_jump(entry,reinterpret_cast<uintptr_t>(profile_init_adapted));
    MachInfo::setKernelWriting(false,nullptr);
    printf("IntelCommpage: Sandbox profile operation layout adapter installed (%u to %u)\n",sandbox_target_ops,sandbox_donor_ops);
}
// Darwin 27.0's libsystem_sandbox confirms a protected directory's storage class with Sandbox call
// 0x108 after setting it (0x105); Tahoe's Sandbox answers only 0x103-0x107 and returns ENOTSUP, so
// rootless_mkdir_protected fails ("storage class changed before protection") and dirhelper cannot make
// /var/folders. Answer 0x108 as 27.0's Sandbox does (sandbox_storage_class.h); pass every other call.
static int (*sandbox_policy_syscall)(void *,int,uint64_t);
static int verify_storage_class(uint64_t arg) {
    uint64_t request[2]={0,0};
    int error=at<int(*)(uint64_t,void *,size_t)>(addr_copyin)(arg,request,sizeof(request));
    if (error) return error;
    if (!request[1]) return SANDBOX_EINVAL;
    char requested[SANDBOX_CLASS_MAX]; size_t copied=0;
    error=at<int(*)(uint64_t,void *,size_t,size_t *)>(addr_copyinstr)(request[1],requested,sizeof(requested),&copied);
    if (error) return error;
    int fd;
    error=sandbox_storage_class_fd(request[0],&fd);
    if (error) return error;
    void *context=at<void *(*)()>(addr_vfs_context_current)();
    void *vnode=nullptr;
    error=at<int(*)(void *,int,void **)>(addr_vnode_getfromfd)(context,fd,&vnode);
    if (error) return error;
    char value[SANDBOX_CLASS_MAX]; size_t length=0;
    memset(value,0,sizeof(value));
    error=at<int(*)(void *,const char *,char *,size_t,size_t *)>(addr_mac_vnop_getxattr)(
        vnode,"com.apple.rootless",value,sizeof(value),&length);
    if (!error) {
        if (length>=sizeof(value)) error=SANDBOX_ERANGE;
        else { value[length]=0; error=sandbox_storage_class_verdict(value,length,requested); }
    }
    at<int(*)(void *)>(addr_vnode_put)(vnode);
    return error;
}
static int policy_syscall_adapted(void *proc, int call, uint64_t arg) {
    if (call!=SANDBOX_VERIFY_STORAGE_CLASS) return sandbox_policy_syscall(proc,call,arg);
    int error=verify_storage_class(arg);
    if (first(error ? 0x20000000U : 0x40000000U))
        printf("IntelCommpage: Sandbox call 0x108 (verify storage class) answered %d\n",error);
    return error;
}
static void install_policy_syscall_adapter(uintptr_t header, const unsigned char *ops) {
    // The donor's own handler in its own slot, with its exact entry bytes; otherwise 0x108 keeps
    // Tahoe's ENOTSUP and every call stays as it is.
    auto entry=reinterpret_cast<uintptr_t *>(const_cast<unsigned char *>(ops)+sandbox_policy_syscall_offset);
    uintptr_t handler=header+sandbox_policy_syscall_delta;
    if (*entry!=handler || memcmp(reinterpret_cast<const void *>(handler),sandbox_policy_syscall_prefix,
                                  sizeof(sandbox_policy_syscall_prefix))) {
        printf("IntelCommpage: Sandbox policy_syscall differs; storage class verification not installed\n");
        return;
    }
    sandbox_policy_syscall=reinterpret_cast<int(*)(void *,int,uint64_t)>(handler);
    if (MachInfo::setKernelWriting(true,nullptr)!=KERN_SUCCESS) {
        printf("IntelCommpage: Sandbox policy not writable; storage class verification not installed\n");
        return;
    }
    *entry=reinterpret_cast<uintptr_t>(policy_syscall_adapted);
    MachInfo::setKernelWriting(false,nullptr);
    printf("IntelCommpage: Sandbox call 0x108 (verify storage class) adapter installed\n");
}
extern "C" void IntelCommpage_observe_policy(const unsigned char *conf) {
    const char *name=*reinterpret_cast<const char *const *>(conf);
    if (!name) return;
    if (!strcmp(name,"EndpointSecurity")) {
        auto ops=*reinterpret_cast<unsigned char *const *>(conf+policy_ops_offset);
        auto entry=reinterpret_cast<uintptr_t *>(ops+endpoint_port_offset);
        auto callback=*entry;
        if (!callback || !matches_uuid(reinterpret_cast<const mach_header_64 *>(callback-endpoint_anchor_delta),endpoint_uuid))
            fail("EndpointSecurity port callback identity",-1);
        endpoint_port_notify=reinterpret_cast<void(*)(void *,void *)>(callback);
        if (MachInfo::setKernelWriting(true,nullptr)!=KERN_SUCCESS)
            fail("EndpointSecurity policy writable",-1);
        *entry=reinterpret_cast<uintptr_t>(endpoint_port_check);
        MachInfo::setKernelWriting(false,nullptr);
        printf("IntelCommpage: EndpointSecurity port notification ABI adapted\n");
        return;
    }
    if (strcmp(name,"Sandbox")) return;
    auto ops=*reinterpret_cast<const unsigned char *const *>(conf+policy_ops_offset);
    auto callback=*reinterpret_cast<const uintptr_t *>(ops+policy_completion_offset);
    if (!callback || !matches_uuid(reinterpret_cast<const mach_header_64 *>(callback-sandbox_anchor_delta),sandbox_uuid))
        fail("Sandbox rename callback identity",-1);
    sandbox_rename_end=reinterpret_cast<void(*)()>(callback);
    printf("IntelCommpage: Sandbox rename completion callback captured\n");
    install_profile_adapter(callback-sandbox_anchor_delta);
    install_policy_syscall_adapter(callback-sandbox_anchor_delta,ops);
}
extern "C" void IntelCommpage_rename_complete() {
    // The donor callback consumes only this thread's pending begin records.
    // A non-swap rename has no record and follows its normal empty-list path.
    if (sandbox_rename_end) sandbox_rename_end();
}
static uintptr_t amfi_interface[(amfi_donor_size+sizeof(uintptr_t))/sizeof(uintptr_t)];
static void register_amfi(const unsigned char *donor) {
    if (*at<const void **>(addr_amfi)) fail("AMFI already registered",-1);
    auto anchor=*reinterpret_cast<const uintptr_t *>(donor+amfi_anchor_offset);
    auto header=reinterpret_cast<const mach_header_64 *>(anchor-amfi_anchor_delta);
    if (!matches_uuid(header,amfi_uuid)
        || *reinterpret_cast<const unsigned *>(donor+amfi_trust_version_offset)!=amfi_trust_version
        || *reinterpret_cast<const unsigned *>(donor+amfi_entitlements_version_offset)!=amfi_entitlements_version)
        fail("AMFI donor mismatch",-1);
    // Darwin 27 inserted optional TrustCache.queryWithLength before the
    // entitlements interface. Null selects its existing legacy-query fallback.
    auto dest=reinterpret_cast<unsigned char *>(amfi_interface);
    memcpy(dest,donor,amfi_insert_offset);
    *reinterpret_cast<uintptr_t *>(dest+amfi_insert_offset)=0;
    memcpy(dest+amfi_insert_offset+sizeof(uintptr_t),donor+amfi_insert_offset,
           amfi_donor_size-amfi_insert_offset);
    *at<const void **>(addr_amfi)=amfi_interface;
    printf("IntelCommpage: Tahoe AMFI interface adapted; legacy trust query retained\n");
}
// Probe, only with boot-arg ic_csr_internal=1: report CSR_ALLOW_APPLE_INTERNAL (0x10) as set. Apple's booter drops
// that bit from csr-active-config on customer Macs; 27.0's dyld honours on-disk roots of shared-cache dylibs only
// when csr_check(0x10) succeeds (OCLP's legacy Metal stack). Offsets come from this kernel's csr_get_active_config.
static void csr_internal_probe() {
    int on=0;
    if(!at<int (*)(const char *,void *,int)>(addr_PE_parse_boot_argn)("ic_csr_internal",&on,sizeof(on)) || !on) return;
    uintptr_t args=*at<uintptr_t *>(addr_PE_state+pe_state_bootargs_offset);
    if(!args) return;
    volatile uint32_t *csr=reinterpret_cast<volatile uint32_t *>(args+boot_args_csr_offset);
    *csr|=0x10;
    printf("IntelCommpage: ic_csr_internal: csr config 0x%x (CSR_ALLOW_APPLE_INTERNAL)\n",*csr);
}
// Complete supported misaligned SSE operations inside this #GP. Signals and Mach exceptions
// can only observe the original state or a completed instruction; no user-mode transaction.
#include <mach/i386/thread_status.h>
#include "sse_fixup.h"
extern "C" void IntelCommpage_sse_execute(const void *,SseWork *);
extern "C" const unsigned char *IntelCommpage_sse_table[];
static volatile UInt32 sse_logged, sse_enabled=1;
static volatile SInt64 sse_fixups;
struct SseKernel {
    void *thread;
    x86_avx512_state64_t state;
    int flavor;
    int read(uint64_t from,void *to,size_t n) {
        return at<int (*)(uint64_t,void *,size_t)>(addr_copyin)(from,to,n);
    }
    int write(const void *from,uint64_t to,size_t n) {
        return at<int (*)(const void *,uint64_t,size_t)>(addr_copyout)(from,to,n);
    }
    bool debug_active() {
        x86_debug_state64_t debug={}; unsigned count=x86_DEBUG_STATE64_COUNT;
        int kr=at<int (*)(void *,int,void *,unsigned *)>(addr_machine_thread_get_state)(thread,x86_DEBUG_STATE64,&debug,&count);
        return kr || (debug.dr7&0xff); // data watchpoints must still observe the original address
    }
    bool get_state(unsigned char *&xmm,unsigned int *&mxcsr) {
        // Preserve the entire active extended state: FLOAT_STATE64 alone clears YMM state
        // in XNU's setter. get_fxstate does not promote a thread to AVX512.
        auto get=at<int (*)(void *,void *,int)>(addr_fpu_get_fxstate);
        flavor=x86_AVX512_STATE64;
        if(get(thread,&state,flavor)) {
            flavor=x86_AVX_STATE64;
            if(get(thread,&state,flavor)) {
                flavor=x86_FLOAT_STATE64;
                if(get(thread,&state,flavor)) return false;
            }
        }
        xmm=reinterpret_cast<unsigned char *>(&state.fpu_xmm0); mxcsr=&state.fpu_mxcsr;
        return true;
    }
    bool put_state() {
        return !at<int (*)(void *,void *,int)>(addr_fpu_set_fxstate)(thread,&state,flavor);
    }
    void execute(const sse_align_insn &d,SseWork &work) {
        auto interrupts=at<boolean_t (*)(boolean_t)>(addr_ml_set_interrupts_enabled);
        boolean_t was=interrupts(FALSE);
        IntelCommpage_sse_execute(IntelCommpage_sse_table[d.operation]+16*d.immediate,&work);
        interrupts(was);
    }
};
extern "C" void IntelCommpage_user_trap(unsigned char *ss) {
    if(sse_enabled && *reinterpret_cast<uint32_t *>(ss+SS_FLAVOR)==15 &&
       (*reinterpret_cast<uint64_t *>(ss+SS_CS)&3)==3 &&
       *reinterpret_cast<uint16_t *>(ss+SS_TRAPNO)==13 && !*reinterpret_cast<uint64_t *>(ss+SS_ERR)) {
        auto interrupts=at<boolean_t (*)(boolean_t)>(addr_ml_set_interrupts_enabled);
        boolean_t was=interrupts(TRUE);
        SseKernel host;
        host.thread=at<void *(*)()>(addr_current_thread)();
        uint64_t rip=*reinterpret_cast<uint64_t *>(ss+SS_RIP);
        if(sse_fixup(host,ss)) {
            OSIncrementAtomic64(&sse_fixups);
            if(!(OSBitOrAtomic(1,&sse_logged)&1))
                printf("IntelCommpage: synchronous misaligned SSE repair (rip 0x%llx)\n",static_cast<unsigned long long>(rip));
            return;
        }
        interrupts(was);
    }
    IntelCommpage_user_trap_original(ss);
}
static void install_sse_align() {
    int on=1;
    at<int (*)(const char *,void *,int)>(addr_PE_parse_boot_argn)("ic_sse_align",&on,sizeof(on));
    if(!on) { sse_enabled=0; printf("IntelCommpage: ic_sse_align=0: misaligned SSE operands not emulated\n"); return; }
    uintptr_t entry=addr_user_trap+slide;
    if(memcmp(reinterpret_cast<const void *>(entry),user_trap_prefix,user_trap_prefix_length)) {
        printf("IntelCommpage: user_trap differs; misaligned SSE emulation not installed\n"); return;
    }
    IntelCommpage_user_trap_resume=entry+user_trap_prefix_length;
    write_jump(entry,reinterpret_cast<uintptr_t>(IntelCommpage_user_trap));
    printf("IntelCommpage: misaligned SSE operand emulation installed (user #GP)\n");
}
static void route(uintptr_t address, uintptr_t destination) {
    write_jump(address+slide,destination);
}
static void publish_os_environment() {
    // Apple-silicon boot supplies this 32-byte normal-OS marker. EFI boot does
    // not. Darwin 27 clients read it without consistently checking for absence.
    // Preserve any booter-provided value, including the ramdisk environment.
    IORegistryEntry *chosen=IORegistryEntry::fromPath("/chosen",gIODTPlane);
    if (!chosen) {
        printf("IntelCommpage: chosen node unavailable; OS environment not published\n");
        return;
    }
    if (!chosen->getProperty("osenvironment")) {
        const unsigned char normal[32]={};
        OSData *value=OSData::withBytes(normal,sizeof(normal));
        bool installed=value && chosen->setProperty("osenvironment",value);
        if (value) value->release();
        printf("IntelCommpage: normal OS environment %s\n",installed?"published":"failed");
    }
    chosen->release();
}
extern "C" kern_return_t IntelCommpage_start(kmod_info_t *,void *) {
    slide=reinterpret_cast<uintptr_t>(&printf)-addr_printf;
    if(!matches_uuid(at<const mach_header_64 *>(addr_kernel_header),target_uuid)) {
        printf("IntelCommpage: target UUID mismatch\n"); return KERN_FAILURE;
    }
    for(unsigned i=0;i<sizeof(hook_sites)/sizeof(hook_sites[0]);i++) {
        if(memcmp(at<void *>(hook_sites[i]),hook_original[i],14)) {
            printf("IntelCommpage: target initializer differs\n");return KERN_FAILURE;
        }
    }
    if(MachInfo::setKernelWriting(true,nullptr)!=KERN_SUCCESS) return KERN_FAILURE;
    route(hook_sites[0],reinterpret_cast<uintptr_t>(data_init));
    route(hook_sites[1],reinterpret_cast<uintptr_t>(text_init));
    route(hook_sites[2],reinterpret_cast<uintptr_t>(enter));
    route(hook_sites[3],reinterpret_cast<uintptr_t>(register_amfi));
    IntelCommpage_exec_resume=hook_sites[4]+slide+exec_prefix_length;
    route(hook_sites[4],reinterpret_cast<uintptr_t>(IntelCommpage_map_exec));
    IntelCommpage_fixed_resume=hook_sites[5]+slide+fixed_prefix_length;
    IntelCommpage_text32_slot=addr_commpage_text32_location+slide;
    IntelCommpage_text64_slot=addr_commpage_text64_location+slide;
    route(hook_sites[5],reinterpret_cast<uintptr_t>(IntelCommpage_fixed_locator));
    IntelCommpage_policy_resume=hook_sites[6]+slide+register_prefix_length;
    route(hook_sites[6],reinterpret_cast<uintptr_t>(IntelCommpage_policy_register));
    route(hook_sites[7],reinterpret_cast<uintptr_t>(IntelCommpage_rename_return));
    csr_internal_probe();
    install_sse_align();
    install_iosurface_snapshot();
    MachInfo::setKernelWriting(false,nullptr);
    publish_os_environment();
    printf("IntelCommpage: macOS 26.5 source restoration installed\n");
    return KERN_SUCCESS;
}
extern "C" kern_return_t IntelCommpage_stop(kmod_info_t *,void *) { return KERN_FAILURE; }
KMOD_EXPLICIT_DECL(com.apple.kec.IntelCommpage,"0.1.0",IntelCommpage_start,IntelCommpage_stop)
