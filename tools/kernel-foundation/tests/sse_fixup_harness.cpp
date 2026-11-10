// Execute the shared transaction code and production register thunks with a mock kernel backend.
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include "../src/sse_fixup.h"
extern "C" void IntelCommpage_sse_execute(const void *,SseWork *);
extern "C" const unsigned char *IntelCommpage_sse_table[];
struct Host {
    alignas(16) unsigned char operand[64], xmm[256], saved[256], code[32];
    unsigned mxcsr=0x1f80, saved_mxcsr=0x1f80, writes=0, reads=0;
    bool readonly=false, debug=false, unreadable=false, reject_state=false;
    int read(uint64_t a,void *to,size_t n) {
        if(a==(uintptr_t)code) { memcpy(to,code,n); return 0; }
        ++reads;
        if(unreadable || a!=(uintptr_t)(operand+8) || n!=16) return 14;
        memcpy(to,operand+8,n); return 0;
    }
    int write(const void *from,uint64_t a,size_t n) {
        ++writes;
        assert(a==(uintptr_t)(operand+8) && n==16); // no writes to user stack
        if(readonly) return 14;
        memcpy(operand+8,from,n); return 0;
    }
    bool debug_active() { return debug; }
    bool get_state(unsigned char *&out,unsigned int *&csr) {
        memcpy(saved,xmm,256); saved_mxcsr=mxcsr; out=saved; csr=&saved_mxcsr; return true;
    }
    bool put_state() {
        if(reject_state) return false;
        memcpy(xmm,saved,256); mxcsr=saved_mxcsr; return true;
    }
    void execute(const sse_align_insn &d,SseWork &work) {
        IntelCommpage_sse_execute(IntelCommpage_sse_table[d.operation]+16*d.immediate,&work);
    }
};
static uint64_t &field(unsigned char *s,unsigned off) {return *reinterpret_cast<uint64_t *>(s+off);}
static void setup(Host &h,unsigned char *ss,const unsigned char *code,size_t n) {
    memset(ss,0,256); memcpy(h.code,code,n);
    field(ss,SS_RIP)=(uintptr_t)h.code; field(ss,sse_reg_offset[7])=(uintptr_t)h.operand;
    field(ss,SS_RSP)=1; // repair must not depend on usable stack space
    for(unsigned i=0;i<sizeof(h.xmm);i++) h.xmm[i]=(unsigned char)i;
    for(unsigned i=0;i<sizeof(h.operand);i++) h.operand[i]=(unsigned char)(255-i);
}
int main() {
    const unsigned char load[]={0x66,0x0f,0x28,0x47,0x08};
    const unsigned char store[]={0x0f,0x29,0x47,0x08};
    alignas(16) unsigned char ss[256], before[256];
    {
        Host h; setup(h,ss,load,sizeof(load)); memcpy(before,ss,256);
        assert(sse_fixup(h,ss)); assert(!memcmp(h.xmm,h.operand+8,16));
        field(before,SS_RIP)+=sizeof(load); assert(!memcmp(before,ss,256)); assert(h.writes==0);
    }
    for(int failure=0;failure<6;failure++) {
        Host h; setup(h,ss,load,sizeof(load));
        if(failure==0) field(ss,SS_RFLAGS)=SS_TF;
        if(failure==1) h.debug=true;
        if(failure==2) h.mxcsr &= ~0x80u;
        if(failure==3) h.unreadable=true;
        if(failure==4) h.reject_state=true;
        if(failure==5) field(ss,sse_reg_offset[7])=0x800000000000ULL;
        memcpy(before,ss,256); unsigned char xmm[256]; memcpy(xmm,h.xmm,256);
        assert(!sse_fixup(h,ss)); assert(!memcmp(before,ss,256)); assert(!memcmp(xmm,h.xmm,256)); assert(!h.writes);
    }
    {
        Host h; setup(h,ss,store,sizeof(store)); h.readonly=true; memcpy(before,ss,256);
        assert(!sse_fixup(h,ss)); assert(!memcmp(before,ss,256)); assert(h.operand[8]==247);
        assert(h.reads==0 && h.writes==1);
        h.readonly=false; h.unreadable=true; assert(sse_fixup(h,ss)); // stores do not read destination
        assert(!memcmp(h.operand+8,h.xmm,16));
    }
    {
        Host h; setup(h,ss,store,sizeof(store)); field(ss,sse_reg_offset[7])=0x1ff0;
        memcpy(before,ss,256); assert(!sse_fixup(h,ss)); assert(!h.writes); assert(!memcmp(before,ss,256));
    }
    {
        const unsigned char add[]={0x66,0x0f,0x58,0x47,0x08};
        Host h; setup(h,ss,add,sizeof(add)); double a[2]={1.25,-4.5}, b[2]={2.5,8.0}, out[2];
        memcpy(h.xmm,a,16); memcpy(h.operand+8,b,16); assert(sse_fixup(h,ss)); memcpy(out,h.xmm,16);
        assert(out[0]==3.75 && out[1]==3.5);
    }
    {
        const unsigned char shuffle[]={0x66,0x0f,0x70,0x47,0x08,0x1b};
        Host h; setup(h,ss,shuffle,sizeof(shuffle)); unsigned a[4]={1,2,3,4},out[4];
        memcpy(h.operand+8,a,16); assert(sse_fixup(h,ss)); memcpy(out,h.xmm,16);
        assert(out[0]==4 && out[1]==3 && out[2]==2 && out[3]==1);
        assert(field(ss,SS_RIP)==(uintptr_t)h.code+sizeof(shuffle));
    }
    {
        // PBLENDVB reads original XMM0 even when it is also the destination.
        const unsigned char blend[]={0x66,0x0f,0x38,0x10,0x47,0x08};
        Host h; setup(h,ss,blend,sizeof(blend));
        for(unsigned i=0;i<16;i++) h.xmm[i]=(i&1)?0x80:0x01;
        assert(sse_fixup(h,ss));
        for(unsigned i=0;i<16;i++) assert(h.xmm[i]==((i&1)?h.operand[i+8]:0x01));
    }
    // There is no per-thread capacity or transaction carried across an exception return.
    for(unsigned i=0;i<1024;i++) {Host h;setup(h,ss,load,sizeof(load));assert(sse_fixup(h,ss));}
    puts("synchronous SSE: loads, stores, faults, debug/TF, MXCSR, immediates, implicit XMM0, 1024 contexts OK");
}
