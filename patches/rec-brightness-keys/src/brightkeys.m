/*
 * brightkeys: brightness, keyboard light and volume keys + bezel for recoveryOS (LaunchAgent in the recovery
 * GUI session, root).
 *
 * Why: the keys reach the HID event system fine (Apple top case page 0xFF: 0x04/0x05 display
 * up/down, 0x08/0x09 keyboard light up/down; external Apple keyboards: consumer 0x6F/0x70), and Apple's corebrightnessd
 * runs and sets the display (DisplayServices), but recovery's WindowServer does not dispatch its brightness hotkeys,
 * corebrightnessd knows no keyboard backlight on this generation (KeyboardBrightnessClient: no keyboards; the LED sits
 * behind AppleLMUController), and OSDUIHelper puts up its bezel window but draws nothing in it. So this:
 *   - display: steps brightness through IOKit (IODisplaySetFloatParameter on IODisplayConnect), 16 steps like macOS.
 *     Not through DisplayServices/corebrightnessd: corebrightnessd's DisplayALSManager fades the
 *     panel back to its ambient-light target seconds after every change, with DisplayBrightnessAuto and
 *     AutoBrightnessEnable both off; without corebrightnessd the level holds and the backlight driver still saves it to
 *     NVRAM backlight-level. DisplayServices stays as the fallback when no IODisplayConnect answers;
 *   - keyboard light: steps AppleLMUController's LED (user client selector 1 get / 2 set, 0..0xfff), 16 steps; the level
 *     is kept in NVRAM (ssd-kbd-backlight-level, see "keyboard light in NVRAM") and set again at the next start;
 *   - volume (F10-F12, consumer 0xE2 mute / 0xEA down / 0xE9 up; audio from rec-audio's AppleHDA): the default output
 *     device's main volume + mute through CoreAudio, 16 steps; any volume key unmutes, like macOS;
 *   - repeats while a key is held; skips a press that something else (a native handler) already acted on;
 *   - draws the bezel itself: Tahoe's banner at top right without the glass (default), or the classic 200x200 dark
 *     square with 16 chiclets (see "bezel" below).
 * Runs on recovery's own dyld cache (AppKit, IOKit, CoreAudio; DisplayServices only as the display fallback).
 */
#import <AppKit/AppKit.h>
#import <QuartzCore/QuartzCore.h>
#include <CoreAudio/CoreAudio.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/graphics/IOGraphicsLib.h>
#include <math.h>

typedef struct __IOHIDEventSystemClient *IOHIDEventSystemClientRef;
typedef struct __IOHIDEvent *IOHIDEventRef;
typedef void (*EventCallback)(void *target, void *refcon, void *sender, IOHIDEventRef event);
extern IOHIDEventSystemClientRef IOHIDEventSystemClientCreateWithType(CFAllocatorRef, int type, CFDictionaryRef);
extern void IOHIDEventSystemClientScheduleWithDispatchQueue(IOHIDEventSystemClientRef, dispatch_queue_t);
extern void IOHIDEventSystemClientRegisterEventCallback(IOHIDEventSystemClientRef, EventCallback, void *, void *);
extern uint32_t IOHIDEventGetType(IOHIDEventRef);
extern CFIndex IOHIDEventGetIntegerValue(IOHIDEventRef, uint32_t field);
extern int DisplayServicesGetBrightness(CGDirectDisplayID, float *);
extern int DisplayServicesSetBrightness(CGDirectDisplayID, float);

#define KB(n) ((3 << 16) | (n))                  /* kIOHIDEventTypeKeyboard fields: page, usage, down */
#define STEPS 16
#define LMU_MAX 0xfff
#define NATIVE_GRACE_MS 120                      /* a native handler would have moved it by then */
#define REPEAT_DELAY_MS 400
#define REPEAT_MS 90
#define BEZEL_SHOW_S 1.0
#define BEZEL_FADE_S 0.3

enum { DISPLAY, KEYBOARD, VOLUME };

/* ---- levels (0..1) ---- */

static io_connect_t lmu;                         /* AppleLMUController user client, 0 when absent */

/* the built-in panel's IODisplayConnect (first one found), looked up once; 0 when absent */
static io_service_t panel(void) {
    static io_service_t s;
    if (!s) s = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("IODisplayConnect"));
    return s;
}

static AudioObjectID outputDevice(void) {
    AudioObjectID d = kAudioObjectUnknown; UInt32 n = sizeof(d);
    AudioObjectPropertyAddress a = {kAudioHardwarePropertyDefaultOutputDevice, kAudioObjectPropertyScopeGlobal,
                                    kAudioObjectPropertyElementMain};
    AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &n, &d);
    return d;
}
/* the main volume as macOS's volume keys see it: the virtual main volume ('vmvc') of the output scope */
static AudioObjectPropertyAddress volumeAddr = {'vmvc', kAudioDevicePropertyScopeOutput, kAudioObjectPropertyElementMain};
static AudioObjectPropertyAddress muteAddr = {kAudioDevicePropertyMute, kAudioDevicePropertyScopeOutput,
                                              kAudioObjectPropertyElementMain};

static int muted(void) {
    UInt32 m = 0, n = sizeof(m); AudioObjectID d = outputDevice();
    return d != kAudioObjectUnknown && !AudioObjectGetPropertyData(d, &muteAddr, 0, NULL, &n, &m) && m;
}
static void setMuted(int m) {
    UInt32 v = m; AudioObjectID d = outputDevice();
    if (d != kAudioObjectUnknown) AudioObjectSetPropertyData(d, &muteAddr, 0, NULL, sizeof(v), &v);
}

static float getLevel(int what) {
    if (what == VOLUME) {
        Float32 v = -1; UInt32 n = sizeof(v); AudioObjectID d = outputDevice();
        if (d == kAudioObjectUnknown || AudioObjectGetPropertyData(d, &volumeAddr, 0, NULL, &n, &v)) return -1;
        return v;
    }
    if (what == DISPLAY) {
        float v = -1;
        if (panel() && !IODisplayGetFloatParameter(panel(), kNilOptions, CFSTR(kIODisplayBrightnessKey), &v)) return v;
        return DisplayServicesGetBrightness(CGMainDisplayID(), &v) ? -1 : v;
    }
    uint64_t in = 0, out = 0; uint32_t n = 1;
    if (!lmu || IOConnectCallScalarMethod(lmu, 1, &in, 1, &out, &n)) return -1;
    return (float)out / LMU_MAX;
}

static void setLevel(int what, float v) {
    if (what == VOLUME) {
        Float32 f = v; AudioObjectID d = outputDevice();
        if (d != kAudioObjectUnknown) AudioObjectSetPropertyData(d, &volumeAddr, 0, NULL, sizeof(f), &f);
        return;
    }
    if (what == DISPLAY) {
        if (!panel() || IODisplaySetFloatParameter(panel(), kNilOptions, CFSTR(kIODisplayBrightnessKey), v))
            DisplayServicesSetBrightness(CGMainDisplayID(), v);
        return;
    }
    uint64_t in[2] = {0, (uint64_t)lroundf(v * LMU_MAX)}, out; uint32_t n = 1;
    if (lmu) IOConnectCallScalarMethod(lmu, 2, in, 2, &out, &n);
}

/* ---- keyboard light in NVRAM ----
 * The display's level already survives a restart: the backlight driver writes backlight-level (raw panel level, 2 bytes
 * little endian) and the firmware sets the panel from it at power-on. The keyboard light has no such variable on this
 * generation (macOS keeps it in CoreBrightness preferences, which recovery does not have), so brightkeys keeps its own
 * the same way: ssd-kbd-backlight-level in Apple's system namespace, 2 bytes little endian, 0..0xfff (the LED's own
 * units), written KBD_SAVE_S after the last change (one write per adjustment, not per step), set at start. */
#define KBD_NVRAM "ssd-kbd-backlight-level"
#define KBD_SAVE_S 2

static int kbdLoad(uint16_t *v) {
    io_registry_entry_t o = IORegistryEntryFromPath(kIOMainPortDefault, "IODeviceTree:/options");
    CFTypeRef d = o ? IORegistryEntryCreateCFProperty(o, CFSTR(KBD_NVRAM), kCFAllocatorDefault, 0) : NULL;
    int ok = d && CFGetTypeID(d) == CFDataGetTypeID() && CFDataGetLength(d) == 2;
    if (ok) { const UInt8 *b = CFDataGetBytePtr(d); *v = (uint16_t)(b[0] | b[1] << 8); }
    if (d) CFRelease(d);
    if (o) IOObjectRelease(o);
    return ok && *v <= LMU_MAX;
}

static void kbdSave(uint16_t v) {
    io_registry_entry_t o = IORegistryEntryFromPath(kIOMainPortDefault, "IODeviceTree:/options");
    if (!o) return;
    UInt8 b[2] = {v & 0xff, v >> 8};
    CFDataRef d = CFDataCreate(kCFAllocatorDefault, b, 2);
    kern_return_t r = IORegistryEntrySetCFProperty(o, CFSTR(KBD_NVRAM), d);
    CFRelease(d);
    IOObjectRelease(o);
    fprintf(stderr, "brightkeys: keyboard light %u saved to NVRAM (%s)\n", v, r ? "failed" : "ok");
}

static unsigned kbdGen;
static void kbdSaveLater(void) {
    unsigned g = ++kbdGen;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, KBD_SAVE_S * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        float v = getLevel(KEYBOARD);
        if (g == kbdGen && v >= 0) kbdSave((uint16_t)lroundf(v * LMU_MAX));
    });
}

/* ---- bezel ---- */
/*
 * Two styles, picked per show so they can be switched live:
 *   tahoe    (default) Tahoe 26's banner without the glass, i.e. what Tahoe draws with Reduce Transparency on:
 *            292x64 plate under the menu bar at top right, title over min symbol / slider with 17 ticks / max symbol.
 *            Geometry and colours measured from a Tahoe 26 VM capture (hud/stock-tahoe/measurements.json), sizes
 *            confirmed against ControlCenter's SystemBanner (M-osd-geometry.md).
 *   classic  the pre-Tahoe bezel: 200x200 dark square, centred low, SF Symbol + 16 chiclets.
 * Order: /private/var/run/brightkeys-style (live; "echo classic > ..."), then --style=X / BRIGHTKEYS_STYLE, then tahoe.
 * The tahoe plate animates as a CALayer of its own (recovery's WindowServer did not run window-frame animations).
 */
enum { CLASSIC, TAHOE };
static int argStyle = TAHOE;

static int parseStyle(const char *s, int dflt) {
    if (!s) return dflt;
    if (!strncmp(s, "classic", 7)) return CLASSIC;
    if (!strncmp(s, "tahoe", 5)) return TAHOE;
    return dflt;
}
static int currentStyle(void) {
    char b[16] = {0}; FILE *f = fopen("/private/var/run/brightkeys-style", "r");
    if (!f) return argStyle;
    fgets(b, sizeof(b), f); fclose(f);
    return parseStyle(b, argStyle);
}

/* the volume title, as Tahoe shows it: the output's name ("Internal Speakers", "Headphones"), else the device's */
static NSString *outputName(void) {
    AudioObjectID d = outputDevice();
    if (d == kAudioObjectUnknown) return nil;
    UInt32 src = 0, n = sizeof(src);
    AudioObjectPropertyAddress a = {kAudioDevicePropertyDataSource, kAudioDevicePropertyScopeOutput,
                                    kAudioObjectPropertyElementMain};
    if (!AudioObjectGetPropertyData(d, &a, 0, NULL, &n, &src)) {
        CFStringRef name = NULL;
        AudioValueTranslation t = {&src, sizeof(src), &name, sizeof(name)};
        a.mSelector = kAudioDevicePropertyDataSourceNameForIDCFString; n = sizeof(t);
        if (!AudioObjectGetPropertyData(d, &a, 0, NULL, &n, &t) && name) return CFBridgingRelease(name);
    }
    CFStringRef name = NULL; n = sizeof(name);
    a = (AudioObjectPropertyAddress){kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    if (!AudioObjectGetPropertyData(d, &a, 0, NULL, &n, &name) && name) return CFBridgingRelease(name);
    return nil;
}

@interface Chiclets : NSView
@property int filled;
@end
@implementation Chiclets
- (void)drawRect:(NSRect)r {
    CGFloat w = 9, gap = 1, x = (self.bounds.size.width - STEPS * (w + gap) + gap) / 2;
    for (int i = 0; i < STEPS; i++) {
        [(i < self.filled ? [NSColor colorWithWhite:1 alpha:0.95] : [NSColor colorWithWhite:0 alpha:0.35]) setFill];
        NSRectFill(NSMakeRect(x + i * (w + gap), 0, w, self.bounds.size.height));
    }
}
@end

/* tahoe geometry, points from the plate's top left (VM capture, 1x) */
#define T_W 292
#define T_H 64
#define T_RADIUS 23
#define T_TITLE_X 17                             /* glyph box of the title (SF 12 medium): left 17, cap top 14 */
#define T_TITLE_CAPTOP 14
#define T_TRACK_X 31
#define T_TRACK_W 222
#define T_TRACK_Y 39
#define T_TRACK_H 4
#define T_TICK_X 35                              /* 17 ticks, 2x2, every 13.25 pt, under the track */
#define T_TICK_STEP 13.25
#define T_TICK_Y 46
#define T_MIN_X 16                               /* symbol boxes: left 16 / 258, glyphs centred on y 41.5 */
#define T_MAX_X 258
#define T_ICON_MID 41.5
#define T_SYM_PAD 2                              /* 12 pt symbol images carry ~2 pt of side bearing */
#define T_DROP 6                                 /* appears from 6 pt higher, scaled 0.92 */
#define T_SCALE 0.92
#define T_APPEAR_S 0.32
#define T_HOLD_S 0.665
#define T_FADE_S 0.28

@interface TahoeContent : CALayer                /* everything on the plate; drawn top-left based */
@property int what, mute;
@property float level;
@property NSString *title;
@end
@implementation TahoeContent
static NSImage *sym(NSString *name, CGFloat pt) {
    NSImageSymbolConfiguration *c = [NSImageSymbolConfiguration configurationWithPointSize:pt weight:NSFontWeightMedium];
    c = [c configurationByApplyingConfiguration:[NSImageSymbolConfiguration configurationWithHierarchicalColor:NSColor.whiteColor]];
    return [[NSImage imageWithSystemSymbolName:name accessibilityDescription:nil] imageWithSymbolConfiguration:c];
}
- (void)drawInContext:(CGContextRef)ctx {
    CGContextTranslateCTM(ctx, 0, T_H); CGContextScaleCTM(ctx, 1, -1);
    NSGraphicsContext *g = [NSGraphicsContext graphicsContextWithCGContext:ctx flipped:YES];
    [NSGraphicsContext saveGraphicsState]; NSGraphicsContext.currentContext = g;
    NSColor *dim = [NSColor colorWithWhite:1 alpha:0.08];
    NSFont *f = [NSFont systemFontOfSize:12 weight:NSFontWeightMedium];   /* medium matches the VM best */
    [self.title drawAtPoint:NSMakePoint(T_TITLE_X - 0.5, T_TITLE_CAPTOP + f.capHeight - f.ascender)
             withAttributes:@{NSFontAttributeName: f, NSForegroundColorAttributeName: NSColor.whiteColor}];
    [dim setFill];
    [[NSBezierPath bezierPathWithRoundedRect:NSMakeRect(T_TRACK_X, T_TRACK_Y, T_TRACK_W, T_TRACK_H)
                                     xRadius:T_TRACK_H / 2 yRadius:T_TRACK_H / 2] fill];
    for (int i = 0; i <= STEPS; i++) NSRectFill(NSMakeRect(round(T_TICK_X + i * T_TICK_STEP), T_TICK_Y, 2, 2));
    float v = self.mute ? 0 : self.level;
    if (v > 0) {
        [NSColor.whiteColor setFill];
        [[NSBezierPath bezierPathWithRoundedRect:NSMakeRect(T_TRACK_X, T_TRACK_Y, fmax(T_TRACK_H, v * T_TRACK_W), T_TRACK_H)
                                         xRadius:T_TRACK_H / 2 yRadius:T_TRACK_H / 2] fill];
    }
    NSString *lo = self.what == DISPLAY ? @"sun.min.fill" : self.what == KEYBOARD ? @"light.min" : @"speaker.fill";
    NSString *hi = self.what == DISPLAY ? @"sun.max.fill" : self.what == KEYBOARD ? @"light.max" : @"speaker.wave.3.fill";
    for (int end = 0; end < 2; end++) {
        NSImage *im = sym(end ? hi : lo, 12);
        NSSize sz = im.size;
        [im drawInRect:NSMakeRect((end ? T_MAX_X : T_MIN_X) - T_SYM_PAD, round(T_ICON_MID - sz.height / 2), sz.width, sz.height)
              fromRect:NSZeroRect operation:NSCompositingOperationSourceOver fraction:1 respectFlipped:YES hints:nil];
    }
    [NSGraphicsContext restoreGraphicsState];
}
@end

@interface Bezel : NSObject
@property int style;
@property NSView *root;                          /* the window's content */
@property NSImageView *icon;                     /* classic */
@property Chiclets *chiclets;
@property CALayer *plate;                        /* tahoe: fill + outline, animated */
@property TahoeContent *content;
@end
@implementation Bezel
/* inWindow: blend with what is behind it inside the same window (preview renders) rather than behind the window */
- (instancetype)initWithStyle:(int)style inWindow:(BOOL)inWindow {
    self = [super init];
    self.style = style;
    if (style == CLASSIC) {
        NSVisualEffectView *glass = [[NSVisualEffectView alloc] initWithFrame:NSMakeRect(0, 0, 200, 200)];
        glass.material = NSVisualEffectMaterialHUDWindow;
        glass.blendingMode = inWindow ? NSVisualEffectBlendingModeWithinWindow : NSVisualEffectBlendingModeBehindWindow;
        glass.state = NSVisualEffectStateActive;
        glass.appearance = [NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
        glass.wantsLayer = YES; glass.layer.cornerRadius = 22; glass.layer.masksToBounds = YES;
        self.icon = [[NSImageView alloc] initWithFrame:NSMakeRect(50, 62, 100, 100)];
        self.icon.contentTintColor = NSColor.whiteColor;
        self.chiclets = [[Chiclets alloc] initWithFrame:NSMakeRect(10, 26, 180, 8)];
        [glass addSubview:self.icon]; [glass addSubview:self.chiclets];
        self.root = glass;
        return self;
    }
    /* Tahoe's Reduce Transparency look in both appearances: grey 35 at 75 %, 1 pt white outline at 35 % */
    self.root = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, T_W, T_H + T_DROP)];
    self.root.wantsLayer = YES;
    self.plate = [CALayer layer];
    self.plate.frame = CGRectMake(0, 0, T_W, T_H);
    self.plate.backgroundColor = [NSColor colorWithWhite:35 / 255.0 alpha:0.75].CGColor;
    self.plate.cornerRadius = T_RADIUS; self.plate.cornerCurve = kCACornerCurveContinuous;
    self.plate.borderWidth = 1; self.plate.borderColor = [NSColor colorWithWhite:1 alpha:0.35].CGColor;
    self.plate.masksToBounds = YES;
    self.content = [TahoeContent layer];
    self.content.frame = self.plate.bounds;
    self.content.needsDisplayOnBoundsChange = YES;
    [self.plate addSublayer:self.content];
    [self.root.layer addSublayer:self.plate];
    return self;
}
static NSString *symbol(int what, float v, int mute) {
    if (what == DISPLAY) return @"sun.max.fill";
    if (what == KEYBOARD) return @"light.max";
    if (mute || v <= 0) return @"speaker.slash.fill";
    return v < 0.34 ? @"speaker.wave.1.fill" : v < 0.67 ? @"speaker.wave.2.fill" : @"speaker.wave.3.fill";
}
- (void)show:(int)what level:(float)v mute:(int)mute title:(NSString *)title {
    if (self.style == CLASSIC) {
        NSImageSymbolConfiguration *cfg = [NSImageSymbolConfiguration configurationWithPointSize:70 weight:NSFontWeightRegular];
        self.icon.image = [[NSImage imageWithSystemSymbolName:symbol(what, v, mute) accessibilityDescription:nil]
                           imageWithSymbolConfiguration:cfg];
        self.chiclets.filled = mute ? 0 : (int)lroundf(v * STEPS); [self.chiclets setNeedsDisplay:YES];
        return;
    }
    /* Tahoe on mute: the same symbols, an empty track (VM capture) */
    self.content.what = what; self.content.level = v; self.content.mute = mute;
    self.content.title = title ?: what == DISPLAY ? @"Display" : what == KEYBOARD ? @"Keyboard Brightness"
                                                     : outputName() ?: @"Sound";
    CGFloat scale = self.root.window.backingScaleFactor ?: 1;
    [CATransaction begin]; [CATransaction setDisableActions:YES];   /* new level at once, no implicit cross-fade */
    self.plate.contentsScale = self.content.contentsScale = scale;
    [self.content setNeedsDisplay];
    [self.content displayIfNeeded];
    [CATransaction commit];
}
- (void)show:(int)what level:(float)v mute:(int)mute { [self show:what level:v mute:mute title:nil]; }
@end

/* never key, never main, never activates: the Recovery window keeps keyboard focus (Tab etc.) while a bezel is up */
@interface BezelPanel : NSPanel
@end
@implementation BezelPanel
- (BOOL)canBecomeKeyWindow { return NO; }
- (BOOL)canBecomeMainWindow { return NO; }
- (BOOL)acceptsFirstResponder { return NO; }
@end

static BezelPanel *bezel;
static Bezel *look;
static unsigned bezelGen;
static int tahoeShown;                           /* plate is up or appearing (not hidden / fading) */

static CATransform3D tahoeHidden(void) {         /* 6 pt higher (layer y is up), 0.92 around the centre */
    return CATransform3DScale(CATransform3DMakeTranslation(0, T_DROP, 0), T_SCALE, T_SCALE, 1);
}
static void tahoeAnimate(CALayer *l, CATransform3D to, float alpha, double s, NSString *timing, void (^done)(void)) {
    CALayer *now = l.presentationLayer ?: l;     /* start from wherever it is, so a key press mid-fade does not jump */
    CATransform3D from = now.transform; float fromAlpha = now.opacity;
    [l removeAllAnimations];
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    [CATransaction setCompletionBlock:done];
    l.transform = to; l.opacity = alpha;
    CABasicAnimation *t = [CABasicAnimation animationWithKeyPath:@"transform"];
    t.fromValue = [NSValue valueWithCATransform3D:from]; t.toValue = [NSValue valueWithCATransform3D:to];
    CABasicAnimation *o = [CABasicAnimation animationWithKeyPath:@"opacity"];
    o.fromValue = @(fromAlpha); o.toValue = @(alpha);
    CAAnimationGroup *grp = [CAAnimationGroup animation];
    grp.animations = @[t, o]; grp.duration = s;
    grp.timingFunction = [CAMediaTimingFunction functionWithName:timing];
    [l addAnimation:grp forKey:@"bezel"];
    [CATransaction commit];
}

static void showBezel(int what, float v) {
    int style = currentStyle();
    if (!look || look.style != style) {
        look = [[Bezel alloc] initWithStyle:style inWindow:NO];
        [bezel orderOut:nil];
        tahoeShown = 0;
        bezel = [[BezelPanel alloc] initWithContentRect:look.root.frame styleMask:NSWindowStyleMaskBorderless |
                 NSWindowStyleMaskNonactivatingPanel backing:NSBackingStoreBuffered defer:NO];
        bezel.becomesKeyOnlyIfNeeded = YES; bezel.hidesOnDeactivate = NO;
        bezel.animationBehavior = NSWindowAnimationBehaviorNone;
        bezel.excludedFromWindowsMenu = YES;
        bezel.level = 2005;                      /* the layer OSDUIHelper's bezel uses */
        bezel.opaque = NO; bezel.backgroundColor = NSColor.clearColor; bezel.hasShadow = NO;
        bezel.ignoresMouseEvents = YES;
        bezel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary |
                                   NSWindowCollectionBehaviorIgnoresCycle | NSWindowCollectionBehaviorFullScreenAuxiliary;
        bezel.contentView = look.root;
    }
    [look show:what level:v mute:what == VOLUME && muted()];
    NSScreen *scr = NSScreen.mainScreen; NSRect s = scr.frame;
    unsigned g = ++bezelGen;
    if (style == CLASSIC) {
        [bezel setFrameOrigin:NSMakePoint(NSMidX(s) - 100, NSMinY(s) + 140)];    /* Apple's old spot: centred, 140 pt up */
        bezel.alphaValue = 1;
        [bezel orderFrontRegardless];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(BEZEL_SHOW_S * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            if (g != bezelGen) return;
            [NSAnimationContext runAnimationGroup:^(NSAnimationContext *c) { c.duration = BEZEL_FADE_S; bezel.animator.alphaValue = 0; }
                                completionHandler:^{ if (g == bezelGen) [bezel orderOut:nil]; }];
        });
        return;
    }
    /* Tahoe's spot: plate top = menu bar + 10 below the screen top, right edge 30 in; the window has T_DROP spare on top */
    CGFloat bar = NSMaxY(s) - NSMaxY(scr.visibleFrame); if (bar <= 0) bar = 24;
    [bezel setFrameOrigin:NSMakePoint(NSMaxX(s) - 30 - T_W, NSMaxY(s) - bar - 10 - T_H)];
    static double appearEnd;
    if (!tahoeShown) {
        if (!bezel.isVisible) { look.plate.transform = tahoeHidden(); look.plate.opacity = 0; }
        [bezel orderFrontRegardless];
        tahoeShown = 1;
        tahoeAnimate(look.plate, CATransform3DIdentity, 1, T_APPEAR_S, kCAMediaTimingFunctionEaseOut, nil);
        appearEnd = CACurrentMediaTime() + T_APPEAR_S;
    }
    /* held T_HOLD_S after the last key press, counted from when it is fully up */
    double hold = fmax(0, appearEnd - CACurrentMediaTime()) + T_HOLD_S;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(hold * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        if (g != bezelGen) return;               /* a later key press keeps it up */
        tahoeShown = 0;
        tahoeAnimate(look.plate, tahoeHidden(), 0, T_FADE_S, kCAMediaTimingFunctionEaseIn, ^{
            if (g == bezelGen) [bezel orderOut:nil];
        });
    });
}

/* ---- keys ---- */

static int heldWhat = -1, heldDir;               /* key being held */
static unsigned keyGen;                          /* invalidates pending repeats on release */

static void step(int what, int dir) {
    float v = getLevel(what);
    if (v < 0) return;
    if (what == VOLUME && muted()) setMuted(0);          /* a volume key unmutes, like macOS */
    float n = fminf(STEPS, fmaxf(0, roundf(v * STEPS) + dir)) / STEPS;   /* snap to the 1/16 grid, like macOS */
    setLevel(what, n);
    showBezel(what, n);
    if (what == KEYBOARD) kbdSaveLater();
}

static void repeat(unsigned g) {
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, REPEAT_MS * NSEC_PER_MSEC), dispatch_get_main_queue(), ^{
        if (g != keyGen || heldWhat < 0) return;
        step(heldWhat, heldDir); repeat(g);
    });
}

static void hidEvent(void *t, void *r, void *s, IOHIDEventRef e) {
    if (IOHIDEventGetType(e) != 3) return;
    long page = IOHIDEventGetIntegerValue(e, KB(0)), usage = IOHIDEventGetIntegerValue(e, KB(1));
    int what = -1, dir = 0;
    if ((page == 0xff && usage == 0x04) || (page == 0x0c && usage == 0x6f)) what = DISPLAY, dir = 1;
    else if ((page == 0xff && usage == 0x05) || (page == 0x0c && usage == 0x70)) what = DISPLAY, dir = -1;
    else if (page == 0xff && usage == 0x08) what = KEYBOARD, dir = 1;
    else if (page == 0xff && usage == 0x09) what = KEYBOARD, dir = -1;
    else if (page == 0x0c && usage == 0xe9) what = VOLUME, dir = 1;
    else if (page == 0x0c && usage == 0xea) what = VOLUME, dir = -1;
    else if (page == 0x0c && usage == 0xe2) {                     /* mute: toggle on key down, no repeat */
        if (IOHIDEventGetIntegerValue(e, KB(2))) {
            int before = muted();
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NATIVE_GRACE_MS * NSEC_PER_MSEC), dispatch_get_main_queue(), ^{
                if (muted() != before) return;                     /* handled natively */
                setMuted(!before); showBezel(VOLUME, fmaxf(0, getLevel(VOLUME)));
            });
        }
        return;
    }
    if (what < 0) return;
    unsigned g = ++keyGen;
    if (!IOHIDEventGetIntegerValue(e, KB(2))) { heldWhat = -1; return; }
    heldWhat = what; heldDir = dir;
    float before = getLevel(what);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NATIVE_GRACE_MS * NSEC_PER_MSEC), dispatch_get_main_queue(), ^{
        if (fabsf(getLevel(what) - before) > 0.004f) return;            /* handled natively */
        step(what, dir);
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (REPEAT_DELAY_MS - NATIVE_GRACE_MS) * NSEC_PER_MSEC),
                       dispatch_get_main_queue(), ^{ if (g == keyGen && heldWhat >= 0) { step(heldWhat, heldDir); repeat(g); } });
    });
}

#ifdef BK_PREVIEW
/*
 * Preview build only (arm64, on a Mac; not the recovery binary): brightkeys --render DIR [background]
 * Entirely offscreen (layers / views drawn into bitmaps, no window ordered front, nothing shows on the Mac running it),
 * at 1x like the 9,2 and the VM capture:
 *   cmp-<what>-gray<N>.png   the tahoe plate as captured in the VM (volume 9/16 titled "Speakers", mute) over flat grey
 *   tahoe-<state>-gray<N>.png  display / keyboard / volume at a few levels, mute
 *   screen-{tahoe,classic}-<bg>.png  placement on a 1280x800 screen (menu bar 24 pt), display at 10/16
 * The classic bezel's HUD blur does not draw offscreen (flat tint); tahoe has no blur, so it renders as on screen.
 */
static CGContextRef bitmap(size_t w, size_t h, CGFloat grey, NSImage *bg) {
    CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGContextRef c = CGBitmapContextCreate(NULL, w, h, 8, 0, cs, (CGBitmapInfo)kCGImageAlphaPremultipliedLast);
    CGColorSpaceRelease(cs);
    if (bg) {
        NSGraphicsContext *g = [NSGraphicsContext graphicsContextWithCGContext:c flipped:NO];
        [NSGraphicsContext saveGraphicsState]; NSGraphicsContext.currentContext = g;
        [bg drawInRect:NSMakeRect(0, 0, w, h)];
        [NSGraphicsContext restoreGraphicsState];
    } else {
        CGContextSetRGBFillColor(c, grey / 255, grey / 255, grey / 255, 1); CGContextFillRect(c, CGRectMake(0, 0, w, h));
    }
    return c;
}
static void writePNG(CGContextRef c, NSString *path) {
    CGImageRef im = CGBitmapContextCreateImage(c);
    NSBitmapImageRep *r = [[NSBitmapImageRep alloc] initWithCGImage:im];
    [[r representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:path atomically:YES];
    CGImageRelease(im); CGContextRelease(c);
    printf("%s\n", path.UTF8String);
}
static void drawTahoe(CGContextRef c, CGFloat x, CGFloat y, int what, float v, int mute, NSString *title) {
    Bezel *b = [[Bezel alloc] initWithStyle:TAHOE inWindow:YES];
    [b show:what level:v mute:mute title:title];
    [b.content displayIfNeeded];
    CGContextSaveGState(c); CGContextTranslateCTM(c, x, y);
    [b.plate renderInContext:c];
    CGContextRestoreGState(c);
}

static void render(NSString *dir, NSString *bgPath) {
    [NSFileManager.defaultManager createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
    int pad = 10;
    for (int grey = 0; grey < 3; grey++) {
        CGFloat gv = (CGFloat[]){40, 128, 235}[grey];
        CGContextRef c = bitmap(T_W + 2 * pad, T_H + 2 * pad, gv, nil);
        drawTahoe(c, pad, pad, VOLUME, 9.0f / 16, 0, @"Speakers");
        writePNG(c, [NSString stringWithFormat:@"%@/cmp-vol9-gray%d.png", dir, (int)gv]);
        c = bitmap(T_W + 2 * pad, T_H + 2 * pad, gv, nil);
        drawTahoe(c, pad, pad, VOLUME, 9.0f / 16, 1, @"Speakers");
        writePNG(c, [NSString stringWithFormat:@"%@/cmp-mute-gray%d.png", dir, (int)gv]);
    }
    struct { const char *name; int what; float v; int mute; NSString *title; } st[] = {
        {"display-0", DISPLAY, 0, 0, nil}, {"display-8", DISPLAY, 0.5f, 0, nil}, {"display-16", DISPLAY, 1, 0, nil},
        {"keyboard-4", KEYBOARD, 0.25f, 0, nil}, {"keyboard-12", KEYBOARD, 0.75f, 0, nil},
        {"volume-12", VOLUME, 0.75f, 0, @"Internal Speakers"}, {"volume-mute", VOLUME, 0.75f, 1, @"Internal Speakers"}};
    for (int grey = 0; grey < 2; grey++) {
        CGFloat gv = grey ? 235 : 40;
        int n = sizeof(st) / sizeof(st[0]);
        CGContextRef all = bitmap(T_W + 2 * pad, n * (T_H + pad) + pad, gv, nil);
        for (int i = 0; i < n; i++) {
            CGContextRef c = bitmap(T_W + 2 * pad, T_H + 2 * pad, gv, nil);
            drawTahoe(c, pad, pad, st[i].what, st[i].v, st[i].mute, st[i].title);
            writePNG(c, [NSString stringWithFormat:@"%@/tahoe-%s-gray%d.png", dir, st[i].name, (int)gv]);
            drawTahoe(all, pad, pad + (n - 1 - i) * (T_H + pad), st[i].what, st[i].v, st[i].mute, st[i].title);
        }
        writePNG(all, [NSString stringWithFormat:@"%@/tahoe-all-gray%d.png", dir, (int)gv]);
    }
    NSImage *bg = bgPath ? [[NSImage alloc] initWithContentsOfFile:bgPath] : nil;
    if (bg) {
        NSString *tag = bgPath.lastPathComponent.stringByDeletingPathExtension;
        CGContextRef c = bitmap(1280, 800, 0, bg);
        drawTahoe(c, 1280 - 30 - T_W, 800 - 24 - 10 - T_H, DISPLAY, 10.0f / 16, 0, nil);
        writePNG(c, [NSString stringWithFormat:@"%@/screen-tahoe-%@.png", dir, tag]);
        c = bitmap(1280, 800, 0, bg);
        Bezel *b = [[Bezel alloc] initWithStyle:CLASSIC inWindow:YES];
        [b show:DISPLAY level:10.0f / 16 mute:0];
        b.root.frame = NSMakeRect(0, 0, 200, 200);
        NSBitmapImageRep *r = [b.root bitmapImageRepForCachingDisplayInRect:b.root.bounds];
        [b.root cacheDisplayInRect:b.root.bounds toBitmapImageRep:r];
        CGContextDrawImage(c, CGRectMake(640 - 100, 140, 200, 200), r.CGImage);
        writePNG(c, [NSString stringWithFormat:@"%@/screen-classic-%@.png", dir, tag]);
    }
    exit(0);
}
#endif

int main(int argc, char **argv) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        NSApp.activationPolicy = NSApplicationActivationPolicyAccessory;   /* no Dock icon, no menu bar */
        /* never stay the active app (the Recovery window must keep the keyboard): if anything activates us, give it back */
        [NSNotificationCenter.defaultCenter addObserverForName:NSApplicationDidBecomeActiveNotification object:nil
                                                         queue:nil usingBlock:^(NSNotification *n) {
            fprintf(stderr, "brightkeys: became active; deactivating\n");
            [NSApp deactivate];
        }];
        argStyle = parseStyle(getenv("BRIGHTKEYS_STYLE"), argStyle);
        int show = 0, rend = 0; (void)rend;
        for (int i = 1; i < argc; i++) {
            if (!strncmp(argv[i], "--style=", 8)) argStyle = parseStyle(argv[i] + 8, argStyle);
            else if (!strcmp(argv[i], "--show")) show = i;
            else if (!strcmp(argv[i], "--render")) rend = i;
        }
#ifdef BK_PREVIEW
        if (rend && rend + 1 < argc) {
            render(@(argv[rend + 1]), rend + 2 < argc && strncmp(argv[rend + 2], "--", 2) ? @(argv[rend + 2]) : nil);
        }
#endif
        if (show && show + 1 < argc) {       /* brightkeys [--style=X] --show display|keyboard|volume [0..1]: one bezel, no keys */
            const char *w = argv[show + 1];
            int what = !strcmp(w, "keyboard") ? KEYBOARD : !strcmp(w, "volume") ? VOLUME : DISPLAY;
            showBezel(what, show + 2 < argc ? atof(argv[show + 2]) : 0.5f);
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{ exit(0); });
            [NSApp run];
        }
        io_service_t s = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("AppleLMUController"));
        if (s) { if (IOServiceOpen(s, mach_task_self(), 0, &lmu)) lmu = 0; IOObjectRelease(s); }
        uint16_t kbd;
        if (lmu && kbdLoad(&kbd)) {
            setLevel(KEYBOARD, (float)kbd / LMU_MAX);
            fprintf(stderr, "brightkeys: keyboard light %u from NVRAM\n", kbd);
        }
        IOHIDEventSystemClientRef c = IOHIDEventSystemClientCreateWithType(kCFAllocatorDefault, 1 /* monitor */, NULL);
        if (!c) { fprintf(stderr, "brightkeys: no HID monitor client\n"); return 1; }
        IOHIDEventSystemClientScheduleWithDispatchQueue(c, dispatch_get_main_queue());
        IOHIDEventSystemClientRegisterEventCallback(c, hidEvent, NULL, NULL);
        fprintf(stderr, "brightkeys: running (keyboard light %s)\n", lmu ? "via AppleLMUController" : "absent");
        [NSApp run];
    }
    return 0;
}
