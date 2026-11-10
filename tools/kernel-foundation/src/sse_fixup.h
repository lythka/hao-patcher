/* Synchronous #GP repair. Host supplies copyin/out, saved FP state and guarded SSE execution.
 * No user scratch, relocated GPR, TF, #DB interception or state surviving this call. */
#ifndef SSE_FIXUP_H
#define SSE_FIXUP_H
#include "sse_align.h"
enum { SS_FLAVOR=0, SS_TRAPNO=0xa0, SS_ERR=0xb0, SS_RIP=0xb8, SS_CS=0xc0, SS_RFLAGS=0xc8, SS_RSP=0xd0, SS_TF=0x100 };
static const unsigned short sse_reg_offset[16]={0x88,0x80,0x20,0x78,SS_RSP,0x70,0x18,0x10,0x30,0x38,0x28,0x68,0x60,0x58,0x50,0x48};
struct SseWork { unsigned char dst[16], src[16], mask[16]; unsigned int mxcsr; };
static inline void sse_bytes(void *dst, const void *src, unsigned n) {
    for(unsigned i=0;i<n;i++) ((unsigned char *)dst)[i]=((const unsigned char *)src)[i];
}
template<class Host> static bool sse_fixup(Host &host, unsigned char *ss) {
    auto &rip=*reinterpret_cast<unsigned long long *>(ss+SS_RIP);
    if(*reinterpret_cast<unsigned long long *>(ss+SS_RFLAGS)&SS_TF) return false;
    unsigned char code[15]; unsigned n=15;
    while(n && host.read(rip,code,n)) --n;
    sse_align_insn d;
    if(!n || !sse_align_decode(code,n,&d)) return false;
    unsigned long long reg[16];
    for(unsigned i=0;i<16;i++) reg[i]=*reinterpret_cast<unsigned long long *>(ss+sse_reg_offset[i]);
    unsigned long long ea=sse_align_address(&d,reg);
    if(!(ea&15) || ea>0x00007fffffffeff0ULL) return false;
    // A failed copyout spanning two pages can have written the first page already.
    // Leave such stores to the normal fault path without attempting a partial repair.
    if(d.store && (ea&4095)>4080) return false;
    if(host.debug_active()) return false;
    SseWork work;
    if(!d.store && host.read(ea,work.src,16)) return false;
    unsigned char *xmm; unsigned int *mxcsr;
    if(!host.get_state(xmm,mxcsr)) return false;
    // Running with masked exceptions would change unmasked exception priority/results.
    // Decline before executing or committing anything; never expose an intermediate #XM.
    if((*mxcsr&0x1f80)!=0x1f80) return false;
    if(d.store) {
        if(host.write(xmm+16*d.xmm,ea,16)) return false;
    } else {
        sse_bytes(work.dst,xmm+16*d.xmm,16);
        sse_bytes(work.mask,xmm,16);
        work.mxcsr=*mxcsr;
        if(sse_align_ops[d.operation].kind==1) sse_bytes(work.dst,work.src,16);
        else host.execute(d,work);
        sse_bytes(xmm+16*d.xmm,work.dst,16);
        *mxcsr=work.mxcsr;
        if(!host.put_state()) return false;
    }
    rip+=d.length;
    return true;
}
#endif
