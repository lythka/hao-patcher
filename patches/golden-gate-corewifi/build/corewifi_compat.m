// CoreWiFi compatibility library (row corewifi_compat, made and checked by make in this folder).
//
// Installed as CoreWiFi, it re-exports Tahoe 25G342's x86_64 CoreWiFi (CoreWiFiTahoe.dylib, cut from that build's
// shared cache by tools/cache-dylib) so that Tahoe's airportd, the only Intel airportd this image has, runs with the
// CoreWiFi it was built with, and every 27.x client uses the same copy (dyld repoints the cached clients' bindings to
// this file). It adds only what 27.x clients bind or send that Tahoe's CoreWiFi lacks; make's coverage check
// (corewifi_coverage.py) stops the build if a client's import or selector is left unanswered.
//
// Symbols, bound strongly by on-disk 27.x clients:
// - CWFEventLinkQualityAssessmentKey (SymptomEvaluator): 27.x's value. Tahoe's CoreWiFi posts no link-quality
//   assessment event, so no event dictionary ever holds the key.
// - CWFColocatedConsentResult (iOSSupport SpeakerGroupSetupKit): 27.x's class (NSObject; status, networks). Tahoe's
//   CoreWiFi has no colocated-network consent scan, so it never creates one.
//
// Methods 27.x clients name that Tahoe's classes lack (kMethods): added to Tahoe's classes when the library loads,
// with class_addMethod, which never replaces an existing method. They are no-ops: getters return 0/NO, setters ignore
// their value, the two scan/coexistence requests report ENOTSUP, and -[CWFConfigurationProfileManager
// activateWithCompletion:] activates the manager the Tahoe way (-activate) and then calls the completion block.
// The types are 27.x's (26B131 x86_64) encodings.
#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <errno.h>
#include <os/log.h>

#define EXPORT __attribute__((visibility("default")))

EXPORT NSString *const CWFEventLinkQualityAssessmentKey = @"LinkQualityAssessment";

EXPORT @interface CWFColocatedConsentResult : NSObject
@property (readonly, nonatomic) NSInteger status;
@property (readonly, copy, nonatomic) NSArray *networks;
@end

@implementation CWFColocatedConsentResult
- (instancetype)__initWithStatus:(NSInteger)status networks:(NSArray *)networks {
    if ((self = [super init])) {
        _status = status;
        _networks = [networks copy];
    }
    return self;
}
- (NSString *)description {
    return [NSString stringWithFormat:@"<CWFColocatedConsentResult status=%ld networks=%lu>", (long)_status,
            (unsigned long)_networks.count];
}
@end

static NSError *Unsupported(void) {
    return [NSError errorWithDomain:NSPOSIXErrorDomain code:ENOTSUP
                           userInfo:@{NSLocalizedDescriptionKey: @"Not supported by this Mac's CoreWiFi"}];
}

// Integer, BOOL or object getters: 0, NO or nil.
static intptr_t ReturnZero(id self, SEL _cmd) { (void)self; (void)_cmd; return 0; }
// Setters taking an integer or BOOL.
static void IgnoreValue(id self, SEL _cmd, uintptr_t value) { (void)self; (void)_cmd; (void)value; }
static void DoNothing(id self, SEL _cmd) { (void)self; (void)_cmd; }

// -[CWFInterface setBTCoexActiveProfileName:error:] (WirelessRadioManagerd): no Bluetooth coexistence profiles here.
static BOOL SetBTCoexProfile(id self, SEL _cmd, NSString *name, NSError **error) {
    (void)self; (void)_cmd; (void)name;
    if (error)
        *error = Unsupported();
    return NO;
}

// -[CWFInterface performColocatedNetworkScanWithConsentStatus:] (SpeakerGroupSetupKit): 27.x calls the handler with
// (result, error) on a global queue; it reports an error when it cannot scan, as here.
static void ColocatedScan(id self, SEL _cmd, void (^handler)(id result, NSError *error)) {
    (void)self; (void)_cmd;
    if (!handler)
        return;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{ handler(nil, Unsupported()); });
}

// -[CWFConfigurationProfileManager activateWithCompletion:] (threadradiod, nearbyd, sharingd, ...): 27.x activates on
// the manager's queue and then calls the completion block (no arguments). Tahoe's manager has -activate.
static void ActivateWithCompletion(id self, SEL _cmd, void (^completion)(void)) {
    (void)_cmd;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        ((void (*)(id, SEL))objc_msgSend)(self, sel_registerName("activate"));
        if (completion)
            completion();
    });
}

typedef struct {
    const char *cls, *sel, *types;
    IMP imp;
} CompatMethod;

static const CompatMethod kMethods[] = {
    {"CWFActivity", "processIdentifier", "i16@0:8", (IMP)ReturnZero},
    {"CWFAutoJoinMetric", "duration", "Q16@0:8", (IMP)ReturnZero},
    {"CWFAutoJoinMetric", "outcome", "Q16@0:8", (IMP)ReturnZero},
    {"CWFAutoJoinMetric", "setOutcome:", "v24@0:8Q16", (IMP)IgnoreValue},
    {"CWFConfigurationProfileManager", "activateWithCompletion:", "v24@0:8@?16", (IMP)ActivateWithCompletion},
    {"CWFInterface", "performColocatedNetworkScanWithConsentStatus:", "v24@0:8@?16", (IMP)ColocatedScan},
    {"CWFInterface", "setBTCoexActiveProfileName:error:", "c32@0:8@16^@24", (IMP)SetBTCoexProfile},
    {"CWFNetworkProfile", "carPlayCheckForInternetState", "q16@0:8", (IMP)ReturnZero},
    {"CWFNetworkProfile", "isConnectivityAssistEnabled", "c16@0:8", (IMP)ReturnZero},
    {"CWFNetworkProfile", "setConnectivityAssistEnabled:", "v20@0:8c16", (IMP)IgnoreValue},
    {"CWFScanParameters", "setExcludeScanFlags:", "v24@0:8Q16", (IMP)IgnoreValue},
    {"CWFScanResult", "mrsnoConnection", "c16@0:8", (IMP)ReturnZero},
    {"CWFScanResult", "rsnPriority", "c16@0:8", (IMP)ReturnZero},
    {"CWFUIScanManager", "resetCache", "v16@0:8", (IMP)DoNothing},
    {"NSDictionary", "mrsnoConnection", "c16@0:8", (IMP)ReturnZero},
    {"NSDictionary", "rsnPriority", "c16@0:8", (IMP)ReturnZero},
};

// The number of kMethods entries this process added (an entry is skipped when its class is missing or already has
// the method). Exported for the host tests.
EXPORT unsigned CoreWiFiCompatMethodsAdded;

__attribute__((constructor)) static void AddMethods(void) {
    for (size_t i = 0; i < sizeof kMethods / sizeof *kMethods; i++) {
        Class cls = objc_getClass(kMethods[i].cls);
        if (!cls) {
            os_log_error(OS_LOG_DEFAULT, "CoreWiFi compat: class %{public}s missing", kMethods[i].cls);
            continue;
        }
        if (class_addMethod(cls, sel_registerName(kMethods[i].sel), kMethods[i].imp, kMethods[i].types))
            CoreWiFiCompatMethodsAdded++;
    }
}
