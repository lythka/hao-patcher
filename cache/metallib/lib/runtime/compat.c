// Host-only compatibility prototype for the pinned MTLCompiler 3802 plugin.
// The caller's module is never edited: lower a clone, verify it, call the plugin,
// and dispose the clone on return. Unrecognized states leave the original intact.
#include <dlfcn.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>
#include <mach/mach_vm.h>
#include <stdatomic.h>
#include <ctype.h>
#include <time.h>
#include <mach-o/loader.h>
#include <uuid/uuid.h>
#define COMPAT_PLUGIN_UUID "0B3DEEEA-66E2-3957-97D2-411C76FA5CB1"
#define COMPAT_LLVM_UUID "D5CE0007-CBC1-3B4E-97B0-9E75C1A2EC13"
typedef void *Ref;
#define APIS(X) \
 X(Ref,CreateStringAttribute,(Ref,const char *,unsigned,const char *,unsigned)) \
 X(void,RemoveStringAttributeAtIndex,(Ref,unsigned,const char *,unsigned)) \
 X(void,RemoveCallSiteStringAttribute,(Ref,unsigned,const char *,unsigned)) \
 X(unsigned,GetAttributeCountAtIndex,(Ref,unsigned)) \
 X(void,GetAttributesAtIndex,(Ref,unsigned,Ref *)) \
 X(unsigned,GetCallSiteAttributeCount,(Ref,unsigned)) \
 X(void,GetCallSiteAttributes,(Ref,unsigned,Ref *)) \
 X(Ref,GetStringAttributeAtIndex,(Ref,unsigned,const char *,unsigned)) \
 X(Ref,GetCallSiteStringAttribute,(Ref,unsigned,const char *,unsigned)) \
 X(Ref,GetReturnType,(Ref)) \
 X(int,IsFunctionVarArg,(Ref)) \
 X(void,SetValueName,(Ref,const char *)) \
 X(void,DeleteFunction,(Ref)) \
 X(void,AddCallSiteAttribute,(Ref,unsigned,Ref)) \
 X(unsigned,GetFunctionCallConv,(Ref)) \
 X(void,SetFunctionCallConv,(Ref,unsigned)) \
 X(void,SetInstructionCallConv,(Ref,unsigned)) \
 X(void,SetTailCall,(Ref,int)) \
 X(void,SetLinkage,(Ref,int)) \
 X(Ref,IsASExtInst,(Ref)) \
 X(Ref,IsAZExtInst,(Ref)) \
 X(Ref,IsAExtractElementInst,(Ref)) \
 X(Ref,GetParam,(Ref,unsigned)) \
 X(unsigned,CountParams,(Ref)) \
 X(const char *,GetValueName,(Ref)) \
 X(unsigned,CountIncoming,(Ref)) \
 X(Ref,GetIncomingValue,(Ref,unsigned)) \
 X(Ref,GetIncomingBlock,(Ref,unsigned)) \
 X(Ref,GetBasicBlockTerminator,(Ref)) \
 X(unsigned,GetNumSuccessors,(Ref)) \
 X(Ref,GetSuccessor,(Ref,unsigned)) \
 X(void,GetNamedMetadataOperands,(Ref,const char *,Ref *)) \
 X(unsigned,GetMDNodeNumOperands,(Ref)) \
 X(void,GetMDNodeOperands,(Ref,Ref *)) \
 X(const char *,GetMDString,(Ref,unsigned *)) \
 X(int,LinkModules2,(Ref,Ref)) \
 X(void,SetDataLayout,(Ref,const char *)) \
 X(const char *,GetTarget,(Ref)) \
 X(void,SetTarget,(Ref,const char *)) \
 X(unsigned,GetNamedMetadataNumOperands,(Ref,const char *)) \
 X(Ref,GetNamedFunction,(Ref,const char *)) \
 X(Ref,GetTypeByName,(Ref,const char *)) \
 X(Ref,FunctionType,(Ref,Ref *,unsigned,int)) \
 X(Ref,VoidTypeInContext,(Ref)) \
 X(Ref,FloatTypeInContext,(Ref)) \
 X(Ref,Int8TypeInContext,(Ref)) \
 X(Ref,AddFunction,(Ref,const char *,Ref)) \
 X(Ref,BuildCall,(Ref,Ref,Ref *,unsigned,const char *)) \
 X(Ref,BuildSIToFP,(Ref,Ref,Ref,const char *)) \
 X(Ref,BuildUIToFP,(Ref,Ref,Ref,const char *)) \
 X(Ref,BuildFAdd,(Ref,Ref,Ref,const char *)) \
 X(Ref,BuildFDiv,(Ref,Ref,Ref,const char *)) \
 X(Ref,BuildSelect,(Ref,Ref,Ref,Ref,const char *)) \
 X(Ref,ConstReal,(Ref,double)) \
 X(Ref,BuildGEP,(Ref,Ref,Ref *,unsigned,const char *)) \
 X(Ref,BuildInBoundsGEP,(Ref,Ref,Ref *,unsigned,const char *)) \
 X(int,IsInBounds,(Ref)) \
 X(Ref,ConstExtractElement,(Ref,Ref)) \
 X(Ref,ConstVector,(Ref *,unsigned)) \
 X(Ref,IsAArgument,(Ref)) \
 X(Ref,IsAConstantInt,(Ref)) \
 X(Ref,IsAConstant,(Ref)) \
 X(long long,ConstIntGetSExtValue,(Ref)) \
 X(unsigned,GetInstructionCallConv,(Ref)) \
 X(int,IsTailCall,(Ref)) \
 X(int,IsDeclaration,(Ref)) \
 X(Ref,GetFirstUse,(Ref)) \
 X(Ref,GetNextUse,(Ref)) \
 X(Ref,GetUser,(Ref)) \
 X(int,GetLinkage,(Ref)) \
 X(int,GetConstOpcode,(Ref)) \
 X(Ref,GetInstructionParent,(Ref)) \
 X(Ref,GetBasicBlockParent,(Ref)) \
 X(Ref,GetEnumAttributeAtIndex,(Ref,unsigned,unsigned)) \
 X(Ref,GetCallSiteEnumAttribute,(Ref,unsigned,unsigned)) \
 X(Ref,IsAConstantExpr,(Ref)) \
 X(Ref,IsAConstantArray,(Ref)) \
 X(Ref,IsAConstantStruct,(Ref)) \
 X(Ref,IsAGlobalAlias,(Ref)) \
 X(Ref,IsAGlobalVariable,(Ref)) \
 X(Ref,IsAAllocaInst,(Ref)) \
 X(Ref,IsALoadInst,(Ref)) \
 X(Ref,IsAStoreInst,(Ref)) \
 X(Ref,IsAGetElementPtrInst,(Ref)) \
 X(Ref,IsABitCastInst,(Ref)) \
 X(Ref,IsAAddrSpaceCastInst,(Ref)) \
 X(Ref,IsAPHINode,(Ref)) \
 X(Ref,IsASelectInst,(Ref)) \
 X(Ref,GetCalledValue,(Ref)) \
 X(Ref,IsAFunction,(Ref)) \
 X(Ref,CreatePassManager,(void)) \
 X(void,AddInstructionCombiningPass,(Ref)) \
 X(void,AddAlwaysInlinerPass,(Ref)) \
 X(int,RunPassManager,(Ref,Ref)) \
 X(void,DisposePassManager,(Ref)) \
 X(Ref,CreateEnumAttribute,(Ref,unsigned,unsigned long long)) \
 X(void,AddAttributeAtIndex,(Ref,unsigned,Ref)) \
 X(Ref,PointerType,(Ref,unsigned)) \
 X(int,IsUndef,(Ref)) \
 X(int,IsNull,(Ref)) \
 X(unsigned,GetPointerAddressSpace,(Ref)) \
 X(Ref,GetEntryBasicBlock,(Ref)) \
 X(Ref,BuildAlloca,(Ref,Ref,const char *)) \
 X(Ref,BuildStore,(Ref,Ref,Ref)) \
 X(Ref,BuildLoad,(Ref,Ref,const char *)) \
 X(void,SetVolatile,(Ref,int)) \
 X(Ref,Int64TypeInContext,(Ref)) \
 X(const char *,GetDataLayoutStr,(Ref)) \
 X(Ref,CreateTargetData,(const char *)) \
 X(void,DisposeTargetData,(Ref)) \
 X(unsigned,PointerSizeForAS,(Ref,unsigned)) \
 X(unsigned,GetEnumAttributeKindForName,(const char *,size_t)) \
 X(void,RemoveEnumAttributeAtIndex,(Ref,unsigned,unsigned)) \
 X(void,RemoveCallSiteEnumAttribute,(Ref,unsigned,unsigned)) \
 X(Ref,IsACallInst,(Ref)) \
 X(Ref,IsAInvokeInst,(Ref)) \
 X(Ref,ContextCreate,(void)) \
 X(void,ContextDispose,(Ref)) \
 X(int,ParseIRInContext,(Ref,Ref,Ref *,char **)) \
 X(Ref,CreateMemoryBufferWithMemoryRangeCopy,(const char *,size_t,const char *)) \
 X(void,DisposeModule,(Ref)) \
 X(void,DisposeMessage,(char *)) \
 X(Ref,CloneModule,(Ref)) \
 X(Ref,GetModuleContext,(Ref)) \
 X(Ref,GetFirstFunction,(Ref)) \
 X(Ref,GetNextFunction,(Ref)) \
 X(Ref,GetFirstBasicBlock,(Ref)) \
 X(Ref,GetNextBasicBlock,(Ref)) \
 X(Ref,GetFirstInstruction,(Ref)) \
 X(Ref,GetNextInstruction,(Ref)) \
 X(int,GetInstructionOpcode,(Ref)) \
 X(int,GetICmpPredicate,(Ref)) \
 X(Ref,TypeOf,(Ref)) \
 X(int,GetTypeKind,(Ref)) \
 X(unsigned,GetVectorSize,(Ref)) \
 X(Ref,GetElementType,(Ref)) \
 X(unsigned,GetIntTypeWidth,(Ref)) \
 X(Ref,GetOperand,(Ref,unsigned)) \
 X(unsigned,GetNumOperands,(Ref)) \
 X(void,SetOperand,(Ref,unsigned,Ref)) \
 X(Ref,InstructionClone,(Ref)) \
 X(void,InsertIntoBuilder,(Ref,Ref)) \
 X(Ref,CreateBuilderInContext,(Ref)) \
 X(void,PositionBuilderBefore,(Ref,Ref)) \
 X(void,DisposeBuilder,(Ref)) \
 X(Ref,BuildExtractElement,(Ref,Ref,Ref,const char *)) \
 X(Ref,BuildInsertElement,(Ref,Ref,Ref,Ref,const char *)) \
 X(Ref,BuildBitCast,(Ref,Ref,Ref,const char *)) \
 X(Ref,BuildBinOp,(Ref,int,Ref,Ref,const char *)) \
 X(Ref,BuildICmp,(Ref,int,Ref,Ref,const char *)) \
 X(void,ReplaceAllUsesWith,(Ref,Ref)) \
 X(void,InstructionEraseFromParent,(Ref)) \
 X(Ref,Int16TypeInContext,(Ref)) \
 X(Ref,Int32TypeInContext,(Ref)) \
 X(Ref,Int1TypeInContext,(Ref)) \
 X(Ref,HalfTypeInContext,(Ref)) \
 X(Ref,ConstInt,(Ref,unsigned long long,int)) \
 X(Ref,GetUndef,(Ref)) \
 X(Ref,VectorType,(Ref,unsigned)) \
 X(int,VerifyModule,(Ref,int,char **)) \
 X(char *,PrintValueToString,(Ref)) \
 X(int,PrintModuleToFile,(Ref,const char *,char **)) \
 X(Ref,IsAInstruction,(Ref)) \
 X(int,HasMetadata,(Ref)) \
 X(unsigned,GetMDKindIDInContext,(Ref,const char *,unsigned)) \
 X(Ref,GetMetadata,(Ref,unsigned)) \
 X(void,SetMetadata,(Ref,unsigned,Ref))
#define DECL(ret,name,args) static ret (*L##name)args;
APIS(DECL)
#undef DECL
static uint64_t now_ns(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000000000ULL+(uint64_t)t.tv_nsec;}
static int bitcast_op,icmp_op,gep_op,bin_ops[10],initialized,enabled;
static int half_kind,int_kind,vector_kind,pointer_kind,eq_pred,ne_pred,internal_linkage,private_linkage;
static const char *bin_names[]={"add","sub","mul","and","or","xor","udiv","sdiv","urem","srem"};
static pthread_mutex_t init_lock=PTHREAD_MUTEX_INITIALIZER;
static _Atomic unsigned sequence;
void *compat_real_entry;
extern void compat_tramp(void);
void *compat_dlsym(void *,const char *);
typedef struct {const void *replacement,*replacee;} Interpose;
__attribute__((used,section("__DATA,__interpose")))
static Interpose interpose_record={(const void *)compat_dlsym,(const void *)dlsym};
static void *lookup(void *handle,const char *name){return ((void *(*)(void *,const char *))interpose_record.replacee)(handle,name);}
#include "image_identity.h"
#include "sampler_abi.h"
// Each intercepted compile has its own scope, including nested calls. Beyond
// this bounded stack the optional block pass is refused; older rewrites remain.
static int block_query_ready;
static __thread unsigned block_query_depth;
static __thread unsigned char block_query_scopes[64];
#ifdef COMPAT_SAMPLER_QUERY
static int block_query_active(void){
 return block_query_depth && block_query_depth<=64 && block_query_scopes[block_query_depth-1];
}
static int query_install(void *handle){
 (void)handle;
 const char *mode=getenv("COMPAT_BLOCK_READS");
 if(!mode || strcmp(mode,"per-axis-mip0") || !sampler_abi_opted_in())return 0;
 void *igil=dlopen("libIGIL-Metal.dylib",RTLD_NOW|RTLD_LOCAL);Dl_info info,own;
 if(!igil)return 0;
 void *f=lookup(igil,"_ZNK4llvm18IGILTargetLowering26LowerIsNormalizedIntrinsicEjNS_7SDValueERNS_12SelectionDAGE");
 if(!f || !supported_image(f,"D5CE0008-8103-3F3D-BB7C-32D0F8F049BD") || !dladdr(f,&info) || !dladdr((void*)query_install,&own))return 0;
 char path[4096];snprintf(path,sizeof path,"%s",own.dli_fname);
 char *slash=strrchr(path,'/');if(!slash)return 0;
 snprintf(slash+1,sizeof path-(size_t)(slash+1-path),"sampler-query.dylib");
 void *extension=dlopen(path,RTLD_NOW|RTLD_LOCAL);
 if(!extension){fprintf(stderr,"[compat] sampler extension: %s\n",dlerror());return 0;}
 int (*install)(void*,void*,int(*)(void))=lookup(extension,"sampler_query_install");
 return install && install(igil,info.dli_fbase,block_query_active);
}
#endif
static int initialize(void *handle){
#define LOAD(ret,name,args) L##name=lookup(handle,"LLVM" #name); if(!L##name){fprintf(stderr,"[compat] missing LLVM%s\n",#name);return 0;}
 APIS(LOAD)
#undef LOAD
 if(!supported_image((void *)LContextCreate,COMPAT_LLVM_UUID)){fprintf(stderr,"[compat] refused untested LLVM image\n");return 0;}
 const char *ir="define internal void @p(<4 x half> %h, <4 x i16> %v) {\n"
 "%b = bitcast <4 x half> %h to <4 x i16>\n"
 "%a = add <4 x i16> %v, %v\n";
 char text[2048];
 int len=snprintf(text,sizeof text,"%s",ir);
 for(int i=1;i<10;i++)len+=snprintf(text+len,sizeof text-(size_t)len,"%%x%d = %s <4 x i16> %%v, %%v\n",i,bin_names[i]);
 snprintf(text+len,sizeof text-(size_t)len,"%%c = icmp eq <4 x i16> %%v, %%v\n%%d = icmp ne <4 x i16> %%v, %%b\n%%g = getelementptr <4 x i16>, <4 x i16>* null, i64 1\nret void\n}\ndefine private void @q() { ret void }\n");
 Ref ctx=LContextCreate(),module=0;char *error=0;
 Ref buffer=LCreateMemoryBufferWithMemoryRangeCopy(text,strlen(text),"compat-opcodes");
 if(LParseIRInContext(ctx,buffer,&module,&error)){
  fprintf(stderr,"[compat] opcode probe: %s\n",error?error:"parse failure");
  if(error)LDisposeMessage(error);LContextDispose(ctx);return 0;
 }
 Ref in=LGetFirstInstruction(LGetFirstBasicBlock(LGetFirstFunction(module)));
 bitcast_op=LGetInstructionOpcode(in);in=LGetNextInstruction(in);
 for(int i=0;i<10;i++){bin_ops[i]=LGetInstructionOpcode(in);in=LGetNextInstruction(in);}
 icmp_op=LGetInstructionOpcode(in);eq_pred=LGetICmpPredicate(in);
 ne_pred=LGetICmpPredicate(LGetNextInstruction(in));
 gep_op=LGetInstructionOpcode(LGetNextInstruction(LGetNextInstruction(in)));
 internal_linkage=LGetLinkage(LGetFirstFunction(module));
 private_linkage=LGetLinkage(LGetNextFunction(LGetFirstFunction(module)));
 pointer_kind=LGetTypeKind(LPointerType(LInt16TypeInContext(ctx),1));
 half_kind=LGetTypeKind(LHalfTypeInContext(ctx));int_kind=LGetTypeKind(LInt16TypeInContext(ctx));
 vector_kind=LGetTypeKind(LVectorType(LInt16TypeInContext(ctx),4));
 int valid=half_kind!=int_kind && int_kind!=vector_kind && half_kind!=vector_kind;
 fprintf(stderr,"[compat] type kinds half=%d integer=%d vector=%d\n",half_kind,int_kind,vector_kind);
 LDisposeModule(module);LContextDispose(ctx);
 return valid;
}
void *compat_dlsym(void *handle,const char *name){
 void *real=lookup(handle,name);
 if(!real || strcmp(name,"MTLCompilerBuildRequestWithOptions"))return real;
 if(!supported_image(real,COMPAT_PLUGIN_UUID)){fprintf(stderr,"[compat] refused untested compiler entry ABI\n");return real;}
 pthread_mutex_lock(&init_lock);
 if(!initialized){
  uint64_t start=now_ns();initialized=1;compat_real_entry=real;enabled=initialize(handle) && !getenv("COMPAT_DISABLE");
#ifdef COMPAT_SAMPLER_QUERY
  if(enabled)block_query_ready=query_install(handle);
#endif
  fprintf(stderr,"[compat] initialize_ns=%llu\n",(unsigned long long)(now_ns()-start));
  fprintf(stderr,"[compat] initialized enabled=%d entry=%p\n",enabled,real);
 }
 int intercept=enabled && compat_real_entry==real;
 pthread_mutex_unlock(&init_lock);
 return intercept?(void *)compat_tramp:real;
}
static unsigned vector_width(Ref type,int kind){
 if(LGetTypeKind(type)!=vector_kind)return 0;
 Ref elem=LGetElementType(type);
 if(LGetTypeKind(elem)!=kind || (kind==int_kind && LGetIntTypeWidth(elem)!=16))return 0;
 return LGetVectorSize(type);
}
static int root(Ref value){
 if(LGetInstructionOpcode(value)!=bitcast_op)return 0;
 unsigned n=vector_width(LTypeOf(value),int_kind);
 return n>=3 && vector_width(LTypeOf(LGetOperand(value,0)),half_kind)==n;
}
static int exit_cast(Ref value){
 if(LGetInstructionOpcode(value)!=bitcast_op)return 0;
 unsigned n=vector_width(LTypeOf(value),half_kind);
 return n>=3 && vector_width(LTypeOf(LGetOperand(value,0)),int_kind)==n;
}
static int binary_index(int op){for(int i=0;i<10;i++)if(op==bin_ops[i])return i;return -1;}
static int candidate(Ref value){
 int op=LGetInstructionOpcode(value);
 return (op==icmp_op || binary_index(op)>=0) && vector_width(LTypeOf(LGetOperand(value,0)),int_kind)>=3;
}
// Skip quoted LLVM identifiers when locating instruction syntax or metadata.
static const char *unquoted(const char *s,const char *needle){
 int quote=0;size_t n=strlen(needle);
 for(;*s;s++){
  if(*s=='\\' && quote && s[1]){s++;continue;}
  if(*s=='"'){quote=!quote;continue;}
  if(!quote && !strncmp(s,needle,n))return s;
 }
 return 0;
}
static int copy_metadata(Ref from,Ref to,Ref ctx){
 if(!LHasMetadata(from))return 1;
 if(!LIsAInstruction(to))return 0;
 char *text=LPrintValueToString(from);if(!text)return 0;
 const char *p=text;int ok=1;
 while((p=unquoted(p,", !"))){
  p+=3;const char *start=p;
  while(isalnum((unsigned char)*p)||*p=='_'||*p=='.'||*p=='-')p++;
  if(p==start || *p!=' '){ok=0;break;}
  unsigned id=LGetMDKindIDInContext(ctx,start,(unsigned)(p-start));
  Ref md=LGetMetadata(from,id);if(!md){ok=0;break;}
  LSetMetadata(to,id,md);
 }
 LDisposeMessage(text);return ok;
}
// Preserve overflow/exact flags through a parsed scalar instruction template.
// This avoids depending on absent old-LLVM flag setter APIs or C++ internals.
static Ref flagged_binary(Ref original,Ref builder,Ref ctx,Ref a,Ref b,int index){
 char *text=LPrintValueToString(original);if(!text)return 0;
 const char *eq=unquoted(text," = ");const char *type=eq?strchr(eq+3,'<'):0;
 if(!eq || !type || (size_t)(type-(eq+3))>80){LDisposeMessage(text);return 0;}
 char prefix[96];size_t n=(size_t)(type-(eq+3));memcpy(prefix,eq+3,n);prefix[n]=0;
 size_t opn=strlen(bin_names[index]);
 if(strncmp(prefix,bin_names[index],opn) || prefix[opn]!=' '){LDisposeMessage(text);return 0;}
 const char *flags=prefix+opn;
 while(*flags==' ')flags++;
 if(!*flags){LDisposeMessage(text);return LBuildBinOp(builder,bin_ops[index],a,b,"");}
 char ir[256];snprintf(ir,sizeof ir,"define i16 @compat_scalar(i16 %%a, i16 %%b) { %%v = %si16 %%a, %%b\nret i16 %%v\n}\n",prefix);
 LDisposeMessage(text);
 Ref module=0;char *error=0;
 Ref buffer=LCreateMemoryBufferWithMemoryRangeCopy(ir,strlen(ir),"compat-flags");
 if(LParseIRInContext(ctx,buffer,&module,&error)){if(error)LDisposeMessage(error);return 0;}
 Ref inst=LInstructionClone(LGetFirstInstruction(LGetFirstBasicBlock(LGetFirstFunction(module))));
 LSetOperand(inst,0,a);LSetOperand(inst,1,b);LInsertIntoBuilder(builder,inst);
 LDisposeModule(module);return inst;
}
typedef struct {Ref key,*lanes;unsigned n;} Lanes;
typedef struct {Ref *all;unsigned char *roots,*island;Lanes *maps;size_t count,nmap;Ref builder,ctx,i16,i32,i1,half;} Work;
static Lanes *find_lanes(Work *w,Ref key){for(size_t i=0;i<w->nmap;i++)if(w->maps[i].key==key)return &w->maps[i];return 0;}
static Lanes *new_lanes(Work *w,Ref key,unsigned n){
 if(w->nmap>=w->count)return 0;
 Ref *lanes=calloc(n,sizeof *lanes);if(!lanes)return 0;
 Lanes *r=&w->maps[w->nmap++];*r=(Lanes){key,lanes,n};return r;
}
static Ref lane(Work *w,Ref value,unsigned k,unsigned n){
 Lanes *m=find_lanes(w,value);
 if(m)return m->n==n?m->lanes[k]:0;
 return LBuildExtractElement(w->builder,value,LConstInt(w->i32,k,0),"");
}
static void free_work(Work *w){
 for(size_t i=0;i<w->nmap;i++)free(w->maps[i].lanes);
 free(w->maps);free(w->all);free(w->roots);free(w->island);
}
static int rewrite_function(Ref function,Ref ctx,Ref builder){
 Work w={0};w.ctx=ctx;w.builder=builder;
 for(Ref bb=LGetFirstBasicBlock(function);bb;bb=LGetNextBasicBlock(bb))
  for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in))w.count++;
 if(!w.count)return 0;
 w.all=calloc(w.count,sizeof *w.all);w.roots=calloc(w.count,1);w.island=calloc(w.count,1);w.maps=calloc(w.count,sizeof *w.maps);
 if(!w.all || !w.roots || !w.island || !w.maps){free_work(&w);return -1;}
 size_t pos=0,roots=0;
 for(Ref bb=LGetFirstBasicBlock(function);bb;bb=LGetNextBasicBlock(bb))
  for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in)){
   w.all[pos]=in;w.roots[pos]=(unsigned char)root(in);roots+=w.roots[pos++];
  }
 if(!roots){free_work(&w);return 0;}
 int changed=1,ops=0;
 while(changed){
  changed=0;
  for(size_t i=0;i<w.count;i++)if(!w.island[i] && candidate(w.all[i])){
   for(unsigned a=0;a<2 && !w.island[i];a++){
    Ref operand=LGetOperand(w.all[i],a);
    for(size_t j=0;j<w.count;j++)if((w.roots[j]||w.island[j]) && w.all[j]==operand){w.island[i]=1;changed=1;ops++;break;}
   }
  }
 }
 if(!ops){free_work(&w);return 0;}
 w.i16=LInt16TypeInContext(ctx);w.i32=LInt32TypeInContext(ctx);w.i1=LInt1TypeInContext(ctx);w.half=LHalfTypeInContext(ctx);
 Ref *replacements=calloc(w.count,sizeof *replacements);if(!replacements){free_work(&w);return -1;}
 int ok=1;
 for(size_t i=0;i<w.count && ok;i++)if(w.roots[i]){
  int used=0;
  for(size_t j=0;j<w.count;j++)if(w.island[j] && (LGetOperand(w.all[j],0)==w.all[i] || LGetOperand(w.all[j],1)==w.all[i])){used=1;break;}
  if(!used)continue;
  Ref original=w.all[i];unsigned n=vector_width(LTypeOf(original),int_kind);
  LPositionBuilderBefore(builder,original);Lanes *m=new_lanes(&w,original,n);if(!m){ok=0;break;}
  for(unsigned k=0;k<n && ok;k++){
   Ref h=LBuildExtractElement(builder,LGetOperand(original,0),LConstInt(w.i32,k,0),"");
   m->lanes[k]=LBuildBitCast(builder,h,w.i16,"");
   ok=copy_metadata(original,m->lanes[k],ctx);
  }
 }
 for(size_t i=0;i<w.count && ok;i++)if(w.island[i]){
  Ref in=w.all[i];int op=LGetInstructionOpcode(in),cmp=op==icmp_op;
  unsigned n=vector_width(LTypeOf(LGetOperand(in,0)),int_kind);
  LPositionBuilderBefore(builder,in);Lanes *m=new_lanes(&w,in,n);if(!m){ok=0;break;}
  Ref acc=LGetUndef(LTypeOf(in));
  for(unsigned k=0;k<n && ok;k++){
   Ref a=lane(&w,LGetOperand(in,0),k,n),b=lane(&w,LGetOperand(in,1),k,n);
   if(!a || !b){ok=0;break;}
   Ref r=cmp?LBuildICmp(builder,LGetICmpPredicate(in),a,b,""):flagged_binary(in,builder,ctx,a,b,binary_index(op));
   if(!r){ok=0;break;}
   ok=copy_metadata(in,r,ctx);m->lanes[k]=r;
   acc=LBuildInsertElement(builder,acc,r,LConstInt(w.i32,k,0),"");
  }
  replacements[i]=acc;
 }
 for(size_t i=0;i<w.count && ok;i++)if(exit_cast(w.all[i])){
  Ref in=w.all[i];Lanes *m=find_lanes(&w,LGetOperand(in,0));if(!m)continue;
  LPositionBuilderBefore(builder,in);Ref acc=LGetUndef(LTypeOf(in));
  for(unsigned k=0;k<m->n;k++){
   Ref h=LBuildBitCast(builder,m->lanes[k],w.half,"");
   acc=LBuildInsertElement(builder,acc,h,LConstInt(w.i32,k,0),"");
  }
  ok=copy_metadata(in,acc,ctx);replacements[i]=acc;
 }
 if(ok){
  for(size_t i=0;i<w.count;i++)if(replacements[i])LReplaceAllUsesWith(w.all[i],replacements[i]);
  for(size_t i=w.count;i>0;i--)if(replacements[i-1])LInstructionEraseFromParent(w.all[i-1]);
 }
 free(replacements);free_work(&w);return ok?ops:-1;
}
static int remaining_bad_pattern(Ref module){
 for(Ref f=LGetFirstFunction(module);f;f=LGetNextFunction(f))
  for(Ref bb=LGetFirstBasicBlock(f);bb;bb=LGetNextBasicBlock(bb))
   for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in))if(candidate(in))
    for(unsigned k=0;k<2;k++){
     Ref operand=LGetOperand(in,k);
     if(LIsAInstruction(operand) && root(operand))return 1;
    }
 return 0;
}

static int null_compare(Ref in){
 if(LGetInstructionOpcode(in)!=icmp_op)return 0;
 int pred=LGetICmpPredicate(in);if(pred!=eq_pred && pred!=ne_pred)return 0;
 Ref a=LGetOperand(in,0),b=LGetOperand(in,1),t=LTypeOf(a);
 return LGetTypeKind(t)==pointer_kind && LGetPointerAddressSpace(t)==1 && (LIsNull(a)||LIsNull(b));
}
static int null_remaining(Ref module){
 for(Ref f=LGetFirstFunction(module);f;f=LGetNextFunction(f))
  for(Ref bb=LGetFirstBasicBlock(f);bb;bb=LGetNextBasicBlock(bb))
   for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in))if(null_compare(in))return 1;
 return 0;
}


typedef struct {Ref *items;size_t count,capacity;} RefSet;
static int contains(const RefSet *set,Ref value){
 for(size_t i=0;i<set->count;i++)if(set->items[i]==value)return 1;
 return 0;
}
static int include(RefSet *set,Ref value){
 if(contains(set,value))return 1;
 if(set->count==set->capacity){
  size_t n=set->capacity?set->capacity*2:16;
  if(n<set->capacity || n>SIZE_MAX/sizeof(Ref))return 0;
  Ref *items=realloc(set->items,n*sizeof *items);if(!items)return 0;
  set->items=items;set->capacity=n;
 }
 set->items[set->count++]=value;return 1;
}
static int local_linkage(Ref value){int l=LGetLinkage(value);return l==internal_linkage || l==private_linkage;}
static int has_null(Ref function){
 for(Ref bb=LGetFirstBasicBlock(function);bb;bb=LGetNextBasicBlock(bb))
  for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in))if(null_compare(in))return 1;
 return 0;
}
static int trace_effect_uses(Ref function,RefSet *functions,RefSet *calls);
// Mark the complete proven caller closure, including local callback slots,
// before running the inliner. No shader or function name selects a fix.
static int inline_null_helpers(Ref module,Ref ctx,int *changed){
 RefSet closure={0};int ok=1;
 for(Ref f=LGetFirstFunction(module);f && ok;f=LGetNextFunction(f))if(has_null(f))ok=include(&closure,f);
 RefSet calls={0};
 for(size_t i=0;i<closure.count && ok;i++)ok=trace_effect_uses(closure.items[i],&closure,&calls);
 free(calls.items);
 if(!ok){free(closure.items);fprintf(stderr,"[compat] null helper graph escapes or is unsupported\n");return 0;}
 unsigned noinline=LGetEnumAttributeKindForName("noinline",8),always=LGetEnumAttributeKindForName("alwaysinline",12);
 unsigned optnone=LGetEnumAttributeKindForName("optnone",7);
 int marked=0;
 if(ok)for(size_t i=0;i<closure.count;i++){
  Ref f=closure.items[i];if(!local_linkage(f))continue;
  // Keep the optnone/noinline contract. Its null compares can still be lowered.
  if(optnone && LGetEnumAttributeAtIndex(f,~0U,optnone))continue;
  LRemoveEnumAttributeAtIndex(f,~0U,noinline);
  LAddAttributeAtIndex(f,~0U,LCreateEnumAttribute(ctx,always,0));marked++;
 }
 if(ok && marked){
  Ref pm=LCreatePassManager();LAddAlwaysInlinerPass(pm);LAddInstructionCombiningPass(pm);
  while(LRunPassManager(pm,module))*changed=1;
  LDisposePassManager(pm);
 }
 // Marking itself is a mutation even if a recursive helper cannot be inlined.
 if(marked)*changed=1;
 fprintf(stderr,"[compat] inline_null_helpers=%d\n",marked);
 free(closure.items);return ok;
}
// Follow concrete uses, including private callback slots, to every affected
// direct or indirect callsite. A function pointer escaping this module refuses
// the transaction: its external caller's memory contract cannot be updated here.
static int private_slot(Ref value){
 // Only concrete local storage can keep a callback within the current module.
 while(LIsABitCastInst(value)||LIsAGetElementPtrInst(value)||LIsAConstantExpr(value)){
  if(LIsAConstantExpr(value) && LGetConstOpcode(value)!=bitcast_op && LGetConstOpcode(value)!=gep_op)return 0;
  value=LGetOperand(value,0);
 }
 return LIsAAllocaInst(value) || (LIsAGlobalVariable(value) && local_linkage(value));
}
static int trace_effect_uses(Ref function,RefSet *functions,RefSet *calls){
 RefSet work={0};int ok=include(&work,function);
 for(size_t i=0;i<work.count && ok;i++){
  Ref value=work.items[i];
  for(Ref use=LGetFirstUse(value);use && ok;use=LGetNextUse(use)){
   Ref user=LGetUser(use);
   if(LIsACallInst(user)||LIsAInvokeInst(user)){
    if(LGetCalledValue(user)!=value){ok=0;break;}
    unsigned uses=0;for(unsigned k=0;k<LGetNumOperands(user);k++)uses+=LGetOperand(user,k)==value;
    if(uses!=1){ok=0;break;}
    Ref owner=LGetBasicBlockParent(LGetInstructionParent(user));
    ok=include(calls,user) && include(functions,owner);
   }else if(LIsAGlobalVariable(user)||LIsAGlobalAlias(user)){
    if(!local_linkage(user)){ok=0;break;}
    ok=include(&work,user);
   }else if(LIsAConstantExpr(user)){
    if(LGetConstOpcode(user)!=bitcast_op && LGetConstOpcode(user)!=gep_op){ok=0;break;}
    ok=include(&work,user);
   }else if(LIsAConstantArray(user)||LIsAConstantStruct(user)||LIsABitCastInst(user)||LIsAGetElementPtrInst(user)||LIsAPHINode(user)||LIsASelectInst(user)){
    ok=include(&work,user);
   }else if(LIsALoadInst(user)){
    if(LGetOperand(user,0)!=value){ok=0;break;}
    ok=include(&work,user);
   }else if(LIsAStoreInst(user)){
    if(LGetOperand(user,0)==value)ok=private_slot(LGetOperand(user,1)) && include(&work,LGetOperand(user,1));
    // A write to a tracked private slot does not create an additional reader.
    else if(LGetOperand(user,1)!=value)ok=0;
   }else if(LIsAInstruction(user) && LGetInstructionOpcode(user)==icmp_op){
    // Observing a function pointer's identity does not invoke the function.
   }else{ok=0;break;}
  }
 }
 free(work.items);return ok;
}
static int relax_effects(RefSet *functions){
 RefSet calls={0};int ok=1;
 for(size_t i=0;i<functions->count && ok;i++)ok=trace_effect_uses(functions->items[i],functions,&calls);
 if(ok){
  const char *names[]={"readnone","readonly","argmemonly","inaccessiblememonly","inaccessiblemem_or_argmemonly","nosync","speculatable"};
  for(unsigned i=0;i<sizeof names/sizeof *names;i++){
   unsigned kind=LGetEnumAttributeKindForName(names[i],strlen(names[i]));if(!kind)continue;
   for(size_t j=0;j<functions->count;j++)LRemoveEnumAttributeAtIndex(functions->items[j],~0U,kind);
   for(size_t j=0;j<calls.count;j++)LRemoveCallSiteEnumAttribute(calls.items[j],~0U,kind);
  }
 }
 fprintf(stderr,"[compat] affected_functions=%zu affected_calls=%zu effects_verified=%d\n",functions->count,calls.count,ok);
 free(calls.items);return ok;
}

static int rewrite_nulls(Ref module,Ref ctx,Ref builder,int *changed){
 if(!null_remaining(module))return 0;

 const char *layout=LGetDataLayoutStr(module);if(!layout || !*layout)return -1;
 Ref td=LCreateTargetData(layout);unsigned width=LPointerSizeForAS(td,1);LDisposeTargetData(td);
 if(width!=8)return -1;
 if(!inline_null_helpers(module,ctx,changed))return -1;
 RefSet affected={0};int ok=1;
 Ref integer=LInt64TypeInContext(ctx);int count=0;
 for(Ref f=LGetFirstFunction(module);f && ok;f=LGetNextFunction(f))
  for(Ref bb=LGetFirstBasicBlock(f);bb && ok;bb=LGetNextBasicBlock(bb))
   for(Ref in=LGetFirstInstruction(bb),next;in && ok;in=next){
    next=LGetNextInstruction(in);if(!null_compare(in))continue;
    Ref p=LGetOperand(in,LIsNull(LGetOperand(in,0))?1:0);
    LPositionBuilderBefore(builder,LGetFirstInstruction(LGetEntryBasicBlock(f)));
    Ref slot=LBuildAlloca(builder,LTypeOf(p),"compat.null.slot");
    LPositionBuilderBefore(builder,in);
    Ref store=LBuildStore(builder,p,slot);LSetVolatile(store,1);
    Ref addr=LBuildBitCast(builder,slot,LPointerType(integer,0),"compat.null.address");
    Ref bits=LBuildLoad(builder,addr,"compat.null.bits");LSetVolatile(bits,1);
    Ref compare=LBuildICmp(builder,LGetICmpPredicate(in),bits,LConstInt(integer,0,0),"compat.null.result");
    if(!copy_metadata(in,compare,ctx) || !include(&affected,f)){ok=0;break;}
    LReplaceAllUsesWith(in,compare);LInstructionEraseFromParent(in);count++;
   }
 if(ok && count)ok=relax_effects(&affected);
 free(affected.items);return ok?count:-1;
}

static void dump(Ref module,unsigned number,const char *label){
 const char *dir=getenv("COMPAT_DUMP");if(!dir)return;
 char path[4096],*error=0;snprintf(path,sizeof path,"%s/module-%03u-%s.ll",dir,number,label);
 int bad=LPrintModuleToFile(module,path,&error);
 if(bad)fprintf(stderr,"[compat] dump failed: %s\n",error?error:"unknown");
 if(error)LDisposeMessage(error);
}
#include "block_reads.c"
#include "logical32.c"
#include "private_generic.c"
#include "legacy_sync.c"
Ref compat_prepare(Ref original){
 if(++block_query_depth<=64)block_query_scopes[block_query_depth-1]=0;
 if(!original)return original;
 unsigned number=atomic_fetch_add(&sequence,1);dump(original,number,"original");
 uint64_t start=now_ns();
 int has_root=0;
 for(Ref f=LGetFirstFunction(original);f && !has_root;f=LGetNextFunction(f))
  for(Ref bb=LGetFirstBasicBlock(f);bb && !has_root;bb=LGetNextBasicBlock(bb))
   for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in))if(root(in)){has_root=1;break;}
 if(!has_root)has_root=null_remaining(original);
 if(!has_root)has_root=block_reads_present(original);
 if(!has_root)has_root=logical32_present(original);
 if(!has_root)has_root=private_generic_present(original);
 if(!has_root)has_root=legacy_sync_present(original);
 if(!has_root){fprintf(stderr,"[compat] module=%u untouched no roots prepare_ns=%llu\n",number,(unsigned long long)(now_ns()-start));return original;}
 Ref module=LCloneModule(original);if(!module)return original;
 Ref ctx=LGetModuleContext(module),builder=LCreateBuilderInContext(ctx);int ops=0,bad=0,optimized=0;
 int sync=rewrite_legacy_sync(module,ctx,builder);if(sync<0)bad=1;else ops+=sync;
 fprintf(stderr,"[compat] legacy_sync=%d\n",sync);
 int private_inlined=bad?0:inline_private_generic_helpers(module,ctx);
 if(private_inlined<0)bad=1;
 fprintf(stderr,"[compat] private_generic_helpers=%d\n",private_inlined);
 int private_loads=bad?0:rewrite_private_generic(module,builder);if(private_loads<0)bad=1;else ops+=private_loads;
 fprintf(stderr,"[compat] private_generic_loads=%d\n",private_loads);
 int logical=bad?0:rewrite_logical32(module,ctx,builder);if(logical<0)bad=1;else ops+=logical;
 fprintf(stderr,"[compat] logical32=%d\n",logical);
 int blocks=bad?0:rewrite_block_reads(module,ctx,builder);if(blocks<0)bad=1;else ops+=blocks;
 fprintf(stderr,"[compat] block_reads=%d\n",blocks);
 int nulls=bad?0:rewrite_nulls(module,ctx,builder,&optimized);if(nulls<0)bad=1;else ops+=nulls;
 fprintf(stderr,"[compat] null_compares=%d\n",nulls);
 for(Ref f=LGetFirstFunction(module);f && !bad;f=LGetNextFunction(f)){
  int n=rewrite_function(f,ctx,builder);if(n<0){bad=1;break;}ops+=n;
 }
 LDisposeBuilder(builder);char *error=0;
 if(!bad && (ops||optimized))bad=remaining_bad_pattern(module) || null_remaining(module) || LVerifyModule(module,2,&error);
 fprintf(stderr,"[compat] module=%u ops=%d verified=%d prepare_ns=%llu error=%s\n",number,ops,!bad,(unsigned long long)(now_ns()-start),error?error:"-");
 if(error)LDisposeMessage(error);
 if(bad || (!ops && !optimized)){LDisposeModule(module);return original;}
 if(blocks>0 && block_query_depth<=64)block_query_scopes[block_query_depth-1]=1;
 dump(module,number,"lowered");return module;
}
void compat_finish(Ref original,Ref used){
 if(block_query_depth)block_query_depth--;
 if(used && used!=original){uint64_t start=now_ns();LDisposeModule(used);fprintf(stderr,"[compat] cleanup_ns=%llu\n",(unsigned long long)(now_ns()-start));}
}
