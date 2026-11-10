/* Select the preserved Apple slice only for an exact image-built executable.
 * This does not change validity checks, requirements, entitlements, or results.
 * identity_map.h is generated from the selected hao payload, never user input.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <CommonCrypto/CommonDigest.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <limits.h>
#include <string.h>
#include <stdlib.h>
#include <os/log.h>

struct identity { const char *path, *executable, *architecture; unsigned char hash[32]; };
#include "identity_map.h"

static int matches(const struct identity *entry) {
    int fd = open(entry->executable, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return 0;
    struct stat before, after, named;
    int ok = !fstat(fd, &before) && S_ISREG(before.st_mode);
    CC_SHA256_CTX ctx;
    CC_SHA256_Init(&ctx);
    unsigned char buf[65536], digest[32];
    ssize_t count = 0;
    while (ok && (count = read(fd, buf, sizeof buf)) > 0)
        CC_SHA256_Update(&ctx, buf, (CC_LONG)count);
    CC_SHA256_Final(digest, &ctx);
    ok = ok && count == 0 && !memcmp(digest, entry->hash, sizeof digest)
        && !fstat(fd, &after) && !lstat(entry->executable, &named)
        && before.st_dev == after.st_dev && before.st_ino == after.st_ino
        && before.st_size == after.st_size
        && before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec
        && before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec
        && after.st_dev == named.st_dev && after.st_ino == named.st_ino;
    close(fd);
    return ok;
}

OSStatus gg_identity_create(CFURLRef path, SecCSFlags flags, SecStaticCodeRef *out) {
    if (!out || flags != kSecCSDefaultFlags)
        return SecStaticCodeCreateWithPath(path, flags, out);
    char supplied[PATH_MAX], canonical[PATH_MAX];
    if (path && CFURLGetFileSystemRepresentation(path, true, (UInt8 *)supplied, sizeof supplied)
        && realpath(supplied, canonical)) {
        for (size_t i = 0; i < sizeof identities / sizeof identities[0]; i++) {
            const struct identity *entry = &identities[i];
            if (strcmp(canonical, entry->path) || !matches(entry)) continue;
            CFStringRef arch = CFStringCreateWithCString(NULL, entry->architecture, kCFStringEncodingUTF8);
            const void *keys[] = {kSecCodeAttributeArchitecture};
            const void *values[] = {arch};
            CFDictionaryRef attrs = CFDictionaryCreate(NULL, keys, values, 1,
                &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
            SecStaticCodeRef candidate = NULL;
            OSStatus status = SecStaticCodeCreateWithPathAndAttributes(path, flags, attrs, &candidate);
            CFRelease(attrs); CFRelease(arch);
            if (!status && candidate) {
                SecRequirementRef apple = NULL;
                status = SecRequirementCreateWithString(CFSTR("anchor apple"), 0, &apple);
                if (!status) status = SecStaticCodeCheckValidity(candidate, kSecCSDefaultFlags, apple);
                if (apple) CFRelease(apple);
                /* Validate the original signed bundle too. A recognized name or
                 * a valid arm slice attached to arbitrary x86 code is insufficient. */
                if (!status && matches(entry)) {
                    os_log(OS_LOG_DEFAULT, "gg-code-identity: verified original %{public}s slice for %{public}s", entry->architecture, entry->path);
                    *out = candidate; return errSecSuccess;
                }
                os_log(OS_LOG_DEFAULT, "gg-code-identity: original signature rejected for %{public}s status=%d", entry->path, (int)status);
                CFRelease(candidate);
            }
            break;
        }
    }
    return SecStaticCodeCreateWithPath(path, flags, out);
}

#ifndef IDENTITY_DIRECT_TEST
__attribute__((used, section("__DATA,__interpose"))) static const struct {
    const void *replacement, *original;
} interpose[] = {{(const void *)gg_identity_create, (const void *)SecStaticCodeCreateWithPath}};
#endif
