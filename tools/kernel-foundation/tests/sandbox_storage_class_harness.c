/* Host harness for src/sandbox_storage_class.h, loaded with ctypes by tests/test_sandbox_storage_class.py:
 * the same functions IntelCommpage runs for Sandbox call 0x108. */
#include <stddef.h>
#include <string.h>
#include "../src/sandbox_storage_class.h"

/* Name offset in buf, -1 for NULL, -2 for the built-in "system". */
long harness_name(const char *buf, size_t n) {
    const char *name = sandbox_storage_class_name(buf, n);
    if (!name) return -1;
    if (name < buf || name > buf + n) return -2;
    return name - buf;
}

int harness_verdict(const char *buf, size_t n, const char *requested) {
    return sandbox_storage_class_verdict(buf, n, requested);
}

int harness_fd(unsigned long long raw, int *fd) {
    return sandbox_storage_class_fd(raw, fd);
}
