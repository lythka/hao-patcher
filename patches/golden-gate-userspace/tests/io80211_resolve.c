// resolve PATH NAMES_FILE: dlopen PATH (an IO80211) and print, for each symbol name in NAMES_FILE (one per line,
// without the leading underscore), where a lookup through PATH lands: "name image-leaf offset-from-image-base",
// or "name -" when it does not resolve. dlsym on the handle searches the library and its re-exports, as a two-level
// bind against IO80211 does. With DYLD_LIBRARY_PATH the re-exported /System paths load from a scratch folder.
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: resolve PATH NAMES_FILE\n"); return 2; }
    void *h = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!h) { fprintf(stderr, "%s\n", dlerror()); return 1; }
    FILE *f = fopen(argv[2], "r");
    if (!f) { perror(argv[2]); return 1; }
    char name[512];
    while (fgets(name, sizeof name, f)) {
        name[strcspn(name, "\n")] = 0;
        if (!*name) continue;
        void *p = dlsym(h, name);
        Dl_info info;
        if (!p || !dladdr(p, &info) || !info.dli_fname) { printf("%s -\n", name); continue; }
        const char *leaf = strrchr(info.dli_fname, '/');
        printf("%s %s %#lx\n", name, leaf ? leaf + 1 : info.dli_fname, (unsigned long)((uintptr_t)p - (uintptr_t)info.dli_fbase));
    }
    return 0;
}
