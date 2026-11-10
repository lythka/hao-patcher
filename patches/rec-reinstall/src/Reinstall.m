/*
 * Reinstall macOS (recoveryOS): installs the hao-patcher macOS image from a USB disk instead of downloading Apple's macOS, which
 * does not start on this Mac.
 *
 * Recovery Springboard's Utilities.plist launches the installer button's Path ("/Install macOS <name>.app/Contents/
 * MacOS/InstallAssistant -RunInOSIEnv") by path (NSTask, no signature or bundle-ID check) and titles the entry
 * "Reinstall %@" from the bundle's display name; it hides its window while the task runs and comes back when it
 * exits. This binary takes that path (apply.sh replaces only that file: Apple's icon, Info.plist and display name
 * stay, so the menu looks the same). No nib: Apple's MainMenu.nib in the bundle belongs to Apple's classes.
 *
 * The work is ssdinstall's (USB bundle -> ssdpush-agent -> internal disk, verified, grown, then startup-internal):
 *   choose image + disk -> confirm (erase) -> progress (write / verify / startup disk) -> Restart.
 *   No image found: says how to attach one and offers Try Again (never Apple's downloader).
 *   InstallAssistant [-RunInOSIEnv] [--simulate FILE]   --simulate: a regular file stands in for the disk (tests)
 */
#import <AppKit/AppKit.h>
#import <IOKit/ps/IOPowerSources.h>
#import <IOKit/pwr_mgt/IOPMLib.h>

/* "macOS Tahoe" from the bundle's display name "Install macOS Tahoe" (the menu shows "Reinstall macOS Tahoe") */
static NSString *osName(void) {
    NSString *n = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleDisplayName"];
    return [n hasPrefix:@"Install "] ? [n substringFromIndex:8] : @"macOS";
}

static NSString *human(double b) {
    return [NSByteCountFormatter stringFromByteCount:(long long)b countStyle:NSByteCountFormatterCountStyleDecimal];
}

/* Inside the installer bundle the icon is Apple's installer icon; run as a bare binary (tests) a system icon. */
static NSImage *appIcon(void) {
    NSString *exe = NSBundle.mainBundle.executablePath.stringByResolvingSymlinksInPath;
    NSString *bundle = [[exe stringByDeletingLastPathComponent] stringByDeletingLastPathComponent].stringByDeletingLastPathComponent;
    if ([bundle.pathExtension isEqual:@"app"]) return [NSWorkspace.sharedWorkspace iconForFile:bundle];
    return [NSImage imageNamed:NSImageNameComputer];
}

@interface Installer : NSObject <NSApplicationDelegate>
@property NSWindow *window;
@property NSView *page;
@property NSString *tool, *simulate;
@property NSArray<NSDictionary *> *images, *disks;
@property NSPopUpButton *imagePop, *diskPop;
@property NSTextField *status, *detail;
@property NSProgressIndicator *bar;
@property NSTask *task;
@property NSMutableString *outBuf;
@property NSString *phase, *lastError, *startupNote;
@property double t0, phaseT0;
@property BOOL running;
@end

@implementation Installer

/* ------------------------------------------------------------------ tool */
- (NSString *)findTool {
    NSString *here = [NSBundle.mainBundle.executablePath stringByDeletingLastPathComponent];
    for (NSString *p in @[@"/usr/local/ssdtools/ssdinstall", [here stringByAppendingPathComponent:@"ssdinstall"]])
        if ([NSFileManager.defaultManager isExecutableFileAtPath:p]) return p;
    return nil;
}

- (NSArray<NSDictionary *> *)runList:(NSArray *)args {
    NSTask *t = [NSTask new];
    t.executableURL = [NSURL fileURLWithPath:self.tool];
    t.arguments = args;
    NSPipe *p = [NSPipe pipe];
    t.standardOutput = p;
    t.standardError = [NSFileHandle fileHandleWithNullDevice];
    if (![t launchAndReturnError:nil]) return @[];
    NSData *d = [p.fileHandleForReading readDataToEndOfFile];
    [t waitUntilExit];
    NSMutableArray *rows = [NSMutableArray new];
    for (NSString *l in [[[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] componentsSeparatedByString:@"\n"]) {
        NSArray *f = [l componentsSeparatedByString:@"\t"];
        if (f.count < 2) continue;
        NSMutableDictionary *r = [@{@"path": f[0]} mutableCopy];
        for (NSUInteger i = 1; i < f.count; i++) {
            NSRange e = [f[i] rangeOfString:@"="];
            if (e.location != NSNotFound) r[[f[i] substringToIndex:e.location]] = [f[i] substringFromIndex:e.location + 1];
            else r[[NSString stringWithFormat:@"f%lu", (unsigned long)i]] = f[i];
        }
        [rows addObject:r];
    }
    return rows;
}

/* ------------------------------------------------------------------ layout helpers */
- (NSTextField *)label:(NSString *)s size:(CGFloat)size bold:(BOOL)bold {
    NSTextField *t = [NSTextField wrappingLabelWithString:s];
    t.font = bold ? [NSFont boldSystemFontOfSize:size] : [NSFont systemFontOfSize:size];
    t.alignment = NSTextAlignmentCenter;
    return t;
}

- (void)showPage:(NSArray<NSView *> *)views buttons:(NSArray<NSButton *> *)buttons {
    [self.page removeFromSuperview];
    NSView *c = self.window.contentView;
    NSImage *img = appIcon();
    img.size = NSMakeSize(96, 96);               /* iconForFile: is 32 pt by default */
    NSImageView *icon = [NSImageView imageViewWithImage:img];
    icon.imageScaling = NSImageScaleProportionallyUpOrDown;
    [icon.widthAnchor constraintEqualToConstant:96].active = YES;
    [icon.heightAnchor constraintEqualToConstant:96].active = YES;
    NSStackView *col = [NSStackView stackViewWithViews:[@[icon] arrayByAddingObjectsFromArray:views]];
    col.orientation = NSUserInterfaceLayoutOrientationVertical;
    col.alignment = NSLayoutAttributeCenterX;
    col.spacing = 14;
    NSStackView *row = [NSStackView stackViewWithViews:buttons];
    row.spacing = 12;
    NSStackView *page = [NSStackView stackViewWithViews:@[col, row]];
    page.orientation = NSUserInterfaceLayoutOrientationVertical;
    page.distribution = NSStackViewDistributionEqualSpacing;
    page.edgeInsets = NSEdgeInsetsMake(28, 40, 24, 40);
    page.translatesAutoresizingMaskIntoConstraints = NO;
    [c addSubview:page];
    [NSLayoutConstraint activateConstraints:@[
        [page.leadingAnchor constraintEqualToAnchor:c.leadingAnchor], [page.trailingAnchor constraintEqualToAnchor:c.trailingAnchor],
        [page.topAnchor constraintEqualToAnchor:c.topAnchor], [page.bottomAnchor constraintEqualToAnchor:c.bottomAnchor]]];
    for (NSView *v in views) if ([v isKindOfClass:NSTextField.class] || [v isKindOfClass:NSProgressIndicator.class])
        [v.widthAnchor constraintEqualToConstant:520].active = YES;
    self.page = page;
    self.window.defaultButtonCell = buttons.lastObject.cell;
}

- (NSButton *)button:(NSString *)title action:(SEL)a {
    NSButton *b = [NSButton buttonWithTitle:title target:self action:a];
    [b.widthAnchor constraintGreaterThanOrEqualToConstant:110].active = YES;
    return b;
}

/* ------------------------------------------------------------------ pages */
- (void)applicationDidFinishLaunching:(NSNotification *)n {
    NSArray *args = NSProcessInfo.processInfo.arguments;
    NSUInteger s = [args indexOfObject:@"--simulate"];
    if (s != NSNotFound && s + 1 < args.count) self.simulate = args[s + 1];
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 640, 480)
                                              styleMask:NSWindowStyleMaskTitled
                                                backing:NSBackingStoreBuffered defer:NO];
    self.window.title = [NSString stringWithFormat:@"Reinstall %@", osName()];
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    self.tool = [self findTool];
    if (!self.tool) { [self fatal:@"The installer tool (ssdinstall) is missing from this recovery system." retry:NO]; return; }
    [self choosePage];
}

- (void)fatal:(NSString *)text retry:(BOOL)retry {
    NSMutableArray *b = [@[[self button:@"Quit" action:@selector(quit:)]] mutableCopy];
    if (retry) [b addObject:[self button:@"Try Again" action:@selector(again:)]];
    [self showPage:@[[self label:@"Reinstall is not available" size:20 bold:YES], [self label:text size:13 bold:NO]]
           buttons:b];
}

- (void)choosePage {
    self.images = [self runList:@[@"list-images"]];
    if (self.simulate) {                        /* the file's size, as list-disks gives a disk's */
        unsigned long long sz = [[NSFileManager.defaultManager attributesOfItemAtPath:self.simulate error:nil] fileSize];
        self.disks = @[@{@"path": self.simulate, @"f1": [NSString stringWithFormat:@"%llu", sz],
                         @"f2": @"Simulated disk (file)", @"f3": self.simulate.lastPathComponent}];
    } else self.disks = [self runList:@[@"list-disks"]];
    if (!self.images.count) {
        [self fatal:[NSString stringWithFormat:@"No %@ image was found. Connect the USB disk that holds the macOS image "
                     "for this Mac (an image folder at the top level of the disk), wait a few seconds, then click Try Again.",
                     osName()] retry:YES];
        return;
    }
    if (!self.disks.count) { [self fatal:@"No internal disk was found to install on." retry:YES]; return; }
    self.imagePop = [NSPopUpButton new];
    for (NSDictionary *i in self.images)
        [self.imagePop addItemWithTitle:[i[@"macos_version"] length]
            ? [NSString stringWithFormat:@"%@ — macOS %@ (%@), %@", i[@"name"], i[@"macos_version"],
               [i[@"macos_build"] length] ? i[@"macos_build"] : @"?", human([i[@"data_bytes"] doubleValue])]
            : [NSString stringWithFormat:@"%@, %@", i[@"name"], human([i[@"data_bytes"] doubleValue])]];
    self.diskPop = [NSPopUpButton new];
    for (NSDictionary *d in self.disks)
        [self.diskPop addItemWithTitle:[NSString stringWithFormat:@"%@ — %@ (%@)", [d[@"f3"] isEqual:@"-"] ? @"Empty disk" : d[@"f3"],
                                        d[@"f2"], human([d[@"f1"] doubleValue])]];
    NSGridView *g = [NSGridView gridViewWithViews:@[@[[NSTextField labelWithString:@"Image:"], self.imagePop],
                                                     @[[NSTextField labelWithString:@"Install on:"], self.diskPop]]];
    [g columnAtIndex:0].xPlacement = NSGridCellPlacementTrailing;
    NSMutableArray *v = [@[[self label:[NSString stringWithFormat:@"Reinstall %@", osName()] size:22 bold:YES],
                           [self label:@"The macOS image on the USB disk will be installed on this Mac's internal disk, "
                                       "ready for setup. Everything currently on that disk will be erased." size:13 bold:NO], g] mutableCopy];
    CFStringRef ps = IOPSGetProvidingPowerSourceType(NULL);
    NSTextField *warn = [self label:(ps && CFEqual(ps, CFSTR(kIOPMACPowerKey)))
                                    ? @"Keep the lid open until the installation is complete."
                                    : @"Connect the power adapter and keep the lid open until the installation is complete."
                               size:12 bold:NO];
    warn.textColor = NSColor.secondaryLabelColor;
    [v addObject:warn];
    [self showPage:v buttons:@[[self button:@"Quit" action:@selector(quit:)], [self button:@"Continue" action:@selector(confirm:)]]];
}

- (void)confirm:(id)sender {
    NSDictionary *d = self.disks[self.diskPop.indexOfSelectedItem], *i = self.images[self.imagePop.indexOfSelectedItem];
    NSAlert *a = [NSAlert new];
    a.alertStyle = NSAlertStyleCritical;
    a.messageText = [NSString stringWithFormat:@"Erase “%@” and install %@?", self.diskPop.titleOfSelectedItem, i[@"name"]];
    a.informativeText = @"All data on this disk will be permanently erased. This cannot be undone.";
    [a addButtonWithTitle:@"Erase and Install"];
    [a addButtonWithTitle:@"Cancel"];
    a.buttons[1].keyEquivalent = @"\e";
    a.buttons[0].keyEquivalent = @"";
    [a beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse r) {
        if (r == NSAlertFirstButtonReturn) [self install:i on:d];
    }];
}

- (void)install:(NSDictionary *)image on:(NSDictionary *)disk {
    self.status = [self label:@"Preparing…" size:15 bold:YES];
    self.detail = [self label:@" " size:12 bold:NO];
    self.detail.textColor = NSColor.secondaryLabelColor;
    self.bar = [NSProgressIndicator new];
    self.bar.indeterminate = YES;
    self.bar.minValue = 0; self.bar.maxValue = 100;
    [self.bar startAnimation:nil];
    [self showPage:@[[self label:[NSString stringWithFormat:@"Installing %@", image[@"name"]] size:20 bold:YES], self.bar, self.status, self.detail]
           buttons:@[]];
    NSMutableArray *args = [@[@"install", image[@"path"], disk[@"path"], @"--yes"] mutableCopy];
    [args addObject:self.simulate ? @"--file" : @"--startup"];
    self.task = [NSTask new];
    self.task.executableURL = [NSURL fileURLWithPath:self.tool];
    self.task.arguments = args;
    NSPipe *p = [NSPipe pipe];
    self.task.standardOutput = p;
    self.task.standardError = p;
    self.outBuf = [NSMutableString new];
    self.lastError = self.startupNote = nil;
    self.t0 = self.phaseT0 = NSDate.timeIntervalSinceReferenceDate;
    p.fileHandleForReading.readabilityHandler = ^(NSFileHandle *h) {
        NSData *d = h.availableData;
        if (!d.length) { h.readabilityHandler = nil; return; }
        NSString *s = [[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] ?: @"";
        dispatch_async(dispatch_get_main_queue(), ^{ [self consume:s]; });
    };
    __weak Installer *w = self;
    self.task.terminationHandler = ^(NSTask *t) {
        dispatch_async(dispatch_get_main_queue(), ^{ [w finished:t.terminationStatus]; });
    };
    NSError *e;
    self.running = [self.task launchAndReturnError:&e];
    if (!self.running) [self failed:e.localizedDescription];
}

- (void)consume:(NSString *)s {
    [self.outBuf appendString:s];
    NSRange r;
    while ((r = [self.outBuf rangeOfString:@"\n"]).location != NSNotFound) {
        NSString *l = [self.outBuf substringToIndex:r.location];
        [self.outBuf deleteCharactersInRange:NSMakeRange(0, r.location + 1)];
        fprintf(stderr, "%s\n", l.UTF8String);                      /* recovery's log / Terminal */
        NSArray *f = [l componentsSeparatedByString:@" "];
        if ([l hasPrefix:@"STEP "]) [self step:f[1]];
        else if ([l hasPrefix:@"PROGRESS "] && f.count == 4) [self progress:f[1] done:[f[2] doubleValue] total:[f[3] doubleValue]];
        else if ([l hasPrefix:@"ERROR "]) self.lastError = [l substringFromIndex:6];
        else if ([l hasPrefix:@"INFO startup: "]) self.startupNote = [l substringFromIndex:14];
    }
}

- (void)step:(NSString *)s {
    self.phase = s;
    self.phaseT0 = NSDate.timeIntervalSinceReferenceDate;
    NSDictionary *t = @{@"prepare": @"Preparing…", @"delta": @"Checking what is already on the disk…", @"write": @"Installing macOS…",
                        @"gpt": @"Writing the partition table…", @"flush": @"Finishing writes…", @"verify": @"Verifying the installation…",
                        @"grow": @"Making the whole disk available…", @"startup": @"Setting the startup disk…", @"done": @"Done"};
    self.status.stringValue = t[s] ?: s;
    if ([s isEqual:@"gpt"] || [s isEqual:@"flush"] || [s isEqual:@"grow"] || [s isEqual:@"startup"]) self.detail.stringValue = @" ";
}

/* overall bar: write 0-80 %, verify 80-98 %, the short steps after; delta (if any) takes write's first 10 % */
- (void)progress:(NSString *)phase done:(double)d total:(double)t {
    if (t <= 0) return;
    double f = d / t, lo = 0, hi = 0;
    if ([phase isEqual:@"delta"]) { lo = 0; hi = 10; }
    else if ([phase isEqual:@"write"]) { lo = 10 * ([self.task.arguments containsObject:@"--delta"]); hi = 80; }
    else if ([phase isEqual:@"verify"]) { lo = 80; hi = 98; }
    self.bar.indeterminate = NO;
    self.bar.doubleValue = lo + (hi - lo) * f;
    double el = NSDate.timeIntervalSinceReferenceDate - self.phaseT0;
    NSString *eta = @"";
    if (f > 0.02 && el > 5) {
        int left = (int)(el / f - el);
        eta = left > 90 ? [NSString stringWithFormat:@" — about %d minutes remaining", (left + 59) / 60]
                        : [NSString stringWithFormat:@" — about %d seconds remaining", MAX(left, 1)];
    }
    self.detail.stringValue = [NSString stringWithFormat:@"%@ of %@%@", human(d), human(t), eta];
}

- (void)finished:(int)status {
    self.running = NO;
    if (status) { [self failed:self.lastError ?: [NSString stringWithFormat:@"The installer stopped (status %d).", status]]; return; }
    int mins = (int)((NSDate.timeIntervalSinceReferenceDate - self.t0 + 59) / 60);
    NSString *how = self.simulate ? @"Simulation complete: the image was written to the file and verified."
                                  : @"macOS was installed and verified. Your Mac will start up from its internal disk.";
    NSTextField *note = [self label:self.startupNote ?: @" " size:11 bold:NO];
    note.textColor = NSColor.secondaryLabelColor;
    [self showPage:@[[self label:@"Installation complete" size:22 bold:YES],
                     [self label:[NSString stringWithFormat:@"%@ (%d min)", how, mins] size:13 bold:NO], note]
           buttons:@[[self button:@"Quit" action:@selector(quit:)], [self button:@"Restart" action:@selector(restart:)]]];
}

- (void)failed:(NSString *)why {
    [self showPage:@[[self label:@"The installation failed" size:22 bold:YES], [self label:why size:13 bold:NO],
                     [self label:@"Your Mac may not start up from its internal disk until an installation completes. "
                                 "You can try again, or quit to use the other recovery tools." size:12 bold:NO]]
           buttons:@[[self button:@"Quit" action:@selector(quit:)], [self button:@"Try Again" action:@selector(again:)]]];
}

- (void)again:(id)s { [self choosePage]; }
- (void)quit:(id)s { [NSApp terminate:nil]; }

- (void)restart:(id)s {
    if (self.simulate) { [NSApp terminate:nil]; return; }
    sync();
    [NSTask launchedTaskWithExecutableURL:[NSURL fileURLWithPath:@"/sbin/shutdown"] arguments:@[@"-r", @"now"] error:nil terminationHandler:nil];
}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)a {
    if (!self.running) return NSTerminateNow;
    NSAlert *x = [NSAlert new];
    x.messageText = @"macOS is being installed";
    x.informativeText = @"Quitting now would leave the internal disk unusable. Wait until the installation is complete.";
    [x beginSheetModalForWindow:self.window completionHandler:nil];
    return NSTerminateCancel;
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)a { return !self.running; }
@end

int main(int argc, char **argv) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        NSApp.activationPolicy = NSApplicationActivationPolicyRegular;
        NSMenu *bar = [NSMenu new], *app = [NSMenu new];
        NSMenuItem *top = [NSMenuItem new];
        top.submenu = app;
        [bar addItem:top];
        [app addItemWithTitle:[NSString stringWithFormat:@"Quit Reinstall %@", osName()] action:@selector(terminate:) keyEquivalent:@"q"];
        NSApp.mainMenu = bar;
        Installer *i = [Installer new];
        NSApp.delegate = i;
        [NSApp run];
    }
    return 0;
}
