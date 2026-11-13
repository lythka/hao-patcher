// libhelloblur.dylib v2 (sysfix-13c, 2026-10-16) = v1 (sysfix-09e/10e, 013930a5) + the hello pill warm-up (below).
// libhelloblur.dylib (sysfix-09e): hello/Welcome blur cache for the HD 4000, delivered as a WEAK dependency of
// OCLP's Metal wrapper, so it is mapped into every process that links Metal but does something only in
// "Language Chooser" (hello) and "Setup Assistant" (Welcome to Mac). Everywhere else the constructor returns after
// one strcmp. Based on handoff/hello-screen/fps/live/fix/hello_blurcache.m (proven live in Language Chooser via
// DYLD_INSERT_LIBRARIES, 2026-10-15: 57.9 -> 60.0/60.1 fps, 0 dropped, GPU median 12.9 -> 7.7 ms).
//
// Cause (NOTES-live.md): every frame SetupAssistantSupportUI's hello renderer re-runs MPSImageGaussianBlur (sigma 8)
// on the same background texture into the same destination texture; the output is bit-identical every frame.
// Fix: after 2 identical repeats of (kernel, sigma, edgeMode, source, destination), skip the encode: the
// destination already holds exactly that result.
//
// Changes against the proven proof of concept (all make it more conservative, none changes what is skipped):
//  - processes: Language Chooser AND Setup Assistant (Welcome to Mac uses the same HelloView renderer);
//  - caller check: only calls whose return address lies in SetupAssistantSupportUI are cached (checked once per
//    new argument tuple with dladdr); any other MPS user in these processes is passed through untouched;
//  - the kernel and both textures are RETAINED while cached, so a freed texture's address cannot be reused by a
//    new texture and mistaken for the cached one;
//  - the blur is re-run anyway every 120th repeat (~2 s at 60 fps), so even a texture whose contents were
//    rewritten in place would be stale for at most ~2 s;
//  - installation is deferred to the main queue (not inside the dyld initializer of the Metal wrapper), and the
//    MPS framework is only loaded there, as in the proof of concept;
//  - kill switches: file /private/var/db/hello_blurcache.disable (checked at process start) or env HELLO_BLURCACHE=0;
//  - links only Foundation, libobjc and libSystem (all already loaded by the Metal wrapper), not Metal itself.
// v2 pill warm-up (Language Chooser only; analysis-2026-10-16/NOTES-hello-stutter-2.md): when the glass "Continue" pill
//   first appears, LC's main thread spent ~380 ms (13a first-boot spindump) in -[NSButton intrinsicContentSize] ->
//   SwiftUI AttributeGraph -> AttributedString.init -> first, uncached swift_conformsToProtocol lookups + cold page-ins,
//   and the hello's frames waited (idle desktop: one 83 ms gap). Right after install (LC just started, screen black)
//   it builds the same button once (as -[SASWelcomeView configure]: buttonWithTitle, controlSize 4 = ExtraLarge,
//   wantsLayer, bezelStyle 0x10 = Glass, VibrantLight) in a never-shown deferred window and asks for its size, then
//   throws it away. Host A/B (x86_64): the app's own first pill layout 37-44 ms -> 6-10 ms after the warm-up.
//   AppKit is not linked (this dylib is mapped into every Metal process): classes by name, methods via a protocol.
//   Kill switches: env HELLO_PILLWARM=0, file /private/var/db/hello_pillwarm.disable.
// Logs (unified log, process Language Chooser / Setup Assistant): "hello_blurcache: installed ...",
// "hello_blurcache: caching ..." once per argument tuple, "hello_blurcache: not caching (caller ...)" if refused.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <dlfcn.h>
#import <string.h>
#import <unistd.h>

#define REFRESH_EVERY 120

// AppKit methods used by the pill warm-up, declared here so nothing links AppKit (it is loaded in LC already).
@protocol HBWarmAppKit
+ (id)buttonWithTitle:(NSString *)title target:(id)target action:(SEL)action;
+ (id)appearanceNamed:(NSString *)name;
- (id)initWithContentRect:(NSRect)r styleMask:(NSUInteger)m backing:(NSUInteger)b defer:(BOOL)d;
- (void)setReleasedWhenClosed:(BOOL)b;
- (id)contentView;
- (void)setAppearance:(id)a;
- (void)setControlSize:(NSUInteger)s;
- (void)setWantsLayer:(BOOL)b;
- (void)setBezelStyle:(NSUInteger)s;
- (void)setAlphaValue:(double)a;
- (void)setFrameOrigin:(NSPoint)p;
- (void)addSubview:(id)v;
- (void)removeFromSuperview;
- (void)layoutSubtreeIfNeeded;
- (void)updateConstraintsIfNeeded;
- (NSSize)intrinsicContentSize;
- (void)close;
@end

static IMP gOrig;
static Class gGauss;
static id gK, gS, gD;                 // retained while cached
static float gSig = -1; static long gEdge = -1, gSame = 0, gOk = 0;

static int callerIsSASUI(void *ra) {
    Dl_info di;
    if (!ra || !dladdr(ra, &di) || !di.dli_fname) return 0;
    return strstr(di.dli_fname, "/SetupAssistantSupportUI") != NULL;
}

static void hEncode(id self, SEL _cmd, id cb, id src, id dst) {
    void *ra = __builtin_return_address(0);
    if (!gGauss || ![self isKindOfClass:gGauss]) { ((void (*)(id, SEL, id, id, id))gOrig)(self, _cmd, cb, src, dst); return; }
    float sig = ((float (*)(id, SEL))objc_msgSend)(self, @selector(sigma));
    long edge = (long)((NSUInteger (*)(id, SEL))objc_msgSend)(self, @selector(edgeMode));
    BOOL same = gK == self && gS == src && gD == dst && gSig == sig && gEdge == edge;
    if (!same) {
        [gK release]; [gS release]; [gD release];
        gK = [self retain]; gS = [src retain]; gD = [dst retain]; gSig = sig; gEdge = edge;
        gSame = 0; gOk = -1;              // caller not checked yet for this tuple
    } else gSame++;
    if (same && gSame >= 2) {
        if (gOk < 0) {
            gOk = callerIsSASUI(ra);
            Dl_info di; const char *who = (ra && dladdr(ra, &di) && di.dli_fname) ? di.dli_fname : "?";
            if (gOk) NSLog(@"hello_blurcache: caching repeated blur (sigma %g, src %p, dst %p, caller %s)", sig, src, dst, who);
            else NSLog(@"hello_blurcache: not caching (caller %s)", who);
        }
        if (gOk && gSame % REFRESH_EVERY != 0) return;   // dst already holds this exact result
    }
    ((void (*)(id, SEL, id, id, id))gOrig)(self, _cmd, cb, src, dst);
}

static void install(void) {
    static int done = 0; if (done) return; done = 1;
    [[NSBundle bundleWithPath:@"/System/Library/Frameworks/MetalPerformanceShaders.framework"] load];
    SEL s = @selector(encodeToCommandBuffer:sourceTexture:destinationTexture:);
    gGauss = NSClassFromString(@"MPSImageGaussianBlur");
    Method mg = gGauss ? class_getInstanceMethod(gGauss, s) : NULL;
    if (!mg) { NSLog(@"hello_blurcache: MPSImageGaussianBlur encode not found; not installed"); return; }
    gOrig = method_setImplementation(mg, (IMP)hEncode);
    NSLog(@"hello_blurcache: installed in %s (pid %d) on %s", getprogname(), getpid(), class_getName(gGauss));
}

static void pillwarm(void) {   // v2: Language Chooser only, on the main queue right after install()
    const char *e = getenv("HELLO_PILLWARM");
    if ((e && !strcmp(e, "0")) || access("/private/var/db/hello_pillwarm.disable", F_OK) == 0) return;
    Class W = NSClassFromString(@"NSWindow"), B = NSClassFromString(@"NSButton"), A = NSClassFromString(@"NSAppearance");
    if (!W || !B || !A) { NSLog(@"hello_pillwarm: AppKit classes not found; skipped"); return; }
    NSDate *t0 = [NSDate date];
    NSSize sz = NSZeroSize;
    @autoreleasepool {
        id<HBWarmAppKit> w = [(id<HBWarmAppKit>)[W alloc] initWithContentRect:NSMakeRect(0, 0, 640, 400) styleMask:0 /* borderless */
                                                                       backing:2 /* buffered */ defer:YES];
        [w setReleasedWhenClosed:NO];
        id<HBWarmAppKit> cv = [w contentView];
        [cv setAppearance:[(Class<HBWarmAppKit>)A appearanceNamed:@"NSAppearanceNameVibrantLight"]];
        id<HBWarmAppKit> b = [(Class<HBWarmAppKit>)B buttonWithTitle:@"Continue" target:nil action:NULL];
        [b setControlSize:4];          // NSControlSizeExtraLarge
        [b setWantsLayer:YES];
        [b setBezelStyle:0x10];        // NSBezelStyleGlass
        [b setAlphaValue:0.005];
        [b setFrameOrigin:NSMakePoint(280, 100)];
        [cv addSubview:b];
        [w updateConstraintsIfNeeded];
        [cv layoutSubtreeIfNeeded];
        sz = [b intrinsicContentSize];
        [b removeFromSuperview];
        [w close];
        [(id)w release];
    }
    NSLog(@"hello_pillwarm: warmed the glass pill in %.1f ms (intrinsic %.0fx%.0f)", -[t0 timeIntervalSinceNow] * 1000, sz.width, sz.height);
}

__attribute__((constructor)) static void hello_blurcache_init(void) {
    const char *p = getprogname();
    if (!p || (strcmp(p, "Language Chooser") != 0 && strcmp(p, "Setup Assistant") != 0)) return;
    const char *e = getenv("HELLO_BLURCACHE");
    int blur = !(e && !strcmp(e, "0")) && access("/private/var/db/hello_blurcache.disable", F_OK) != 0;
    int lc = strcmp(p, "Language Chooser") == 0;    // pill warm-up: own kill switches, independent of the blur cache
    if (!blur && !lc) return;
    // not inside the dyld initializer: wait for the main queue (the first frames are blurred normally anyway)
    dispatch_async(dispatch_get_main_queue(), ^{ if (blur) install(); if (lc) pillwarm(); });
}
