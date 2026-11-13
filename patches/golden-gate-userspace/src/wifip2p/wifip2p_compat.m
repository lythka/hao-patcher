// WiFiPeerToPeer compatibility library (input row wifip2p_compat, made and checked by make in this folder).
//
// OCLP's Modern Wireless wrapper (PatcherSupportPkg 13.7.2-25) replaces WiFiPeerToPeer and re-exports
// WiFiPeerToPeerOld.dylib. Once a file overrides the cached library, dyld repoints every cached client's binding to
// it, and the wrapper lacks the 36 Wi-Fi Aware (NAN) classes below, which 26A428 and 26B131 bind identically. dyld sets
// the missing class references to 0xbad4007, and libobjc then aborts any process that loads a category on one of them:
// APTransport's category on WiFiAwareDatapathInfo kills coreaudiod (through the AirPlay HAL plug-in),
// AirPlayXPCHelper, ControlCenter and sharingd.
//
// In both builds' own x86_64 WiFiPeerToPeer every one of these classes is a direct NSObject subclass. This MacBook Pro
// has no Wi-Fi Aware hardware, so the classes are defined empty: clients' categories attach to them and objects can be
// allocated, but no Wi-Fi Aware behavior is implemented. Every other name resolves to the re-exported wrapper,
// renamed WiFiPeerToPeerOCLP.dylib.
//
// A stand-in also answers the methods a 27.x on-disk client sends to it (make's method check, ../io80211/methods.py,
// stops the build otherwise), as no-ops:
// - WiFiP2PKeychainAgent -activate/-invalidate: 27.x's WiFiAgent creates and activates one in every user (Aqua)
//   session, before it opens its com.apple.wifi.WiFiAgent listener; the unrecognized selector stopped that, so Setup's
//   own Wi-Fi join timed out. The real agent syncs AirDrop/peer-to-peer Wi-Fi keychain items over XPC.
// - WiFiAwareStateMonitor monitoring and handler setters: named by 27.x's airportd. The handlers are never called.
//
// And methods 27.x clients send to OCLP's own classes that OCLP's 13.7.2 library lacks (kMethods, added when the
// library loads with class_addMethod, which never replaces an existing method). oclp-methods.txt records OCLP's real
// method lists (read on the laptop at run time; make checks every client-named selector against it and kMethods):
// - -[AWDLServiceDiscoveryManager addAWDLDiscoveryClient:forService:error:] and remove...: 27.x's mDNSResponder
//   registers each AWDL Bonjour service (AirDrop's) through them; the unrecognized selector killed mDNSResponder at
//   every registration (16 crashes in one session, 2026-11-13), taking DNS and Bonjour down with it. They report
//   ENOTSUP, which mDNSResponder handles: no crash, but no AWDL discovery either (no bridge to OCLP's
//   startServiceDiscoveryWithConfiguration: yet).
// - Wi-Fi Aware methods newer than 13.7.2 on OCLP's WiFiAwareDataSession, WiFiAwarePublisher and the publish/subscribe
//   configurations (no Wi-Fi Aware hardware here): getters answer nil/0, setters ignore, the secure-connection
//   initializer fails (nil), pairing mode reports ENOTSUP to its completion block.
// - -[WiFiP2PUIAgent userNotificationCenter:didReceiveNotificationResponse:withCompletionHandler:]: calls the
//   completion handler (UNUserNotificationCenterDelegate requires it) and does nothing else.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <errno.h>
#include <os/log.h>

#define STAND_IN __attribute__((visibility("default")))
#define WIFI_AWARE_CLASS(name) STAND_IN @interface name : NSObject @end @implementation name @end

WIFI_AWARE_CLASS(WiFiAwareBandSchedule)
WIFI_AWARE_CLASS(WiFiAwareChannelConfiguration)
WIFI_AWARE_CLASS(WiFiAwareChannelSchedule)
WIFI_AWARE_CLASS(WiFiAwareDatapathInfo)
WIFI_AWARE_CLASS(WiFiAwareDataPathSessionInfo)
WIFI_AWARE_CLASS(WiFiAwareDataPathState)
WIFI_AWARE_CLASS(WiFiAwareDataSessionConfig)
WIFI_AWARE_CLASS(WiFiAwareInterfaceInfo)
WIFI_AWARE_CLASS(WiFiAwareNDIInfo)
WIFI_AWARE_CLASS(WiFiAwareNDISessionCounters)
WIFI_AWARE_CLASS(WiFiAwarePairingConfig)
WIFI_AWARE_CLASS(WiFiAwarePairingPasswordVoucher)
WIFI_AWARE_CLASS(WiFiAwarePairingPasswordVoucherStore)
WIFI_AWARE_CLASS(WiFiAwarePairingSession)
WIFI_AWARE_CLASS(WiFiAwarePeerAvailBandEntry)
WIFI_AWARE_CLASS(WiFiAwarePeerAvailChannelEntry)
WIFI_AWARE_CLASS(WiFiAwarePeerAvailInfo)
WIFI_AWARE_CLASS(WiFiAwarePeerDevice)
WIFI_AWARE_CLASS(WiFiAwarePeerInfo)
WIFI_AWARE_CLASS(WiFiAwarePerformanceForecast)
WIFI_AWARE_CLASS(WiFiAwarePKBootstrappingRecordIngestionResult)
WIFI_AWARE_CLASS(WiFiAwarePKBootstrappingRecords)
WIFI_AWARE_CLASS(WiFiAwarePKBootstrappingRecordsIngestionReport)
WIFI_AWARE_CLASS(WiFiAwarePKBootstrappingRecordsStore)
WIFI_AWARE_CLASS(WiFiAwareRadioSchedule)
WIFI_AWARE_CLASS(WiFiAwareSrvInfo)
WIFI_AWARE_CLASS(WiFiAwareSrvSessionInfo)
WIFI_AWARE_CLASS(WiFiAwareState)
WIFI_AWARE_CLASS(WiFiAwareStateMonitorConfiguration)
WIFI_AWARE_CLASS(WiFiAwareTDSConfig)
WIFI_AWARE_CLASS(WiFiAwareTDSDevice)
WIFI_AWARE_CLASS(WiFiAwareTDSSession)
WIFI_AWARE_CLASS(WiFiP2PNANStateMonitor)
WIFI_AWARE_CLASS(WiFiP2PNANStateMonitorConfiguration)

STAND_IN @interface WiFiAwareStateMonitor : NSObject @end
@implementation WiFiAwareStateMonitor
- (void)beginMonitoring {}
- (void)endMonitoring {}
- (void)setStateUpdatedHandler:(id)handler { (void)handler; }
- (void)setRealtimeModeUpdatedHandler:(id)handler { (void)handler; }
@end

STAND_IN @interface WiFiP2PKeychainAgent : NSObject @end
@implementation WiFiP2PKeychainAgent
- (void)activate {}
- (void)invalidate {}
@end

static BOOL AWDLDiscoveryUnsupported(id self, SEL _cmd, id client, id service, NSError **error) {
    (void)self; (void)client; (void)service;
    os_log_error(OS_LOG_DEFAULT, "WiFiPeerToPeer compat: %{public}s unsupported (OCLP's AWDL has no such call)",
                 sel_getName(_cmd));
    if (error)
        *error = [NSError errorWithDomain:NSPOSIXErrorDomain code:ENOTSUP userInfo:nil];
    return NO;
}

static id ReturnNil(id self, SEL _cmd) { (void)self; (void)_cmd; return nil; }
static void IgnoreObject(id self, SEL _cmd, id value) { (void)self; (void)_cmd; (void)value; }
static void DoNothing(id self, SEL _cmd) { (void)self; (void)_cmd; }
static id InitFails(id self, SEL _cmd, id a, id b) { (void)self; (void)_cmd; (void)a; (void)b; return nil; }

static void PairingUnsupported(id self, SEL _cmd, id config, id delegate, void (^completion)(NSError *)) {
    (void)self; (void)config; (void)delegate;
    os_log_error(OS_LOG_DEFAULT, "WiFiPeerToPeer compat: %{public}s unsupported (no Wi-Fi Aware)", sel_getName(_cmd));
    if (completion)
        completion([NSError errorWithDomain:NSPOSIXErrorDomain code:ENOTSUP userInfo:nil]);
}

static void NotificationResponse(id self, SEL _cmd, id center, id response, void (^completion)(void)) {
    (void)self; (void)_cmd; (void)center; (void)response;
    if (completion)
        completion();
}

typedef struct {
    const char *cls, *sel, *types;
    IMP imp;
} CompatMethod;

static const CompatMethod kMethods[] = {
    {"AWDLServiceDiscoveryManager", "addAWDLDiscoveryClient:forService:error:", "c40@0:8@16@24^@32",
     (IMP)AWDLDiscoveryUnsupported},
    {"AWDLServiceDiscoveryManager", "removeAWDLDiscoveryClient:forService:error:", "c40@0:8@16@24^@32",
     (IMP)AWDLDiscoveryUnsupported},
    {"WiFiAwareDataSession", "datapathID", "@16@0:8", (IMP)ReturnNil},
    {"WiFiAwareDataSession", "initSecureConnectionWithPairedDevice:configuration:", "@32@0:8@16@24", (IMP)InitFails},
    {"WiFiAwareDataSession", "initiatorDataAddress", "@16@0:8", (IMP)ReturnNil},
    {"WiFiAwareDataSession", "multicastConfiguration", "@16@0:8", (IMP)ReturnNil},
    {"WiFiAwareDataSession", "setMulticastConfiguration:", "v24@0:8@16", (IMP)IgnoreObject},
    {"WiFiAwarePublishConfiguration", "setUserID:", "v24@0:8@16", (IMP)IgnoreObject},
    {"WiFiAwarePublisher", "activatePairingModeWithConfig:delegate:completionHandler:", "v40@0:8@16@24@?32",
     (IMP)PairingUnsupported},
    {"WiFiAwarePublisher", "deactivatePairingMode", "v16@0:8", (IMP)DoNothing},
    {"WiFiAwareSubscribeConfiguration", "setUserID:", "v24@0:8@16", (IMP)IgnoreObject},
    {"WiFiP2PUIAgent", "userNotificationCenter:didReceiveNotificationResponse:withCompletionHandler:",
     "v40@0:8@16@24@?32", (IMP)NotificationResponse},
};

// The number of kMethods entries this process added (an entry is skipped when its class is missing or already has
// the method). Exported for the host tests.
STAND_IN unsigned WiFiP2PCompatMethodsAdded;

__attribute__((constructor)) static void AddMethods(void) {
    for (size_t i = 0; i < sizeof kMethods / sizeof *kMethods; i++) {
        Class cls = objc_getClass(kMethods[i].cls);
        if (!cls) {
            os_log_error(OS_LOG_DEFAULT, "WiFiPeerToPeer compat: class %{public}s missing", kMethods[i].cls);
            continue;
        }
        if (class_addMethod(cls, sel_registerName(kMethods[i].sel), kMethods[i].imp, kMethods[i].types))
            WiFiP2PCompatMethodsAdded++;
    }
}
