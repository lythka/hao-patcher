/* Select the preserved Apple slice only for an exact image-built executable.
 * Requirements and entitlements are unchanged; validity of unlisted code is never altered.
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
#include <libproc.h>

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

static const struct identity *lookup(CFURLRef path) {
    char supplied[PATH_MAX], canonical[PATH_MAX];
    if (!path || !CFURLGetFileSystemRepresentation(path, true, (UInt8 *)supplied, sizeof supplied)
        || !realpath(supplied, canonical))
        return NULL;
    for (size_t i = 0; i < sizeof identities / sizeof identities[0]; i++)
        if (!strcmp(canonical, identities[i].path))
            return matches(&identities[i]) ? &identities[i] : NULL;
    return NULL;
}

/* The preserved original slice, accepted only if Apple signed it and the whole file is still the mapped one. */
static SecStaticCodeRef original_code(const struct identity *entry, CFURLRef path) {
    CFStringRef arch = CFStringCreateWithCString(NULL, entry->architecture, kCFStringEncodingUTF8);
    const void *keys[] = {kSecCodeAttributeArchitecture};
    const void *values[] = {arch};
    CFDictionaryRef attrs = CFDictionaryCreate(NULL, keys, values, 1,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    SecStaticCodeRef candidate = NULL;
    OSStatus status = SecStaticCodeCreateWithPathAndAttributes(path, kSecCSDefaultFlags, attrs, &candidate);
    CFRelease(attrs); CFRelease(arch);
    if (!status && candidate) {
        SecRequirementRef apple = NULL;
        status = SecRequirementCreateWithString(CFSTR("anchor apple"), 0, &apple);
        if (!status) status = SecStaticCodeCheckValidity(candidate, kSecCSDefaultFlags, apple);
        if (apple) CFRelease(apple);
        /* Validate the original signed bundle too. A recognized name or
         * a valid arm slice attached to arbitrary x86 code is insufficient. */
        if (!status && matches(entry)) return candidate;
    }
    os_log(OS_LOG_DEFAULT, "gg-code-identity: original signature rejected for %{public}s status=%d", entry->path, (int)status);
    if (candidate) CFRelease(candidate);
    return NULL;
}

OSStatus gg_identity_create(CFURLRef path, SecCSFlags flags, SecStaticCodeRef *out) {
    if (!out || flags != kSecCSDefaultFlags)
        return SecStaticCodeCreateWithPath(path, flags, out);
    const struct identity *entry = lookup(path);
    SecStaticCodeRef candidate = entry ? original_code(entry, path) : NULL;
    if (candidate) {
        os_log(OS_LOG_DEFAULT, "gg-code-identity: verified original %{public}s slice for %{public}s", entry->architecture, entry->path);
        *out = candidate; return errSecSuccess;
    }
    return SecStaticCodeCreateWithPath(path, flags, out);
}

/* Dynamic identity (a running guest, e.g. from an audit token) is substituted only in the
 * observers the map names, and only when the ordinary check failed on the requirement alone. */
static int dynamic_observer(void) {
    static int answer = -1;
    if (answer < 0) {
        char own[PROC_PIDPATHINFO_MAXSIZE], canonical[PATH_MAX];
        int found = 0;
        if (proc_pidpath(getpid(), own, sizeof own) > 0 && realpath(own, canonical))
            for (const char *const *observer = dynamic_observers; *observer; observer++)
                found |= !strcmp(canonical, *observer);
        answer = found;
    }
    return answer;
}

static OSStatus dynamic_identity(SecCodeRef code, SecCSFlags flags, SecRequirementRef requirement,
                                 CFErrorRef *errors, OSStatus status) {
    if (status != errSecCSReqFailed || !code || !requirement || flags != kSecCSDefaultFlags
        || CFGetTypeID(code) != SecCodeGetTypeID() || !dynamic_observer())
        return status;
    /* The guest must be validly signed and running exactly the on-disk code (the dynamic
     * check compares the kernel's cdhash with the file), and that file must be mapped. */
    if (SecCodeCheckValidity(code, kSecCSDefaultFlags, NULL)) return status;
    CFURLRef path = NULL;
    if (SecCodeCopyPath(code, kSecCSDefaultFlags, &path) || !path) return status;
    const struct identity *entry = lookup(path);
    SecStaticCodeRef candidate = entry ? original_code(entry, path) : NULL;
    CFRelease(path);
    if (!candidate) return status;
    /* The caller's own requirement, unchanged, against the original Apple slice. */
    OSStatus original = SecStaticCodeCheckValidity(candidate, kSecCSDefaultFlags, requirement);
    CFRelease(candidate);
    if (original || !matches(entry) || SecCodeCheckValidity(code, kSecCSDefaultFlags, NULL)) {
        os_log(OS_LOG_DEFAULT, "gg-code-identity: running %{public}s not accepted status=%d", entry->path, (int)original);
        return status;
    }
    os_log(OS_LOG_DEFAULT, "gg-code-identity: verified running %{public}s against its original %{public}s slice", entry->path, entry->architecture);
    if (errors && *errors) { CFRelease(*errors); *errors = NULL; }
    return errSecSuccess;
}

OSStatus gg_identity_check(SecCodeRef code, SecCSFlags flags, SecRequirementRef requirement) {
    return dynamic_identity(code, flags, requirement, NULL, SecCodeCheckValidity(code, flags, requirement));
}

OSStatus gg_identity_check_errors(SecCodeRef code, SecCSFlags flags, SecRequirementRef requirement, CFErrorRef *errors) {
    return dynamic_identity(code, flags, requirement, errors,
                            SecCodeCheckValidityWithErrors(code, flags, requirement, errors));
}

#ifndef IDENTITY_DIRECT_TEST
__attribute__((used, section("__DATA,__interpose"))) static const struct {
    const void *replacement, *original;
} interpose[] = {
    {(const void *)gg_identity_create, (const void *)SecStaticCodeCreateWithPath},
    {(const void *)gg_identity_check, (const void *)SecCodeCheckValidity},
    {(const void *)gg_identity_check_errors, (const void *)SecCodeCheckValidityWithErrors},
};
#endif
