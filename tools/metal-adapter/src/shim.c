// libHaoMetalShim.dylib (x86_64): the symbols OCLP's HD 4000 Metal stack binds from Metal that macOS 27.2.1's Metal no
// longer exports. The bundle's Metal27 (27.2.1's own x86_64 Metal, cut from the 26B131 cache) re-exports this dylib
// (LC_REEXPORT_DYLIB), so OCLP's driver and its 31001 MTLCompiler, which link /System/.../Metal, resolve them unchanged.
//   - 31 ivar offsets the 11.7.10 HD 4000 driver binds, + 13 more the OCLP 31001 MTLCompiler cut binds: filled at load
//     from the Objective-C runtime (the offsets 27's Metal really uses).
//   - copyAndNullifyRefsInDescriptor: bound by the 31001 cut, never called in the host test; a loud stub.
//   - render: the private descriptor structs the driver reads by MetalOld's offsets (render pipeline, its colour
//     attachments, texture), handed to the driver in MetalOld's layout (layouts.inc, see install_layouts).
//   - HAO_METAL27_INTERNAL=1: make MTLEnvVarAggregator::isInternalBuild(false) return true in this process, so
//     MTL_MONOLITHIC_COMPILER=1 selects the in-process compiler connection. A data write (the function's cached
//     answer), located by instruction match in the loaded Metal27; any mismatch refuses (exit 70), nothing is guessed.
// Also refuses (exit 70) unless the process's only Metal is the bundle's Metal27 (UUID) and MetalOld is not loaded.
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <objc/runtime.h>
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <mach-o/loader.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef METAL27_UUID
#error "build with -DMETAL27_UUID=\"<uuid of the bundle's Metal27>\""
#endif

#define IVAR(i, cls, ivar, sym, drv) __attribute__((visibility("default"))) ptrdiff_t hao_ivar_##i __asm__(sym) = -1;
#include "ivars.inc"
#undef IVAR
static const struct { const char *cls, *ivar; ptrdiff_t *slot; int driver; } table[] = {
#define IVAR(i, cls, ivar, sym, drv) {#cls, #ivar, &hao_ivar_##i, drv},
#include "ivars.inc"
#undef IVAR
};

__attribute__((visibility("default"))) void copyAndNullifyRefsInDescriptor(void) {
  fprintf(stderr, "[shim] copyAndNullifyRefsInDescriptor called: stub for the OCLP 31001 cut, not implemented\n");
  abort();
}

static void refuse(const char *why) {
  fprintf(stderr, "[shim] REFUSE: %s\n", why);
  fflush(stderr);
  _exit(70);
}

static void uuid_of(const struct mach_header_64 *h, char out[37]) {
  const uint8_t *p = (const uint8_t *)(h + 1);
  out[0] = 0;
  for (uint32_t i = 0; i < h->ncmds; i++) {
    const struct load_command *lc = (const void *)p;
    if (lc->cmd == LC_UUID) {
      const uint8_t *u = ((const struct uuid_command *)lc)->uuid;
      snprintf(out, 37, "%02X%02X%02X%02X-%02X%02X-%02X%02X-%02X%02X-%02X%02X%02X%02X%02X%02X", u[0], u[1], u[2], u[3],
               u[4], u[5], u[6], u[7], u[8], u[9], u[10], u[11], u[12], u[13], u[14], u[15]);
      return;
    }
    p += lc->cmdsize;
  }
}

static const struct mach_header_64 *gMetal;

static void check_metal(void) {
  int metals = 0, old = 0;
  for (uint32_t i = 0; i < _dyld_image_count(); i++) {
    const char *n = _dyld_get_image_name(i);
    if (!n) continue;
    if (strstr(n, "MetalOld")) { old++; fprintf(stderr, "[shim] loaded: %s\n", n); }
    size_t l = strlen(n), k = strlen("/Metal.framework/Versions/A/Metal");
    if (l >= k && !strcmp(n + l - k, "/Metal.framework/Versions/A/Metal")) {
      char u[37]; uuid_of((const struct mach_header_64 *)_dyld_get_image_header(i), u);
      fprintf(stderr, "[shim] Metal image: %s uuid %s\n", n, u);
      metals++;
      if (!strcmp(u, METAL27_UUID)) gMetal = (const struct mach_header_64 *)_dyld_get_image_header(i);
    }
  }
  if (!gMetal) refuse("the loaded Metal is not the bundle's Metal27 (uuid " METAL27_UUID ")");
  if (metals != 1) refuse("more than one Metal image is loaded");
  if (old) refuse("MetalOld is loaded next to Metal27");
}

static void fill_ivars(void) {
  int bad_driver = 0, bad_extra = 0;
  for (size_t i = 0; i < sizeof table / sizeof *table; i++) {
    Class c = objc_getClass(table[i].cls);
    Ivar v = c ? class_getInstanceVariable(c, table[i].ivar) : NULL;
    if (!v) {
      fprintf(stderr, "[shim] MISSING ivar %s.%s (%s)\n", table[i].cls, table[i].ivar, table[i].driver ? "driver" : "31001");
      if (table[i].driver) bad_driver++; else bad_extra++;
      continue;
    }
    *table[i].slot = ivar_getOffset(v);
    if (getenv("HAO_SHIM_LOG")) fprintf(stderr, "[shim] %s.%s = %td\n", table[i].cls, table[i].ivar, *table[i].slot);
  }
  fprintf(stderr, "[shim] %zu ivar offsets filled (driver missing %d, 31001 missing %d)\n",
          sizeof table / sizeof *table, bad_driver, bad_extra);
  if (bad_driver) refuse("an ivar the HD 4000 driver needs is missing in this Metal");
}

// MTLEnvVarAggregator::isInternalBuild(bool), 27.2 / 27.2.1 x86_64 (Metal 383.1, the same code in 26B123 and 26B131):
//   55 48 89 e5 53 50       push rbp; mov rbp,rsp; push rbx; push rax
//   b0 01 85 ff 75 10       mov al,1; test edi,edi; jne +0x10        (argument true -> return 1)
//   8a 05 <guard rel32>     mov al,[rip+guard]                       (function-local static: guard byte)
//   84 c0 74 ..             test al,al; je <init>
//   8a 05 <value rel32>     mov al,[rip+value]                       (cached answer)
static const uint8_t kHead[] = {0x55, 0x48, 0x89, 0xe5, 0x53, 0x50, 0xb0, 0x01, 0x85, 0xff, 0x75, 0x10, 0x8a, 0x05};
#define EXPECTED_OFFSET 0x1a72d8 /* 0x7ff8177b72d8 - 0x7ff817610000 (Metal __TEXT) */

static int matches(const uint8_t *f) {
  return !memcmp(f, kHead, sizeof kHead) && f[0x12] == 0x84 && f[0x13] == 0xc0 && f[0x14] == 0x74 && f[0x16] == 0x8a &&
         f[0x17] == 0x05;
}

static void flip_internal(void) {
  unsigned long tsize = 0, dsize = 0;
  const uint8_t *text = getsectiondata(gMetal, "__TEXT", "__text", &tsize);
  const uint8_t *dirty = getsegmentdata(gMetal, "__DATA_DIRTY", &dsize);
  if (!text || !dirty) refuse("Metal27 has no __TEXT,__text or __DATA_DIRTY");
  const uint8_t *hit = NULL; int count = 0;
  for (unsigned long i = 0; i + 0x1c <= tsize; i++)
    if (text[i] == 0x55 && matches(text + i)) { hit = text + i; count++; }
  fprintf(stderr, "[shim] isInternalBuild pattern: %d match(es) in Metal27 __text\n", count);
  if (count != 1) refuse("isInternalBuild instruction pattern not found exactly once");
  long off = (long)(hit - (const uint8_t *)gMetal);
  if (off != EXPECTED_OFFSET) {
    fprintf(stderr, "[shim] match at +%#lx, expected +%#x\n", off, EXPECTED_OFFSET);
    refuse("isInternalBuild match is not at the 26B131 offset");
  }
  int32_t dg, dv; memcpy(&dg, hit + 0x0e, 4); memcpy(&dv, hit + 0x18, 4);
  volatile uint8_t *guard = (uint8_t *)(hit + 0x12 + dg), *value = (uint8_t *)(hit + 0x1c + dv);
  if ((const uint8_t *)guard < dirty || (const uint8_t *)guard >= dirty + dsize ||
      (const uint8_t *)value < dirty || (const uint8_t *)value >= dirty + dsize)
    refuse("isInternalBuild static is not in Metal27 __DATA_DIRTY");
  fprintf(stderr, "[shim] isInternalBuild fn=+%#lx guard=%p (was %u) value=%p (was %u)\n", off, guard, *guard, value, *value);
  *value = 1; *guard = 1;
  setenv("MTL_MONOLITHIC_COMPILER", "1", 0);
  fprintf(stderr, "[shim] isInternalBuild forced to true; MTL_MONOLITHIC_COMPILER=%s\n", getenv("MTL_MONOLITHIC_COMPILER"));
}

// ---- private descriptor structs (render). OCLP's driver asks a descriptor for its private struct
// (-_descriptorPrivate / -descriptorPrivate) and reads fields at MetalOld's offsets. 27 inserted fields and widened
// bitfields in three of them (layouts.inc, generated by abi.py from both binaries' ObjC metadata). For a caller
// inside the driver image the method returns a MetalOld-layout copy, rebuilt from 27's struct on every call and kept
// alive with the descriptor (associated object); every other caller gets 27's own struct. Nothing is translated if
// 27's encoding of _private is not the one in the table. HAO_METAL27_LAYOUTS=0 turns it off (A/B).
typedef struct { const char *name; int oo, os, ob, ow, no, ns, nb, nw, cv; } hao_field;
typedef struct {
  const char *cls, *sel, *sname; size_t osize, nsize; const char *nenc; const hao_field *f; size_t n;
  IMP orig; int active;
} hao_layout;
#define LAYOUT_BEGIN(c, s, st, os, ns, enc, oenc) static const hao_field fields_##c[] = {
#define FIELD(n, oo, os, ob, ow, no, ns, nb, nw, cv) {n, oo, os, ob, ow, no, ns, nb, nw, cv},
#define LAYOUT_END(c) };
#include "layouts.inc"
#undef LAYOUT_BEGIN
#undef FIELD
#undef LAYOUT_END
#define LAYOUT_BEGIN(c, s, st, os, ns, enc, oenc) {#c, #s, #st, os, ns, enc, fields_##c, sizeof fields_##c / sizeof *fields_##c, 0, 0},
#define FIELD(...)
#define LAYOUT_END(c)
static hao_layout layouts[] = {
#include "layouts.inc"
};
#undef LAYOUT_BEGIN
#undef FIELD
#undef LAYOUT_END
#define NLAYOUTS (sizeof layouts / sizeof *layouts)

static uint64_t getbits(const uint8_t *p, int bit, int bits) {
  uint64_t v = 0; memcpy(&v, p + bit / 8, (size_t)((bit % 8 + bits + 7) / 8));
  return (v >> (bit % 8)) & (bits == 64 ? ~0ull : (1ull << bits) - 1);
}
static void putbits(uint8_t *p, int bit, int bits, uint64_t val) {
  size_t n = (size_t)((bit % 8 + bits + 7) / 8); uint64_t v = 0, m = (bits == 64 ? ~0ull : (1ull << bits) - 1) << (bit % 8);
  memcpy(&v, p + bit / 8, n); v = (v & ~m) | ((val << (bit % 8)) & m); memcpy(p + bit / 8, &v, n);
}
// exported for the host test (tests/test_shim.py): translate struct SRC (27 layout) of layout K into DST (MetalOld)
__attribute__((visibility("default"))) void hao_translate(size_t k, const void *src, void *dst) {
  const hao_layout *L = &layouts[k]; const uint8_t *s = src; uint8_t *d = dst;
  memset(d, 0, L->osize);
  for (size_t i = 0; i < L->n; i++) {
    const hao_field *f = &L->f[i];
    if (f->no < 0) continue;
    if (f->os) { memcpy(d + f->oo, s + f->no, (size_t)(f->os < f->ns ? f->os : f->ns)); continue; }
    uint64_t v = getbits(s + f->no, f->nb, f->nw);
    if (f->cv == 1) v = v != 0; else if (f->cv == 2) v = v == 1;
    putbits(d + f->oo, f->ob, f->ow, v);
  }
}
__attribute__((visibility("default"))) size_t hao_layout_count(void) { return NLAYOUTS; }
__attribute__((visibility("default"))) const char *hao_layout_name(size_t k, size_t *osize, size_t *nsize) {
  if (osize) *osize = layouts[k].osize;
  if (nsize) *nsize = layouts[k].nsize;
  return layouts[k].sname;
}

static int from_driver(const void *ra) {
  Dl_info i;
  return ra && dladdr(ra, &i) && i.dli_fname && strstr(i.dli_fname, "GraphicsMTLDriver.bundle/");
}
static void *private_for(size_t k, id self, SEL cmd, const void *ra) {
  hao_layout *L = &layouts[k];
  void *p = ((void *(*)(id, SEL))L->orig)(self, cmd);
  if (!p || !from_driver(ra)) return p;
  CFMutableDataRef d = (CFMutableDataRef)objc_getAssociatedObject(self, L);
  if (!d) {
    d = CFDataCreateMutable(NULL, (CFIndex)L->osize); CFDataSetLength(d, (CFIndex)L->osize);
    objc_setAssociatedObject(self, L, (id)(void *)d, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    CFRelease(d);
  }
  uint8_t *o = CFDataGetMutableBytePtr(d);
  hao_translate(k, p, o);
  return o;
}
#define TRAMP(k) static void *tramp##k(id self, SEL cmd) { return private_for(k, self, cmd, __builtin_return_address(0)); }
TRAMP(0) TRAMP(1) TRAMP(2) TRAMP(3) TRAMP(4) TRAMP(5) TRAMP(6) TRAMP(7)
static void *(*const tramps[])(id, SEL) = {tramp0, tramp1, tramp2, tramp3, tramp4, tramp5, tramp6, tramp7};
_Static_assert(NLAYOUTS <= sizeof tramps / sizeof *tramps, "more layouts than trampolines");

static void install_layouts(void) {
  const char *off = getenv("HAO_METAL27_LAYOUTS");
  if (off && !strcmp(off, "0")) { fprintf(stderr, "[shim] layouts: off (HAO_METAL27_LAYOUTS=0)\n"); return; }
  for (size_t k = 0; k < NLAYOUTS; k++) {
    hao_layout *L = &layouts[k];
    Class c = objc_getClass(L->cls);
    Ivar v = c ? class_getInstanceVariable(c, "_private") : NULL;
    Method m = c ? class_getInstanceMethod(c, sel_registerName(L->sel)) : NULL;
    const char *enc = v ? ivar_getTypeEncoding(v) : NULL;
    if (!m || !enc || strcmp(enc, L->nenc)) {
      fprintf(stderr, "[shim] layouts: %s.%s not as expected (%s); left untranslated\n", L->cls, L->sel,
              !m ? "no method" : !enc ? "no _private" : "encoding differs");
      continue;
    }
    L->orig = method_setImplementation(m, (IMP)tramps[k]);
    L->active = 1;
    fprintf(stderr, "[shim] layouts: -[%s %s] -> %s in MetalOld layout for the driver (%zu -> %zu B, %zu fields)\n",
            L->cls, L->sel, L->sname, L->nsize, L->osize, L->n);
  }
}

__attribute__((constructor)) static void shim_init(void) {
  fprintf(stderr, "[shim] libHaoMetalShim loaded (pid %d)\n", getpid());
  check_metal();
  fill_ivars();
  install_layouts();
  if (getenv("HAO_METAL27_INTERNAL") && !strcmp(getenv("HAO_METAL27_INTERNAL"), "1")) flip_internal();
  else fprintf(stderr, "[shim] isInternalBuild left alone (HAO_METAL27_INTERNAL unset): XPC compiler path\n");
}
