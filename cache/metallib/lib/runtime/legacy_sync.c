// Runtime counterpart of lib/legacy_sync.py. Preserve flags, scope, values and
// volatility; remove only a proved redundant order/fence operand. Work on the
// caller-owned clone. Unrepresentable forms leave the original module unchanged.
typedef struct {unsigned arity,drop;int barrier,compare;} SyncForm;
static int sync_form(Ref f,SyncForm *form){
 const char *name=LGetValueName(f);SyncForm v={0};
 if(!strcmp(name,"air.wg.barrier") || !strcmp(name,"air.simdgroup.barrier")){
  v.arity=3;v.drop=1;v.barrier=1;
 }else{
  const char *p;
  if(!strncmp(name,"air.atomic.global.",18))p=name+18;
  else if(!strncmp(name,"air.atomic.local.",17))p=name+17;
  else return 0;
  static const char *ops[]={"load","store","exchange","cmpxchg.weak","cmpxchg.strong",
   "add","sub","and","or","xor","min","max"};
  int found=-1;
  for(unsigned i=0;i<sizeof ops/sizeof *ops;i++){
   size_t n=strlen(ops[i]);if(strncmp(p,ops[i],n)||p[n]!='.')continue;
   const char *type=p+n+1;
   if(i>=5 && (type[0]=='u'||type[0]=='s') && type[1]=='.')type+=2;
   if(!strcmp(type,"i32")||!strcmp(type,"i64")||!strcmp(type,"f32")){found=(int)i;break;}
  }
  if(found<0)return 0;
  v.compare=found==3||found==4;v.arity=v.compare?8:found==0?5:6;v.drop=v.arity-2;
 }
 if(!LIsDeclaration(f) || LCountParams(f)!=v.arity)return 0;
 if(form)*form=v;return 1;
}
static int sync_constant(Ref value,long long expected){
 return LIsAConstantInt(value) && LGetTypeKind(LTypeOf(value))==int_kind &&
        LGetIntTypeWidth(LTypeOf(value))==32 && LConstIntGetSExtValue(value)==expected;
}
static int legacy_sync_present(Ref module){
 for(Ref f=LGetFirstFunction(module);f;f=LGetNextFunction(f))if(sync_form(f,0))return 1;
 return 0;
}
static int sync_supported(Ref f,SyncForm v,Ref ctx){
 Ref type=LGetElementType(LTypeOf(f));if(LIsFunctionVarArg(type))return 0;
 unsigned first=v.barrier?0:v.arity-4;
 for(unsigned i=first;i<v.arity;i++){
  Ref wanted=!v.barrier && i==v.arity-1?LInt1TypeInContext(ctx):LInt32TypeInContext(ctx);
  if(LTypeOf(LGetParam(f,i))!=wanted)return 0;
 }
 if(v.barrier && LGetReturnType(type)!=LVoidTypeInContext(ctx))return 0;
 for(Ref use=LGetFirstUse(f);use;use=LGetNextUse(use)){
  Ref call=LGetUser(use);
  if(!LIsACallInst(call)||LGetCalledValue(call)!=f||LGetNumOperands(call)!=v.arity+1)return 0;
  unsigned occurrences=0;for(unsigned i=0;i<v.arity+1;i++)occurrences+=LGetOperand(call,i)==f;
  if(occurrences!=1)return 0;
  char *text=LPrintValueToString(call);if(!text)return 0;
  int unsafe=unquoted(text,"musttail ")!=0 || unquoted(text,"notail ")!=0 || unquoted(text," [")!=0;
  LDisposeMessage(text);if(unsafe)return 0;
  if(v.barrier){if(!sync_constant(LGetOperand(call,1),5))return 0;}
  else if(!sync_constant(LGetOperand(call,v.arity-4),0)||
          !sync_constant(LGetOperand(call,v.drop),0)||
          (v.compare&&!sync_constant(LGetOperand(call,v.arity-5),0)))return 0;
 }
 return 1;
}
static int sync_attributes(Ref from,Ref to,unsigned oldindex,unsigned newindex,int call,Ref ctx){
 // Pinned LLVM CountAtIndex dereferences null for an empty attribute slot.
 // A temporary marker on the clone makes enumeration safe. Remove it before
 // copying, retaining every original AttributeRef including arbitrary keys.
 static const char key[]="hao.compat.enumerate.attributes";
 unsigned length=sizeof key-1;
 Ref existing=call?LGetCallSiteStringAttribute(from,oldindex,key,length):LGetStringAttributeAtIndex(from,oldindex,key,length);
 if(existing)return 0;
 Ref marker=LCreateStringAttribute(ctx,key,length,"",0);
 if(call)LAddCallSiteAttribute(from,oldindex,marker);else LAddAttributeAtIndex(from,oldindex,marker);
 unsigned n=call?LGetCallSiteAttributeCount(from,oldindex):LGetAttributeCountAtIndex(from,oldindex);
 Ref *attrs=calloc(n,sizeof *attrs);
 if(attrs){
  if(call)LGetCallSiteAttributes(from,oldindex,attrs);else LGetAttributesAtIndex(from,oldindex,attrs);
 }
 if(call)LRemoveCallSiteStringAttribute(from,oldindex,key,length);else LRemoveStringAttributeAtIndex(from,oldindex,key,length);
 if(!attrs)return 0;
 for(unsigned i=0;i<n;i++)if(attrs[i]!=marker){
  if(call)LAddCallSiteAttribute(to,newindex,attrs[i]);else LAddAttributeAtIndex(to,newindex,attrs[i]);
 }
 free(attrs);return 1;
}
static int sync_all_attributes(Ref from,Ref to,SyncForm v,int call,Ref ctx){
 if(!sync_attributes(from,to,~0U,~0U,call,ctx)||!sync_attributes(from,to,0,0,call,ctx))return 0;
 for(unsigned i=0,j=0;i<v.arity;i++)if(i!=v.drop){
  if(!sync_attributes(from,to,i+1,++j,call,ctx))return 0;
 }
 return 1;
}
static int rewrite_legacy_sync(Ref module,Ref ctx,Ref builder){
 // Validate all selected uses before changing any signature.
 for(Ref f=LGetFirstFunction(module);f;f=LGetNextFunction(f)){
  SyncForm v;if(sync_form(f,&v)&&!sync_supported(f,v,ctx)){
   fprintf(stderr,"[compat] unrepresentable legacy synchronization: %s\n",LGetValueName(f));return -1;
  }
 }
 int changed=0;
 for(Ref f=LGetFirstFunction(module),next;f;f=next){
  next=LGetNextFunction(f);SyncForm v;if(!sync_form(f,&v))continue;
  char *name=strdup(LGetValueName(f));if(!name)return -1;
  Ref params[8];unsigned n=0;
  for(unsigned i=0;i<v.arity;i++)if(i!=v.drop)params[n++]=LTypeOf(LGetParam(f,i));
  Ref type=LFunctionType(LGetReturnType(LGetElementType(LTypeOf(f))),params,n,0);
  LSetValueName(f,"compat.old.sync");Ref replacement=LAddFunction(module,name,type);free(name);
  LSetFunctionCallConv(replacement,LGetFunctionCallConv(f));LSetLinkage(replacement,LGetLinkage(f));
  int attrs_ok=sync_all_attributes(f,replacement,v,0,ctx);
  if(!attrs_ok)return -1;
  for(Ref use=LGetFirstUse(f);use;use=LGetFirstUse(f)){
   Ref call=LGetUser(use),args[8];n=0;
   for(unsigned i=0;i<v.arity;i++)if(i!=v.drop)args[n++]=LGetOperand(call,i);
   LPositionBuilderBefore(builder,call);Ref repaired=LBuildCall(builder,replacement,args,n,"");
   LSetInstructionCallConv(repaired,LGetInstructionCallConv(call));LSetTailCall(repaired,LIsTailCall(call));
   attrs_ok=sync_all_attributes(call,repaired,v,1,ctx);
   if(!attrs_ok||!copy_metadata(call,repaired,ctx))return -1;
   LReplaceAllUsesWith(call,repaired);LInstructionEraseFromParent(call);
  }
  LDeleteFunction(f);changed++;
 }
 return changed;
}

