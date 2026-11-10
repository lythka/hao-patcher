// Rebind two IGIL imports after the 31001 frontend has finished. Late dlopen
// interposing does not change these existing bindings.
// Only data pointers in the UUID-checked IGIL image are changed, never code.
#include <mach-o/loader.h>
#include <mach-o/nlist.h>
#include <mach/mach.h>
#include <string.h>
static int query_bind_imports(void *base,const Interpose pairs[2]){
 const struct mach_header_64 *h=base;
 const struct symtab_command *symbols=NULL;
 const struct dysymtab_command *indirect=NULL;
 uintptr_t slide=0,linkedit=0;
 const unsigned char *commands=(const void*)(h+1),*p=commands;
 for(unsigned i=0;i<h->ncmds;i++){
  const struct load_command *lc=(const void*)p;
  if(lc->cmd==LC_SYMTAB)symbols=(const void*)p;
  if(lc->cmd==LC_DYSYMTAB)indirect=(const void*)p;
  if(lc->cmd==LC_SEGMENT_64){const struct segment_command_64 *s=(const void*)p;
   if(!strcmp(s->segname,"__TEXT"))slide=(uintptr_t)base-s->vmaddr;
   if(!strcmp(s->segname,"__LINKEDIT"))linkedit=s->vmaddr-s->fileoff;
  }
  p+=lc->cmdsize;
 }
 if(!symbols || !indirect || !linkedit)return 0;
 linkedit+=slide;
 const struct nlist_64 *names=(const void*)(linkedit+symbols->symoff);
 const char *strings=(const void*)(linkedit+symbols->stroff);
 const unsigned *indices=(const void*)(linkedit+indirect->indirectsymoff);
 const char *wanted[]={"__ZN4llvm12SelectionDAG7getNodeEjRKNS_5SDLocENS_3EVTENS_7SDValueES5_S5_","__ZN4llvm12SelectionDAG11getRegisterEjNS_3EVTE"};
 unsigned found=0;p=commands;
 for(unsigned i=0;i<h->ncmds;i++){
  const struct load_command *lc=(const void*)p;
  if(lc->cmd==LC_SEGMENT_64){const struct segment_command_64 *s=(const void*)p;
   const struct section_64 *sections=(const void*)(s+1);
   for(unsigned j=0;j<s->nsects;j++){
    const struct section_64 *section=sections+j;unsigned type=section->flags&SECTION_TYPE;
    if(type!=S_LAZY_SYMBOL_POINTERS && type!=S_NON_LAZY_SYMBOL_POINTERS)continue;
    void **slots=(void*)(slide+section->addr);
    for(unsigned k=0;k<section->size/sizeof(void*);k++){
     unsigned index=indices[section->reserved1+k];if(index&(INDIRECT_SYMBOL_LOCAL|INDIRECT_SYMBOL_ABS))continue;
     const char *name=strings+names[index].n_un.n_strx;
     for(unsigned n=0;n<2;n++)if(!strcmp(name,wanted[n])){
      // Old IGIL's import slots are writable __DATA. Refuse another layout;
      // the UUID check already pins the image this recipe was measured against.
      if(!(s->initprot&VM_PROT_WRITE))return 0;
      slots[k]=pairs[n].replacement;found|=1U<<n;
     }
    }
   }
  }
  p+=lc->cmdsize;
 }
 return found==3;
}
