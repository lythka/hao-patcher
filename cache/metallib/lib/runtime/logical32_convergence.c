// Workgroup convergence check for the reviewed specialization. A new collective
// must not be controlled by a varying branch. Postdominance accounts for the
// reconvergence after the lane-strided histogram loop; SSA uniformity accounts
// for the outer ballot-driven loops. Unknown calls/loads are varying.
typedef struct {Ref value;unsigned block;int uniform;} LogicalValue;
typedef struct {
 Ref *blocks;unsigned n;
 unsigned char *postdom,*control;
 LogicalValue *values;size_t count;
 Ref kernel;
} LogicalGraph;
static int logical32_order(const void *a,const void *b){
 uintptr_t x=(uintptr_t)((const LogicalValue*)a)->value,y=(uintptr_t)((const LogicalValue*)b)->value;
 return x<y?-1:x>y;
}
static LogicalValue *logical32_value(LogicalGraph *g,Ref value){
 LogicalValue key={.value=value};return bsearch(&key,g->values,g->count,sizeof key,logical32_order);
}
static int logical32_uniform(LogicalGraph *g,Ref value){
 if(LIsUndef(value))return 0;
 if(LIsAConstant(value))return 1;
 LogicalValue *v=logical32_value(g,value);return v && v->uniform;
}
static unsigned logical32_block_index(LogicalGraph *g,Ref block){
 for(unsigned i=0;i<g->n;i++)if(g->blocks[i]==block)return i;
 return g->n;
}
static int logical32_control_uniform(LogicalGraph *g,unsigned block){
 for(unsigned j=0;j<g->n;j++)if(g->control[(size_t)block*g->n+j]){
  Ref term=LGetBasicBlockTerminator(g->blocks[j]);
  if(!logical32_uniform(g,LGetOperand(term,0)))return 0;
 }
 return 1;
}
static int logical32_value_uniform(LogicalGraph *g,LogicalValue *v){
 Ref in=v->value;
 if(LIsAArgument(in))return in!=LGetParam(g->kernel,9);
 if(LIsAAllocaInst(in))return 0;
 if(LIsAPHINode(in)){
  unsigned n=LCountIncoming(in);int same=1;Ref first=LGetIncomingValue(in,0);
  for(unsigned i=0;i<n;i++){
   Ref value=LGetIncomingValue(in,i);if(!logical32_uniform(g,value))return 0;
   if(value!=first)same=0;
  }
  if(same)return 1;
  for(unsigned i=0;i<n;i++){
   unsigned b=logical32_block_index(g,LGetIncomingBlock(in,i));
   if(b==g->n || !logical32_control_uniform(g,b))return 0;
  }
  return 1;
 }
 if(LIsALoadInst(in)){
  Ref p=LGetOperand(in,0);
  return LGetPointerAddressSpace(LTypeOf(p))==2 && logical32_uniform(g,p);
 }
 unsigned n=LGetNumOperands(in);
 if(LIsACallInst(in)){
  Ref callee=LGetCalledValue(in);if(!LIsAFunction(callee))return 0;
  const char *name=LGetValueName(callee);
  if(!strcmp(name,"air.simd_ballot.i64"))return 1;
  if(!strcmp(name,"air.simd_broadcast.f32") || !strcmp(name,"air.simd_broadcast.u.i32"))
   return logical32_uniform(g,LGetOperand(in,1));
  const char *numeric[]={"air.ctz.i32", "air.max.s.i32", "air.min.s.i32", "llvm.fabs.f32",
   "air.fast_fmin.f32", "air.fast_fmax.f32", "air.fast_fmin.v4f32", "air.fast_fmax.v4f32",
   "air.fast_fabs.f32", "air.fast_pow.f32", "air.fast_round.f32", "air.fast_sqrt.f32",
   "air.convert.f.f32.s.i16", "air.convert.f.f32.s.i32", "air.convert.s.i16.f.f32",
   "air.convert.u.i16.f.f32", "air.convert.s.i32.f.f32"};
  int known=0;for(unsigned i=0;i<sizeof numeric/sizeof *numeric;i++)if(!strcmp(name,numeric[i]))known=1;
  if(!known)return 0;
  unsigned pure=LGetEnumAttributeKindForName("readnone",8);
  if(!LGetEnumAttributeAtIndex(callee,~0U,pure) || !strncmp(name,"air.simd",8))return 0;
  n--; // The callee is not a data operand.
 }
 if(in==LGetBasicBlockTerminator(g->blocks[v->block]))return 0;
 for(unsigned i=0;i<n;i++)if(!logical32_uniform(g,LGetOperand(in,i)))return 0;
 return 1;
}
static int logical32_converged(Ref kernel,Ref *intrinsics){
 LogicalGraph g={.kernel=kernel};int ok=0;
 for(Ref bb=LGetFirstBasicBlock(kernel);bb;bb=LGetNextBasicBlock(bb)){
  g.n++;for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in))g.count++;
 }
 g.count+=LCountParams(kernel);
 if(!g.n || g.n>4096)return 0;
 g.blocks=calloc(g.n,sizeof *g.blocks);g.values=calloc(g.count,sizeof *g.values);
 g.postdom=malloc((size_t)g.n*g.n);g.control=calloc((size_t)g.n*g.n,1);
 unsigned char *row=malloc(g.n);
 if(!g.blocks || !g.values || !g.postdom || !g.control || !row)goto done;
 unsigned b=0;size_t v=0;
 for(unsigned i=0;i<LCountParams(kernel);i++)g.values[v++]=(LogicalValue){LGetParam(kernel,i),0,1};
 for(Ref bb=LGetFirstBasicBlock(kernel);bb;bb=LGetNextBasicBlock(bb),b++){
  g.blocks[b]=bb;
  for(Ref in=LGetFirstInstruction(bb);in;in=LGetNextInstruction(in))g.values[v++]=(LogicalValue){in,b,1};
 }
 qsort(g.values,g.count,sizeof *g.values,logical32_order);
 memset(g.postdom,1,(size_t)g.n*g.n);
 for(unsigned i=0;i<g.n;i++){
  Ref term=LGetBasicBlockTerminator(g.blocks[i]);if(!term)goto done;
  unsigned n=LGetNumSuccessors(term);
  // This reviewed shader has branch/return control flow, not switches/indirectbr.
  if(n>2)goto done;
  if(!n){memset(g.postdom+(size_t)i*g.n,0,g.n);g.postdom[(size_t)i*g.n+i]=1;}
 }
 // The finite-CFG postdominator analysis below requires a reachable return.
 // Refuse closed cycles instead of treating their all-ones initialization as proof.
 memset(row,0,g.n);
 for(unsigned i=0;i<g.n;i++)if(!LGetNumSuccessors(LGetBasicBlockTerminator(g.blocks[i])))row[i]=1;
 int changed=1;
 while(changed){
  changed=0;
  for(unsigned i=0;i<g.n;i++)if(!row[i]){
   Ref term=LGetBasicBlockTerminator(g.blocks[i]);
   for(unsigned j=0;j<LGetNumSuccessors(term);j++){
    unsigned s=logical32_block_index(&g,LGetSuccessor(term,j));if(s==g.n)goto done;
    if(row[s]){row[i]=1;changed=1;break;}
   }
  }
 }
 for(unsigned i=0;i<g.n;i++)if(!row[i])goto done;
 changed=1;
 while(changed){
  changed=0;
  for(unsigned i=0;i<g.n;i++){
   Ref term=LGetBasicBlockTerminator(g.blocks[i]);unsigned n=LGetNumSuccessors(term);if(!n)continue;
   memset(row,1,g.n);
   for(unsigned k=0;k<n;k++){
    unsigned s=logical32_block_index(&g,LGetSuccessor(term,k));if(s==g.n)goto done;
    for(unsigned j=0;j<g.n;j++)row[j]&=g.postdom[(size_t)s*g.n+j];
   }
   row[i]=1;
   if(memcmp(row,g.postdom+(size_t)i*g.n,g.n)){changed=1;memcpy(g.postdom+(size_t)i*g.n,row,g.n);}
  }
 }
 for(unsigned i=0;i<g.n;i++){
  Ref term=LGetBasicBlockTerminator(g.blocks[i]);if(LGetNumSuccessors(term)!=2)continue;
  unsigned a=logical32_block_index(&g,LGetSuccessor(term,0)),b=logical32_block_index(&g,LGetSuccessor(term,1));
  for(unsigned j=0;j<g.n;j++)g.control[(size_t)j*g.n+i]=
   g.postdom[(size_t)a*g.n+j]!=g.postdom[(size_t)b*g.n+j];
 }
 changed=1;
 while(changed){
  changed=0;
  for(size_t i=0;i<g.count;i++)if(g.values[i].uniform && !logical32_value_uniform(&g,&g.values[i])){
   g.values[i].uniform=0;changed=1;
  }
 }
 for(unsigned i=0;i<5;i++)if(intrinsics[i])
  for(Ref use=LGetFirstUse(intrinsics[i]);use;use=LGetNextUse(use)){
   Ref call=LGetUser(use);unsigned b=logical32_block_index(&g,LGetInstructionParent(call));
   if(b==g.n || !logical32_control_uniform(&g,b)){
    fprintf(stderr,"[compat] logical32 varying control at %s\n",logical32_intrinsics[i]);goto done;
   }
   if((i==2 || i==3) && !logical32_uniform(&g,LGetOperand(call,1)))goto done;
  }
 // A lane-strided maximum is regroupable only against one uniform bound.
 if(intrinsics[5])for(Ref use=LGetFirstUse(intrinsics[5]);use;use=LGetNextUse(use)){
  Ref max=LGetUser(use),loop=LGetInstructionParent(max);
  Ref condition=LGetOperand(LGetBasicBlockTerminator(loop),0);
  if(!logical32_uniform(&g,LGetOperand(condition,1)))goto done;
 }
 ok=1;
done:
 free(row);free(g.blocks);free(g.values);free(g.postdom);free(g.control);return ok;
}
