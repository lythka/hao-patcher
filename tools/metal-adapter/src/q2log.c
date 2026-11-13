// DYLD_INSERT_LIBRARIES helper (scratch only): echo os_log fault/error messages whose format starts with "LLVM ERROR"
// or that come from MTLCompiler, decoding %s arguments from the os_log buffer (private strings included), to stderr.
#include <os/log.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <dlfcn.h>
typedef struct { const void *r, *o; } ip_t;
#define INTERPOSE(rep, orig) __attribute__((used)) static const ip_t _ip_##rep __attribute__((section("__DATA,__interpose"))) = {(const void *)rep, (const void *)orig};
extern void _os_log_fault_impl(void *dso, os_log_t log, os_log_type_t type, const char *fmt, uint8_t *buf, uint32_t size);
extern void _os_log_error_impl(void *dso, os_log_t log, os_log_type_t type, const char *fmt, uint8_t *buf, uint32_t size);
static void echo(const char *kind, void *dso, const char *fmt, const uint8_t *buf, uint32_t size) {
  Dl_info i; const char *who = (dladdr(dso, &i) && i.dli_fname) ? strrchr(i.dli_fname, '/') + 1 : "?";
  if (strncmp(who, "MTLCompiler", 11) && strncmp(who, "libLLVM", 7) && strncmp(who, "libMTLIntel", 11) && strncmp(who, "libGPUCompiler", 14)) return;
  fprintf(stderr, "[q2log] %s %s fmt=\"%s\"", kind, who, fmt ? fmt : "-");
  if (buf && size >= 2) { uint32_t off = 2; for (int k = 0; k < buf[1] && off + 2 <= size; k++) {
      uint8_t desc = buf[off], sz = buf[off + 1]; const uint8_t *d = buf + off + 2;
      if ((desc >> 4) == 2 && sz == 8) { const char *s; memcpy(&s, d, 8); fprintf(stderr, " arg%d=\"%s\"", k, s ? s : "(null)"); }
      else if (sz == 4) { int v; memcpy(&v, d, 4); fprintf(stderr, " arg%d=%d", k, v); }
      else if (sz == 8) { long v; memcpy(&v, d, 8); fprintf(stderr, " arg%d=%#lx", k, v); }
      off += 2 + sz; } }
  fputc('\n', stderr); }
static void my_fault(void *dso, os_log_t log, os_log_type_t t, const char *fmt, uint8_t *buf, uint32_t size) { echo("fault", dso, fmt, buf, size); _os_log_fault_impl(dso, log, t, fmt, buf, size); }
static void my_error(void *dso, os_log_t log, os_log_type_t t, const char *fmt, uint8_t *buf, uint32_t size) { echo("error", dso, fmt, buf, size); _os_log_error_impl(dso, log, t, fmt, buf, size); }
INTERPOSE(my_fault, _os_log_fault_impl)
INTERPOSE(my_error, _os_log_error_impl)
