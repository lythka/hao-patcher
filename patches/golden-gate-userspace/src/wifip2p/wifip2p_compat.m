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
#import <objc/NSObject.h>

#define WIFI_AWARE_CLASS(name) \
    __attribute__((visibility("default"))) @interface name : NSObject @end @implementation name @end

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
WIFI_AWARE_CLASS(WiFiAwareStateMonitor)
WIFI_AWARE_CLASS(WiFiAwareStateMonitorConfiguration)
WIFI_AWARE_CLASS(WiFiAwareTDSConfig)
WIFI_AWARE_CLASS(WiFiAwareTDSDevice)
WIFI_AWARE_CLASS(WiFiAwareTDSSession)
WIFI_AWARE_CLASS(WiFiP2PKeychainAgent)
WIFI_AWARE_CLASS(WiFiP2PNANStateMonitor)
WIFI_AWARE_CLASS(WiFiP2PNANStateMonitorConfiguration)
