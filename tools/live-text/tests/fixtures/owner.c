#include <dlfcn.h>
int run(const char *p) { void *h=dlopen(p,RTLD_NOW|RTLD_LOCAL); if(!h)return -1; int(*f)(void)=dlsym(h,"marker"); return f?f():-2; }
