// hao's Metal wrapper: what runs when the wrapper loads, besides the generated stubs.
//
// Matches what OCLP's Moraea wrapper does today (analysed read-only on its PSP 2.0.5 build), so the first laptop
// check compares like with like, except the hello skip, which we drop (Setup Assistant plays hello). Each piece
// can be switched off for an A/B on the laptop: HAO_METAL_DISABLE=coreimage,webgl,glass,safari,wallpaper,photos,
// pixelmator,keynote,addimp. Process checks are substrings of argv[0], as Moraea's are. Built without ARC.
#import <Foundation/Foundation.h>
#import <crt_externs.h>
#import <dlfcn.h>
#import <mach-o/dyld.h>
#import <mach-o/nlist.h>
#import <objc/runtime.h>
#import <string.h>

// Test builds prefix every class name (-DHAO_METAL_CLASS_PREFIX=\"Hao\"): on a macOS 27 host every x86_64 process
// already has the system's Metal loaded, so tests use stand-in classes under other names. Shipping builds don't.
#ifndef HAO_METAL_CLASS_PREFIX
#define HAO_METAL_CLASS_PREFIX ""
#endif
#define CLS(name) HAO_METAL_CLASS_PREFIX name

static const char *program(void) {
    char **argv = *_NSGetArgv();
    return argv && argv[0] ? argv[0] : "";
}

static BOOL running(const char *needle) { return strstr(program(), needle) != NULL; }

static BOOL enabled(const char *name) {
    const char *off = getenv("HAO_METAL_DISABLE");
    if (!off) return YES;
    size_t n = strlen(name);
    for (const char *p = off; (p = strstr(p, name)); p += n)
        if ((p == off || p[-1] == ',') && (p[n] == ',' || p[n] == 0)) return NO;
    return YES;
}

static void trace(NSString *what) { NSLog(@"hao-metal: %@ (%s)", what, program()); }

static long zero(id self, SEL _cmd, ...) { return 0; }
static BOOL yes(id self, SEL _cmd, ...) { return YES; }

// Replace an existing method (the class must have it); returns the old implementation.
static IMP replace(const char *cls, const char *sel, BOOL instance, IMP imp) {
    Class c = objc_getClass(cls);
    if (!c) { trace([NSString stringWithFormat:@"no class %s", cls]); return NULL; }
    Method m = instance ? class_getInstanceMethod(c, sel_registerName(sel))
                        : class_getClassMethod(c, sel_registerName(sel));
    if (!m) { trace([NSString stringWithFormat:@"no method %s %s", cls, sel]); return NULL; }
    return method_setImplementation(m, imp);
}

// Add a method only where the class lacks it (never overrides).
static void add(const char *cls, const char *sel, IMP imp, const char *types) {
    Class c = objc_getClass(cls);
    if (c && !class_getInstanceMethod(c, sel_registerName(sel)))
        class_addMethod(c, sel_registerName(sel), imp, types);
}

// --- every process: selectors newer clients send that MetalOld's internal classes lack (Moraea's 22 addImp,
// minus requireThreadsPerThreadgroup, which no 27.2.1 code sends). Getters answer 0/nil/NO, setters do nothing.
static void add_missing(void) {
    static const struct { const char *cls, *sel, *types; } rows[] = {
        {CLS("MTLHeapDescriptorInternal"), "addressRanges", "@@:"},
        {CLS("MTLHeapDescriptorInternal"), "setAddressRanges:", "v@:@"},
        {CLS("MTLHeapDescriptorInternal"), "maxCompatiblePlacementSparsePageSize", "q@:"},
        {CLS("MTLHeapDescriptorInternal"), "setMaxCompatiblePlacementSparsePageSize:", "v@:q"},
        {CLS("MTLHeapDescriptorInternal"), "memoryPoolId", "Q@:"},
        {CLS("MTLHeapDescriptorInternal"), "setMemoryPoolId:", "v@:Q"},
        {CLS("MTLCompileOptionsInternal"), "mathMode", "q@:"},
        {CLS("MTLCompileOptionsInternal"), "setMathMode:", "v@:q"},
        {CLS("MTLCompileOptionsInternal"), "mathFloatingPointFunctions", "q@:"},
        {CLS("MTLCompileOptionsInternal"), "setMathFloatingPointFunctions:", "v@:q"},
        {CLS("MTLCompileOptionsInternal"), "compileSymbolVisibility", "q@:"},
        {CLS("MTLCompileOptionsInternal"), "setCompileSymbolVisibility:", "v@:q"},
        {CLS("MTLCompileOptionsInternal"), "allowReferencingUndefinedSymbols", "B@:"},
        {CLS("MTLCompileOptionsInternal"), "setAllowReferencingUndefinedSymbols:", "v@:B"},
        {CLS("MTLCompileOptionsInternal"), "maxTotalThreadsPerThreadgroup", "Q@:"},
        {CLS("MTLCompileOptionsInternal"), "setMaxTotalThreadsPerThreadgroup:", "v@:Q"},
        {CLS("MTLCompileOptionsInternal"), "enableLogging", "B@:"},
        {CLS("MTLCompileOptionsInternal"), "setEnableLogging:", "v@:B"},
        {CLS("NSProcessInfo"), "isDeviceCertifiedFor:", "B@:q"},
        {CLS("NSProcessInfo"), "hasPerformanceProfile:", "B@:q"},
    };
    for (size_t i = 0; i < sizeof rows / sizeof rows[0]; i++)
        add(rows[i].cls, rows[i].sel, (IMP)zero, rows[i].types);
}

// --- every process: Core Image's language checks against MetalOld's compiler.
static void coreimage(void) {
    replace(CLS("MTLCompiler"), "validateLanguageAndAIRVersionForFunction:completionHandler:", YES, (IMP)yes);
    replace(CLS("MTLCompileOptionsInternal"), "setLanguageVersion:", YES, (IMP)zero);
}

// --- WebKit: ANGLE's Metal backend refuses MacBookPro models below 13 and needs MTLGPUFamilyMac2.
static IMP real_supports_family;
static BOOL supports_family(id self, SEL _cmd, long family) {
    return family == 2002 ? YES : ((BOOL (*)(id, SEL, long))real_supports_family)(self, _cmd, family);
}

// Find a (non-exported) symbol in a loaded image's own symbol table.
static void *local_symbol(const char *image, const char *symbol) {
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *name = _dyld_get_image_name(i);
        if (!name || !strstr(name, image)) continue;
        const struct mach_header_64 *mh = (const struct mach_header_64 *)_dyld_get_image_header(i);
        intptr_t slide = _dyld_get_image_vmaddr_slide(i);
        const struct load_command *lc = (const void *)(mh + 1);
        const struct symtab_command *st = NULL;
        const struct segment_command_64 *linkedit = NULL, *text = NULL;
        for (uint32_t k = 0; k < mh->ncmds; k++, lc = (const void *)((const char *)lc + lc->cmdsize)) {
            if (lc->cmd == LC_SYMTAB) st = (const void *)lc;
            if (lc->cmd == LC_SEGMENT_64) {
                const struct segment_command_64 *seg = (const void *)lc;
                if (!strcmp(seg->segname, SEG_LINKEDIT)) linkedit = seg;
                if (!strcmp(seg->segname, SEG_TEXT)) text = seg;
            }
        }
        if (!st || !linkedit || !text) return NULL;
        uintptr_t base = linkedit->vmaddr + slide - linkedit->fileoff;
        const struct nlist_64 *syms = (const void *)(base + st->symoff);
        const char *strs = (const char *)(base + st->stroff);
        for (uint32_t k = 0; k < st->nsyms; k++)
            if (syms[k].n_un.n_strx && !strcmp(strs + syms[k].n_un.n_strx, symbol))
                return (void *)(syms[k].n_value + slide);
        return NULL;
    }
    return NULL;
}

static void webgl(void) {
    real_supports_family = replace(CLS("_MTLDevice"), "supportsFamily:", YES, (IMP)supports_family);
    dlopen("libANGLE-shared.dylib", RTLD_NOW | RTLD_NOLOAD);
    char *queried = local_symbol("libANGLE-shared.dylib", "__ZZN5angle24IsMetalRendererAvailableEvE19queriedMachineModel");
    if (queried) *queried = 1;
    else trace(@"can't find angle::IsMetalRendererAvailable()::queriedMachineModel");
    replace(CLS("MTLIOAccelDevice"), "registryID", YES, (IMP)zero);
}

// --- app-specific validator skips Moraea applies today (each a laptop A/B candidate).
static void validators(void) {
    if (enabled("glass") && (running("iconservicesagent") || running("com.apple.dock.extra") || running("Spotlight")))
        replace(CLS("MTLTextureDescriptorInternal"), "validateWithDevice:", YES, (IMP)yes);
    if (enabled("safari") && running("Safari")) {
        replace(CLS("_MTLFunction"), "newArgumentEncoderWithBufferIndex:reflection:functionReflection:", YES, (IMP)zero);
        replace(CLS("_MTLDevice"), "newArgumentEncoderWithBufferBinding:", YES, (IMP)zero);
    }
    if (enabled("wallpaper") && running("WallpaperSequoiaExtension"))
        replace(CLS("MTLRenderPipelineDescriptorInternal"), "validateWithDevice:error:", YES, (IMP)yes);
    if (enabled("photos") && running("Photos"))
        replace(CLS("MTLComputePipelineDescriptorInternal"), "validateWithDevice:error:", YES, (IMP)yes);
}

// --- third-party apps Moraea special-cases (kept for parity; candidates to drop).
static long tessellation(id self, SEL _cmd) { return 64; }
static void third_party(void) {
    if (enabled("keynote") && running("Keynote"))
        replace(CLS("TSDMetalDeviceCapabilities"), "maximumTessellationFactor", YES, (IMP)tessellation);
    if (enabled("pixelmator") && running("Pixelmator Pro.app/Contents/MacOS/Pixelmator Pro")) {
        long *once = local_symbol("PTFoundation", "PTDeviceModel.onceToken");          // as Moraea looks them up;
        id *model = local_symbol("PTFoundation", "PTDeviceModel.deviceModelString");    // unverified (third-party)
        if (once && model) {
            *once = -1;
            *model = [@"MacPro7,1" retain];
            setenv("CI_ENABLE_METAL_DAG", "0", 1);
        }
    }
}

__attribute__((constructor)) static void hao_metal_load(void) {
    @autoreleasepool {
        if (enabled("addimp")) add_missing();
        if (enabled("coreimage")) coreimage();
        if (enabled("webgl") && running("com.apple.WebKit")) webgl();
        validators();
        third_party();
    }
}
