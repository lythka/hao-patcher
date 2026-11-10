// Reviewed MPS SIFT kernel only, after frontend specialization. The opt-in is
// the caller's dispatch contract: every threadgroup is exactly (32,1,1), using
// the original grid/tiling/resources. This is NOT arbitrary SIMD emulation.
// Validate the divergent max/fold/exit chains before introducing any barriers.
#include <float.h>
#include "logical32_helpers.h"
#define LOGICAL32_KERNEL "ExtractFeaturePointsAndOrientationFusedMultifetch"
static const char *logical32_intrinsics[]={
 "air.simd_shuffle_down.v4f32", "air.simd_ballot.i64",
 "air.simd_broadcast.f32", "air.simd_broadcast.u.i32",
 "air.simdgroup.barrier", "air.simd_max.f32"};
static const char *logical32_helpers[]={
 "logical32_shuffle_v4f32", "logical32_ballot", "logical32_broadcast_f32",
 "logical32_broadcast_i32", "air.wg.barrier", "logical32_max"};
static int logical32_present(Ref module){
 const char *mode=getenv("COMPAT_LOGICAL32");
 if(!mode || strcmp(mode,"mps-sift-32x1x1") || !LGetNamedFunction(module,LOGICAL32_KERNEL))return 0;
 for(unsigned i=0;i<6;i++){
  Ref f=LGetNamedFunction(module,logical32_intrinsics[i]);if(f && LGetFirstUse(f))return 1;
 }
 return 0; // Already translated/repaired code has no work here.
}
static int logical32_call(Ref call,Ref f,unsigned argc){
 return call && f && LIsACallInst(call) && LGetCalledValue(call)==f &&
  LGetNumOperands(call)==argc+1 && !LGetInstructionCallConv(call) && !LHasMetadata(call);
}
static Ref logical32_only_user(Ref value){
 Ref use=LGetFirstUse(value);return use && !LGetNextUse(use)?LGetUser(use):NULL;
}
static int logical32_integer(Ref value,long long n){
 return LIsAConstantInt(value) && LConstIntGetSExtValue(value)==n;
}
static int logical32_pair(Ref term,Ref a,Ref b){
 return term && LGetNumSuccessors(term)==2 &&
  ((LGetSuccessor(term,0)==a && LGetSuccessor(term,1)==b) ||
   (LGetSuccessor(term,0)==b && LGetSuccessor(term,1)==a));
}
static Ref logical32_unextend(Ref value){
 return (LIsASExtInst(value) || LIsAZExtInst(value))?LGetOperand(value,0):value;
}
static int logical32_lane(Ref value,Ref kernel){
 return LIsAExtractElementInst(value) && LGetOperand(value,0)==LGetParam(kernel,9) &&
  logical32_integer(LGetOperand(value,1),0);
}
static Ref logical32_histogram_bound(Ref preheader,Ref loop,Ref exit,Ref kernel){
 Ref pre=LGetBasicBlockTerminator(preheader),back=LGetBasicBlockTerminator(loop);
 if(!logical32_pair(pre,loop,exit) || !logical32_pair(back,loop,exit) ||
    LGetSuccessor(pre,0)!=loop || LGetSuccessor(back,0)!=loop)return NULL;
 Ref start=LGetOperand(pre,0),more=LGetOperand(back,0);
 if(!LIsAInstruction(start) || !LIsAInstruction(more) ||
    LGetInstructionOpcode(start)!=icmp_op || LGetInstructionOpcode(more)!=icmp_op)return NULL;
 int pred=LGetICmpPredicate(start);
 if((pred!=36 /* unsigned < */ && pred!=40 /* signed < */) || LGetICmpPredicate(more)!=pred ||
    LGetOperand(start,1)!=LGetOperand(more,1))return NULL;
 Ref lane=logical32_unextend(LGetOperand(start,0)),next=logical32_unextend(LGetOperand(more,0));
 if(!logical32_lane(lane,kernel) || !LIsAInstruction(next) || LGetInstructionOpcode(next)!=bin_ops[0] ||
    !logical32_integer(LGetOperand(next,1),32))return NULL;
 Ref index=LGetOperand(next,0);
 if(!LIsAPHINode(index) || LGetInstructionParent(index)!=loop || LCountIncoming(index)!=2)return NULL;
 int initial=0,increment=0;
 for(unsigned i=0;i<2;i++){
  Ref value=LGetIncomingValue(index,i),bb=LGetIncomingBlock(index,i);
  if(bb==preheader && value==lane)initial++;
  if(bb==loop && value==next)increment++;
 }
 return initial==1 && increment==1?LGetOperand(start,1):NULL;
}
static Ref logical32_max_exit(Ref max,Ref module,Ref ctx){
 Ref fold=logical32_only_user(max);
 if(!logical32_call(fold,LGetNamedFunction(module,"air.fast_fmax.f32"),2) || LGetOperand(fold,1)!=max)return NULL;
 Ref local=LGetOperand(fold,0),loop=LGetInstructionParent(max);
 if(!LIsAPHINode(local) || LGetInstructionParent(local)!=loop ||
    LGetInstructionParent(fold)!=loop || logical32_only_user(local)!=fold)return NULL;
 Ref use=LGetFirstUse(fold);if(!use)return NULL;
 Ref p=LGetUser(use);use=LGetNextUse(use);if(!use || LGetNextUse(use))return NULL;
 Ref q=LGetUser(use),exited=p==local?q:q==local?p:NULL;
 if(!exited || !LIsAPHINode(exited))return NULL;
 Ref exit=LGetInstructionParent(exited),preheader=NULL;
 Ref neutral=LConstReal(LFloatTypeInContext(ctx),-FLT_MAX);
 Ref phis[]={local,exited};
 for(unsigned i=0;i<2;i++){
  if(LCountIncoming(phis[i])!=2)return NULL;
  int found=0;
  for(unsigned j=0;j<2;j++){
   Ref value=LGetIncomingValue(phis[i],j),bb=LGetIncomingBlock(phis[i],j);
   if(value==fold && bb==loop)found++;
   else if(value==neutral && bb!=loop){if(preheader && preheader!=bb)return NULL;preheader=bb;}
   else return NULL;
  }
  if(found!=1)return NULL;
 }
 if(!logical32_histogram_bound(preheader,loop,exit,LGetBasicBlockParent(loop)))return NULL;
 Ref broadcast=logical32_only_user(exited);
 if(!logical32_call(broadcast,LGetNamedFunction(module,"air.simd_broadcast.f32"),2) ||
    LGetInstructionParent(broadcast)!=exit || !logical32_integer(LGetOperand(broadcast,1),0))return NULL;
 // The broadcast is immediately after the exit phis: no extra divergent work.
 Ref in=LGetFirstInstruction(exit);while(in && LIsAPHINode(in))in=LGetNextInstruction(in);
 return in==broadcast?broadcast:NULL;
}
static int logical32_metadata_tag(Ref node,unsigned index,const char *tag){
 unsigned n=LGetMDNodeNumOperands(node);if(n<2 || n>64)return 0;
 Ref operands[64];LGetMDNodeOperands(node,operands);
 if(!logical32_integer(operands[0],index))return 0;
 unsigned len=0;const char *s=LGetMDString(operands[1],&len);
 return s && strlen(tag)==len && !memcmp(s,tag,len);
}
static int logical32_kernel_abi(Ref module,Ref kernel,Ref ctx){
 if(LGetNamedMetadataNumOperands(module,"air.kernel")!=1 ||
    LGetNamedMetadataNumOperands(module,"air.vertex") || LGetNamedMetadataNumOperands(module,"air.fragment") ||
    LCountParams(kernel)!=10)return 0;
 Ref v3=LVectorType(LInt16TypeInContext(ctx),3);
 for(unsigned i=7;i<10;i++)if(LTypeOf(LGetParam(kernel,i))!=v3)return 0;
 if(LTypeOf(LGetParam(kernel,5))!=LPointerType(LFloatTypeInContext(ctx),3))return 0;
 Ref node;LGetNamedMetadataOperands(module,"air.kernel",&node);
 if(LGetMDNodeNumOperands(node)!=3)return 0;
 Ref entry[3];LGetMDNodeOperands(node,entry);if(entry[0]!=kernel)return 0;
 if(LGetMDNodeNumOperands(entry[2])!=10)return 0;
 Ref args[10];LGetMDNodeOperands(entry[2],args);
 return logical32_metadata_tag(args[7],7,"air.threadgroup_position_in_grid") &&
  logical32_metadata_tag(args[8],8,"air.threads_per_threadgroup") &&
  logical32_metadata_tag(args[9],9,"air.thread_position_in_threadgroup");
}
#include "logical32_convergence.c"
static int rewrite_logical32(Ref module,Ref ctx,Ref builder){
 if(!logical32_present(module))return 0;
 Ref kernel=LGetNamedFunction(module,LOGICAL32_KERNEL);
 if(!logical32_kernel_abi(module,kernel,ctx))return -1;
 for(Ref f=LGetFirstFunction(module);f;f=LGetNextFunction(f))
  if(!LIsDeclaration(f) && f!=kernel)return -1;
 // No unreviewed SIMD operation may remain mixed with the shared-memory pass.
 for(Ref f=LGetFirstFunction(module);f;f=LGetNextFunction(f)){
  if(strncmp(LGetValueName(f),"air.simd",8) || !LGetFirstUse(f))continue;
  int known=0;for(unsigned i=0;i<6;i++)if(!strcmp(LGetValueName(f),logical32_intrinsics[i]))known=1;
  if(!known)return -1;
 }
 Ref intrinsics[6],maxima[8]={0},broadcasts[8]={0};unsigned counts[6]={0};
 Ref i16=LInt16TypeInContext(ctx),i32=LInt32TypeInContext(ctx),ft=LFloatTypeInContext(ctx);
 Ref vf=LVectorType(ft,4),types[]={vf,LInt64TypeInContext(ctx),ft,i32,LVoidTypeInContext(ctx),ft};
 for(unsigned i=0;i<6;i++){
  intrinsics[i]=LGetNamedFunction(module,logical32_intrinsics[i]);Ref f=intrinsics[i];if(!f)continue;
  unsigned argc=(i==1 || i==5)?1:2;
  Ref args[]={i==1?LInt1TypeInContext(ctx):i==4?i32:types[i],i==4?i32:i16};
  if(!LIsDeclaration(f) || LGetElementType(LTypeOf(f))!=LFunctionType(types[i],args,argc,0))return -1;
  for(Ref use=LGetFirstUse(f);use;use=LGetNextUse(use)){
   Ref call=LGetUser(use);
   if(!logical32_call(call,f,argc) || LGetBasicBlockParent(LGetInstructionParent(call))!=kernel)return -1;
   if(i==0 && !logical32_integer(LGetOperand(call,1),1))return -1;
   if(i==4 && (!logical32_integer(LGetOperand(call,0),2) || !logical32_integer(LGetOperand(call,1),4)))return -1;
   if(i==5){
    if(counts[i]>=8)return -1;
    maxima[counts[i]]=call;broadcasts[counts[i]]=logical32_max_exit(call,module,ctx);
    if(!broadcasts[counts[i]])return -1;
   }
   counts[i]++;
  }
 }
 // The captured zero-layer and one-layer specializations. Other shapes remain
 // refused until their convergence/dispatch evidence is reviewed separately.
 int empty=counts[0]==4 && !counts[1] && !counts[2] && !counts[3] && !counts[4] && !counts[5];
 int full=counts[0]==6 && counts[1]==4 && counts[2]==20 && counts[3]==16 && counts[4]==16 && counts[5]==4;
 if(!empty && !full)return -1;
 if(full){
  int neighbor=0;
  for(Ref bb=LGetFirstBasicBlock(kernel);bb;bb=LGetNextBasicBlock(bb))
   for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in))
    if(LGetInstructionOpcode(in)==icmp_op && LGetICmpPredicate(in)==36 &&
       logical32_lane(LGetOperand(in,0),kernel) && logical32_integer(LGetOperand(in,1),31))neighbor=1;
  if(!neighbor)return -1;
 }
 if(!logical32_converged(kernel,intrinsics))return -1;
 for(unsigned i=0;i<6;i++)if(i!=4 && LGetNamedFunction(module,logical32_helpers[i]))return -1;
 Ref helper_module=NULL;char *error=NULL;
 Ref buffer=LCreateMemoryBufferWithMemoryRangeCopy(logical32_helper_ir,strlen(logical32_helper_ir),"logical32-helpers");
 if(LParseIRInContext(ctx,buffer,&helper_module,&error)){
  fprintf(stderr,"[compat] logical32 helper parse: %s\n",error?error:"-");if(error)LDisposeMessage(error);return -1;
 }
 LSetDataLayout(helper_module,LGetDataLayoutStr(module));LSetTarget(helper_module,LGetTarget(module));
 // External linkage during linking keeps the otherwise-unreferenced helpers.
 // Their definitions are internalized after the kernel's references exist.
 if(LLinkModules2(module,helper_module))return -1;
 Ref helpers[6];for(unsigned i=0;i<6;i++){
  helpers[i]=LGetNamedFunction(module,logical32_helpers[i]);if(!helpers[i])return -1;
  if(i!=4)LSetLinkage(helpers[i],internal_linkage);
 }
 LPositionBuilderBefore(builder,LGetFirstInstruction(LGetEntryBasicBlock(kernel)));
 Ref lane=LBuildExtractElement(builder,LGetParam(kernel,9),LConstInt(i32,0,0),"logical32.lane");
 int count=0;
 for(unsigned i=0;i<5;i++){
  Ref f=intrinsics[i];if(!f)continue;
  while(LGetFirstUse(f)){
   Ref call=LGetUser(LGetFirstUse(f));LPositionBuilderBefore(builder,call);
   Ref target=helpers[i],args[3]={LGetOperand(call,0),NULL,lane};unsigned argc=3;
   if(i==1){args[1]=lane;argc=2;}
   else if(i==4){args[1]=LConstInt(i32,1,0);argc=2;}
   else args[1]=LGetOperand(call,1);
   for(unsigned j=0;j<counts[5];j++)if(call==broadcasts[j]){target=helpers[5];args[1]=lane;argc=2;}
   Ref value=LBuildCall(builder,target,args,argc,i==4?"":"logical32.exchange");
   if(i!=4)LReplaceAllUsesWith(call,value);
   LInstructionEraseFromParent(call);count++;
  }
 }
 for(unsigned i=0;i<counts[5];i++){
  LReplaceAllUsesWith(maxima[i],LGetOperand(maxima[i],0));LInstructionEraseFromParent(maxima[i]);count++;
 }
 RefSet affected={0};int ok=include(&affected,kernel) && relax_effects(&affected);free(affected.items);
 if(!ok)return -1;
 Ref pm=LCreatePassManager();LAddAlwaysInlinerPass(pm);LRunPassManager(pm,module);LDisposePassManager(pm);
 return count;
}
