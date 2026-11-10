// replay: in-process MetalOld + HD4000 MTLDriver under Rosetta with a faked IOAccelerator device.
// The MTLCodeGenService* entry points are exported here; the patched MetalOld copy (monolithic compiler) dlopen(NULL)s them,
// so every request/reply passes through and is dumped to $RP_OUT.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <stdio.h>
static void* gMC; static int gN;
typedef void* (*create_t)(void); typedef void (*destroy_t)(void*); typedef unsigned (*setpp_t)(void*, const char*, const void*, size_t);
typedef void (*build_t)(void*, unsigned, unsigned, const void*, size_t, void (^)(unsigned, const void*, size_t, const char*));
static void* mc(const char* s){ if(!gMC){ gMC=dlopen(getenv("RP_MTLCOMPILER"),RTLD_NOW|RTLD_GLOBAL); if(!gMC){fprintf(stderr,"dlopen MTLCompiler: %s\n",dlerror()); abort();} } return dlsym(gMC,s);}
static void dump(const char* tag,int n,const void* p,size_t sz){ const char* o=getenv("RP_OUT"); if(!o||!p) return; char fn[1024]; snprintf(fn,sizeof fn,"%s/%03d.%s",o,n,tag); FILE*f=fopen(fn,"wb"); if(f){fwrite(p,1,sz,f);fclose(f);} }
__attribute__((visibility("default"),used)) void* MTLCodeGenServiceCreate(void){ void*r=((create_t)mc("MTLCodeGenServiceCreate"))(); fprintf(stderr,"[rp] MTLCodeGenServiceCreate -> %p\n",r); return r;}
__attribute__((visibility("default"),used)) void MTLCodeGenServiceDestroy(void*s){ ((destroy_t)mc("MTLCodeGenServiceDestroy"))(s);}
__attribute__((visibility("default"),used)) unsigned MTLCodeGenServiceSetPluginPath(void*s,const char*p,const void*td,size_t tds){ fprintf(stderr,"[rp] SetPluginPath %s targetData=%zu\n",p,tds); dump("targetdata",0,td,tds); return ((setpp_t)mc("MTLCodeGenServiceSetPluginPath"))(s,p,td,tds);}
__attribute__((visibility("default"),used)) void MTLCodeGenServiceBuildRequest(void*s,unsigned type,unsigned llvm,const void*d,size_t sz,void (^cb)(unsigned,const void*,size_t,const char*)){
  int n=++gN; fprintf(stderr,"[rp] BuildRequest #%d type=%u llvm=%u size=%zu\n",n,type,llvm,sz); char tag[64]; snprintf(tag,sizeof tag,"req%u",type); dump(tag,n,d,sz);
  ((build_t)mc("MTLCodeGenServiceBuildRequest"))(s,type,llvm,d,sz,^(unsigned st,const void*r,size_t rs,const char*err){ fprintf(stderr,"[rp] reply #%d status=%u size=%zu err=%s\n",n,st,rs,err?err:"-"); dump("reply",n,r,rs); cb(st,r,rs,err); });
}
int main(int argc,char**argv){ @autoreleasepool{
  if(argc<4){ fprintf(stderr,"usage: replay <Metal(MetalOld copy)> <driver.bundle> <metallib> [function] [mode]\n"); return 2;}
  void* m=dlopen(argv[1],RTLD_NOW|RTLD_GLOBAL); if(!m){fprintf(stderr,"dlopen Metal: %s\n",dlerror()); return 1;}
  NSBundle* b=[NSBundle bundleWithPath:@(argv[2])]; NSError* e=nil; if(![b loadAndReturnError:&e]){ fprintf(stderr,"bundle: %s\n",e.description.UTF8String); return 1;}
  Class C=NSClassFromString(@"MTLIGAccelDevice"); fprintf(stderr,"[rp] MTLIGAccelDevice=%p\n",C);
  id dev=((id(*)(id,SEL,unsigned))objc_msgSend)([C alloc],sel_registerName("initWithAcceleratorPort:"),0x4d54u);
  fprintf(stderr,"[rp] device=%p name=%s\n",dev,dev?[[dev name] UTF8String]:"-"); if(!dev) return 1;
  ((void(*)(id,SEL))objc_msgSend)(dev,sel_registerName("initLimits"));
  ((void(*)(id,SEL))objc_msgSend)(dev,sel_registerName("initFeatureQueries"));
  fprintf(stderr,"[rp] initLimits+initFeatureQueries done\n");
  if(argc>5 && !strcmp(argv[5],"inspect")) {
    NSData *data=[NSData dataWithContentsOfFile:@(argv[3])];
    dispatch_data_t dd=dispatch_data_create(data.bytes,data.length,dispatch_get_main_queue(),DISPATCH_DATA_DESTRUCTOR_DEFAULT);
    id library=((id(*)(id,SEL,id,NSError**))objc_msgSend)(dev,sel_registerName("newLibraryWithData:error:"),dd,&e);
    NSArray *names=library?((id(*)(id,SEL))objc_msgSend)(library,sel_registerName("functionNames")):nil;
    fprintf(stderr,"[rp] LIBRARY loaded=%s functions=%lu error=%s\n",library?"yes":"no",(unsigned long)names.count,e?e.description.UTF8String:"-");
    return library?0:1;
  }
  NSMutableArray* jobs=[NSMutableArray array];
  if(argc>5 && !strcmp(argv[5],"batch")){ // argv[3] = list file: metallib<TAB>function per line
    for(NSString* l in [[NSString stringWithContentsOfFile:@(argv[3]) encoding:NSUTF8StringEncoding error:nil] componentsSeparatedByString:@"\n"]){ NSArray* f=[l componentsSeparatedByString:@"\t"]; if(f.count==2) [jobs addObject:f]; }
  } else if(argc<=5 || !strcmp(argv[5],"compute")) [jobs addObject:@[@(argv[3]), argc>4?@(argv[4]):@""]];
  int ok=0, bad=0;
  for(NSArray* j in jobs){ @autoreleasepool{
    NSData* lib=[NSData dataWithContentsOfFile:j[0]];
    dispatch_data_t dd=dispatch_data_create(lib.bytes,lib.length,dispatch_get_main_queue(),DISPATCH_DATA_DESTRUCTOR_DEFAULT);
    e=nil; id L=((id(*)(id,SEL,id,NSError**))objc_msgSend)(dev,sel_registerName("newLibraryWithData:error:"),dd,&e);
    if(!L){ fprintf(stderr,"[rp] JOB %s lib FAIL %s\n",[j[0] UTF8String],e.description.UTF8String); bad++; continue; }
    NSString* fn=[j[1] length]?j[1]:[((id(*)(id,SEL))objc_msgSend)(L,sel_registerName("functionNames")) firstObject];
    id F=((id(*)(id,SEL,id))objc_msgSend)(L,sel_registerName("newFunctionWithName:"),fn);
    if(!F){ fprintf(stderr,"[rp] JOB %s %s func FAIL\n",[j[0] UTF8String],fn.UTF8String); bad++; continue; }
    NSDictionary *constants=((id(*)(id,SEL))objc_msgSend)(F,sel_registerName("functionConstantsDictionary"));
    if(constants.count) {
      id values=[[NSClassFromString(@"MTLFunctionConstantValues") alloc] init];
      unsigned char zero[256]={0};
      for(id constant in constants.allValues) {
        NSUInteger type=((NSUInteger(*)(id,SEL))objc_msgSend)(constant,sel_registerName("type"));
        NSUInteger index=((NSUInteger(*)(id,SEL))objc_msgSend)(constant,sel_registerName("index"));
        NSString *label=((id(*)(id,SEL))objc_msgSend)(constant,sel_registerName("name"));
        fprintf(stderr,"[rp] CONSTANT %s index=%lu type=%lu value=zero\n",label.UTF8String,(unsigned long)index,(unsigned long)type);
        memset(zero,0,sizeof zero);
        if(type==53 && getenv("RP_BOOL_TRUE")) {
          NSArray *names=[@(getenv("RP_BOOL_TRUE")) componentsSeparatedByString:@","];
          if([names containsObject:label]) { zero[0]=1; fprintf(stderr,"[rp] CONSTANT %s set true\n",label.UTF8String); }
        }
        if(getenv("RP_UINT_CONSTANTS")) {
          NSData *json=[@(getenv("RP_UINT_CONSTANTS")) dataUsingEncoding:NSUTF8StringEncoding];
          id overrides=[NSJSONSerialization JSONObjectWithData:json options:0 error:nil];
          if(![overrides isKindOfClass:[NSDictionary class]]) { fprintf(stderr,"invalid uint constant controls\n"); return 2; }
          id number=overrides[label];
          if(number) {
            if(type!=33 || ![number isKindOfClass:[NSNumber class]] || [number doubleValue]<0 ||
               [number doubleValue]>UINT32_MAX || [number doubleValue]!=(double)[number unsignedIntValue]) {
              fprintf(stderr,"invalid uint constant %s\n",label.UTF8String); return 2;
            }
            uint32_t value=[number unsignedIntValue]; memcpy(zero,&value,sizeof value);
            fprintf(stderr,"[rp] CONSTANT %s set uint %u\n",label.UTF8String,value);
          }
        }
        ((void(*)(id,SEL,const void*,NSUInteger,NSUInteger))objc_msgSend)(values,sel_registerName("setConstantValue:type:atIndex:"),zero,type,index);
      }
      F=((id(*)(id,SEL,id,id,NSError**))objc_msgSend)(L,sel_registerName("newFunctionWithName:constantValues:error:"),fn,values,&e);
      if(!F){fprintf(stderr,"[rp] SPECIALIZATION FAIL %s\n",e.description.UTF8String);bad++;continue;}
    }
    e=nil; id P=((id(*)(id,SEL,id,NSError**))objc_msgSend)(dev,sel_registerName("newComputePipelineStateWithFunction:error:"),F,&e);
    fprintf(stderr,"[rp] JOB %s %s pipeline=%s %s\n",[[j[0] lastPathComponent] UTF8String],fn.UTF8String,P?"OK":"FAIL",e?e.localizedDescription.UTF8String:"");
    if(P) ok++; else bad++;
  }}
  if(argc>6 && !strcmp(argv[5],"render")) { @autoreleasepool {
    NSData *lib=[NSData dataWithContentsOfFile:@(argv[3])];
    dispatch_data_t dd=dispatch_data_create(lib.bytes,lib.length,dispatch_get_main_queue(),DISPATCH_DATA_DESTRUCTOR_DEFAULT);
    e=nil;id L=((id(*)(id,SEL,id,NSError**))objc_msgSend)(dev,sel_registerName("newLibraryWithData:error:"),dd,&e);
    if(!L){fprintf(stderr,"[rp] RENDER library FAIL %s\n",e.description.UTF8String);return 1;}
    id V=((id(*)(id,SEL,id))objc_msgSend)(L,sel_registerName("newFunctionWithName:"),@(argv[4]));
    id F=strcmp(argv[6],"-")?((id(*)(id,SEL,id))objc_msgSend)(L,sel_registerName("newFunctionWithName:"),@(argv[6])):nil;
    if(!V || (strcmp(argv[6],"-") && !F)){fprintf(stderr,"[rp] RENDER function missing V=%p F=%p\n",V,F);return 1;}
    id (^specialize)(id,NSString*)=^id(id fn,NSString *name) {
      if(!fn)return nil;
      NSDictionary *constants=((id(*)(id,SEL))objc_msgSend)(fn,sel_registerName("functionConstantsDictionary"));
      if(!constants.count)return fn;
      id values=[[NSClassFromString(@"MTLFunctionConstantValues") alloc] init];
      unsigned char zero[256]={0};
      for(id constant in constants.allValues) {
        NSUInteger type=((NSUInteger(*)(id,SEL))objc_msgSend)(constant,sel_registerName("type"));
        NSUInteger index=((NSUInteger(*)(id,SEL))objc_msgSend)(constant,sel_registerName("index"));
        NSString *label=((id(*)(id,SEL))objc_msgSend)(constant,sel_registerName("name"));
        fprintf(stderr,"[rp] CONSTANT %s index=%lu type=%lu value=zero\n",label.UTF8String,(unsigned long)index,(unsigned long)type);
        memset(zero,0,sizeof zero);
        if(type==53 && getenv("RP_BOOL_TRUE")) {
          // Test-only: comma-separated bool constant names to specialize as true.
          NSArray *names=[@(getenv("RP_BOOL_TRUE")) componentsSeparatedByString:@","];
          if([names containsObject:label]) { zero[0]=1; fprintf(stderr,"[rp] CONSTANT %s set true\n",label.UTF8String); }
        }
        if([label hasSuffix:@"_shader_state"] && getenv("RP_SHADER_STATE")) {
          unsigned words[4];char trailing;
          if(type!=36 || sscanf(getenv("RP_SHADER_STATE"),"%x,%x,%x,%x%c",words,words+1,words+2,words+3,&trailing)!=4){fprintf(stderr,"invalid shader-state control\n");exit(2);}
          memcpy(zero,words,16);
          fprintf(stderr,"[rp] SHADER_STATE %08x,%08x,%08x,%08x\n",words[0],words[1],words[2],words[3]);
        }
        ((void(*)(id,SEL,const void*,NSUInteger,NSUInteger))objc_msgSend)(values,sel_registerName("setConstantValue:type:atIndex:"),zero,type,index);
      }
      NSError *error=nil;
      id specialized=((id(*)(id,SEL,id,id,NSError**))objc_msgSend)(L,sel_registerName("newFunctionWithName:constantValues:error:"),name,values,&error);
      if(!specialized)fprintf(stderr,"[rp] SPECIALIZATION FAIL %s\n",error.description.UTF8String);
      return specialized;
    };
    V=specialize(V,@(argv[4]));if(F)F=specialize(F,@(argv[6]));
    if(!V || (strcmp(argv[6],"-") && !F))return 1;
    id D=[[NSClassFromString(@"MTLRenderPipelineDescriptor") alloc] init];
    ((void(*)(id,SEL,id))objc_msgSend)(D,sel_registerName("setVertexFunction:"),V);
    if(F)((void(*)(id,SEL,id))objc_msgSend)(D,sel_registerName("setFragmentFunction:"),F);
    else ((void(*)(id,SEL,BOOL))objc_msgSend)(D,sel_registerName("setRasterizationEnabled:"),NO);
    if(getenv("RP_VERTEX_DESCRIPTOR") && !strcmp(getenv("RP_VERTEX_DESCRIPTOR"),"auto")) {
      // Test-only: describe every active stage_in attribute in one interleaved buffer (index 30).
      NSArray *attributes=((id(*)(id,SEL))objc_msgSend)(V,sel_registerName("vertexAttributes"));
      id VD=[[NSClassFromString(@"MTLVertexDescriptor") alloc] init];
      id va=((id(*)(id,SEL))objc_msgSend)(VD,sel_registerName("attributes"));
      unsigned long offset=0; int described=0;
      for(id a in attributes) {
        if(!((BOOL(*)(id,SEL))objc_msgSend)(a,sel_registerName("isActive"))) continue;
        unsigned long type=((unsigned long(*)(id,SEL))objc_msgSend)(a,sel_registerName("attributeType"));
        unsigned long index=((unsigned long(*)(id,SEL))objc_msgSend)(a,sel_registerName("attributeIndex"));
        unsigned long format=0, size=0;
        switch(type) {
          case 3: format=28; size=4; break;  case 4: format=29; size=8; break;
          case 5: format=30; size=12; break; case 6: format=31; size=16; break;
          case 16: format=53; size=2; break; case 17: format=25; size=4; break;
          case 18: format=26; size=6; break; case 19: format=27; size=8; break;
          case 29: format=32; size=4; break; case 30: format=33; size=8; break;
          case 31: format=34; size=12; break; case 32: format=35; size=16; break;
          case 33: format=36; size=4; break; case 34: format=37; size=8; break;
          case 35: format=38; size=12; break; case 36: format=39; size=16; break;
          case 37: format=50; size=2; break; case 38: format=16; size=4; break;
          case 39: format=17; size=6; break; case 40: format=18; size=8; break;
          case 41: format=49; size=2; break; case 42: format=13; size=4; break;
          case 43: format=14; size=6; break; case 44: format=15; size=8; break;
          case 45: format=46; size=1; break; case 46: format=4; size=2; break;
          case 47: format=5; size=3; break;  case 48: format=6; size=4; break;
          case 49: format=45; size=1; break; case 50: format=1; size=2; break;
          case 51: format=2; size=3; break;  case 52: format=3; size=4; break;
          default: fprintf(stderr,"[rp] VERTEX_DESCRIPTOR unsupported attribute type %lu\n",type); exit(2);
        }
        id slot=((id(*)(id,SEL,unsigned long))objc_msgSend)(va,sel_registerName("objectAtIndexedSubscript:"),index);
        ((void(*)(id,SEL,unsigned long))objc_msgSend)(slot,sel_registerName("setFormat:"),format);
        ((void(*)(id,SEL,unsigned long))objc_msgSend)(slot,sel_registerName("setOffset:"),offset);
        ((void(*)(id,SEL,unsigned long))objc_msgSend)(slot,sel_registerName("setBufferIndex:"),30);
        fprintf(stderr,"[rp] VERTEX_ATTRIBUTE index=%lu type=%lu format=%lu offset=%lu\n",index,type,format,offset);
        offset+=(size+3)&~3ul; described++;
      }
      id layouts=((id(*)(id,SEL))objc_msgSend)(VD,sel_registerName("layouts"));
      id layout=((id(*)(id,SEL,unsigned long))objc_msgSend)(layouts,sel_registerName("objectAtIndexedSubscript:"),30);
      ((void(*)(id,SEL,unsigned long))objc_msgSend)(layout,sel_registerName("setStride:"),offset?offset:4);
      if(described) ((void(*)(id,SEL,id))objc_msgSend)(D,sel_registerName("setVertexDescriptor:"),VD);
      fprintf(stderr,"[rp] VERTEX_DESCRIPTOR attributes=%d stride=%lu\n",described,offset);
    }
    id attachments=((id(*)(id,SEL))objc_msgSend)(D,sel_registerName("colorAttachments"));
    id attachment=((id(*)(id,SEL,unsigned long))objc_msgSend)(attachments,sel_registerName("objectAtIndexedSubscript:"),0);
    unsigned long pf=argc>7?strtoul(argv[7],NULL,10):80;
    ((void(*)(id,SEL,unsigned long))objc_msgSend)(attachment,sel_registerName("setPixelFormat:"),pf);
    e=nil;id P=((id(*)(id,SEL,id,NSError**))objc_msgSend)(dev,sel_registerName("newRenderPipelineStateWithDescriptor:error:"),D,&e);
    fprintf(stderr,"[rp] RENDER %s %s pf=%lu pipeline=%s %s\n",argv[4],argv[6],pf,P?"OK":"FAIL",e?e.description.UTF8String:"");
    if(P)ok++;else bad++;
  }}
  fprintf(stderr,"[rp] jobs ok=%d bad=%d\n",ok,bad);
  fprintf(stderr,"[rp] done, requests=%d\n",gN);
  sleep(getenv("RP_SLEEP")?atoi(getenv("RP_SLEEP")):3);
 } return 0; }
