// Guest-only regression tests: real traps, signals, debugger state and extended FP state.
// Build: clang -arch x86_64 -O2 -mavx ssealign_regress.c -o ssealign_regress; codesign -s - ssealign_regress
#include <assert.h>
#include <mach/mach.h>
#include <mach/i386/thread_status.h>
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/time.h>
#include <unistd.h>
#include <immintrin.h>
extern void probe_load(void *), probe_store(void *);
extern char probe_load_end, probe_store_end;
__asm__(".text\n.globl _probe_load\n_probe_load:\nmovapd 8(%rdi),%xmm0\n.globl _probe_load_end\n_probe_load_end:\nretq\n"
        ".globl _probe_store\n_probe_store:\nmovaps %xmm0,8(%rdi)\n.globl _probe_store_end\n_probe_store_end:\nretq\n");
static volatile sig_atomic_t faults, alarms, jump_now;
static void *expected_base, *expected_pc, *resume_pc;
static sigjmp_buf escape;
static double data[16] __attribute__((aligned(64)));
static void segv(int sig,siginfo_t *info,void *ctx) {
    (void)info;
    ucontext_t *u=ctx;
    assert(sig==SIGSEGV || sig==SIGBUS);
    assert(u->uc_mcontext->__ss.__rdi==(uintptr_t)expected_base);
    assert(u->uc_mcontext->__ss.__rip==(uintptr_t)expected_pc);
    assert(!(u->uc_mcontext->__ss.__rflags&0x100));
    ++faults; u->uc_mcontext->__ss.__rip=(uintptr_t)resume_pc;
}
static void alarm_handler(int sig) {
    (void)sig; ++alarms;
    if(jump_now) siglongjmp(escape,1);
    // Nested repair on the same thread must not replace another instruction's state.
    probe_load(data);
}
static void timer(unsigned us) {
    struct itimerval t={{0,us},{0,us}}; assert(!setitimer(ITIMER_REAL,&t,0));
}
int main(void) {
    struct sigaction sa={0}; sa.sa_sigaction=segv;sa.sa_flags=SA_SIGINFO;
    sigemptyset(&sa.sa_mask); assert(!sigaction(SIGSEGV,&sa,0));assert(!sigaction(SIGBUS,&sa,0));
    sa.sa_handler=alarm_handler;sa.sa_flags=0;assert(!sigaction(SIGALRM,&sa,0));
    for(int i=0;i<16;i++) data[i]=i+1.25;
    size_t page=(size_t)getpagesize();
    unsigned char *p=mmap(0,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(p!=MAP_FAILED);
    memset(p,0x55,page*2);assert(!mprotect(p,page,PROT_READ));
    expected_base=p;expected_pc=probe_store;resume_pc=&probe_store_end;
    probe_store(p);assert(faults==1);for(int i=0;i<32;i++) assert(p[i]==0x55);
    puts("read-only store: original fault and GPR, no silent success OK");
    assert(!mprotect(p,page,PROT_READ|PROT_WRITE));assert(!mprotect(p+page,page,PROT_NONE));
    expected_base=p+page-16;probe_store(expected_base);assert(faults==2);
    for(int i=0;i<16;i++) assert(p[page-16+i]==0x55);
    puts("cross-page store: original fault, no partial write OK");
    unsigned csr=_mm_getcsr();_mm_setcsr(csr&~0x80u);
    expected_base=data;expected_pc=probe_load;resume_pc=&probe_load_end;
    probe_load(data);assert(faults==3);_mm_setcsr(csr);
    puts("unmasked MXCSR: original fault with original register state OK");
    mach_port_t thread=mach_thread_self();x86_debug_state64_t debug={0};
    debug.__dr0=(uintptr_t)data;debug.__dr7=1|(3<<16)|(2<<18);
    assert(thread_set_state(thread,x86_DEBUG_STATE64,(thread_state_t)&debug,x86_DEBUG_STATE64_COUNT)==KERN_SUCCESS);
    probe_load(data);assert(faults==4);
    memset(&debug,0,sizeof(debug));assert(thread_set_state(thread,x86_DEBUG_STATE64,(thread_state_t)&debug,x86_DEBUG_STATE64_COUNT)==KERN_SUCCESS);
    mach_port_deallocate(mach_task_self(),thread);
    puts("hardware watchpoint active: repair declines OK");
    uint64_t lanes[4]={1,2,0x1122334455667788ULL,0x8877665544332211ULL},out[4],other[4];
    __asm__ volatile("vmovdqu (%1),%%ymm0\nvmovdqu (%1),%%ymm15\nmovapd 8(%3),%%xmm0\nmulpd 8(%3),%%xmm0\nvmovdqu %%ymm0,(%0)\nvmovdqu %%ymm15,(%2)\nvzeroupper"
                     ::"r"(out),"r"(lanes),"r"(other),"r"(data):"xmm0","xmm15","memory");
    assert(out[2]==lanes[2] && out[3]==lanes[3] && !memcmp(lanes,other,32));
    puts("AVX: destination upper YMM and unrelated full YMM preserved OK");
    double x87in=1.23456789,x87out;
    __asm__ volatile("fldl (%1)\nmovapd 8(%2),%%xmm0\nmulpd 8(%2),%%xmm0\nfstpl (%0)"
                     ::"r"(&x87out),"r"(&x87in),"r"(data):"st","xmm0","memory");
    assert(x87out==x87in);puts("x87 value survives repairs OK");
    timer(1000);
    for(unsigned i=0;i<10000;i++) {
        double outd[2];
        __asm__ volatile("movapd 8(%1),%%xmm0\nmulpd 8(%1),%%xmm0\nmovupd %%xmm0,(%0)"::"r"(outd),"r"(data):"xmm0","memory");
        assert(outd[0]==data[1]*data[1] && outd[1]==data[2]*data[2]);
    }
    timer(0);assert(alarms>0);printf("signals with nested repairs: %d deliveries, correct results OK\n",alarms);
    for(volatile unsigned i=0;i<300;i++) {
        if(!sigsetjmp(escape,1)) {jump_now=1;timer(1000);for(;;) probe_load(data);}
        timer(0);jump_now=0;probe_load(data);
    }
    puts("300 signal longjmp exits, subsequent repairs OK");
    munmap(p,page*2);puts("PASS");return 0;
}
