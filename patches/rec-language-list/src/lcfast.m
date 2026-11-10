// lcfast.dylib: loaded into Apple's Language Chooser by lcwrap (DYLD_INSERT_LIBRARIES, amfi=0x80).
// Why: with no NVRAM prev-lang:kbd the Language Chooser puts up its language list but never ends its fake progress, so
// recovery's "Installer Progress" boot screen (black, Apple logo, bar) stays over the list until the 60 s abort timer
// (25G241 disassembly: -[LCApplicationController finishFakeProgressWindow] is called on every path except the list).
// What it does: right after -[LCAChoiceWindowController showLanguagePickerWindowNotifiy:withSpeechDelay:] has put the
// list on screen, it calls Apple's own finishFakeProgressWindow, the call every other path makes: the bar runs to 100 %
// at the fast speed and the boot screen hides, showing the list. Nothing else changes; if a class or method is not
// what 25G241 has, it does nothing (stock behaviour). Log: /var/log/lcfast.log.
#import <AppKit/AppKit.h>
#import <objc/message.h>
#import <objc/runtime.h>

static void say(NSString *fmt, ...) NS_FORMAT_FUNCTION(1, 2);
static void say(NSString *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    NSString *m = [[NSString alloc] initWithFormat:fmt arguments:ap];
    va_end(ap);
    FILE *f = fopen("/var/log/lcfast.log", "a");
    if (!f) return;
    time_t now = time(NULL); char ts[32]; strftime(ts, sizeof ts, "%H:%M:%S", localtime(&now));
    fprintf(f, "%s [%d] %s\n", ts, getpid(), m.UTF8String);
    fclose(f);
}

typedef char (*show_fn)(id, SEL, id, double);
static show_fn show_orig;

static char show_list(id self, SEL _cmd, id notify, double delay) {
    char r = show_orig(self, _cmd, notify, delay);
    say(@"language list shown (speech delay %.1f s)", delay);
    dispatch_async(dispatch_get_main_queue(), ^{     // after the window is ordered in
        id d = NSApp.delegate;
        if ([d respondsToSelector:@selector(finishFakeProgressWindow)]) {
            ((void (*)(id, SEL))objc_msgSend)(d, @selector(finishFakeProgressWindow));
            say(@"finishFakeProgressWindow sent: boot screen finishes and hides");
        } else
            say(@"app delegate %@ has no finishFakeProgressWindow: stock 60 s", d);
    });
    return r;
}

// Language Chooser's done (-[IASUnifiedProgressClient setDone]) ends Installer Progress "via 100 % progress", which only
// hides its windows: Installer Progress stays on the session's frontmost-only list (it adds itself in showProgressUI;
// only its hideProgressUI, -[IPAppDelegate forceToFront:NO], takes it off), so WindowServer keeps it as the front app
// behind its invisible window and refuses the Language Chooser ("not in the list of permittedFrontASNs"): no cursor,
// keys and clicks go nowhere. Stock recovery never shows the list in this state: Recovery
// Springboard's abortProgressUI sends that hideProgressUI. So after done we send it too (1 s later, after the bar's
// 100 %): Installer Progress leaves the list and activates the Language Chooser.
typedef void (*done_fn)(id, SEL);
static done_fn done_orig;

static void done_then_hide(id self, SEL _cmd) {
    done_orig(self, _cmd);
    say(@"progress done sent");
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC), dispatch_get_main_queue(), ^{
        ((void (*)(id, SEL))objc_msgSend)(self, @selector(hideProgressUI));
        say(@"hideProgressUI sent: Installer Progress gives the front back to the Language Chooser");
    });
}

__attribute__((constructor)) static void lcfast_init(void) {
    unsetenv("DYLD_INSERT_LIBRARIES");               // not into Recovery Springboard and the utilities
    Method m = class_getInstanceMethod(NSClassFromString(@"LCAChoiceWindowController"),
                                       NSSelectorFromString(@"showLanguagePickerWindowNotifiy:withSpeechDelay:"));
    if (!m || strcmp(method_getTypeEncoding(m), "c32@0:8@16d24") != 0) {
        say(@"Language Chooser is not 25G241's (method %s): nothing changed", m ? method_getTypeEncoding(m) : "missing");
        return;
    }
    Class client = NSClassFromString(@"IASUnifiedProgressClient");
    Method d = class_getInstanceMethod(client, @selector(setDone));
    if (!d || strcmp(method_getTypeEncoding(d), "v16@0:8") != 0 || !class_getInstanceMethod(client, @selector(hideProgressUI))) {
        say(@"IASUnifiedProgressClient is not 25G241's (setDone %s): nothing changed", d ? method_getTypeEncoding(d) : "missing");
        return;
    }
    show_orig = (show_fn)method_setImplementation(m, (IMP)show_list);
    done_orig = (done_fn)method_setImplementation(d, (IMP)done_then_hide);
    say(@"loaded");
}
