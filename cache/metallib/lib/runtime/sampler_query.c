#include <stdint.h>
#include "sampler_abi.h"
#include <stddef.h>
#include <dlfcn.h>
typedef struct {void *replacement,*replacee;} Interpose;
static int (*block_query_active)(void);
// Pinned IGIL sampler-query extension, used only during an opted-in block clone.
// Bound is.normalized queries return zero for pixel coordinates or packed S/T
// address enums for normalized coordinates. The separate tagged constant is
// supplied by sampler_upload.c; the driver's ordinary S constant is unchanged.
// IGIL's ordinary sample lowering tests !=0, preserving its coordinate predicate.
// Existing input declarations of the intrinsic are refused by block_reads.c.
// The two ABI layouts and call-site offsets below are specific to the UUID.
typedef struct {void *node;unsigned result,pad;} QValue;
typedef struct {unsigned simple,pad;void *extended;} QType;
extern QValue llvm_getnode(void*,unsigned,const void*,QType,QValue,QValue,QValue) __asm__("__ZN4llvm12SelectionDAG7getNodeEjRKNS_5SDLocENS_3EVTENS_7SDValueES5_S5_");
extern QValue llvm_getregister(void*,unsigned,QType) __asm__("__ZN4llvm12SelectionDAG11getRegisterEjNS_3EVTE");
extern QValue llvm_getconstant(void*,uint64_t,const void*,QType,int,int) __asm__("__ZN4llvm12SelectionDAG11getConstantEyRKNS_5SDLocENS_3EVTEbb");
void *compat_query_return,*compat_address_return;
static unsigned (*compat_alloc_constant)(void*,void*,unsigned,unsigned,unsigned);
void *compat_real_getregister=(void*)llvm_getregister;
static __thread QValue address_value;
QValue compat_register_impl(void *dag,unsigned reg,QType type,void *surface,unsigned index){
 if(!block_query_active())return llvm_getregister(dag,reg,type);
 unsigned address=compat_alloc_constant(surface,dag,23,index,SAMPLER_AXIS_CHANNEL);
 address_value=llvm_getregister(dag,address,type);
 return llvm_getregister(dag,reg,type);
}
static QValue compat_query_getnode(void *dag,unsigned opcode,const void *loc,QType type,QValue a,QValue b,QValue c){
 QValue normal=llvm_getnode(dag,opcode,loc,type,a,b,c);
 if(__builtin_return_address(0)!=compat_query_return || !block_query_active())return normal;
 QValue zero=llvm_getconstant(dag,0,loc,type,0,0);
 return llvm_getnode(dag,111,loc,type,normal,address_value,zero);
}
extern void compat_query_getregister(void);
static Interpose query_pairs[]={ {(void*)compat_query_getnode,(void*)llvm_getnode}, {(void*)compat_query_getregister,(void*)llvm_getregister} };
#include "sampler_imports.c"
int sampler_query_install(void *igil,void *base,int (*active)(void)){
 block_query_active=active;
 compat_alloc_constant=dlsym(igil,"_ZN4llvm19IGILConstRegSurface24AllocateConstantRegisterERNS_12SelectionDAGENS_18IGILAnnotationTypeEjj");
 if(!compat_alloc_constant)return 0;
 compat_query_return=(char*)base+0xb257c;
 compat_address_return=(char*)base+0xb24d0;
 return query_bind_imports(base,query_pairs);
}
