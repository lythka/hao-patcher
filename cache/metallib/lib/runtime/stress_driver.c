// Concurrent core test: independent old-LLVM contexts, verified clone disposal.
#include "compat.c"
#include <assert.h>
static const char *input;
static size_t input_size;
static char *(*print_string)(Ref);
static _Atomic unsigned completed;
static void *worker(void *unused){
 (void)unused;
 for(unsigned i=0;i<100;i++){
  Ref ctx=LContextCreate(),original=0;char *error=0;
  Ref buffer=LCreateMemoryBufferWithMemoryRangeCopy(input,input_size,"concurrent-core");
  assert(!LParseIRInContext(ctx,buffer,&original,&error));
  char *before=print_string(original);
  Ref used=compat_prepare(original);assert(used!=original);
  char *after=print_string(original);assert(!strcmp(before,after));
  LDisposeMessage(before);LDisposeMessage(after);
  assert(!LVerifyModule(used,2,&error));if(error)LDisposeMessage(error);
  assert(!remaining_bad_pattern(used));
  compat_finish(original,used);LDisposeModule(original);LContextDispose(ctx);
  atomic_fetch_add(&completed,1);
 }
 return 0;
}
int main(int argc,char **argv){
 if(argc!=3)return 2;
 void *handle=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);assert(handle && initialize(handle));
 assert(!supported_image((void *)main,COMPAT_PLUGIN_UUID));
 print_string=lookup(handle,"LLVMPrintModuleToString");assert(print_string);
 FILE *f=fopen(argv[2],"rb");assert(f);fseek(f,0,SEEK_END);input_size=(size_t)ftell(f);rewind(f);
 char *data=malloc(input_size+1);assert(data && fread(data,1,input_size,f)==input_size);fclose(f);data[input_size]=0;input=data;
 pthread_t threads[4];for(int i=0;i<4;i++)assert(!pthread_create(&threads[i],0,worker,0));
 for(int i=0;i<4;i++)assert(!pthread_join(threads[i],0));
 assert(atomic_load(&completed)==400);free(data);
 puts("PASS: 400 verified clones across 4 concurrent LLVM contexts; originals unchanged; clones and contexts disposed; wrong image UUID refused");
}
