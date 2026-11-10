/*
 * ssdvm: headless arm64 macOS VM on Apple's Virtualization framework; runs the image builder's guest steps.
 *
 *   ssdvm install <vmdir> <restore.ipsw> [disk-GB]      create disk/aux/hardware model, install macOS from the IPSW
 *   ssdvm run <vmdir> [--recovery] [--vnc PORT] [--share DIR] [--mac ADDRESS] [--display WxH[@PPI]] [--usb IMG]... [--disk IMG]...
 *
 * Always headless: no window, no Dock icon, no AppKit, nothing that can raise a host permission prompt.
 * run starts the VM (recoveryOS with --recovery) and a VNC server inside this process (Virtualization's private
 * _VZVNCServer, the same one tart --vnc-experimental uses). VNC needs no Screen Recording permission, works headless,
 * and gives both screenshots and keyboard/mouse input, so the whole loop is scriptable (see vmctl). The VNC port and
 * password go to <vmdir>/vnc.json. --share exposes DIR as virtiofs tag "ssd" (mount_virtiofs ssd <mnt>), --usb
 * attaches a raw image as a USB mass-storage device (the target image), --disk as a virtio block device.
 */
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <Virtualization/Virtualization.h>
#import <netinet/in.h>
#import <sys/socket.h>
#import <net/if.h>
#import <sys/file.h>

/* _VZVNCServer has no bind-address API and listens on the wildcard address (dyld interposing of bind() does not reach
 * it: the call is inside the shared cache). So after it starts, find its listening socket in this process and bind it to
 * the loopback interface (IP[V6]_BOUND_IF = lo0): connections arriving on any other interface are dropped by the kernel.
 * Returns the number of listening sockets restricted. */
static int restrictToLoopback(uint16_t port) {
    unsigned lo = if_nametoindex("lo0");
    int n = 0;
    for (int fd = 0; fd < 4096; fd++) {
        struct sockaddr_storage ss; socklen_t len = sizeof ss;
        if (getsockname(fd, (struct sockaddr *)&ss, &len)) continue;          /* (SO_ACCEPTCONN is not readable here) */
        if (ss.ss_family == AF_INET6 && ntohs(((struct sockaddr_in6 *)&ss)->sin6_port) == port) {
            int rc = setsockopt(fd, IPPROTO_IPV6, IPV6_BOUND_IF, &lo, sizeof lo);
            if (rc) fprintf(stderr, "ssdvm: IPV6_BOUND_IF on fd %d: %s\n", fd, strerror(errno));
            n += !rc;
        }
        else if (ss.ss_family == AF_INET && ntohs(((struct sockaddr_in *)&ss)->sin_port) == port)
            n += !setsockopt(fd, IPPROTO_IP, IP_BOUND_IF, &lo, sizeof lo);
    }
    return n;
}

@interface _VZVNCServer : NSObject
- (instancetype)initWithPort:(uint16_t)port queue:(dispatch_queue_t)q securityConfiguration:(id)sec;
@property (strong) VZVirtualMachine *virtualMachine;
@property (readonly) uint16_t port;
- (void)start;
- (void)stop;
@end
@interface _VZVNCAuthenticationSecurityConfiguration : NSObject
- (instancetype)initWithPassword:(NSString *)password;
@end

static void die(NSString *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    fprintf(stderr, "ssdvm: %s\n", [[NSString alloc] initWithFormat:fmt arguments:ap].UTF8String);
    va_end(ap); exit(1);
}
static NSURL *at(NSString *dir, NSString *name) { return [NSURL fileURLWithPath:[dir stringByAppendingPathComponent:name]]; }

static VZVirtualMachineConfiguration *config(NSString *dir, VZMacHardwareModel *hw, VZMacMachineIdentifier *mid,
                                             NSString *share, NSArray *usb, NSArray *disks, NSString *mac, NSString *display) {
    VZVirtualMachineConfiguration *c = [[VZVirtualMachineConfiguration alloc] init];
    VZMacPlatformConfiguration *p = [[VZMacPlatformConfiguration alloc] init];
    p.hardwareModel = hw;
    p.machineIdentifier = mid;
    p.auxiliaryStorage = [[VZMacAuxiliaryStorage alloc] initWithURL:at(dir, @"aux.img")];
    c.platform = p;
    c.bootLoader = [[VZMacOSBootLoader alloc] init];
    c.CPUCount = MIN(8, MAX(4, (NSInteger)NSProcessInfo.processInfo.processorCount / 2));
    c.memorySize = 8ull << 30;
    VZMacGraphicsDeviceConfiguration *g = [[VZMacGraphicsDeviceConfiguration alloc] init];
    /* --display WxH[@PPI]: e.g. a 13" 1280x800@113 Mac's panel, so the guest lays out its screens as that Mac does */
    NSInteger w = 1440, h = 900, ppi = 100;
    if (display && sscanf(display.UTF8String, "%ldx%ld@%ld", &w, &h, &ppi) < 2) die(@"--display wants WxH[@PPI], got %@", display);
    g.displays = @[[[VZMacGraphicsDisplayConfiguration alloc] initWithWidthInPixels:w heightInPixels:h pixelsPerInch:ppi]];
    c.graphicsDevices = @[g];
    c.keyboards = @[[[VZUSBKeyboardConfiguration alloc] init]];
    c.pointingDevices = @[[[VZUSBScreenCoordinatePointingDeviceConfiguration alloc] init]];
    VZVirtioNetworkDeviceConfiguration *n = [[VZVirtioNetworkDeviceConfiguration alloc] init];
    if (mac) {
        n.MACAddress = [[VZMACAddress alloc] initWithString:mac];
        if (!n.MACAddress || !n.MACAddress.isLocallyAdministeredAddress || n.MACAddress.isMulticastAddress) die(@"invalid local unicast MAC %@", mac);
    }
    n.attachment = [[VZNATNetworkDeviceAttachment alloc] init];
    c.networkDevices = @[n];
    /* sound device so the guest has an output with a volume (Tahoe's volume OSD); no sink: nothing plays on the host */
    VZVirtioSoundDeviceConfiguration *snd = [[VZVirtioSoundDeviceConfiguration alloc] init];
    snd.streams = @[[[VZVirtioSoundDeviceOutputStreamConfiguration alloc] init]];
    c.audioDevices = @[snd];
    NSError *e = nil;
    NSMutableArray *st = [NSMutableArray new];
    VZDiskImageStorageDeviceAttachment *root = [[VZDiskImageStorageDeviceAttachment alloc] initWithURL:at(dir, @"disk.img") readOnly:NO error:&e];
    if (!root) die(@"disk.img: %@", e);
    [st addObject:[[VZVirtioBlockDeviceConfiguration alloc] initWithAttachment:root]];
    for (NSString *d in disks) {
        BOOL ro = [d hasSuffix:@":ro"]; NSString *path = ro ? [d substringToIndex:d.length - 3] : d;
        id a = [[VZDiskImageStorageDeviceAttachment alloc] initWithURL:[NSURL fileURLWithPath:path] readOnly:ro error:&e];
        if (!a) die(@"%@: %@", path, e);
        [st addObject:[[VZVirtioBlockDeviceConfiguration alloc] initWithAttachment:a]];
    }
    for (NSString *d in usb) {
        BOOL ro = [d hasSuffix:@":ro"]; NSString *path = ro ? [d substringToIndex:d.length - 3] : d;
        id a = [[VZDiskImageStorageDeviceAttachment alloc] initWithURL:[NSURL fileURLWithPath:path] readOnly:ro error:&e];
        if (!a) die(@"%@: %@", path, e);
        [st addObject:[[VZUSBMassStorageDeviceConfiguration alloc] initWithAttachment:a]];
    }
    c.storageDevices = st;
    if (share) {
        VZVirtioFileSystemDeviceConfiguration *fs = [[VZVirtioFileSystemDeviceConfiguration alloc] initWithTag:@"ssd"];
        fs.share = [[VZSingleDirectoryShare alloc] initWithDirectory:[[VZSharedDirectory alloc] initWithURL:[NSURL fileURLWithPath:share] readOnly:NO]];
        c.directorySharingDevices = @[fs];
    }
    c.entropyDevices = @[[[VZVirtioEntropyDeviceConfiguration alloc] init]];
    if (![c validateWithError:&e]) die(@"config: %@", e);
    return c;
}

static int install(NSString *dir, NSString *ipsw, unsigned long long gb) {
    [NSFileManager.defaultManager createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
    if ([NSFileManager.defaultManager fileExistsAtPath:[dir stringByAppendingPathComponent:@"disk.img"]]) die(@"%@ already has disk.img", dir);
    __block VZMacOSRestoreImage *img = nil;
    [VZMacOSRestoreImage loadFileURL:[NSURL fileURLWithPath:ipsw] completionHandler:^(VZMacOSRestoreImage *i, NSError *e) {
        if (!i) die(@"restore image: %@", e);
        img = i; CFRunLoopStop(CFRunLoopGetMain());
    }];
    CFRunLoopRun();
    VZMacOSConfigurationRequirements *req = img.mostFeaturefulSupportedConfiguration;
    if (!req || !req.hardwareModel.supported) die(@"restore image %@ not supported on this host", img.buildVersion);
    printf("restore image %s (%ld.%ld.%ld), min %lu CPUs / %llu GB\n", img.buildVersion.UTF8String,
           (long)img.operatingSystemVersion.majorVersion, (long)img.operatingSystemVersion.minorVersion,
           (long)img.operatingSystemVersion.patchVersion, (unsigned long)req.minimumSupportedCPUCount, req.minimumSupportedMemorySize >> 30);
    NSError *e = nil;
    VZMacMachineIdentifier *mid = [[VZMacMachineIdentifier alloc] init];
    [req.hardwareModel.dataRepresentation writeToURL:at(dir, @"hw.bin") atomically:YES];
    [mid.dataRepresentation writeToURL:at(dir, @"machine.bin") atomically:YES];
    if (![[VZMacAuxiliaryStorage alloc] initCreatingStorageAtURL:at(dir, @"aux.img") hardwareModel:req.hardwareModel options:0 error:&e]) die(@"aux: %@", e);
    int fd = open([dir stringByAppendingPathComponent:@"disk.img"].fileSystemRepresentation, O_RDWR | O_CREAT | O_EXCL, 0644);
    if (fd < 0 || ftruncate(fd, (off_t)(gb << 30))) die(@"disk.img: %s", strerror(errno));
    close(fd);
    VZVirtualMachine *vm = [[VZVirtualMachine alloc] initWithConfiguration:config(dir, req.hardwareModel, mid, nil, @[], @[], nil, nil)];
    VZMacOSInstaller *ins = [[VZMacOSInstaller alloc] initWithVirtualMachine:vm restoreImageURL:[NSURL fileURLWithPath:ipsw]];
    __block int rc = 0;
    NSTimer *t = [NSTimer scheduledTimerWithTimeInterval:10 repeats:YES block:^(NSTimer *x) {
        printf("install %3.0f%%\n", ins.progress.fractionCompleted * 100); fflush(stdout);
    }];
    [ins installWithCompletionHandler:^(NSError *err) {
        if (err) { fprintf(stderr, "ssdvm: install failed: %s\n", err.description.UTF8String); rc = 1; }
        else printf("install done\n");
        CFRunLoopStop(CFRunLoopGetMain());
    }];
    CFRunLoopRun();
    [t invalidate];
    return rc;
}

@interface Runner : NSObject <VZVirtualMachineDelegate>
@property VZVirtualMachine *vm;
@property _VZVNCServer *vnc;
@property NSString *dir;
@end
@implementation Runner
- (void)guestDidStopVirtualMachine:(VZVirtualMachine *)vm { printf("guest stopped\n"); [self quit:0]; }
- (void)virtualMachine:(VZVirtualMachine *)vm didStopWithError:(NSError *)e { fprintf(stderr, "ssdvm: stopped: %s\n", e.description.UTF8String); [self quit:1]; }
- (void)quit:(int)rc {
    [self.vnc stop];
    [NSFileManager.defaultManager removeItemAtPath:[self.dir stringByAppendingPathComponent:@"vnc.json"] error:nil];
    [NSFileManager.defaultManager removeItemAtPath:[self.dir stringByAppendingPathComponent:@"run.pid"] error:nil];
    exit(rc);
}
- (void)stop {
    if (self.vm.state != VZVirtualMachineStateRunning) [self quit:0];
    NSError *e = nil;
    if (self.vm.canRequestStop && [self.vm requestStopWithError:&e]) {      /* guest shutdown first, hard stop after 30 s */
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 30 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{ [self hardStop]; });
    } else [self hardStop];
}
- (void)hardStop { [self.vm stopWithCompletionHandler:^(NSError *e) { [self quit:e ? 1 : 0]; }]; }
@end

static int run(NSString *dir, NSArray<NSString *> *args) {
    /* Also protects direct invocations outside start.sh; the kernel releases this on exit. */
    NSString *runDir = [dir stringByAppendingString:@".run"];
    [NSFileManager.defaultManager createDirectoryAtPath:runDir withIntermediateDirectories:YES attributes:nil error:nil];
    int lockfd = open([runDir stringByAppendingPathComponent:@"instance.lock"].fileSystemRepresentation, O_CREAT | O_RDWR, 0600);
    if (lockfd < 0 || flock(lockfd, LOCK_EX | LOCK_NB)) die(@"this VM is already running or cannot be locked: %@", dir);
    NSString *mac = nil, *display = nil;
    BOOL recovery = NO; uint16_t port = 0;                 /* random by default */ NSString *share = nil;
    NSMutableArray *usb = [NSMutableArray new], *disks = [NSMutableArray new];
    for (NSUInteger i = 0; i < args.count; i++) {
        NSString *a = args[i];
        if ([a isEqual:@"--recovery"]) recovery = YES;
        else if ([a isEqual:@"--vnc"] && i + 1 < args.count) port = (uint16_t)args[++i].intValue;
        else if ([a isEqual:@"--share"] && i + 1 < args.count) share = args[++i].stringByStandardizingPath;
        else if ([a isEqual:@"--mac"] && i + 1 < args.count) mac = args[++i];
        else if ([a isEqual:@"--display"] && i + 1 < args.count) display = args[++i];
        else if ([a isEqual:@"--usb"] && i + 1 < args.count) [usb addObject:args[++i]];
        else if ([a isEqual:@"--disk"] && i + 1 < args.count) [disks addObject:args[++i]];
        else die(@"unknown option %@", a);
    }
    NSData *hwd = [NSData dataWithContentsOfURL:at(dir, @"hw.bin")], *midd = [NSData dataWithContentsOfURL:at(dir, @"machine.bin")];
    if (!hwd || !midd) die(@"%@ is not an installed VM (run install first)", dir);
    VZMacHardwareModel *hw = [[VZMacHardwareModel alloc] initWithDataRepresentation:hwd];
    VZMacMachineIdentifier *mid = [[VZMacMachineIdentifier alloc] initWithDataRepresentation:midd];
    Runner *r = [Runner new];
    r.dir = dir;
    r.vm = [[VZVirtualMachine alloc] initWithConfiguration:config(dir, hw, mid, share, usb, disks, mac, display)];
    r.vm.delegate = r;
    /* VNC: localhost-only is not configurable, so a random password guards it; vmctl reads it from vnc.json */
    NSString *pw = [[NSUUID UUID].UUIDString substringToIndex:8];
    r.vnc = [[NSClassFromString(@"_VZVNCServer") alloc] initWithPort:port queue:dispatch_queue_create("ssdvm.vnc", DISPATCH_QUEUE_SERIAL)
                securityConfiguration:[[NSClassFromString(@"_VZVNCAuthenticationSecurityConfiguration") alloc] initWithPassword:pw]];
    r.vnc.virtualMachine = r.vm;
    [r.vnc start];
    int sigs[] = {SIGINT, SIGTERM};
    for (int k = 0; k < 2; k++) { int sig = sigs[k];
        signal(sig, SIG_IGN);
        dispatch_source_t s = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, sig, 0, dispatch_get_main_queue());
        dispatch_source_set_event_handler(s, ^{ [r stop]; });
        dispatch_resume(s);
        objc_setAssociatedObject(r, (void *)(intptr_t)sig, s, OBJC_ASSOCIATION_RETAIN);
    }
    VZMacOSVirtualMachineStartOptions *o = [[VZMacOSVirtualMachineStartOptions alloc] init];
    o.startUpFromMacOSRecovery = recovery;
    [r.vm startWithOptions:o completionHandler:^(NSError *e) {
        if (e) die(@"start: %@", e);
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{   /* listener opens late */
            int lo = 0;
            for (int t = 0; t < 150 && !(r.vnc.port && (lo = restrictToLoopback(r.vnc.port))); t++) usleep(100000);
            dispatch_async(dispatch_get_main_queue(), ^{
                if (!lo) {
                    fprintf(stderr, "ssdvm: could not restrict the VNC listener to lo0; stopping (VNC would be open on the network)\n");
                    [r hardStop];
                    return;
                }
                NSDictionary *j = @{@"port": @(r.vnc.port), @"password": pw, @"pid": @(getpid()), @"recovery": @(recovery), @"share": share ?: @""};
                [[NSJSONSerialization dataWithJSONObject:j options:0 error:nil] writeToURL:at(dir, @"vnc.json") atomically:YES];
                [[NSString stringWithFormat:@"%d\n", getpid()] writeToURL:at(dir, @"run.pid") atomically:YES encoding:NSUTF8StringEncoding error:nil];
                printf("running%s; VNC localhost:%u only (lo0), password-protected (see %s/vnc.json)\n", recovery ? " recoveryOS" : "", r.vnc.port, dir.UTF8String);
                fflush(stdout);
            });
        });
    }];
    dispatch_main();
    return 0;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc == 2 && !strcmp(argv[1], "capabilities")) {
            puts("ssdvm-clone-mac-v1"); return 0;
        }
        NSMutableArray<NSString *> *a = [NSMutableArray new];
        for (int i = 1; i < argc; i++) [a addObject:@(argv[i])];
        if (a.count >= 3 && [a[0] isEqual:@"install"])
            return install(a[1].stringByStandardizingPath, a[2].stringByStandardizingPath, a.count > 3 ? (unsigned long long)a[3].longLongValue : 80);
        if (a.count >= 2 && [a[0] isEqual:@"run"])
            return run(a[1].stringByStandardizingPath, [a subarrayWithRange:NSMakeRange(2, a.count - 2)]);
        fprintf(stderr, "usage: ssdvm install <vmdir> <restore.ipsw> [disk-GB]\n"
                        "       ssdvm run <vmdir> [--recovery] [--vnc PORT] [--share DIR] [--mac ADDRESS] [--display WxH[@PPI]] [--usb IMG[:ro]]... [--disk IMG[:ro]]...\n");
        return 2;
    }
}
