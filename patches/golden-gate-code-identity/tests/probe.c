#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL, (const UInt8 *)argv[1], strlen(argv[1]), false);
    SecStaticCodeRef code = NULL;
    SecRequirementRef apple = NULL;
    OSStatus status = SecStaticCodeCreateWithPath(url, 0, &code);
    if (!status) status = SecRequirementCreateWithString(CFSTR("anchor apple"), 0, &apple);
    if (!status) status = SecStaticCodeCheckValidity(code, 0, apple);
    printf("%d\n", (int)status);
    if (apple) CFRelease(apple);
    if (code) CFRelease(code);
    CFRelease(url);
    return status ? 1 : 0;
}
