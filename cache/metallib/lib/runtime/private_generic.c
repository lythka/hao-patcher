// AIR's generic AS8 can hide a known private pointer from the old Intel backend.
// Only loads rooted in this function's private alloca are specialized. In
// particular, the loaded value may itself be a device pointer: its type and all
// subsequent device accesses stay unchanged. Bounded direct-call local leaf
// helpers can be inlined first; the same alloca proof must then succeed. No
// guessing through phi/select, integer round-trips or function arguments.
static int pointer_space(Ref value,unsigned space){
 Ref type=LTypeOf(value);
 return LGetTypeKind(type)==pointer_kind && LGetPointerAddressSpace(type)==space;
}
static int private_alloca_origin(Ref pointer,unsigned depth){
 if(depth>64 || !pointer_space(pointer,0))return 0;
 if(LIsAAllocaInst(pointer))return 1;
 if(LIsABitCastInst(pointer) || LIsAGetElementPtrInst(pointer))
  return private_alloca_origin(LGetOperand(pointer,0),depth+1);
 return 0;
}
static int generic_private_origin(Ref pointer,unsigned depth){
 if(depth>64 || !pointer_space(pointer,8))return 0;
 if(LIsAAddrSpaceCastInst(pointer))return private_alloca_origin(LGetOperand(pointer,0),depth+1);
 if(LIsABitCastInst(pointer))return generic_private_origin(LGetOperand(pointer,0),depth+1);
 if(LIsAGetElementPtrInst(pointer) && LGetNumOperands(pointer)<=33)
  return generic_private_origin(LGetOperand(pointer,0),depth+1);
 return 0;
}
// Expose an alloca passed inside a by-value aggregate without guessing the
// provenance of a helper argument. Restrict expansion to direct-call local
// leaves. Device/mixed callsites still fail the ordinary alloca proof afterward.
static int private_generic_leaf(Ref function,unsigned *instructions,unsigned *sites){
 if(!local_linkage(function) || LIsDeclaration(function))return 0;
 unsigned noinline=LGetEnumAttributeKindForName("noinline",8);
 unsigned optnone=LGetEnumAttributeKindForName("optnone",7);
 if((noinline && LGetEnumAttributeAtIndex(function,~0U,noinline)) ||
    (optnone && LGetEnumAttributeAtIndex(function,~0U,optnone)))return 0;
 unsigned count=0,calls=0;int generic=0;
 for(Ref bb=LGetFirstBasicBlock(function);bb;bb=LGetNextBasicBlock(bb))
  for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in)){
   if(++count>2048)return 0;
   if(LIsALoadInst(in) && pointer_space(LGetOperand(in,0),8))generic=1;
   if(LIsAInvokeInst(in))return 0;
   if(LIsACallInst(in)){
    Ref callee=LGetCalledValue(in);
    // No recursion, mutual recursion, indirect calls or nested expansion.
    if(!LIsAFunction(callee) || !LIsDeclaration(callee))return 0;
   }
  }
 if(!generic)return 0;
 for(Ref use=LGetFirstUse(function);use;use=LGetNextUse(use)){
  Ref call=LGetUser(use);
  if(!LIsACallInst(call) || LGetCalledValue(call)!=function)return 0;
  unsigned occurrences=0;
  for(unsigned i=0;i<LGetNumOperands(call);i++)occurrences+=LGetOperand(call,i)==function;
  if(occurrences!=1 || ++calls>8)return 0;
  if((noinline && LGetCallSiteEnumAttribute(call,~0U,noinline)) ||
     (optnone && LGetCallSiteEnumAttribute(call,~0U,optnone)))return 0;
  Ref owner=LGetBasicBlockParent(LGetInstructionParent(call));
  if(optnone && LGetEnumAttributeAtIndex(owner,~0U,optnone))return 0;
 }
 if(!calls)return 0;
 if(instructions)*instructions=count;
 if(sites)*sites=calls;
 return 1;
}
static int inline_private_generic_helpers(Ref module,Ref ctx){
 Ref selected[16];unsigned count=0,expanded=0;
 for(Ref f=LGetFirstFunction(module);f;f=LGetNextFunction(f)){
  unsigned instructions=0,sites=0;
  if(!private_generic_leaf(f,&instructions,&sites))continue;
  if(count==16 || instructions*sites>4096-expanded)return 0;
  selected[count++]=f;expanded+=instructions*sites;
 }
 if(!count)return 0;
 unsigned always=LGetEnumAttributeKindForName("alwaysinline",12);
 if(!always)return -1;
 // LLVM's always-inliner is module-wide. Refuse exposure if it could also
 // expand anything outside the selected budget, including callsite attributes.
 for(Ref f=LGetFirstFunction(module);f;f=LGetNextFunction(f)){
  int chosen=0;for(unsigned i=0;i<count;i++)chosen|=selected[i]==f;
  if(!chosen && LGetEnumAttributeAtIndex(f,~0U,always))return 0;
  for(Ref bb=LGetFirstBasicBlock(f);bb;bb=LGetNextBasicBlock(bb))
   for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in))
    if((LIsACallInst(in)||LIsAInvokeInst(in)) && LGetCallSiteEnumAttribute(in,~0U,always)){
     Ref callee=LGetCalledValue(in);chosen=0;
     for(unsigned i=0;i<count;i++)chosen|=selected[i]==callee;
     if(!chosen)return 0;
    }
 }
 for(unsigned i=0;i<count;i++)LAddAttributeAtIndex(selected[i],~0U,LCreateEnumAttribute(ctx,always,0));
 Ref pm=LCreatePassManager();
 LAddAlwaysInlinerPass(pm);LAddInstructionCombiningPass(pm);
 LRunPassManager(pm,module);LDisposePassManager(pm);
 return (int)count;
}
static int private_generic_present(Ref module){
 for(Ref f=LGetFirstFunction(module);f;f=LGetNextFunction(f)){
  if(private_generic_leaf(f,0,0))return 1;
  for(Ref bb=LGetFirstBasicBlock(f);bb;bb=LGetNextBasicBlock(bb))
   for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in))
    if(LIsALoadInst(in) && generic_private_origin(LGetOperand(in,0),0))return 1;
 }
 return 0;
}
static Ref private_equivalent(Ref pointer,Ref builder,Ref ctx,unsigned depth){
 if(depth>64)return 0;
 Ref source=LGetOperand(pointer,0),equivalent=0;
 Ref type=LPointerType(LGetElementType(LTypeOf(pointer)),0);
 if(LIsAAddrSpaceCastInst(pointer)){
  equivalent=LTypeOf(source)==type?source:LBuildBitCast(builder,source,type,"compat.private.cast");
 }else{
  source=private_equivalent(source,builder,ctx,depth+1);
  if(!source)return 0;
  if(LIsABitCastInst(pointer))equivalent=LBuildBitCast(builder,source,type,"compat.private.cast");
  else if(LIsAGetElementPtrInst(pointer)){
   unsigned count=LGetNumOperands(pointer)-1;Ref indices[32];
   if(count>32)return 0;
   for(unsigned i=0;i<count;i++)indices[i]=LGetOperand(pointer,i+1);
   equivalent=LIsInBounds(pointer)?LBuildInBoundsGEP(builder,source,indices,count,"compat.private.gep"):
                                  LBuildGEP(builder,source,indices,count,"compat.private.gep");
  }
 }
 if(!equivalent || LTypeOf(equivalent)!=type)return 0;
 if(LIsAInstruction(equivalent) && equivalent!=source && !copy_metadata(pointer,equivalent,ctx))return 0;
 return equivalent;
}
static int rewrite_private_generic(Ref module,Ref builder){
 int count=0;Ref ctx=LGetModuleContext(module);
 for(Ref f=LGetFirstFunction(module);f;f=LGetNextFunction(f))
  for(Ref bb=LGetFirstBasicBlock(f);bb;bb=LGetNextBasicBlock(bb))
   for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in)){
    if(!LIsALoadInst(in))continue;
    Ref pointer=LGetOperand(in,0);
    if(!generic_private_origin(pointer,0))continue;
    LPositionBuilderBefore(builder,in);
    Ref equivalent=private_equivalent(pointer,builder,ctx,0);
    if(!equivalent)return -1;
    // Keep the original load, including volatility, ordering, alignment and
    // metadata. Only its pointer operand changes to the proved private address.
    LSetOperand(in,0,equivalent);count++;
   }
 return count;
}
