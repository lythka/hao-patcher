#include <dlfcn.h>
#ifndef HELPER
#error HELPER
#endif
__attribute__((constructor)) static void init(void) { dlopen(HELPER,RTLD_NOW|RTLD_GLOBAL); }
