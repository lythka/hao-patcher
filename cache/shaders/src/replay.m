// replay: in-process MetalOld + profile-selected MTLDriver under Rosetta with a modeled IOAccelerator device.
// The MTLCodeGenService* entry points are exported here; the patched MetalOld copy (monolithic compiler) dlopen(NULL)s them,
// so every request/reply passes through and is dumped to $RP_OUT (unless RP_NODUMP is set).
//
// usage: replay <Metal(shim)> <driver.bundle> <metallib> [function] [compute]
//        replay <Metal(shim)> <driver.bundle> <list>  ""  batch     list: metallib<TAB>function per line (compute kernels;
//                                                                  function constants from RP_FC="idx.type=value;...")
//        replay <Metal(shim)> <driver.bundle> <jobs>  ""  render    jobs: one render pipeline per line, see below
//
// render job line (TAB separated; '#' lines ignored):
//   id <TAB> vlib <TAB> vfn <TAB> flib <TAB> ffn <TAB> props
//   flib/ffn "-" = no fragment function. props = ';'-separated key=value, applied with KVC (unknown keys fail the job):
//     <key>=v           MTLRenderPipelineDescriptor property (e.g. depthAttachmentPixelFormat=252, rasterSampleCount=4,
//                       alphaToCoverageEnabled=1, rasterizationEnabled=0, inputPrimitiveTopology=3)
//     cN.<key>=v        colorAttachments[N] property (pixelFormat, blendingEnabled, sourceRGBBlendFactor, ..., writeMask)
//     vaN.<key>=v       vertexDescriptor.attributes[N] (format, offset, bufferIndex)
//     vlN.<key>=v       vertexDescriptor.layouts[N] (stride, stepFunction, stepRate)
//     vbN.mutability=v / fbN.mutability=v   vertexBuffers/fragmentBuffers[N]
//     vfc.<idx>.<type>=v / ffc.<idx>.<type>=v   function constants for the vertex/fragment function (MTLDataType number;
//                       value as integer or float); the function is then made with newFunctionWithName:constantValues:error:
//     opt=<MTLPipelineOption>   options passed to newRenderPipelineStateWithDescriptor:options:reflection:error:
//     rp.dryvertex=1    do not compile the vertex function (fragment-only key; the pipeline then fails, the fragment
//                       and its linking record are still cached); rp.emptyvd=1 set an empty vertex descriptor;
//     rp.note=...       ignored
//   A vertexDescriptor is set only when a va/vl key is present.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <stdio.h>
#include <CommonCrypto/CommonDigest.h>
#include <unistd.h>
#include "memo.h"
static void* gMC; static int gN; static char gJob[200]="-"; static int gDryVertex;
typedef void* (*create_t)(void); typedef void (*destroy_t)(void*); typedef int (*setpp_t)(void*, const char*, const void*, size_t);
typedef void (*build_t)(void*, unsigned, unsigned, const void*, size_t, void (^)(unsigned, const void*, size_t, const char*));
static void* mc(const char* s){ if(!gMC){ gMC=dlopen(getenv("RP_MTLCOMPILER"),RTLD_NOW|RTLD_GLOBAL); if(!gMC){fprintf(stderr,"dlopen MTLCompiler: %s\n",dlerror()); abort();} } return dlsym(gMC,s);}
static void dump(const char* tag,int n,const void* p,size_t sz){ const char* o=getenv("RP_OUT"); if(!o||!*o||!p||getenv("RP_NODUMP")) return; char fn[1024]; snprintf(fn,sizeof fn,"%s/%s.%03d.%s",o,gJob,n,tag); FILE*f=fopen(fn,"wb"); if(f){fwrite(p,1,sz,f);fclose(f);} }
__attribute__((visibility("default"),used)) void* MTLCodeGenServiceCreate(void){ void*r=((create_t)mc("MTLCodeGenServiceCreate"))(); fprintf(stderr,"[rp] MTLCodeGenServiceCreate -> %p\n",r); return r;}
__attribute__((visibility("default"),used)) void MTLCodeGenServiceDestroy(void*s){ ((destroy_t)mc("MTLCodeGenServiceDestroy"))(s); rpForgetService(s); }
// MetalOld consumes the returned plugin slot as BuildRequest's second argument.
// The old void declaration only worked while its tail call happened to preserve eax.
__attribute__((visibility("default"),used)) int MTLCodeGenServiceSetPluginPath(void*s,const char*p,const void*td,size_t tds){
  NSData* identity=rpServiceIdentity(p,td,tds);
  int slot=((setpp_t)mc("MTLCodeGenServiceSetPluginPath"))(s,p,td,tds);
  fprintf(stderr,"[rp] SetPluginPath %s targetData=%zu slot=%d\n",p,tds,slot);
  dump("targetdata",0,td,tds);if(slot>=0)rpSetIdentity(s,(unsigned)slot,identity);return slot;
}
__attribute__((visibility("default"),used)) void MTLCodeGenServiceBuildRequest(void*s,unsigned type,unsigned llvm,const void*d,size_t sz,void (^cb)(unsigned,const void*,size_t,const char*)){
  int n=++gN; fprintf(stderr,"[rp] BuildRequest #%d type=%u llvm=%u size=%zu\n",n,type,llvm,sz); char tag[64]; snprintf(tag,sizeof tag,"req%u",type); dump(tag,n,d,sz);
  // RP_DRY: keys only. Requests of type 0 (backend compiles) fail at once, so a cache miss costs no compile; cache hits
  // (RP_CACHE_DIR preloaded with harvested entries) and the 31001 downgrade/specialization steps (type != 0 unless RP_DRY=all) still run.
  const char* dry=getenv("RP_DRY"); int isvert=(sz>=32 && ((const uint32_t*)d)[7]==12);
  if(!dry && gDryVertex) dry="vertex";                      // job prop rp.dryvertex=1
  if(dry && !strcmp(dry,"vertex") && !isvert) dry=NULL;   // RP_DRY=vertex: only vertex requests (framework key of 12 bytes) fail
  if(dry && (type==0 || !strcmp(dry,"all"))){ fprintf(stderr,"[rp] dry: request #%d not compiled\n",n); cb(1,NULL,0,"rp dry run"); return; }
  // A complete runtime and per-service identity is required only for reuse.
  // Missing identity still exercises the compiler and reports memo unavailable.
  const char* memo=getenv("RP_MEMO"); char mf[1200]={0};
  NSString* key=memo&&*memo?rpMemoKey(s,type,llvm,d,sz):nil;
  if(key){ snprintf(mf,sizeof mf,"%s/%s",memo,key.UTF8String);
    FILE* f=fopen(mf,"rb"); if(f){ fseek(f,0,SEEK_END); long L=ftell(f); fseek(f,0,SEEK_SET); void* r=malloc(L>0?L:1);
      if(fread(r,1,L,f)==(size_t)L){ fclose(f); fprintf(stderr,"[rp] reply #%d status=0 size=%ld memo\n",n,L); dump("reply",n,r,L); cb(0,r,L,NULL); free(r); return; }
      fclose(f); free(r); } }
  fprintf(stderr,"[rp] compile #%d memo=%s key=%s\n",n,key?"miss":(memo?"unavailable":"disabled"),key?key.UTF8String:"-");
  char* mfp=mf[0]?strdup(mf):NULL;
  ((build_t)mc("MTLCodeGenServiceBuildRequest"))(s,type,llvm,d,sz,^(unsigned st,const void*r,size_t rs,const char*err){ fprintf(stderr,"[rp] reply #%d status=%u size=%zu err=%s\n",n,st,rs,err?err:"-"); dump("reply",n,r,rs);
    if(mfp && st==0 && r){ char tmp[1300]; snprintf(tmp,sizeof tmp,"%s.%d.tmp",mfp,getpid()); FILE* f=fopen(tmp,"wb"); if(f){ fwrite(r,1,rs,f); fclose(f); rename(tmp,mfp);} }
    cb(st,r,rs,err); free(mfp); });
}
#define MSG(T,...) ((T)objc_msgSend)(__VA_ARGS__)
static id gDev;
static NSMutableDictionary* gLibs;
static id lib(NSString* path, NSError** e){
  id L=gLibs[path]; if(L) return L;
  NSData* d=[NSData dataWithContentsOfFile:path]; if(!d){ if(e) *e=[NSError errorWithDomain:@"rp" code:1 userInfo:@{NSLocalizedDescriptionKey:@"cannot read metallib"}]; return nil; }
  dispatch_data_t dd=dispatch_data_create(d.bytes,d.length,dispatch_get_main_queue(),DISPATCH_DATA_DESTRUCTOR_DEFAULT);
  L=MSG(id(*)(id,SEL,id,NSError**),gDev,sel_registerName("newLibraryWithData:error:"),dd,e); if(L) gLibs[path]=L; return L; }
// function with optional constants: cs = dictionary "idx.type" -> value string
static id func(NSString* path, NSString* name, NSDictionary* cs, NSError** e){
  if([path isEqual:@"-"]) return nil;
  id L=lib(path,e); if(!L) return nil;
  if(!cs.count) return MSG(id(*)(id,SEL,id),L,sel_registerName("newFunctionWithName:"),name);
  id CV=[[NSClassFromString(@"MTLFunctionConstantValues") alloc] init];
  for(NSString* k in cs){ NSArray* p=[k componentsSeparatedByString:@"."]; NSUInteger idx=[p[0] integerValue], ty=[p[1] integerValue];
    unsigned char buf[64]={0}; NSArray* vs=[cs[k] componentsSeparatedByString:@","]; size_t o=0;
    for(NSString* v in vs){ // scalar or vector; element size by MTLDataType family
      if(ty>=3&&ty<=6){ float f=[v floatValue]; memcpy(buf+o,&f,4); o+=4; }               // float..float4
      else if(ty>=16&&ty<=19){ uint16_t h; __fp16 x=[v floatValue]; memcpy(&h,&x,2); memcpy(buf+o,&h,2); o+=2; } // half
      else if(ty>=29&&ty<=36){ int32_t x=(int32_t)[v longLongValue]; memcpy(buf+o,&x,4); o+=4; } // int/uint
      else if(ty>=37&&ty<=44){ int16_t x=(int16_t)[v longLongValue]; memcpy(buf+o,&x,2); o+=2; } // short/ushort
      else if(ty>=45&&ty<=52){ int8_t x=(int8_t)[v longLongValue]; memcpy(buf+o,&x,1); o+=1; }   // char/uchar
      else if(ty>=53&&ty<=56){ buf[o++]=[v integerValue]?1:0; }                                // bool
      else if(ty>=81&&ty<=88){ int64_t x=[v longLongValue]; memcpy(buf+o,&x,8); o+=8; }          // long/ulong
      else { int32_t x=(int32_t)[v longLongValue]; memcpy(buf+o,&x,4); o+=4; } }
    MSG(void(*)(id,SEL,const void*,NSUInteger,NSUInteger),CV,sel_registerName("setConstantValue:type:atIndex:"),buf,ty,idx); }
  return MSG(id(*)(id,SEL,id,id,NSError**),L,sel_registerName("newFunctionWithName:constantValues:error:"),name,CV,e); }
static id num(NSString* v){ if([v rangeOfString:@"."].location!=NSNotFound) return @([v doubleValue]); return @([v longLongValue]); }
#include "capture-replay.m"
static int renderJobs(const char* path){
  Class RPD=NSClassFromString(@"MTLRenderPipelineDescriptor"), VD=NSClassFromString(@"MTLVertexDescriptor");
  int ok=0, bad=0;
  for(NSString* line in [[NSString stringWithContentsOfFile:@(path) encoding:NSUTF8StringEncoding error:nil] componentsSeparatedByString:@"\n"]){ @autoreleasepool{
    if(!line.length || [line hasPrefix:@"#"]) continue;
    NSArray* f=[line componentsSeparatedByString:@"\t"]; if(f.count<6){ fprintf(stderr,"[rp] RJOB malformed: %s\n",line.UTF8String); bad++; continue; }
    NSString* jid=f[0]; fprintf(stderr,"[rp] RJOB-START %s\n",jid.UTF8String); fflush(stderr); snprintf(gJob,sizeof gJob,"%s",jid.UTF8String); NSMutableDictionary *vfc=[NSMutableDictionary dictionary], *ffc=[NSMutableDictionary dictionary];
    NSMutableArray* kv=[NSMutableArray array]; NSUInteger opt=0; NSMutableDictionary* rp=[NSMutableDictionary dictionary];
    for(NSString* s in [f[5] componentsSeparatedByString:@";"]){ NSRange r=[s rangeOfString:@"="]; if(r.location==NSNotFound) continue;
      NSString *k=[s substringToIndex:r.location], *v=[s substringFromIndex:r.location+1];
      if([k hasPrefix:@"vfc."]) vfc[[k substringFromIndex:4]]=v; else if([k hasPrefix:@"ffc."]) ffc[[k substringFromIndex:4]]=v;
      else if([k isEqual:@"opt"]) opt=[v integerValue];
      else if([k hasPrefix:@"rp."]) rp[[k substringFromIndex:3]]=v;   // harness controls: dryvertex, emptyvd, note
      else [kv addObject:@[k,v]]; }
    gDryVertex=[rp[@"dryvertex"] integerValue]!=0;
    if(rp[@"capture"]) {
      NSError *error=nil; BOOL passed=NO;
      @try { passed=capturePipeline(rp[@"capture"],&error); }
      @catch(NSException *ex) { error=[NSError errorWithDomain:@"Capture" code:1 userInfo:@{NSLocalizedDescriptionKey:ex.reason?:@"capture replay exception"}]; }
      fprintf(stderr,"[rp] RJOB %s pipeline=%s %s\n",jid.UTF8String,passed?"OK":"FAIL",error?error.localizedDescription.UTF8String:"");
      if(passed) ok++; else bad++;
      continue;
    }
    NSError* e=nil; id V=func(f[1],f[2],vfc,&e);
    if(!V){ fprintf(stderr,"[rp] RJOB %s FAIL vertex %s: %s\n",jid.UTF8String,[f[2] UTF8String],e?e.description.UTF8String:"not found"); bad++; continue; }
    id F=nil; if(![f[3] isEqual:@"-"]){ e=nil; F=func(f[3],f[4],ffc,&e);
      if(!F){ fprintf(stderr,"[rp] RJOB %s FAIL fragment %s: %s\n",jid.UTF8String,[f[4] UTF8String],e?e.description.UTF8String:"not found"); bad++; continue; } }
    id D=[[RPD alloc] init]; id VDesc=nil; BOOL kvok=YES;
    MSG(void(*)(id,SEL,id),D,sel_registerName("setVertexFunction:"),V);
    if(F) MSG(void(*)(id,SEL,id),D,sel_registerName("setFragmentFunction:"),F);
    @try{
      for(NSArray* p in kv){ NSString* k=p[0]; id v=num(p[1]); id target=D; NSString* key=k;
        NSRange dot=[k rangeOfString:@"."];
        if(dot.location!=NSNotFound){ NSString* obj=[k substringToIndex:dot.location]; key=[k substringFromIndex:dot.location+1];
          NSUInteger i=0; NSString* pre=nil; NSScanner* sc=[NSScanner scannerWithString:obj];
          [sc scanCharactersFromSet:[NSCharacterSet letterCharacterSet] intoString:&pre]; NSInteger ii=0; [sc scanInteger:&ii]; i=ii;
          if([pre isEqual:@"c"]) target=[[D valueForKey:@"colorAttachments"] objectAtIndexedSubscript:i];
          else if([pre isEqual:@"va"]||[pre isEqual:@"vl"]){ if(!VDesc){ VDesc=[[VD alloc] init]; }
            target=[[VDesc valueForKey:[pre isEqual:@"va"]?@"attributes":@"layouts"] objectAtIndexedSubscript:i]; }
          else if([pre isEqual:@"vb"]) target=[[D valueForKey:@"vertexBuffers"] objectAtIndexedSubscript:i];
          else if([pre isEqual:@"fb"]) target=[[D valueForKey:@"fragmentBuffers"] objectAtIndexedSubscript:i];
          else { fprintf(stderr,"[rp] RJOB %s unknown prefix %s\n",jid.UTF8String,obj.UTF8String); kvok=NO; break; } }
        [target setValue:v forKey:key]; }
      if(!VDesc && [rp[@"emptyvd"] integerValue]) VDesc=[[VD alloc] init];
      if(VDesc) MSG(void(*)(id,SEL,id),D,sel_registerName("setVertexDescriptor:"),VDesc);
    } @catch(NSException* x){ fprintf(stderr,"[rp] RJOB %s KVC exception %s\n",jid.UTF8String,x.reason.UTF8String); kvok=NO; }
    if(!kvok){ bad++; continue; }
    e=nil; id P=MSG(id(*)(id,SEL,id,NSUInteger,id*,NSError**),gDev,sel_registerName("newRenderPipelineStateWithDescriptor:options:reflection:error:"),D,opt,NULL,&e);
    fprintf(stderr,"[rp] RJOB %s pipeline=%s %s\n",jid.UTF8String,P?"OK":"FAIL",e?e.localizedDescription.UTF8String:"");
    if(P) ok++; else bad++;
  }}
  fprintf(stderr,"[rp] render jobs ok=%d bad=%d\n",ok,bad); return bad; }
// RP_LOGKEYS: log the driver keys MTLIGAccelDevice returns for every render pipeline ("[rp] DKEY V|F <hex>"), before
// MetalOld's in-memory variant cache can hide them (the vertex key carries the fragment's varyings).
typedef void* (*fkey_t)(id,SEL,id,unsigned long long,id,unsigned long long*);
static fkey_t gOrigV, gOrigF;
static void logkey(const char* t, const void* p, unsigned long long n){ if(!p) return; fprintf(stderr,"[rp] DKEY %s %s ",t,gJob); for(unsigned long long i=0;i<n;i++) fprintf(stderr,"%02x",((const unsigned char*)p)[i]); fputc('\n',stderr); }
static void* myV(id s,SEL c,id d,unsigned long long o,id v,unsigned long long* n){ void* r=gOrigV(s,c,d,o,v,n); logkey("V",r,n?*n:0); return r; }
static void* myF(id s,SEL c,id d,unsigned long long o,id v,unsigned long long* n){ void* r=gOrigF(s,c,d,o,v,n); logkey("F",r,n?*n:0); return r; }
static void hookKeys(Class C){
  Method m=class_getInstanceMethod(C,sel_registerName("vertexFunctionKeyWithRenderPipelineDescriptor:options:nextStageVariant:vertexKeySize:"));
  if(m) gOrigV=(fkey_t)method_setImplementation(m,(IMP)myV);
  m=class_getInstanceMethod(C,sel_registerName("fragmentFunctionKeyWithRenderPipelineDescriptor:options:previousStateVariant:fragmentKeySize:"));
  if(m) gOrigF=(fkey_t)method_setImplementation(m,(IMP)myF);
  fprintf(stderr,"[rp] driver key hooks %s %s\n",gOrigV?"V":"-",gOrigF?"F":"-"); }
int main(int argc,char**argv){ @autoreleasepool{
  if(argc<4){ fprintf(stderr,"usage: replay <Metal(MetalOld copy)> <driver.bundle> <metallib|list|jobs> [function] [compute|batch|render]\n"); return 2;}
  void* m=dlopen(argv[1],RTLD_NOW|RTLD_GLOBAL); if(!m){fprintf(stderr,"dlopen Metal: %s\n",dlerror()); return 1;}
  NSBundle* b=[NSBundle bundleWithPath:@(argv[2])]; NSError* e=nil; if(![b loadAndReturnError:&e]){ fprintf(stderr,"bundle: %s\n",e.description.UTF8String); return 1;}
  Class C=NSClassFromString(@(getenv("RP_DEVICE_CLASS")?:"")); fprintf(stderr,"[rp] device class=%s %p runtime=%s\n",getenv("RP_DEVICE_CLASS")?:"unset",C,getenv("RP_RUNTIME_ID")?:"memo-disabled");
  if(getenv("RP_LOGKEYS")) hookKeys(C);
  id dev=MSG(id(*)(id,SEL,unsigned),[C alloc],sel_registerName("initWithAcceleratorPort:"),0x4d54u);
  fprintf(stderr,"[rp] device=%p name=%s\n",dev,dev?[[dev name] UTF8String]:"-"); if(!dev) return 1;
  MSG(void(*)(id,SEL),dev,sel_registerName("initLimits"));
  MSG(void(*)(id,SEL),dev,sel_registerName("initFeatureQueries"));
  fprintf(stderr,"[rp] initLimits+initFeatureQueries done\n");
  gDev=dev; gLibs=[NSMutableDictionary dictionary];
  const char* mode=argc>5?argv[5]:"compute"; int rc=0;
  if(!strcmp(mode,"render")) rc=renderJobs(argv[3]);
  else if(!strcmp(mode,"info")){ // print each function's type and function constants: fn <TAB> type <TAB> idx.type.name.required,...
    e=nil; id L=lib(@(argv[3]),&e); if(!L){ fprintf(stderr,"lib FAIL\n"); return 1; }
    for(NSString* fn in MSG(id(*)(id,SEL),L,sel_registerName("functionNames"))){ @autoreleasepool{
      id F=MSG(id(*)(id,SEL,id),L,sel_registerName("newFunctionWithName:"),fn); NSMutableArray* cs=[NSMutableArray array];
      NSDictionary* d=MSG(id(*)(id,SEL),F,sel_registerName("functionConstantsDictionary"));
      for(NSString* k in d){ id c=d[k]; [cs addObject:[NSString stringWithFormat:@"%lu.%lu.%@.%d",(unsigned long)MSG(NSUInteger(*)(id,SEL),c,sel_registerName("index")),(unsigned long)MSG(NSUInteger(*)(id,SEL),c,sel_registerName("type")),k,(int)MSG(BOOL(*)(id,SEL),c,sel_registerName("required"))]]; }
      NSMutableArray* va=[NSMutableArray array];
      for(id a in MSG(id(*)(id,SEL),F,sel_registerName("vertexAttributes"))) [va addObject:[NSString stringWithFormat:@"%lu.%lu.%@",(unsigned long)MSG(NSUInteger(*)(id,SEL),a,sel_registerName("attributeIndex")),(unsigned long)MSG(NSUInteger(*)(id,SEL),a,sel_registerName("attributeType")),MSG(id(*)(id,SEL),a,sel_registerName("name"))]];
      printf("%s\t%lu\t%s\t%s\n",fn.UTF8String,(unsigned long)MSG(NSUInteger(*)(id,SEL),F,sel_registerName("functionType")),[[cs componentsJoinedByString:@","] UTF8String],[[va componentsJoinedByString:@","] UTF8String]); }}
    fflush(stdout); return 0; }
  else {
    NSMutableArray* jobs=[NSMutableArray array];
    if(!strcmp(mode,"batch")){
      for(NSString* l in [[NSString stringWithContentsOfFile:@(argv[3]) encoding:NSUTF8StringEncoding error:nil] componentsSeparatedByString:@"\n"]){ NSArray* f=[l componentsSeparatedByString:@"\t"]; if(f.count==2) [jobs addObject:f]; }
    } else [jobs addObject:@[@(argv[3]), argc>4?@(argv[4]):@""]];
    int ok=0, bad=0;
    for(NSArray* j in jobs){ @autoreleasepool{
      e=nil; id L=lib(j[0],&e);
      if(!L){ fprintf(stderr,"[rp] JOB %s lib FAIL %s\n",[j[0] UTF8String],e.description.UTF8String); bad++; continue; }
      NSString* fn=[j[1] length]?j[1]:[MSG(id(*)(id,SEL),L,sel_registerName("functionNames")) firstObject];
      // RP_FC="idx.type=value;...": function constants for compute jobs (as vfc./ffc. for render jobs)
      NSMutableDictionary* cs=[NSMutableDictionary dictionary];
      for(NSString* s in [@(getenv("RP_FC")?getenv("RP_FC"):"") componentsSeparatedByString:@";"]){ NSRange r=[s rangeOfString:@"="]; if(r.location!=NSNotFound) cs[[s substringToIndex:r.location]]=[s substringFromIndex:r.location+1]; }
      id F=cs.count?func(j[0],fn,cs,&e):MSG(id(*)(id,SEL,id),L,sel_registerName("newFunctionWithName:"),fn);
      if(!F){ fprintf(stderr,"[rp] JOB %s %s func FAIL\n",[j[0] UTF8String],fn.UTF8String); bad++; continue; }
      e=nil; id P=MSG(id(*)(id,SEL,id,NSError**),dev,sel_registerName("newComputePipelineStateWithFunction:error:"),F,&e);
      fprintf(stderr,"[rp] JOB %s %s pipeline=%s %s\n",[[j[0] lastPathComponent] UTF8String],fn.UTF8String,P?"OK":"FAIL",e?e.localizedDescription.UTF8String:"");
      if(P) ok++; else bad++;
    }}
    fprintf(stderr,"[rp] jobs ok=%d bad=%d\n",ok,bad); rc=bad;
  }
  fprintf(stderr,"[rp] done, requests=%d\n",gN);
  sleep(getenv("RP_SLEEP")?atoi(getenv("RP_SLEEP")):3);
  return rc?3:0;
 } }
