// wifijoin.dylib: loaded into Apple's airportd (DYLD_INSERT_LIBRARIES via launchctl debug from wifijoin.sh, amfi=0x80) so recovery auto-joins the
// Wi-Fi network saved in NVRAM (40A0DDD2-…:current-network, written by airportd itself on every menu join, password
// included) at boot, after sleep and after Wi-Fi off/on.
// Why: before every auto-join airportd asks -[CWXPCInterfaceContext __allowAutoJoinWithTrigger:reply:]; that asks the
// interface ([[self wifiClient] interfaceWithName:] ssidData) for its current SSID and, if it gets one, refuses unless __allowSSIDTransitionForTrigger: permits a network
// change ("Already associated, and network transition not permitted", 25G241 airportd 0x1000b6b7b..0x1000b6bbd). The
// legacy Broadcom driver (OCLP, BCM4331) keeps reporting the last SSID while disassociated, so auto-join was refused
// for every trigger (boot, power on, wake) and recovery never used the saved network: the menu join asked for the
// password again.
// (Allowing the transition is not enough: with an SSID present only a few triggers may auto-join, 0x1000b6bc3..b6c1f.)
// What it does: in airportd, -[CWInterface ssidData] (CoreWLAN) returns nil while the interface's link is down (SIOCGIFMEDIA: no
// IFM_ACTIVE, i.e. not associated), which is what a current driver reports. With the link up, Apple's answer stands. If
// the method is not 25G241's it does nothing.
// Log: /var/log/wifijoin.log.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <net/if.h>
#include <net/if_media.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

static void say(NSString *fmt, ...) NS_FORMAT_FUNCTION(1, 2);
static void say(NSString *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    NSString *m = [[NSString alloc] initWithFormat:fmt arguments:ap];
    va_end(ap);
    FILE *f = fopen("/var/log/wifijoin.log", "a");
    if (!f) return;
    time_t now = time(NULL); char ts[32]; strftime(ts, sizeof ts, "%H:%M:%S", localtime(&now));
    fprintf(f, "%s [%d] %s\n", ts, getpid(), m.UTF8String);
    fclose(f);
}

// 1 = link up (associated), 0 = down, -1 = unknown (then Apple's answer stands)
static int link_up(const char *ifname) {
    struct ifmediareq r;
    int s = socket(AF_INET, SOCK_DGRAM, 0), up = -1;
    if (s < 0) return -1;
    memset(&r, 0, sizeof r);
    strlcpy(r.ifm_name, ifname, sizeof r.ifm_name);
    if (ioctl(s, SIOCGIFMEDIA, &r) == 0 && (r.ifm_status & IFM_AVALID)) up = (r.ifm_status & IFM_ACTIVE) ? 1 : 0;
    close(s);
    return up;
}

// -[CWInterface ssidData] (CoreWLAN; airportd asks its own CWWiFiClient interface): nil while the link is down
typedef id (*ssid_fn)(id, SEL);
static ssid_fn ssid_orig;
static int quiet;

static id ssid(id self, SEL _cmd) {
    id d = ssid_orig(self, _cmd);
    if (!d) return d;
    id name = [self respondsToSelector:@selector(interfaceName)] ? [self valueForKey:@"interfaceName"] : nil;
    if (link_up([name isKindOfClass:[NSString class]] ? [name UTF8String] : "en1") != 0) return d;
    if (quiet++ < 20)
        say(@"%@ link down: stale SSID from the driver ignored (auto-join no longer refused as \"already associated\")", name);
    return nil;
}

__attribute__((constructor)) static void wifijoin_init(void) {
    unsetenv("DYLD_INSERT_LIBRARIES");
    if (strcmp(getprogname(), "airportd") != 0) return;
    Method m = class_getInstanceMethod(NSClassFromString(@"CWInterface"), @selector(ssidData));
    if (!m || strcmp(method_getTypeEncoding(m), "@16@0:8") != 0) {
        say(@"CoreWLAN is not 25G241's (%s): nothing changed", m ? method_getTypeEncoding(m) : "method missing");
        return;
    }
    ssid_orig = (ssid_fn)method_setImplementation(m, (IMP)ssid);
    say(@"loaded");
}
