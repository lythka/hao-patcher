/*
 * Darwin 27.0 Sandbox policy syscall 0x108 (rootless_verify_storage_class) for Tahoe's Sandbox, which
 * answers only 0x103-0x107. Shared by IntelCommpage (commpage.cpp) and the host harness
 * (tests/sandbox_storage_class_harness.c). The includer provides size_t and strcmp.
 *
 * 27.0 behaviour, read from 27.0's own arm64e Sandbox (kernelcache.release.vma2 of the 26A428 IPSW,
 * _syscall_rootless_verify_storage_class and _extract_storage_class_name_from_xattr):
 *   copyin 16 bytes {u64 fd, u64 class pointer}; class NULL: EINVAL; copyinstr the class (128 bytes with
 *   its NUL); fd not a sign-extended int: EBADF; vnode_getfromfd in the current context;
 *   mac_vnop_getxattr(vp, "com.apple.rootless", buf, 128, &n); n > 127: ERANGE; then the name test below.
 *   Every error of copyin, copyinstr, vnode_getfromfd and getxattr is returned as is.
 * The xattr is what Tahoe's own 0x105 (rootless_set_storage_class) writes: the class string without NUL.
 */
#ifndef SANDBOX_STORAGE_CLASS_H
#define SANDBOX_STORAGE_CLASS_H

enum { SANDBOX_VERIFY_STORAGE_CLASS = 0x108, SANDBOX_CLASS_MAX = 128 };
enum { SANDBOX_EBADF = 9, SANDBOX_EINVAL = 22, SANDBOX_ERANGE = 34 };

/* Storage-class name in an xattr value of n bytes (buf[n] is NUL): NULL if it has a ':'; else the text
 * after the first '/', or all of it; an empty name is "system". */
static inline const char *sandbox_storage_class_name(const char *buf, size_t n) {
    const char *name = buf;
    for (size_t i = 0; i < n; i++)
        if (buf[i] == ':') return 0;
    for (size_t i = 0; i < n; i++)
        if (buf[i] == '/') { name = buf + i + 1; break; }
    return *name ? name : "system";
}

/* 0 when the vnode's class (xattr value of n bytes, n <= 127, buf[n] NUL) is the requested one; a
 * requested "" matches "system". Otherwise EINVAL. */
static inline int sandbox_storage_class_verdict(const char *buf, size_t n, const char *requested) {
    const char *name = sandbox_storage_class_name(buf, n);
    if (!name) return SANDBOX_EINVAL;
    if (!strcmp(name, requested)) return 0;
    if (strcmp(name, "system")) return SANDBOX_EINVAL;
    return *requested ? SANDBOX_EINVAL : 0;
}

/* The u64 fd field as 27.0 accepts it: a sign-extended 32-bit value, else EBADF. */
static inline int sandbox_storage_class_fd(unsigned long long raw, int *fd) {
    if ((unsigned long long)(long long)(int)raw != raw) return SANDBOX_EBADF;
    *fd = (int)raw;
    return 0;
}

#endif
