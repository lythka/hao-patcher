// Included by compat.c: lower intact two-texel blocks at the Intel entry.
// Opt-in prototype: bound samplers with per-axis address modes, mip zero.
// Requires the companion driver upload adapter; no image recipe enables it.
static const char *block_name="air.block_read_texture_2d_array.v4f32";
static int block_reads_present(Ref module){
 Ref f=LGetNamedFunction(module,block_name);
 const char *mode=getenv("COMPAT_BLOCK_READS");
 return mode && !strcmp(mode,"per-axis-mip0") && block_query_ready && f && LGetFirstUse(f);
}
static Ref block_decl(Ref module,const char *name,Ref result,Ref *args,unsigned count){
 Ref type=LFunctionType(result,args,count,0),f=LGetNamedFunction(module,name);
 if(f)return LGetElementType(LTypeOf(f))==type?f:NULL;
 f=LAddFunction(module,name,type);
 if(!strncmp(name,"llvm.igil.",10)){
  const char *attrs[]={"nounwind","readnone"};
  for(unsigned i=0;i<2;i++){
   unsigned k=LGetEnumAttributeKindForName(attrs[i],strlen(attrs[i]));
   LAddAttributeAtIndex(f,~0U,LCreateEnumAttribute(LGetModuleContext(module),k,0));
  }
 }
 return f;
}
static int rewrite_block_reads(Ref module,Ref ctx,Ref builder){
 if(!block_reads_present(module))return 0;
 if(block_query_depth>64)return -1;
 // The companion upload adapter covers the compute driver's constant path.
 if(!LGetNamedMetadataNumOperands(module,"air.kernel") ||
    LGetNamedMetadataNumOperands(module,"air.vertex") ||
    LGetNamedMetadataNumOperands(module,"air.fragment"))return -1;
 // Reserve these declarations: the backend requires known pure signatures.
 if(LGetNamedFunction(module,"llvm.igil.is.normalized.") ||
    LGetNamedFunction(module,"llvm.igil.get.sampler.highbits."))return -1;
 Ref f=LGetNamedFunction(module,block_name);
 Ref tex=LGetTypeByName(module,"struct._texture_2d_array_t");
 Ref sampler=LGetTypeByName(module,"struct._sampler_t");
 if(!tex || !sampler || !LIsDeclaration(f))return -1;
 Ref i32=LInt32TypeInContext(ctx),v2=LVectorType(i32,2);
 Ref vf=LVectorType(LFloatTypeInContext(ctx),4),tp=LPointerType(tex,1),sp=LPointerType(sampler,2);
 Ref args[]={tp,sp,v2,i32,v2,i32,i32,LPointerType(vf,0),LPointerType(LInt8TypeInContext(ctx),0),v2};
 if(LGetElementType(LTypeOf(f))!=LFunctionType(LVoidTypeInContext(ctx),args,10,0))return -1;
 Ref zero=LConstInt(i32,0,0),one=LConstInt(i32,1,0);
 // Validate every use before adding instructions. Any refusal discards the clone.
 for(Ref use=LGetFirstUse(f);use;use=LGetNextUse(use)){
  Ref call=LGetUser(use);
  if(!LIsACallInst(call) || LGetCalledValue(call)!=f || LGetNumOperands(call)!=11 ||
     LGetInstructionCallConv(call) || LIsTailCall(call) || LHasMetadata(call) ||
     !LIsAArgument(LGetOperand(call,1)) || !LIsNull(LGetOperand(call,8)) || !LIsNull(LGetOperand(call,5)))return -1;
  Ref shape=LGetOperand(call,9);if(!LIsAConstant(shape))return -1;
  Ref x=LConstExtractElement(shape,zero),y=LConstExtractElement(shape,one);
  if(!LIsAConstantInt(x) || !LIsAConstantInt(y))return -1;
  long long w=LConstIntGetSExtValue(x),h=LConstIntGetSExtValue(y);
  if(!((w==2 && h==1)||(w==1 && h==2)))return -1;
 }
 // Reuse Intel's AIR sample lowering and helper instead of constructing its
 // texture leaf intrinsic here. Sampler queries need readnone declarations:
 // otherwise SelectionDAG adds a chain operand and the target reads wrong slots.
 Ref ft=LFloatTypeInContext(ctx),v2f=LVectorType(ft,2),i1=LInt1TypeInContext(ctx);
 Ref ra[]={tp,sp,v2f,i32,i1,v2,i1,ft,i32};
 Ref read=block_decl(module,"air.sample_texture_2d_array.v4f32",vf,ra,9);
 Ref na[]={sp,i32},ha[]={sp},da[]={tp,i32};
 Ref high=block_decl(module,"llvm.igil.get.sampler.highbits.",i32,ha,1);
 Ref normalized=block_decl(module,"llvm.igil.is.normalized.",i32,na,2);
 Ref width=block_decl(module,"air.get_width_texture_2d_array",i32,da,2);
 Ref height=block_decl(module,"air.get_height_texture_2d_array",i32,da,2);
 if(!read || !high || !normalized || !width || !height)return -1;
 int count=0;
 while(LGetFirstUse(f)){
  Ref call=LGetUser(LGetFirstUse(f));LPositionBuilderBefore(builder,call);
  Ref a[10];for(unsigned j=0;j<10;j++)a[j]=LGetOperand(call,j);
  Ref bits=LBuildCall(builder,high,&a[1],1,"compat_block_sampler_bits");
  Ref na[]={a[1],bits};
  Ref norm=LBuildCall(builder,normalized,na,2,"compat_block_normalized");
  Ref modes=LGetUndef(v2);
  Ref s_mode=LBuildBinOp(builder,bin_ops[3],norm,LConstInt(i32,15,0),"compat_block_s_mode");
  Ref t_mode=LBuildBinOp(builder,bin_ops[6],norm,LConstInt(i32,16,0),"compat_block_t_mode");
  modes=LBuildInsertElement(builder,modes,s_mode,zero,"compat_block_modes");
  modes=LBuildInsertElement(builder,modes,t_mode,one,"compat_block_modes");
  Ref two=LConstInt(i32,2,0);
  Ref wrap=LBuildICmp(builder,34 /* LLVMIntUGT */,modes,LConstVector((Ref[]){two,two},2),"compat_block_wrap");
  norm=LBuildICmp(builder,ne_pred,norm,zero,"compat_block_is_normalized");
  Ref da[]={a[0],zero};
  Ref dims=LGetUndef(v2);
  dims=LBuildInsertElement(builder,dims,LBuildCall(builder,width,da,2,"compat_block_width"),zero,"compat_block_dims");
  dims=LBuildInsertElement(builder,dims,LBuildCall(builder,height,da,2,"compat_block_height"),one,"compat_block_dims");
  Ref limit=LBuildBinOp(builder,bin_ops[1],dims,LConstVector((Ref[]){one,one},2),"compat_block_limit");
  dims=LBuildUIToFP(builder,dims,v2f,"compat_block_dimensions");
  Ref w=LConstExtractElement(a[9],zero);
  Ref delta[2]={LConstIntGetSExtValue(w)==2?one:zero,LConstIntGetSExtValue(w)==2?zero:one};
  for(unsigned n=0;n<2;n++){
   Ref xy=n?LBuildBinOp(builder,bin_ops[0],a[2],LConstVector(delta,2),"compat_block_next"):a[2];
   xy=LBuildBinOp(builder,bin_ops[0],xy,a[4],"compat_block_offset");
   Ref lower=LBuildICmp(builder,40 /* LLVMIntSLT */,xy,LConstVector((Ref[]){zero,zero},2),"compat_block_negative");
   Ref bounded=LBuildSelect(builder,lower,LConstVector((Ref[]){zero,zero},2),xy,"compat_block_lower");
   Ref upper=LBuildICmp(builder,38 /* LLVMIntSGT */,bounded,limit,"compat_block_over");
   bounded=LBuildSelect(builder,upper,limit,bounded,"compat_block_upper");
   xy=LBuildSelect(builder,wrap,bounded,xy,"compat_block_clamp");
   Ref half[]={LConstReal(ft,0.5),LConstReal(ft,0.5)};
   Ref coord=LBuildSIToFP(builder,xy,v2f,"compat_block_float");
   coord=LBuildFAdd(builder,coord,LConstVector(half,2),"compat_block_center");
   Ref scaled=LBuildFDiv(builder,coord,dims,"compat_block_scaled");
   coord=LBuildSelect(builder,norm,scaled,coord,"compat_block_coordinates");
   Ref offsets[]={zero,zero};
   Ref operands[]={a[0],a[1],coord,a[3],LConstInt(i1,0,0),LConstVector(offsets,2),LConstInt(i1,1,0),LConstReal(ft,0),a[6]};
   Ref value=LBuildCall(builder,read,operands,9,"compat_block_read");
   Ref index=LConstInt(i32,n,0),out=LBuildGEP(builder,a[7],&index,1,"compat_block_out");
   LBuildStore(builder,value,out);
  }
  LInstructionEraseFromParent(call);count++;
 }
 return count;
}
