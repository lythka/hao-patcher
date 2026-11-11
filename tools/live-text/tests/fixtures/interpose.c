#include <dlfcn.h>
#include <string.h>
static int replacement(void) { return 42; }
static void *hook(void *h, const char *n);
__attribute__((used,section("__DATA,__interpose"))) static struct {const void *replacement,*replacee;} pair = {hook,dlsym};
static void *hook(void *h, const char *n) { void *r=((void *(*)(void *,const char *))pair.replacee)(h,n); return r&&!strcmp(n,"marker") ? (void *)replacement:r; }
