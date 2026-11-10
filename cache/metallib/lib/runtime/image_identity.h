// Shared Mach-O identity check; no LLVM or driver loading.
#include <dlfcn.h>
#include <stdint.h>
#include <string.h>
#include <mach-o/loader.h>
#include <uuid/uuid.h>
static int supported_image(const void *address,const char *expected){
 Dl_info info;if(!dladdr(address,&info) || !info.dli_fbase)return 0;
 const struct mach_header_64 *header=info.dli_fbase;
 if(header->magic!=MH_MAGIC_64)return 0;
 const unsigned char *cursor=(const unsigned char *)(header+1),*end=cursor+header->sizeofcmds;
 for(uint32_t i=0;i<header->ncmds;i++){
  if((size_t)(end-cursor)<sizeof(struct load_command))return 0;
  const struct load_command *command=(const void *)cursor;
  if(command->cmdsize<sizeof *command || (size_t)(end-cursor)<command->cmdsize)return 0;
  if(command->cmd==LC_UUID && command->cmdsize==sizeof(struct uuid_command)){
   uuid_t uuid;if(uuid_parse(expected,uuid))return 0;
   return !memcmp(((const struct uuid_command *)command)->uuid,uuid,16);
  }
  cursor+=command->cmdsize;
 }
 return 0;
}
