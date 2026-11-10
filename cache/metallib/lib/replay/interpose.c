// DYLD_INSERT_LIBRARIES interposer: fakes the HD 4000 IOAccelerator user client for an in-process MetalOld device.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <mach/mach.h>
#include <IOKit/IOKitLib.h>
#define FAKEPORT 0x4d54u
#define FAKECONN 0x4d55u
#define FAKESHCONN 0x4d56u
#define LOG(...) do{ if(getenv("RP_LOG")) {fprintf(stderr,"[ip] " __VA_ARGS__); fputc('\n',stderr);} }while(0)
typedef struct { const void* r; const void* o; } interpose_t;
#define INTERPOSE(rep,orig) __attribute__((used)) static const interpose_t _ip_##orig __attribute__((section("__DATA,__interpose"))) = {(const void*)rep,(const void*)orig};
extern void* IOAccelDeviceCreateWithAPIProperty(io_service_t, const char*);
extern int IOAccelDeviceGetConfig64(void*, uint64_t*, uint64_t*, uint64_t*, uint64_t*, uint64_t*);
extern uint64_t IOAccelDeviceGetGlobalTraceObjectID(void*);
extern int IOAccelDeviceGetPeerInfo(void*, uint64_t*, uint32_t*, uint32_t*);
extern int IOAccelDeviceGetSharedMemorySize(void*, uint64_t*);
extern int IOAccelDeviceTraceEvent(uint64_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t,uint64_t);
extern io_connect_t IOAccelDeviceGetConnect(void*);
extern io_connect_t IOAccelSharedGetConnect(void*);
extern void* IOAccelSharedCreate(void*, ...);
extern uint64_t IOAccelDeviceGetMaxResourceSize(void*);
extern void* IOAccelResourceCreate(void*, ...);
static char fakedev[4096], fakeshared[4096];
static void* my_DevCreate(io_service_t s, const char* api){ LOG("IOAccelDeviceCreateWithAPIProperty(%x,%s)",s,api); if(s==FAKEPORT) return fakedev; return IOAccelDeviceCreateWithAPIProperty(s,api);}
INTERPOSE(my_DevCreate, IOAccelDeviceCreateWithAPIProperty)
static int my_Cfg(void*d,uint64_t*a,uint64_t*b,uint64_t*c,uint64_t*e,uint64_t*f){ LOG("GetConfig64"); if(d==fakedev){ uint64_t vram=getenv("RP_VRAM")?strtoull(getenv("RP_VRAM"),0,0):0; if(a)*a=0; if(b)*b=0; if(c)*c=vram; if(e)*e=vram; if(f)*f=0; return 0;} return IOAccelDeviceGetConfig64(d,a,b,c,e,f);}
INTERPOSE(my_Cfg, IOAccelDeviceGetConfig64)
static uint64_t my_Trace(void*d){ if(d==fakedev) return 0x1234; return IOAccelDeviceGetGlobalTraceObjectID(d);}
INTERPOSE(my_Trace, IOAccelDeviceGetGlobalTraceObjectID)
static int my_Peer(void*d,uint64_t*g,uint32_t*i,uint32_t*c){ LOG("GetPeerInfo"); if(d==fakedev){ if(g)*g=0; if(i)*i=0; if(c)*c=0; return 0;} return IOAccelDeviceGetPeerInfo(d,g,i,c);}
INTERPOSE(my_Peer, IOAccelDeviceGetPeerInfo)
static int my_Shm(void*d,uint64_t*s){ LOG("GetSharedMemorySize"); if(d==fakedev){ *s=1536ull<<20; return 0;} return IOAccelDeviceGetSharedMemorySize(d,s);}
INTERPOSE(my_Shm, IOAccelDeviceGetSharedMemorySize)
static uint64_t my_MaxRes(void*d){ if(d==fakedev) return 1536ull<<20; return IOAccelDeviceGetMaxResourceSize(d);}
INTERPOSE(my_MaxRes, IOAccelDeviceGetMaxResourceSize)
static io_connect_t my_GetConn(void*d){ LOG("IOAccelDeviceGetConnect"); if(d==fakedev) return FAKECONN; return IOAccelDeviceGetConnect(d);}
INTERPOSE(my_GetConn, IOAccelDeviceGetConnect)
static io_connect_t my_ShConn(void*d){ LOG("IOAccelSharedGetConnect"); if(d==fakeshared) return FAKESHCONN; return IOAccelSharedGetConnect(d);}
INTERPOSE(my_ShConn, IOAccelSharedGetConnect)
static void* my_ShCreate(void*d, uint64_t a, uint64_t b, uint64_t c){ LOG("IOAccelSharedCreate(%p)",d); if(d==fakedev) return fakeshared; return IOAccelSharedCreate(d,a,b,c);}
INTERPOSE(my_ShCreate, IOAccelSharedCreate)
static void* my_ResCreate(void*d, uint64_t a, uint64_t b, uint64_t c, uint64_t e, uint64_t f){ LOG("IOAccelResourceCreate(%p) -> NULL",d); if(d==fakedev||d==fakeshared) return NULL; return IOAccelResourceCreate(d,a,b,c,e,f);}
INTERPOSE(my_ResCreate, IOAccelResourceCreate)
static kern_return_t my_RegID(io_registry_entry_t e, uint64_t* id){ if(e==FAKEPORT){ *id=0x100000a1f; return 0;} return IORegistryEntryGetRegistryEntryID(e,id);}
INTERPOSE(my_RegID, IORegistryEntryGetRegistryEntryID)
static kern_return_t my_Retain(io_object_t o){ if(o==FAKEPORT) return 0; return IOObjectRetain(o);}
INTERPOSE(my_Retain, IOObjectRetain)
static kern_return_t my_Release(io_object_t o){ if(o==FAKEPORT) return 0; return IOObjectRelease(o);}
INTERPOSE(my_Release, IOObjectRelease)
static kern_return_t my_CallStruct(mach_port_t c, uint32_t sel, const void* in, size_t inSz, void* out, size_t* outSz){
  if(c==FAKECONN||c==FAKESHCONN){ LOG("IOConnectCallStructMethod(%x,sel=%u,in=%zu,out=%zu)",c,sel,inSz,outSz?*outSz:0);
    if(sel==0xb && out && outSz && *outSz>=12){ uint32_t*o=out; o[0]=getenv("RP_STEP0")?strtoul(getenv("RP_STEP0"),0,0):0; o[1]=getenv("RP_STEP1")?strtoul(getenv("RP_STEP1"),0,0):0; o[2]=0x01668086; return 0;}
    if(out&&outSz) memset(out,0,*outSz); return 0; }
  return IOConnectCallStructMethod(c,sel,in,inSz,out,outSz);}
INTERPOSE(my_CallStruct, IOConnectCallStructMethod)
static kern_return_t my_Call(mach_port_t c, uint32_t sel, const uint64_t* in, uint32_t inCnt, const void* inS, size_t inSSz, uint64_t* out, uint32_t* outCnt, void* outS, size_t* outSSz){
  if(c==FAKECONN||c==FAKESHCONN){ LOG("IOConnectCallMethod(%x,sel=%u,outS=%zu)",c,sel,outSSz?*outSSz:0);
    if(outS&&outSSz){ memset(outS,0,*outSSz);
      if(sel==0xa && getenv("RP_WATABLE")){ FILE*f=fopen(getenv("RP_WATABLE"),"rb"); if(f){ fread(outS,1,*outSSz,f); fclose(f);} } }
    if(out&&outCnt) memset(out,0,*outCnt*8); return 0; }
  return IOConnectCallMethod(c,sel,in,inCnt,inS,inSSz,out,outCnt,outS,outSSz);}
INTERPOSE(my_Call, IOConnectCallMethod)
static size_t my_confstr(int name, char* buf, size_t len){
  const char* d=getenv("RP_CACHE_DIR");
  if(name==_CS_DARWIN_USER_CACHE_DIR && d){ size_t n=strlen(d)+1; if(buf&&len){ strncpy(buf,d,len); buf[len-1]=0;} LOG("confstr(cache)->%s",d); return n; }
  return confstr(name,buf,len);}
INTERPOSE(my_confstr, confstr)
#include <dlfcn.h>
static char fakecomm[65536];
__attribute__((constructor)) static void rp_init(void){
  void** gp=(void**)dlsym(RTLD_DEFAULT,"__globalCommPage"); if(!gp) gp=(void**)dlsym(RTLD_DEFAULT,"_globalCommPage");
  LOG("__globalCommPage at %p val %p", gp, gp?*gp:0); if(gp && !*gp) *gp=fakecomm;
}
extern int IOAccelSharedSetupDirtyRing(void*);
static int my_Dirty(void*s){ LOG("SetupDirtyRing"); if(s==fakeshared) return 0; return IOAccelSharedSetupDirtyRing(s);}
INTERPOSE(my_Dirty, IOAccelSharedSetupDirtyRing)
extern int IOAccelSharedCreateDeviceShmem(void*, uint64_t, void*);
static char fakeshmem[1<<16];
static int my_Shmem(void*s,uint64_t sz,void*out){ LOG("CreateDeviceShmem(%llu)",sz); if(s==fakeshared){ if(out) memset(out,0,32); return 0;} return IOAccelSharedCreateDeviceShmem(s,sz,out);}
INTERPOSE(my_Shmem, IOAccelSharedCreateDeviceShmem)
static void* my_dlopen(const char* p, int m){ void* h=dlopen(p,m); if(getenv("RP_LOG")||!h) fprintf(stderr,"[ip] dlopen(%s,%x) -> %p %s\n",p?p:"NULL",m,h,h?"":dlerror()); return h;}
INTERPOSE(my_dlopen, dlopen)
#include <sys/stat.h>
#include <fcntl.h>
#include <stdarg.h>
// path redirect: /System/Library/Extensions/<x>.bundle/Contents/MacOS/<leaf> -> $DYLD_LIBRARY_PATH/<leaf>;
// /System/Library/Extensions/... (other) -> $RP_ROOT/System/Library/Extensions/... when that file exists
static const char* rp_map(const char* p, char* q, size_t n){
  if(!p) return p;
  const char* dl=getenv("DYLD_LIBRARY_PATH"); const char* root=getenv("RP_ROOT"); struct stat st;
  extern int stat_i64(const char*, struct stat*) __asm("_stat$INODE64");
  if(!strncmp(p,"/System/Library/Extensions/",27)){
    if(dl && strstr(p,".bundle/Contents/MacOS/")){ snprintf(q,n,"%s/%s",dl,strrchr(p,'/')+1); if(stat_i64(q,&st)==0){ LOG("map %s -> %s",p,q); return q;} }
    if(root){ snprintf(q,n,"%s%s",root,p); if(stat_i64(q,&st)==0){ LOG("map %s -> %s",p,q); return q;} }
  }
  return p;}
extern int stat_i64(const char*, struct stat*) __asm("_stat$INODE64");
static int my_stat(const char* p, struct stat* s){ char q[1024]; return stat_i64(rp_map(p,q,sizeof q),s);}
INTERPOSE(my_stat, stat_i64)
static int my_open(const char* p, int fl, ...){ int mode=0; if(fl&O_CREAT){ va_list a; va_start(a,fl); mode=va_arg(a,int); va_end(a);} char q[1024]; return open(rp_map(p,q,sizeof q),fl,mode);}
INTERPOSE(my_open, open)
static FILE* my_fopen(const char* p, const char* m){ char q[1024]; return fopen(rp_map(p,q,sizeof q),m);}
INTERPOSE(my_fopen, fopen)
static int my_access(const char* p, int m){ char q[1024]; return access(rp_map(p,q,sizeof q),m);}
INTERPOSE(my_access, access)
extern int gpu_bundle_is_path_trusted(const char*);
static int my_trusted(const char* p){ LOG("gpu_bundle_is_path_trusted(%s) -> 0 (trusted)",p); return 0; }
INTERPOSE(my_trusted, gpu_bundle_is_path_trusted)
#include <signal.h>
#include <sys/ucontext.h>
static void pr(const char* tag, uintptr_t a){ Dl_info i; if(dladdr((void*)a,&i)&&i.dli_fname) fprintf(stderr,"[segv] %s %#lx %s+%#lx %s\n",tag,a,strrchr(i.dli_fname,'/')+1,a-(uintptr_t)i.dli_fbase,i.dli_sname?i.dli_sname:""); else fprintf(stderr,"[segv] %s %#lx ?\n",tag,a);}
static void segv(int sig, siginfo_t* si, void* uc_){ ucontext_t* uc=uc_; _STRUCT_X86_THREAD_STATE64* s=&uc->uc_mcontext->__ss;
  fprintf(stderr,"[segv] sig %d addr %p rip %#llx rsp %#llx rbp %#llx rax %#llx rdi %#llx rsi %#llx\n",sig,si->si_addr,s->__rip,s->__rsp,s->__rbp,s->__rax,s->__rdi,s->__rsi);
  pr("rip",s->__rip); uintptr_t* sp=(uintptr_t*)s->__rsp; pr("[rsp]",sp[0]);
  uintptr_t* bp=(uintptr_t*)s->__rbp; for(int i=0;i<14 && bp && ((uintptr_t)bp&7)==0;i++){ pr("ret",bp[1]); bp=(uintptr_t*)bp[0]; }
  _exit(128 + sig);}
__attribute__((constructor)) static void rp_sig(void){ struct sigaction sa={0}; sa.sa_sigaction=segv; sa.sa_flags=SA_SIGINFO; sigaction(SIGSEGV,&sa,0); sigaction(SIGBUS,&sa,0); sigaction(SIGILL,&sa,0); sigaction(SIGABRT,&sa,0);}
// Record the compiler's fatal-error reason (for example "Cannot select") before the process aborts.
extern void abort_with_reason(uint32_t, uint64_t, const char*, uint64_t);
extern void abort_with_payload(uint32_t, uint64_t, void*, uint32_t, const char*, uint64_t);
static void my_abort_reason(uint32_t ns, uint64_t code, const char* why, uint64_t flags){ fprintf(stderr,"[abort] ns=%u code=%llu %s\n",ns,(unsigned long long)code,why?why:"-"); abort_with_reason(ns,code,why,flags);}
INTERPOSE(my_abort_reason, abort_with_reason)
static void my_abort_payload(uint32_t ns, uint64_t code, void* p, uint32_t n, const char* why, uint64_t flags){ fprintf(stderr,"[abort] ns=%u code=%llu %s\n",ns,(unsigned long long)code,why?why:"-"); abort_with_payload(ns,code,p,n,why,flags);}
INTERPOSE(my_abort_payload, abort_with_payload)
