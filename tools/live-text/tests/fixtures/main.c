#include <dlfcn.h>
#include <stdio.h>
int main(int c,char **v) { if(c!=3)return 2; void *h=dlopen(v[1],RTLD_NOW|RTLD_LOCAL); if(!h){puts(dlerror());return 3;} int(*r)(const char *)=dlsym(h,"run"); if(!r)return 4; int n=r(v[2]);printf("result=%d\n",n);return 0; }
