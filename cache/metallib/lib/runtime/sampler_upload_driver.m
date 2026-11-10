// Execute the actual stored driver's constant uploader on synthetic CPU data.
// No Metal device, command queue, GUI or GPU submission is created.
#import <Foundation/Foundation.h>
#include <mach-o/dyld.h>
#include "image_identity.h"
#include "sampler_abi.h"
int main(int argc,char **argv){@autoreleasepool{
 if(argc!=4)return 2;
 void *adapter=dlopen(argv[3],RTLD_NOW|RTLD_LOCAL);if(!adapter)return 9;
 int (*ready)(void)=dlsym(adapter,"compat_gpu_client_ready");if(!ready)return 10;
 if(!dlopen(argv[1],RTLD_NOW|RTLD_GLOBAL)){fprintf(stderr,"Metal: %s\n",dlerror());return 3;}
 NSError *error=nil;
 if(![[NSBundle bundleWithPath:@(argv[2])] loadAndReturnError:&error]){fprintf(stderr,"driver: %s\n",error.description.UTF8String);return 4;}
 if(!ready())return 5;
 const unsigned char *base=NULL;
 for(uint32_t i=0;i<_dyld_image_count();i++){
  const struct mach_header *header=_dyld_get_image_header(i);
  if(supported_image(header,"D5CE0008-CAE6-34E9-90E3-7F28F989A2CB"))base=(const void*)header;
 }
 if(!base)return 11;
 ptrdiff_t *sampler_state_offset=(void*)(base+0x21defa90);
 void (*sampler_upload)(void*,unsigned char*,unsigned,const void*)=(void*)(base+0x27c32);
 unsigned char context[0x680]={0},kernel[0x4b28]={0};
 uintptr_t manager[4]={0},nodes[5][6]={{0}};unsigned info[5][10]={{0}},curbe[6];
 memcpy(context+0x658,&(void*){kernel},8);memcpy(kernel+0x4b20,&(void*){manager},8);
 manager[1]=(uintptr_t)nodes[0];
 uint64_t keys[]={ (14ULL<<48)|(3ULL<<32), (14ULL<<48)|(3ULL<<32)|SAMPLER_AXIS_CHANNEL,
  (14ULL<<48)|(4ULL<<32)|SAMPLER_AXIS_CHANNEL, (15ULL<<48)|(3ULL<<32), (21ULL<<48)|(3ULL<<32)};
 for(unsigned i=0;i<5;i++){
  if(i<4)nodes[i][1]=(uintptr_t)nodes[i+1];nodes[i][4]=keys[i];nodes[i][5]=(uintptr_t)info[i];
  info[i][1]=i*4;info[i][7]=4;
 }
 unsigned char *sampler=calloc(1,(size_t)*sampler_state_offset+16);if(!sampler)return 6;
 const unsigned hw[]={0,1,2,4},expected[]={3,4,2,1};unsigned count=0;
 for(unsigned s=0;s<4;s++)for(unsigned t=0;t<4;t++)for(unsigned norm=0;norm<2;norm++)for(unsigned linear=0;linear<2;linear++){
  uint64_t state[2]={linear?((1ULL<<14)|(1ULL<<17)):0,((uint64_t)hw[s]<<38)|((uint64_t)hw[t]<<35)|((uint64_t)!norm<<42)};
  memcpy(sampler+*sampler_state_offset,state,16);
  for(unsigned i=0;i<6;i++)curbe[i]=0xdeadbeef;
  sampler_upload(context,(void*)curbe,3,&sampler);
  if(curbe[0]!=expected[s] || curbe[1]!=(expected[s]|(expected[t]<<4)) ||
     curbe[2]!=0xdeadbeef || curbe[3]!=(norm?8U:0U) || curbe[5]!=0xdeadbeef){
   fprintf(stderr,"case %u/%u/%u/%u: %x %x %x %x %x %x\n",s,t,norm,linear,curbe[0],curbe[1],curbe[2],curbe[3],curbe[4],curbe[5]);return 7;
  }
  count++;
 }
 sampler_upload(context,NULL,0,NULL); // Preserve the original null-CURBE early return.
 const void *empty=NULL;unsigned before[6];memcpy(before,curbe,sizeof before);
 sampler_upload(context,(void*)curbe,3,&empty);
 if(memcmp(before,curbe,sizeof before))return 8;
 free(sampler);printf("%u driver upload controls passed\n",count);return 0;
}}
