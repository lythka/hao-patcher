// lcwrap: sits at Language Chooser.app/Contents/MacOS/Language Chooser; Apple's binary is next to it as
// "Language Chooser.real". Runs Apple's binary with the same arguments, with lcfast.dylib (same folder) inserted when it
// is there (see lcfast.m). A missing dylib = Apple's binary unchanged.
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char self[PATH_MAX], real[PATH_MAX + 8], lib[PATH_MAX + 16];
    uint32_t n = sizeof self;
    if (_NSGetExecutablePath(self, &n) != 0)
        strcpy(self, "/System/Library/CoreServices/Language Chooser.app/Contents/MacOS/Language Chooser");
    snprintf(real, sizeof real, "%s.real", self);
    snprintf(lib, sizeof lib, "%s", self);
    char *slash = strrchr(lib, '/');
    if (slash) strcpy(slash + 1, "lcfast.dylib");
    if (access(lib, R_OK) == 0) setenv("DYLD_INSERT_LIBRARIES", lib, 1);
    execv(real, argv);
    perror(real);
    return 1;
}
