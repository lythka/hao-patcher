#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <pthread.h>
#include <stdatomic.h>
typedef unsigned __int128 U128;
typedef struct {uint64_t word;double real;} Mixed;
typedef struct {double a,b;} Reals;
#define PARAMS uintptr_t a,uintptr_t b,uintptr_t c,uintptr_t d,uintptr_t module,uintptr_t f,uintptr_t g,uintptr_t h,uintptr_t i,uintptr_t j,uintptr_t k,uintptr_t l,double x0,double x1,double x2,double x3,double x4,double x5,double x6,double x7
#define VALUES 0x1122334455667788ULL,2,3,4,0x55,6,7,8,9,10,11,0xffeeddccbbaa9988ULL,0.5,1.5,2.5,3.5,4.5,5.5,6.5,7.5
extern void compat_tramp(void);
void *compat_real_entry;
static _Atomic unsigned prepared,finished;
static void clobber(void){
 __asm__ volatile("pxor %%xmm0, %%xmm0\npxor %%xmm1, %%xmm1\npxor %%xmm2, %%xmm2\npxor %%xmm3, %%xmm3\npxor %%xmm4, %%xmm4\npxor %%xmm5, %%xmm5\npxor %%xmm6, %%xmm6\npxor %%xmm7, %%xmm7"
 :::"xmm0","xmm1","xmm2","xmm3","xmm4","xmm5","xmm6","xmm7","rax","rcx","rdx","rdi","rsi","r8","r9","r10","r11","memory");
}
void *compat_prepare(void *module){assert((uintptr_t)module==0x55);atomic_fetch_add(&prepared,1);clobber();return (void *)0x66;}
void compat_finish(void *original,void *used){assert((uintptr_t)original==0x55 && (uintptr_t)used==0x66);atomic_fetch_add(&finished,1);clobber();}
static void check(PARAMS){
 assert(a==0x1122334455667788ULL && b==2 && c==3 && d==4 && module==0x66 && f==6);
 assert(g==7 && h==8 && i==9 && j==10 && k==11 && l==0xffeeddccbbaa9988ULL);
 assert(x0==0.5 && x1==1.5 && x2==2.5 && x3==3.5 && x4==4.5 && x5==5.5 && x6==6.5 && x7==7.5);
}
#define CHECK check(a,b,c,d,module,f,g,h,i,j,k,l,x0,x1,x2,x3,x4,x5,x6,x7)
static U128 integers(PARAMS){CHECK;return ((U128)0x1020304050607080ULL<<64)|0xfedcba9876543210ULL;}
static Mixed mixed(PARAMS){CHECK;return (Mixed){0xabcdef0123456789ULL,123.5};}
static Reals reals(PARAMS){CHECK;return (Reals){123.5,456.5};}
static void *worker(void *unused){
 (void)unused;
 for(unsigned i=0;i<1000;i++){
  U128 result=((U128 (*)(PARAMS))compat_tramp)(VALUES);
  assert((uint64_t)result==0xfedcba9876543210ULL && (uint64_t)(result>>64)==0x1020304050607080ULL);
 }
 return 0;
}
int main(void){
 compat_real_entry=(void *)integers;
 pthread_t threads[4];for(int i=0;i<4;i++)assert(!pthread_create(&threads[i],0,worker,0));
 for(int i=0;i<4;i++)assert(!pthread_join(threads[i],0));
 compat_real_entry=(void *)mixed;Mixed m=((Mixed (*)(PARAMS))compat_tramp)(VALUES);assert(m.word==0xabcdef0123456789ULL && m.real==123.5);
 compat_real_entry=(void *)reals;Reals r=((Reals (*)(PARAMS))compat_tramp)(VALUES);assert(r.a==123.5 && r.b==456.5);
 assert(atomic_load(&prepared)==4002 && atomic_load(&finished)==4002);
 puts("PASS: 12 integer/pointer and 8 SIMD arguments; integer, mixed and SIMD returns; 4 concurrent threads; 4002 cleanup calls");
}
