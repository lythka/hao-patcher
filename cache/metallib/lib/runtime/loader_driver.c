// Isolated loading controls. No GPU driver or compiler frontend is required.
#define COMPAT_SAMPLER_QUERY 1
#include "compat.c"
#include <mach-o/dyld.h>
int main(int argc,char **argv){
 if(argc!=3)return 2;
 if(!strcmp(argv[1],"compiler")){
  void *llvm=dlopen(argv[2],RTLD_NOW|RTLD_LOCAL);if(!llvm)return 3;
  // Match the real hook's post-specialization ordering.
  if(!initialize(llvm) || !query_install(llvm))return 4;
  if(lookup(RTLD_DEFAULT,"compat_gpu_client_ready"))return 5;
  for(uint32_t i=0;i<_dyld_image_count();i++)if(strstr(_dyld_get_image_name(i),"GraphicsMTLDriver"))return 6;
  puts("compiler query installed without GPU client or driver");return 0;
 }
 if(!strcmp(argv[1],"client")){
  void *client=dlopen(argv[2],RTLD_NOW|RTLD_LOCAL);if(!client)return 7;
  const char *(*abi)(void)=lookup(client,"compat_gpu_client_abi");
  int (*ready)(void)=lookup(client,"compat_gpu_client_ready");
  if(!abi || strcmp(abi(),SAMPLER_CLIENT_ABI) || !ready || ready())return 8;
  for(uint32_t i=0;i<_dyld_image_count();i++){
   const char *name=_dyld_get_image_name(i);
   if(strstr(name,"LLVM") || strstr(name,"sampler-query") || strstr(name,"MTLCompiler"))return 9;
  }
  puts("GPU client loaded without compiler or LLVM; awaiting driver");return 0;
 }
 return 10;
}
