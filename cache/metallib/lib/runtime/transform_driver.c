// CPU/IR test driver: compile in this implementation to test the actual C core.
#include "compat.c"
int main(int argc,char **argv){
 if(argc!=4)return 2;
 void *handle=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);
 if(!handle){fprintf(stderr,"dlopen: %s\n",dlerror());return 3;}
 if(!initialize(handle))return 4;
 FILE *f=fopen(argv[2],"rb");if(!f)return 5;
 fseek(f,0,SEEK_END);long size=ftell(f);rewind(f);
 char *text=malloc((size_t)size+1);if(!text)return 6;
 if(fread(text,1,(size_t)size,f)!=(size_t)size)return 7;
 fclose(f);text[size]=0;
 Ref ctx=LContextCreate(),original=0;char *error=0;
 Ref buffer=LCreateMemoryBufferWithMemoryRangeCopy(text,(size_t)size,"runtime-test");free(text);
 if(LParseIRInContext(ctx,buffer,&original,&error)){fprintf(stderr,"parse: %s\n",error?error:"-");return 8;}
 char *(*print_string)(Ref)=lookup(handle,"LLVMPrintModuleToString");
 char *before=print_string(original);
 // IR-only driver: exercise the rewrite without executing IGIL code generation.
 block_query_ready=1;
 Ref used=compat_prepare(original);
 char *after=print_string(original);
 int equal=!strcmp(before,after);LDisposeMessage(before);LDisposeMessage(after);
 int valid=!LVerifyModule(used,2,&error);if(error)LDisposeMessage(error);error=0;
 int bad=LPrintModuleToFile(used,argv[3],&error);if(error)LDisposeMessage(error);
 printf("{\"changed\":%s,\"original_unchanged\":%s,\"valid\":%s,\"remaining_pattern\":%s,\"remaining_null\":%s}\n",used!=original?"true":"false",equal?"true":"false",valid?"true":"false",remaining_bad_pattern(used)?"true":"false",null_remaining(used)?"true":"false");
 compat_finish(original,used);LDisposeModule(original);LContextDispose(ctx);
 return !equal || !valid || bad;
}
