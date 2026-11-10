// Loaded by gpu-client.dylib before app GPU work starts (cold and cached runs).
// The old driver uploads S alone. Add a separate, tagged constant containing S/T;
// the original S constant and hardware sampler state remain untouched.
#include <mach-o/dyld.h>
#include <mach/mach.h>
#include <sys/mman.h>
#include <unistd.h>
#include "sampler_abi.h"
#define SAMPLER_DRIVER_UUID "D5CE0008-CAE6-34E9-90E3-7F28F989A2CB"
static int sampler_upload_ready;
static ptrdiff_t *sampler_state_offset;
static void (*sampler_upload_original)(void*,unsigned char*,unsigned,const void*);
static unsigned (*sampler_upload_scalar)(void*,unsigned char*,uint64_t,unsigned,unsigned);
static unsigned sampler_address_mode(unsigned hardware){
 static const unsigned convert[8]={3,4,2,0,1,2,0,0};
 return convert[hardware&7];
}
static void sampler_upload(void *context,unsigned char *curbe,unsigned index,const void *argument){
 sampler_upload_original(context,curbe,index,argument);
 if(!curbe)return;
 const unsigned char *sampler=*(const unsigned char*const*)argument;
 if(!sampler)return;
 uint64_t state;memcpy(&state,sampler+*sampler_state_offset+8,sizeof state);
 unsigned s=sampler_address_mode((unsigned)(state>>38));
 unsigned t=sampler_address_mode((unsigned)(state>>35));
 uint64_t key=(14ULL<<48)|((uint64_t)index<<32)|SAMPLER_AXIS_CHANNEL;
 sampler_upload_scalar(context,curbe,key,s|(t<<4),0);
}
static void sampler_jump(unsigned char code[14],const void *target){
 const unsigned char op[6]={0xff,0x25,0,0,0,0};memcpy(code,op,6);memcpy(code+6,&target,8);
}
static void sampler_driver_added(const struct mach_header *header,intptr_t slide){
 (void)slide;
 unsigned char *base=(void*)header,*entry=base+0x27c32;
 if(sampler_upload_ready || !supported_image(header,SAMPLER_DRIVER_UUID))return;
 // Exactly fourteen bytes of register-save instructions, with no relative data.
 const unsigned char prologue[14]={0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,0x41,0x55,0x41,0x54,0x53,0x50};
 if(memcmp(entry,prologue,sizeof prologue))return;
 size_t page=(size_t)getpagesize();
 unsigned char *trampoline=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
 if(trampoline==MAP_FAILED)return;
 memcpy(trampoline,entry,14);sampler_jump(trampoline+14,entry+14);
 if(mprotect(trampoline,page,PROT_READ|PROT_EXEC)){munmap(trampoline,page);return;}
 uintptr_t start=(uintptr_t)entry&~(page-1);
 if(mach_vm_protect(mach_task_self(),start,page,FALSE,VM_PROT_READ|VM_PROT_WRITE|VM_PROT_COPY)!=KERN_SUCCESS){munmap(trampoline,page);return;}
 sampler_upload_original=(void*)trampoline;
 sampler_upload_scalar=(void*)(base+0x27650);
 sampler_state_offset=(void*)(base+0x21defa90);
 unsigned char patch[14];sampler_jump(patch,(void*)sampler_upload);memcpy(entry,patch,14);
 if(mach_vm_protect(mach_task_self(),start,page,FALSE,VM_PROT_READ|VM_PROT_EXECUTE)!=KERN_SUCCESS)abort();
 sampler_upload_ready=1;
 fprintf(stderr,"[compat] per-axis sampler upload installed\n");
}
__attribute__((constructor)) static void sampler_upload_init(void){
 const char *mode=getenv("COMPAT_BLOCK_READS");
 if(mode && !strcmp(mode,"per-axis-mip0") && sampler_abi_opted_in())_dyld_register_func_for_add_image(sampler_driver_added);
}
