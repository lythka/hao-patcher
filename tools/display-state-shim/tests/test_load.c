// The shim loads in an x86_64 process and binds its CoreDisplay import (dlopen: interposing itself applies
// only to images loaded at launch, so this checks load and binding, not the interposer).
#include <dlfcn.h>
#include <stdio.h>
int main(int argc, char **argv)
{
    void *h = dlopen(argc > 1 ? argv[1] : "out/dss_shim.dylib", RTLD_NOW);
    printf("%s: dlopen %s\n", h ? "PASS" : "FAIL", h ? "ok" : dlerror());
    return !h;
}
