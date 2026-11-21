// Build an offline entry redirect and fallback trampoline with Frida Gum's
// decoder/writer. No Frida code or library enters the resulting cache.
#include "frida-gum.h"
#include "gumx86relocator.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void check(int ok,const char *message) {
  if (!ok) { fprintf(stderr,"%s\n",message); exit(2); }
}
int main(int argc,char **argv) {
  check(argc==7,"usage: relocate input original-address wrapper-address trampoline-address patch-output trampoline-output");
  uint64_t original=strtoull(argv[2],NULL,0),wrapper=strtoull(argv[3],NULL,0),trampoline=strtoull(argv[4],NULL,0);
  uint8_t input[256],patch[256],code[1024];
  FILE *f=fopen(argv[1],"rb"); check(f!=NULL,"open source instructions");
  size_t size=fread(input,1,sizeof(input),f); fclose(f); check(size>=32,"truncated source instructions");
  gum_init_embedded();
  GumX86Writer entry,output;
  gum_x86_writer_init(&entry,patch); gum_x86_writer_set_target_cpu(&entry,GUM_CPU_AMD64); entry.pc=original;
  check(gum_x86_writer_put_jmp_address(&entry,wrapper),"entry destination is out of range");
  // An absolute jump embeds a pointer that cannot slide as ordinary text. The
  // image allocator must place this wrapper within rel32 reach of the entry.
  check(gum_x86_writer_offset(&entry)==5,"entry requires a non-position-independent jump");
  gum_x86_writer_init(&output,code); gum_x86_writer_set_target_cpu(&output,GUM_CPU_AMD64); output.pc=trampoline;
  GumX86Relocator relocator;
  gum_x86_relocator_init(&relocator,input,&output); relocator.input_pc=original;
  guint consumed=0;
  while (consumed<5) {
    consumed=gum_x86_relocator_read_one(&relocator,NULL);
    check(consumed && consumed<size,"cannot relocate complete entry instructions");
  }
  gum_x86_relocator_write_all(&relocator);
  check(gum_x86_writer_put_jmp_address(&output,original+consumed),"fallback destination is out of range");
  check(gum_x86_writer_flush(&output),"fallback label resolution failed");
  while (gum_x86_writer_offset(&entry)<consumed) gum_x86_writer_put_nop(&entry);
  check(gum_x86_writer_flush(&entry),"entry label resolution failed");
  f=fopen(argv[5],"wb"); check(f!=NULL,"open patch output");
  check(fwrite(patch,1,consumed,f)==consumed,"write patch output"); fclose(f);
  size_t length=gum_x86_writer_offset(&output);
  f=fopen(argv[6],"wb"); check(f!=NULL,"open trampoline output");
  check(fwrite(code,1,length,f)==length,"write trampoline output"); fclose(f);
  printf("{\"displaced\":%u,\"trampoline_bytes\":%zu}\n",consumed,length);
  gum_x86_relocator_clear(&relocator); gum_x86_writer_clear(&output); gum_x86_writer_clear(&entry);
  gum_deinit_embedded(); return 0;
}
