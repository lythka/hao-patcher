// Private native compiler path/cache redirects, extracted from cache/shaders/src/interpose.c.
// Uses the actual GPU; no IOAccelerator device or connection is faked.
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

#include <dlfcn.h>
static size_t my_confstr(int name, char* buf, size_t len){
  const char* d=getenv("RP_CACHE_DIR");
  if(name==_CS_DARWIN_USER_CACHE_DIR && d){ size_t n=strlen(d)+1; if(buf&&len){ strncpy(buf,d,len); buf[len-1]=0;} LOG("confstr(cache)->%s",d); return n; }
  return confstr(name,buf,len);}
INTERPOSE(my_confstr, confstr)
#include <sys/stat.h>
#include <fcntl.h>
#include <stdarg.h>
// dlopen of a compiler framework's System path (libGPUCompiler loads libGPUCompilerImplLazy.dylib by absolute path
// for some vertex compiles, which fails with "no such file" outside the System) -> the runtime's copy in $DYLD_FRAMEWORK_PATH
static const char* rp_map(const char*, char*, size_t);
static void* my_dlopen(const char* p, int m){
  static const char* pre[]={"/System/Library/PrivateFrameworks/","/System/Library/Frameworks/"}; char q[1024], driver[1024]; struct stat st;
  const char* fw=getenv("DYLD_FRAMEWORK_PATH");
  // Explicit compiler-only mapping for hardened app launches. Do not redirect
  // Metal itself or introduce a second set of Metal Objective-C classes.
  if(!fw && p && (!strncmp(p,"/System/Library/PrivateFrameworks/MTLCompiler.framework/",56) ||
                 !strncmp(p,"/System/Library/PrivateFrameworks/GPUCompiler.framework/",56)))
    fw=getenv("RP_COMPILER_FRAMEWORK_PATH");
  for(int i=0;p&&fw&&i<2;i++) if(!strncmp(p,pre[i],strlen(pre[i]))){ snprintf(q,sizeof q,"%s/%s",fw,p+strlen(pre[i])); if(stat(q,&st)==0){ p=q; break; } }
  if(getenv("RP_COMPILER_LIBRARY_PATH")) p=rp_map(p,driver,sizeof driver);
  void* h=dlopen(p,m); if(getenv("RP_LOG")||!h) fprintf(stderr,"[ip] dlopen(%s,%x) -> %p %s\n",p?p:"NULL",m,h,h?"":dlerror()); return h;}
INTERPOSE(my_dlopen, dlopen)
// path redirect: /System/Library/Extensions/<x>.bundle/Contents/MacOS/<leaf> -> $DYLD_LIBRARY_PATH/<leaf>;
// /System/Library/Extensions/... (other) -> $RP_ROOT/System/Library/Extensions/... when that file exists
static const char* rp_map(const char* p, char* q, size_t n){
  if(!p) return p;
  const char* dl=getenv("DYLD_LIBRARY_PATH"); const char* root=getenv("RP_ROOT"); struct stat st;
  if(!dl) dl=getenv("RP_COMPILER_LIBRARY_PATH");
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
