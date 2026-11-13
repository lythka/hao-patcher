// MTLCompiler stand-in (x86_64), installed in the bundle as fw/MTLCompiler.framework/Versions/{32023,32024}/MTLCompiler.
// 27's Metal, on its in-process (monolithic) compiler connection, dlopens
// /System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/3202x/MTLCompiler; DYLD_FRAMEWORK_PATH=<bundle>/fw
// makes that this file. It exports the four MTLCodeGenService* entry points and routes every request to OCLP's
// compilers, as MetalOld does (from the Q2 host test, q2proxy.c):
//   plugin-bound type 1 (backend): 27's AIR module -> OCLP 31001 downgrade (type 7, 13.2.1 framing, built from a
//     captured MetalOld template with 27's name and module spliced in) -> 27's own request with the downgraded module
//     -> OCLP 3802 + the Intel plugin;
//   plugin-less requests (13 frontend, 15 specialization, ...): to OCLP 31001 as they are (13: 3 options translated).
// Paths (defaults are the OCLP files on a Golden Gate image; env overrides for the host test):
//   HAO_MTLCOMPILER_3802  /System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/3802/MTLCompiler
//   HAO_MTLCOMPILER_31001 /System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/31001/MTLCompiler
//   HAO_LLVM_HELPER_31001 /System/Library/PrivateFrameworks/GPUCompiler.framework/Versions/31001/Libraries/libLLVM_helper.dylib
//   HAO_31001_CWD         /  (directory for the fallback retry, see open_31001)
//   HAO_DOWNGRADE_TEMPLATE  (required) the captured type-7 request
//   HAO_STANDIN_DUMP        optional directory: every request/reply is written there
//   HAO_FE_MODULE_CACHE     optional (test harness): directory for the 31001 frontend's module cache (see modcache)
#include <dlfcn.h>
#include <execinfo.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef void (^reply_t)(unsigned, const void *, size_t, const char *);
typedef void *(*create_t)(void);
typedef int (*setpath_t)(void *, const char *, const void *, size_t);
typedef void (*build_t)(void *, unsigned, unsigned, const void *, size_t, reply_t);

static const char *env(const char *k, const char *d) { const char *v = getenv(k); return v && *v ? v : d; }
#define P3802 "/System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/3802/MTLCompiler"
#define P31001 "/System/Library/PrivateFrameworks/MTLCompiler.framework/Versions/31001/MTLCompiler"

#define PHELPER31001 "/System/Library/PrivateFrameworks/GPUCompiler.framework/Versions/31001/Libraries/libLLVM_helper.dylib"

// OCLP's 31001 libLLVM.dylib (payload 13.2.1-25) links libLLVM_helper.dylib by a RELATIVE path,
// "System/Library/PrivateFrameworks/GPUCompiler.framework/Versions/31001/Libraries/libLLVM_helper.dylib" (no leading
// slash), and that is also the helper's install name. dyld resolves a relative load path against the current
// directory. MTLCompilerService runs with cwd "/", so it finds the helper; an app whose cwd is anything else gets
// "Library not loaded: System/Library/.../libLLVM_helper.dylib" (laptop run 1, 2026-11-12). Fix: load the helper by
// its absolute path first (its only initializer sets two trace flags, so loading it early changes nothing); dyld then
// binds libLLVM's relative load command to that already-loaded image by install name. Fallback, only if the 31001
// dlopen still fails: retry once with cwd = HAO_31001_CWD ("/", as in the service), then restore the cwd.
static void *open_31001(const char *path) {
  const char *hp = env("HAO_LLVM_HELPER_31001", PHELPER31001);
  void *hh = dlopen(hp, RTLD_NOW | RTLD_LOCAL);
  fprintf(stderr, "[standin] preload 31001 helper %s -> %p %s\n", hp, hh, hh ? "" : dlerror());
  void *h = dlopen(path, RTLD_NOW | RTLD_LOCAL);
  fprintf(stderr, "[standin] dlopen 31001 %s -> %p %s\n", path, h, h ? "" : dlerror());
  if (!h) {
    const char *root = env("HAO_31001_CWD", "/");
    int fd = open(".", O_RDONLY | O_CLOEXEC);
    if (fd >= 0 && chdir(root) == 0) {
      h = dlopen(path, RTLD_NOW | RTLD_LOCAL);
      fprintf(stderr, "[standin] dlopen 31001 retry with cwd %s -> %p %s\n", root, h, h ? "" : dlerror());
      if (fchdir(fd) != 0) fprintf(stderr, "[standin] WARNING could not restore the cwd\n");
    } else fprintf(stderr, "[standin] fallback: cannot chdir to %s\n", root);
    if (fd >= 0) close(fd);
  }
  return h;
}
static void *open_or_die(const char *what, const char *path) {
  void *h;
  if (!strcmp(what, "31001")) h = open_31001(path);
  else {
    h = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    fprintf(stderr, "[standin] dlopen %s %s -> %p %s\n", what, path, h, h ? "" : dlerror());
  }
  if (!h) { fprintf(stderr, "[standin] FATAL cannot load %s\n", what); fflush(stderr); abort(); }
  return h;
}
static void *g3802, *g31001, *gsvc31001;
static void *sym(void **h, const char *what, const char *path, const char *s) {
  if (!*h) *h = open_or_die(what, path);
  void *f = dlsym(*h, s);
  if (!f) { fprintf(stderr, "[standin] FATAL %s lacks %s\n", what, s); fflush(stderr); abort(); }
  return f;
}
#define S3802(s) sym(&g3802, "3802", env("HAO_MTLCOMPILER_3802", P3802), s)
#define S31001(s) sym(&g31001, "31001", env("HAO_MTLCOMPILER_31001", P31001), s)

static int n;
static void dump(const char *tag, int i, const void *p, size_t sz) {
  const char *o = getenv("HAO_STANDIN_DUMP"); if (!o || !p) return;
  char fn[1024]; snprintf(fn, sizeof fn, "%s/%03d.%s", o, i, tag);
  FILE *f = fopen(fn, "wb"); if (f) { fwrite(p, 1, sz, f); fclose(f); }
}
static void bt(void) {
  void *a[32]; int k = backtrace(a, 32); char **s = backtrace_symbols(a, k);
  for (int i = 0; i < k; i++) fprintf(stderr, "[standin]   %s\n", s[i]);
  free(s);
}
static void *svc31001(void) {
  if (!gsvc31001) { gsvc31001 = ((create_t)S31001("MTLCodeGenServiceCreate"))();
    fprintf(stderr, "[standin] 31001 service %p\n", gsvc31001); }
  return gsvc31001;
}

__attribute__((visibility("default"))) void *MTLCodeGenServiceCreate(void *processName) {
  fprintf(stderr, "[standin] MTLCodeGenServiceCreate(%p) from:\n", processName); bt();
  void *r = ((create_t)S3802("MTLCodeGenServiceCreate"))();  // 3802's Create takes no argument
  fprintf(stderr, "[standin] -> 3802 service %p\n", r);
  return r;
}
__attribute__((visibility("default"))) void MTLCodeGenServiceDestroy(void *s) {
  fprintf(stderr, "[standin] MTLCodeGenServiceDestroy(%p)\n", s);
  ((void (*)(void *))S3802("MTLCodeGenServiceDestroy"))(s);
}
__attribute__((visibility("default"))) int MTLCodeGenServiceSetPluginPath(void *s, const char *p, const void *td, size_t tds) {
  fprintf(stderr, "[standin] SetPluginPath(%p, %s, targetData %zu B)\n", s, p ? p : "-", tds);
  dump("targetdata", 0, td, tds);
  int r = ((setpath_t)S3802("MTLCodeGenServiceSetPluginPath"))(s, p, td, tds);
  fprintf(stderr, "[standin] SetPluginPath -> %d\n", r);
  return r;
}

static uint8_t *readfile(const char *p, size_t *len) {
  FILE *f = fopen(p, "rb"); if (!f) return NULL;
  fseek(f, 0, SEEK_END); long l = ftell(f); fseek(f, 0, SEEK_SET);
  uint8_t *b = malloc(l); if (fread(b, 1, l, f) != (size_t)l) { fclose(f); free(b); return NULL; }
  fclose(f); *len = l; return b;
}
// Replace the name (words 22/23, optional) and module (words 24/25) of request R; shift header words that pointed at
// the old module end or the old total size. Same framing in 13.2.1 and 27: ... name | pad to 8 | module | tail.
static uint8_t *resplice(const uint8_t *r, size_t rsz, const uint8_t *nm, uint32_t nl, const uint8_t *mod, uint32_t ms, size_t *osz) {
  const uint32_t *w = (const uint32_t *)r; uint32_t no = w[22], ol = w[23], mo = w[24], oms = w[25];
  if (!nm) { nm = r + no; nl = ol; }
  if (rsz < 0x68 || mo != ((no + ol + 7) & ~7u) || (size_t)mo + oms > rsz) {
    fprintf(stderr, "[standin] unexpected layout: name %u+%u module %u+%u size %zu\n", no, ol, mo, oms, rsz); return NULL; }
  uint32_t nmo = (no + nl + 7) & ~7u, tail = (uint32_t)(rsz - mo - oms);
  *osz = nmo + ms + tail; uint8_t *o = calloc(1, *osz);
  memcpy(o, r, no); memcpy(o + no, nm, nl); memcpy(o + nmo, mod, ms); memcpy(o + nmo + ms, r + mo + oms, tail);
  uint32_t *ow = (uint32_t *)o; ow[23] = nl; ow[24] = nmo; ow[25] = ms;
  for (uint32_t k = 0; k < w[2] / 4; k++) { if (k >= 22 && k <= 25) continue;
    if (w[k] == mo + oms) ow[k] = nmo + ms; else if (w[k] == rsz) ow[k] = (uint32_t)*osz; }
  return o;
}
static int downgrade(int i, const void *d, size_t sz, uint8_t **mod, uint32_t *ms) {
  const char *tp = getenv("HAO_DOWNGRADE_TEMPLATE");
  size_t tn; uint8_t *t = tp ? readfile(tp, &tn) : NULL;
  if (!t) { fprintf(stderr, "[standin] no downgrade template (HAO_DOWNGRADE_TEMPLATE=%s)\n", tp ? tp : "unset"); return -1; }
  const uint32_t *w = d; size_t qn;
  uint8_t *q = resplice(t, tn, (const uint8_t *)d + w[22], w[23], (const uint8_t *)d + w[24], w[25], &qn);
  free(t); if (!q) return -1;
  dump("req7", i, q, qn);
  fprintf(stderr, "[standin] #%d downgrade: type 7 to 31001, %zu B, module %u B, name %.*s\n", i, qn, w[25], (int)w[23], (const char *)d + w[22]);
  __block int ok = -1; __block uint8_t *m = NULL; __block uint32_t mn = 0;
  ((build_t)S31001("MTLCodeGenServiceBuildRequest"))(svc31001(), 0xffffffffu, 7, q, qn,
    ^(unsigned st, const void *r, size_t rs, const char *err) {
      fprintf(stderr, "[standin] #%d downgrade reply status=%u size=%zu err=%s\n", i, st, rs, err ? err : "-");
      dump("reply7", i, r, rs);
      if (st == 0 && rs >= 48) { const uint32_t *rw = r; uint32_t bo = rw[10], bl;
        if ((size_t)bo + 4 <= rs) { memcpy(&bl, (const uint8_t *)r + bo, 4);
          if ((size_t)bo + 4 + bl <= rs) { m = malloc(bl); memcpy(m, (const uint8_t *)r + bo + 4, bl); mn = bl; ok = 0; } } } });
  *mod = m; *ms = mn; return ok;  // q is not freed: the compiler may keep it (replies were synchronous on the host)
}

// Test harness only (run.sh sets HAO_FE_MODULE_CACHE): 27 puts -fmodules-cache-path="<user cache>/com.apple.metalfe"
// into a source (type 13) request, and OCLP's 31001 frontend writes its monolithic_metal .pcm there. run.sh's sandbox
// denies that write ("unable to open output file ... monolithic_metal-*.pcm", host replay of the laptop's own req13),
// so the path is moved into the run's own folder. Type-13 framing (27, accepted by 31001 as is): u64 source size,
// u64 options size, source, pad to 8, options (NUL-terminated). Only a request with exactly that layout is rewritten.
static uint8_t *modcache(int i, const uint8_t *r, size_t rsz, size_t *osz) {
  const char *dir = getenv("HAO_FE_MODULE_CACHE"); if (!dir || !*dir || rsz < 16) return NULL;
  uint64_t sl, ol; memcpy(&sl, r, 8); memcpy(&ol, r + 8, 8);
  size_t oo = (16 + sl + 7) & ~(size_t)7;
  if (sl > rsz || ol > rsz || oo + ol != rsz || !ol || r[rsz - 1]) {
    fprintf(stderr, "[standin] #%d type 13 layout not recognized (%llu/%llu/%zu): module cache left as is\n", i,
            (unsigned long long)sl, (unsigned long long)ol, rsz); return NULL; }
  static const char key[] = "-fmodules-cache-path=\"";
  const char *o = (const char *)r + oo, *k = strstr(o, key);
  if (!k) return NULL;
  const char *v = k + strlen(key), *q = strchr(v, '"'); if (!q) return NULL;
  size_t pre = (size_t)(v - o), dl = strlen(dir), post = ol - (size_t)(q - o);  // post includes the quote and NUL
  size_t nol = pre + dl + post; *osz = oo + nol;
  uint8_t *n = calloc(1, *osz); memcpy(n, r, oo); memcpy(n + 8, &(uint64_t){nol}, 8);
  memcpy(n + oo, o, pre); memcpy(n + oo + pre, dir, dl); memcpy(n + oo + pre + dl, q, post);
  fprintf(stderr, "[standin] #%d module cache %.*s -> %s (%zu -> %zu B)\n", i, (int)(q - v), v, dir, rsz, *osz);
  return n;
}

__attribute__((visibility("default"))) void MTLCodeGenServiceBuildRequest(void *s, unsigned plugin, unsigned type, const void *d, size_t sz, reply_t cb) {
  int i = ++n;
  fprintf(stderr, "[standin] #%d BuildRequest service=%p plugin=%d type=%u size=%zu\n", i, s, (int)plugin, type, sz);
  char tag[32]; snprintf(tag, sizeof tag, "req%u", type); dump(tag, i, d, sz);
  if (i == 1) { fprintf(stderr, "[standin] first BuildRequest from:\n"); bt(); }
  if (plugin == 0xffffffffu) {  // plugin-less: frontend 13, specialization 15, ... -> 31001 as MetalOld does
    uint8_t *copy = NULL;
    if (type == 13) {  // 27 frontend options the 13.x frontend rejects; same length, space padded
      static const char *pairs[][2] = {{"-fmetal-math-mode=fast", "-ffast-math           "},
        {"-fmetal-math-fp32-functions=fast", "                                "}, {"-std=metal4.1", "-std=metal3.0"}};
      copy = malloc(sz); memcpy(copy, d, sz);
      for (int k = 0; k < 3; k++) { uint8_t *hit = memmem(copy, sz, pairs[k][0], strlen(pairs[k][0]));
        if (hit) { memcpy(hit, pairs[k][1], strlen(pairs[k][1])); fprintf(stderr, "[standin] #%d option %s -> '%s'\n", i, pairs[k][0], pairs[k][1]); } }
      d = copy;
      size_t nsz; uint8_t *moved = modcache(i, copy, sz, &nsz);
      if (moved) { d = moved; sz = nsz; dump("sent", i, d, sz); } }
    fprintf(stderr, "[standin] #%d type %u to 31001 as is\n", i, type);
    ((build_t)S31001("MTLCodeGenServiceBuildRequest"))(svc31001(), plugin, type, d, sz,
      ^(unsigned st, const void *r, size_t rs, const char *err) {
        fprintf(stderr, "[standin] #%d 31001 reply status=%u size=%zu err=%s\n", i, st, rs, err ? err : "-");
        dump("reply", i, r, rs); cb(st, r, rs, err); });
    return;
  }
  uint8_t *sent = NULL;
  if (type == 1) {
    uint8_t *m; uint32_t mn;
    if (downgrade(i, d, sz, &m, &mn)) fprintf(stderr, "[standin] #%d downgrade FAILED, sending 27's module as is\n", i);
    else { size_t on; sent = resplice(d, sz, NULL, 0, m, mn, &on); free(m);
      if (sent) { fprintf(stderr, "[standin] #%d 27 request %zu -> %zu B with the downgraded module (%u B)\n", i, sz, on, mn);
        d = sent; sz = on; dump("sent", i, d, sz); } }
  }
  ((build_t)S3802("MTLCodeGenServiceBuildRequest"))(s, plugin, type, d, sz,
    ^(unsigned st, const void *r, size_t rs, const char *err) {
      fprintf(stderr, "[standin] #%d 3802 reply status=%u size=%zu err=%s\n", i, st, rs, err ? err : "-");
      dump("reply", i, r, rs); cb(st, r, rs, err); });
  fprintf(stderr, "[standin] #%d BuildRequest returned\n", i);
}
