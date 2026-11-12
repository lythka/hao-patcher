// air_hook.c: run argbuf's IR transforms on the AIR module the Intel compiler plugin hands to the IGIL target.
//
// MTLIntelCompiler::generateIGIL (libMTLIntelCompilerPlugin) builds a legacy PassManager for the IGIL target
// (addPassesToEmitFile) and calls llvm::legacy::PassManager::run(Module&) once, through the plugin's import of that
// symbol from GPUCompiler 3802's libLLVM. At load, this rebinds that import (a data pointer, no code is patched) so
// air_transform() sees the module right before the target's own passes, and then the original run proceeds.
// The iCBE wrapper is loaded by the plugin before its first compile, so its constructor installs this.
#include "air_hook.h"
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach-o/nlist.h>
#include <mach/mach.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syslog.h>

#define RUN_SYMBOL "__ZN4llvm6legacy11PassManager3runERNS_6ModuleE"
typedef _Bool (*run_fn)(void *pm, void *module);
static run_fn orig_run;

static void install_pass_wrappers(void);
static _Bool hook_run(void *pm, void *module) {
    install_pass_wrappers();
    air_transform(module);
    return orig_run(pm, module);
}

// Rebind one imported symbol in one image (lazy and non-lazy symbol pointers, classic dyld info).
static int rebind(const struct mach_header_64 *h, intptr_t slide, const char *symbol, void *replacement, void **original) {
    const struct load_command *lc = (const void *)(h + 1);
    const struct segment_command_64 *linkedit = NULL;
    const struct symtab_command *st = NULL; const struct dysymtab_command *dst = NULL;
    for (uint32_t i = 0; i < h->ncmds; i++, lc = (const void *)((const char *)lc + lc->cmdsize)) {
        if (lc->cmd == LC_SEGMENT_64 && !strcmp(((const struct segment_command_64 *)lc)->segname, SEG_LINKEDIT)) linkedit = (const void *)lc;
        else if (lc->cmd == LC_SYMTAB) st = (const void *)lc;
        else if (lc->cmd == LC_DYSYMTAB) dst = (const void *)lc;
    }
    if (!linkedit || !st || !dst || !dst->nindirectsyms) return 0;
    uintptr_t base = (uintptr_t)slide + linkedit->vmaddr - linkedit->fileoff;
    const struct nlist_64 *syms = (const void *)(base + st->symoff);
    const char *strs = (const char *)(base + st->stroff);
    const uint32_t *indirect = (const void *)(base + dst->indirectsymoff);
    int found = 0;
    lc = (const void *)(h + 1);
    for (uint32_t i = 0; i < h->ncmds; i++, lc = (const void *)((const char *)lc + lc->cmdsize)) {
        if (lc->cmd != LC_SEGMENT_64) continue;
        const struct segment_command_64 *seg = (const void *)lc;
        const struct section_64 *sec = (const void *)(seg + 1);
        for (uint32_t s = 0; s < seg->nsects; s++, sec++) {
            uint32_t type = sec->flags & SECTION_TYPE;
            if (type != S_LAZY_SYMBOL_POINTERS && type != S_NON_LAZY_SYMBOL_POINTERS) continue;
            void **ptrs = (void **)((uintptr_t)slide + sec->addr);
            for (uint64_t k = 0; k < sec->size / sizeof(void *); k++) {
                uint32_t si = indirect[sec->reserved1 + k];
                if (si == INDIRECT_SYMBOL_ABS || si == INDIRECT_SYMBOL_LOCAL || si == (INDIRECT_SYMBOL_LOCAL | INDIRECT_SYMBOL_ABS)) continue;
                if (strcmp(strs + syms[si].n_un.n_strx, symbol)) continue;
                if (vm_protect(mach_task_self(), (vm_address_t)&ptrs[k], sizeof(void *), 0, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY)) {
                    syslog(LOG_ERR, "icbe_wrap: cannot make %s's import of %s writable", seg->segname, symbol); continue;
                }
                if (!*original) *original = air_llvm() ? dlsym(air_llvm(), symbol + 1) : NULL;
                if (!*original) { syslog(LOG_ERR, "icbe_wrap: %s not found in GPUCompiler 3802 libLLVM", symbol); return 0; }
                ptrs[k] = replacement; found++;
            }
        }
    }
    return found;
}

// Local (non-exported) symbol of a loaded image, from its LC_SYMTAB.
static uintptr_t local_symbol(const struct mach_header_64 *h, intptr_t slide, const char *name) {
    const struct load_command *lc = (const void *)(h + 1);
    const struct segment_command_64 *linkedit = NULL; const struct symtab_command *st = NULL;
    for (uint32_t i = 0; i < h->ncmds; i++, lc = (const void *)((const char *)lc + lc->cmdsize)) {
        if (lc->cmd == LC_SEGMENT_64 && !strcmp(((const struct segment_command_64 *)lc)->segname, SEG_LINKEDIT)) linkedit = (const void *)lc;
        else if (lc->cmd == LC_SYMTAB) st = (const void *)lc;
    }
    if (!linkedit || !st) return 0;
    uintptr_t base = (uintptr_t)slide + linkedit->vmaddr - linkedit->fileoff;
    const struct nlist_64 *syms = (const void *)(base + st->symoff);
    const char *strs = (const char *)(base + st->stroff);
    for (uint32_t i = 0; i < st->nsyms; i++)
        if (!(syms[i].n_type & N_STAB) && (syms[i].n_type & N_TYPE) == N_SECT && !strcmp(strs + syms[i].n_un.n_strx, name))
            return (uintptr_t)slide + syms[i].n_value;
    return 0;
}

air_pass_run air_tte_orig;
// Point the IGIL TextureTypeEncode pass's runOnFunction vtable slot at air_tte_run (data only, no code patched).
static void install_pass_wrappers(void) {
    static int done;
    if (done) return;
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *name = _dyld_get_image_name(i);
        if (!name || !strstr(name, "libIGIL-Metal.dylib")) continue;
        done = 1;
        const struct mach_header_64 *h = (const void *)_dyld_get_image_header(i); intptr_t slide = _dyld_get_image_vmaddr_slide(i);
        uintptr_t vt = local_symbol(h, slide, "__ZTVN12_GLOBAL__N_121IGILTextureTypeEncodeE");
        uintptr_t run = local_symbol(h, slide, "__ZN12_GLOBAL__N_121IGILTextureTypeEncode13runOnFunctionERN4llvm8FunctionE");
        if (!vt || !run) { syslog(LOG_ERR, "icbe_wrap: IGILTextureTypeEncode symbols not found"); return; }
        void **slots = (void **)(vt + 16);
        for (int k = 0; k < 48; k++) {
            if ((uintptr_t)slots[k] != run) continue;
            if (vm_protect(mach_task_self(), (vm_address_t)&slots[k], sizeof(void *), 0, VM_PROT_READ | VM_PROT_WRITE | VM_PROT_COPY)) {
                syslog(LOG_ERR, "icbe_wrap: IGILTextureTypeEncode vtable not writable"); return;
            }
            air_tte_orig = (air_pass_run)slots[k]; slots[k] = (void *)air_tte_run;
            syslog(LOG_NOTICE, "icbe_wrap: IGILTextureTypeEncode wrapped (vtable slot %d)", k);
            return;
        }
        syslog(LOG_ERR, "icbe_wrap: IGILTextureTypeEncode runOnFunction not in its vtable");
        return;
    }
}

void *air_llvm(void) {
    static void *h;
    if (h) return h;
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *name = _dyld_get_image_name(i);
        size_t n = name ? strlen(name) : 0, t = strlen("/3802/Libraries/libLLVM.dylib");
        if (n > t && !strcmp(name + n - t, "/3802/Libraries/libLLVM.dylib")) return h = dlopen(name, RTLD_LAZY | RTLD_NOLOAD);
    }
    return NULL;
}

void air_hook_install(void) {
    static int done;
    if (done) return;
    if (getenv("ARGBUF_AIR_DISABLE")) { done = 1; return; }
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *name = _dyld_get_image_name(i);
        if (!name || !strstr(name, "libMTLIntelCompilerPlugin.dylib")) continue;
        int n = rebind((const struct mach_header_64 *)_dyld_get_image_header(i), _dyld_get_image_vmaddr_slide(i), RUN_SYMBOL,
                       (void *)hook_run, (void **)&orig_run);
        done = 1;
        if (!n || !orig_run) { syslog(LOG_ERR, "icbe_wrap: AIR hook not installed (%d imports, original %p)", n, (void *)orig_run); return; }
        air_transform_init();
        install_pass_wrappers();   // if libIGIL-Metal is already loaded; otherwise at the first compile
        syslog(LOG_NOTICE, "icbe_wrap: AIR hook installed (%d imports)", n);
        return;
    }
}
