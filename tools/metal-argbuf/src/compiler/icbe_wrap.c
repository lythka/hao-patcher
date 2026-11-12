// icbe_wrap.c: iCBE.dylib wrapper for the HD 4000 (prototype; see ../../README.md).
// Re-exports the real iCBE (installed beside it as iCBE-real.dylib) and overrides Create/Delete. Create returns a
// proxy TC::CTranslationBlock whose Translate runs igil_rewrite on the IGIL text before calling the real one, so
// argument-buffer textures and buffers resolve through the fixed slot ranges of ../argbuf_config.h. Programs without
// argument-buffer accesses pass through unchanged.
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syslog.h>

#define REAL "/System/Library/Extensions/AppleIntelHD4000GraphicsGLDriver.bundle/Contents/MacOS/iCBE-real.dylib"
enum { ARGS_COPY = 0x80 };

typedef struct TB TB;
typedef struct { void *off, *rtti; int (*translate)(TB*, const void*, void*); int (*freealloc)(TB*, void*);
                 void (*dtor)(TB*); void (*ddtor)(TB*); } VT;
struct TB { void **vptr; TB *real; };

static void *real_lib(void) {
    static void *h;
    if (!h && !(h = dlopen(REAL, RTLD_NOW | RTLD_GLOBAL))) syslog(LOG_ERR, "icbe_wrap: dlopen %s: %s", REAL, dlerror());
    return h;
}
static void *real_sym(const char *n) { void *h = real_lib(); return h ? dlsym(h, n) : NULL; }
static void **rvt(TB *real) { return real->vptr; }   // real vptr points at its first virtual (Translate)

// ---- IGIL rewrite (igil_rewrite.c) -----------------------------------------------------------------------------
#include "igil_rewrite.h"
#include "../argbuf_config.h"
#include "air_hook.h"
static const igil_config CFG = { AGB_TEX_SLOT, AGB_TEX_COUNT, AGB_BUF_SLOT, AGB_BUF_COUNT, AGB_BUF_BTI, AGB_SAMP_SLOT, AGB_SAMP_COUNT };
static char *rewrite(const char *in) {
    igil_stats st; char *r = igil_rewrite(in, &CFG, &st);
    if (r) syslog(LOG_NOTICE, "icbe_wrap: argument buffers: %d texture reads (t%d.., slots %d..%d), %d buffer accesses (u%d.., slots %d..%d), %d samplers (s%d..)%s",
                  st.textures, st.tex_base, CFG.tex_slot, CFG.tex_slot + CFG.tex_count - 1,
                  st.buffers, st.buf_base, CFG.buf_slot, CFG.buf_slot + CFG.buf_count - 1, st.samplers, st.samp_base,
                  st.unresolved ? ", SOME POINTER ACCESSES UNRESOLVED" : "");
    return r;
}

// ---- proxy translation block ---------------------------------------------------------------------------------
// ICBE_WRAP_DUMP_DIR=DIR (host harness): every program is saved as DIR/N.in.igil, and its rewrite as DIR/N.out.igil
static void dump(const char *kind, int n, const char *text) {
    const char *dir = getenv("ICBE_WRAP_DUMP_DIR"); if (!dir || !text) return;
    char p[1024]; snprintf(p, sizeof p, "%s/%d.%s.igil", dir, n, kind);
    FILE *f = fopen(p, "w"); if (f) { fputs(text, f); fclose(f); }
}
// ICBE_WRAP_INJECT_DIR=DIR (host harness): a program whose SHADER/PIXELSHADER/VERTEXSHADER name has DIR/NAME.igil is
// replaced by that file (hand-edited IGIL experiments)
static char *inject(const char *text) {
    const char *dir = getenv("ICBE_WRAP_INJECT_DIR"); if (!dir) return NULL;
    const char *k = strstr(text, "SHADER "); if (!k) return NULL;
    k += 7; size_t n = strcspn(k, " \t\r\n"); if (!n || n > 200) return NULL;
    char p[1024]; snprintf(p, sizeof p, "%s/%.*s.igil", dir, (int)n, k);
    FILE *f = fopen(p, "rb"); if (!f) return NULL;
    fseek(f, 0, SEEK_END); long len = ftell(f); rewind(f);
    char *s = malloc((size_t)len + 1); size_t got = fread(s, 1, (size_t)len, f); s[got] = 0; fclose(f);
    syslog(LOG_NOTICE, "icbe_wrap: injected %s", p);
    return s;
}
static int p_translate(TB *self, const void *in, void *out) {
    int (*t)(TB*, const void*, void*) = (int (*)(TB*, const void*, void*))rvt(self->real)[0];
    const char *text = *(const char *const *)in;
    static int seq; int n = __atomic_add_fetch(&seq, 1, __ATOMIC_RELAXED);
    dump("in", n, text);
    char *nt = text ? inject(text) : NULL;
    if (!nt && text) nt = rewrite(text);
    dump("out", n, nt);
    if (!nt) return t(self->real, in, out);
    unsigned char args[ARGS_COPY]; memcpy(args, in, sizeof args);
    *(const char **)args = nt; *(uint32_t *)(args + 8) = (uint32_t)strlen(nt) + 1;
    if (getenv("ICBE_WRAP_DUMP")) { FILE *f = fopen(getenv("ICBE_WRAP_DUMP"), "w"); if (f) { fputs(nt, f); fclose(f); } }
    int r = t(self->real, args, out);
    free(nt);
    return r;
}
static int p_free(TB *self, void *out) { return ((int (*)(TB*, void*))rvt(self->real)[1])(self->real, out); }
static void p_dtor(TB *self) { (void)self; }
static void p_ddtor(TB *self) { free(self); }
static void *proxy_vt[] = { 0, 0, (void *)p_translate, (void *)p_free, (void *)p_dtor, (void *)p_ddtor };

__attribute__((visibility("default"))) void *Create(const void *args) {
    void *(*c)(const void *) = (void *(*)(const void *))real_sym("Create");
    TB *real = c ? c(args) : NULL;
    if (!real) return NULL;
    TB *p = calloc(1, sizeof *p); p->vptr = proxy_vt + 2; p->real = real;
    return p;
}
__attribute__((visibility("default"))) void Delete(void *tb) {
    void (*d)(void *) = (void (*)(void *))real_sym("Delete");
    TB *p = tb;
    if (p && p->vptr == proxy_vt + 2) { if (d) d(p->real); free(p); }
    else if (d) d(tb);
}

// The plugin loads iCBE before its first compile: rebind its PassManager::run import so argbuf's IR legalization
// (air_pass.c) runs on every AIR module before the IGIL target.
__attribute__((constructor)) static void init(void) { air_hook_install(); }
