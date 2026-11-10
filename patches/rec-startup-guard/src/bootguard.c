/*
 * bootguard ESP_UUID ESP_BSD: keep "Startup Disk" from booting Tahoe without OpenCore (recovery LaunchDaemon, root).
 *
 * Why: recovery's Startup Disk = bless --setBoot on the internal macOS, which writes
 *   efi-boot-device      -> <internal Preboot> \<vol>\System\Library\CoreServices\boot.efi   (Apple NVRAM)
 *   Boot0080 + BootOrder -> the same device path (UEFI global; OpenCore's RequestBootVarRouting does not catch it here)
 * i.e. the firmware would start Tahoe's boot.efi directly, which this Mac cannot boot without OpenCore (the internal
 * ESP has only \EFI\OC\OpenCore.efi, no \EFI\BOOT fallback). This watches efi-boot-device (every second, and once more
 * on SIGTERM at shutdown/restart) and, when it names a boot.efi on an internal volume, points it at the internal ESP's
 * \EFI\OC\OpenCore.efi instead (whose default is that macOS); the kernel derives efi-boot-device-data from it, and the
 * UEFI Boot#### entry that BootOrder starts with gets the same device path (same header + description). Any other
 * choice (NetBoot, an external disk) is left alone. ESP_UUID/ESP_BSD come from bootguard.sh (internal-esp).
 *
 * Routing: with OpenCore's RequestBootVarRouting on, recovery's
 * reads and writes of 8BE4DF61 Boot#### / BootOrder land in OpenCore's 4D1FDA02 namespace (OCBt*), not the firmware's
 * (seen from Linux after a recovery bless: OCBt0081 / OCBtNext, while recovery read the firmware's BootOrder 0080 as
 * "not found"). There, Startup Disk's routed OCBt0080 = the internal macOS boot.efi is exactly what the internal
 * OpenCore wants (it picks that macOS as default); pointing it at \EFI\OC\OpenCore.efi would hand OpenCore an entry
 * for itself ("OC: Found previous image, aborting" if it ever starts it). So the Boot#### mirror runs only when routing
 * is off; efi-boot-device (Apple namespace, never routed) is fixed either way.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define GLOBAL "8BE4DF61-93CA-11D2-AA0D-00E098032B8C:"
#define OCV "4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102:"
static io_registry_entry_t nv;
static int routed;                               /* OpenCore boot-variable routing on in this boot */
static char target[1024];                        /* the OpenCore efi-boot-device XML */
static volatile sig_atomic_t stop;

static CFTypeRef get(const char *k) {
    CFStringRef s = CFStringCreateWithCString(NULL, k, kCFStringEncodingUTF8);
    CFTypeRef v = IORegistryEntryCreateCFProperty(nv, s, NULL, 0);
    CFRelease(s); return v;
}
static int set(const char *k, CFTypeRef v) {
    CFStringRef s = CFStringCreateWithCString(NULL, k, kCFStringEncodingUTF8);
    kern_return_t r = IORegistryEntrySetCFProperty(nv, s, v);
    CFRelease(s); return r == KERN_SUCCESS ? 0 : -1;
}
static void del(const char *k) {
    CFStringRef s = CFStringCreateWithCString(NULL, k, kCFStringEncodingUTF8);
    IORegistryEntrySetCFProperty(nv, CFSTR(kIONVRAMDeletePropertyKey), s);
    CFRelease(s);
}
/* probe write of a Boot-prefixed global variable; routed, it shows up as OCBt... in OpenCore's namespace (nvboot.c) */
static int routing(void) {
    uint8_t one = 1; CFDataRef d = CFDataCreate(NULL, &one, 1);
    del(OCV "OCBtSsdProbe");
    int w = set(GLOBAL "BootSsdProbe", d); CFRelease(d);
    usleep(100000);
    CFTypeRef r = get(OCV "OCBtSsdProbe");
    int on = !w && r;
    if (r) CFRelease(r);
    del(GLOBAL "BootSsdProbe"); del(OCV "OCBtSsdProbe");
    return w ? 1 : on;                           /* probe failed: assume routed (the safe side: no Boot#### writes) */
}
static int str(CFTypeRef v, char *buf, size_t n) {   /* efi-boot-device as text (string or data) */
    if (!v) return -1;
    if (CFGetTypeID(v) == CFStringGetTypeID()) return CFStringGetCString(v, buf, n, kCFStringEncodingUTF8) ? 0 : -1;
    if (CFGetTypeID(v) == CFDataGetTypeID()) {
        size_t l = CFDataGetLength(v); if (l >= n) return -1;
        memcpy(buf, CFDataGetBytePtr(v), l); buf[l] = 0; return 0;
    }
    return -1;
}

/* true when the volume with this UUID sits on an internal disk (IOMedia "UUID" -> parent chain "Internal") */
static int internalVolume(const char *uuid) {
    CFMutableDictionaryRef m = IOServiceMatching("IOMedia"), pm = CFDictionaryCreateMutable(NULL, 0,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFStringRef u = CFStringCreateWithCString(NULL, uuid, kCFStringEncodingUTF8);
    CFDictionarySetValue(pm, CFSTR("UUID"), u); CFDictionarySetValue(m, CFSTR(kIOPropertyMatchKey), pm);
    CFRelease(u); CFRelease(pm);
    io_service_t s = IOServiceGetMatchingService(kIOMainPortDefault, m);
    if (!s) return 0;
    /* APFS volume -> container -> physical store partition -> whole disk -> ... -> device with Protocol Characteristics */
    CFTypeRef pc = IORegistryEntrySearchCFProperty(s, kIOServicePlane, CFSTR("Protocol Characteristics"), NULL,
                                                   kIORegistryIterateRecursively | kIORegistryIterateParents);
    IOObjectRelease(s);
    int internal = 0;
    if (pc && CFGetTypeID(pc) == CFDictionaryGetTypeID()) {
        CFStringRef loc = CFDictionaryGetValue(pc, CFSTR("Physical Interconnect Location"));
        internal = loc && CFEqual(loc, CFSTR("Internal"));
    }
    if (pc) CFRelease(pc);
    return internal;
}

static void fixBootEntry(void) {
    /* the kernel derived efi-boot-device-data from our efi-boot-device; mirror it into BootOrder's first Boot#### */
    CFTypeRef order = get(GLOBAL "BootOrder"), data = get("efi-boot-device-data");
    if (!order || !data || CFGetTypeID(order) != CFDataGetTypeID() || CFDataGetLength(order) < 2 ||
        CFGetTypeID(data) != CFDataGetTypeID()) goto out;
    const uint8_t *o = CFDataGetBytePtr(order);
    char key[64]; snprintf(key, sizeof(key), GLOBAL "Boot%04X", o[0] | o[1] << 8);
    CFTypeRef old = get(key);
    if (!old || CFGetTypeID(old) != CFDataGetTypeID() || CFDataGetLength(old) < 8) { if (old) CFRelease(old); goto out; }
    /* EFI_LOAD_OPTION: UINT32 attributes, UINT16 FilePathListLength, CHAR16 description[] (NUL-terminated), path */
    const uint8_t *b = CFDataGetBytePtr(old); CFIndex n = CFDataGetLength(old), d = 6;
    while (d + 1 < n && (b[d] || b[d + 1])) d += 2;
    d += 2;
    if (d > n) { CFRelease(old); goto out; }
    CFIndex pl = CFDataGetLength(data);
    CFMutableDataRef nw = CFDataCreateMutable(NULL, 0);
    CFDataAppendBytes(nw, b, 4);
    uint8_t len[2] = {pl & 0xff, pl >> 8}; CFDataAppendBytes(nw, len, 2);
    CFDataAppendBytes(nw, b + 6, d - 6);
    CFDataAppendBytes(nw, CFDataGetBytePtr(data), pl);
    if (!CFEqual(nw, old)) fprintf(stderr, "bootguard: %s -> OpenCore path: %s\n", key + strlen(GLOBAL), set(key, nw) ? "FAILED" : "ok");
    CFRelease(nw); CFRelease(old);
out:
    if (order) CFRelease(order);
    if (data) CFRelease(data);
}

static void check(void) {
    char cur[4096];
    CFTypeRef v = get("efi-boot-device");
    int ok = str(v, cur, sizeof(cur));
    if (v) CFRelease(v);
    if (ok || !strstr(cur, "\\System\\Library\\CoreServices\\boot.efi")) return;
    char *u = strstr(cur, "<key>UUID</key><string>"), uuid[64] = "";
    if (u) sscanf(u + strlen("<key>UUID</key><string>"), "%63[^<]", uuid);
    if (!uuid[0] || !internalVolume(uuid)) return;
    CFStringRef t = CFStringCreateWithCString(NULL, target, kCFStringEncodingUTF8);
    int r = set("efi-boot-device", t); CFRelease(t);
    fprintf(stderr, "bootguard: Startup Disk chose internal boot.efi (%s): firmware -> internal OpenCore %s\n", uuid,
            r ? "FAILED" : "ok");
    if (!r && !routed) { usleep(200000); fixBootEntry(); }
}

static void onTerm(int s) { stop = 1; }

int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: bootguard ESP_UUID ESP_BSD\n"); return 2; }
    nv = IORegistryEntryFromPath(kIOMainPortDefault, "IODeviceTree:/options");
    if (!nv) { fprintf(stderr, "bootguard: no NVRAM\n"); return 1; }
    snprintf(target, sizeof(target), "<array><dict><key>IOMatch</key><dict><key>IOProviderClass</key><string>IOMedia"
             "</string><key>IOPropertyMatch</key><dict><key>UUID</key><string>%s</string></dict></dict><key>BLLastBSDName"
             "</key><string>%s</string></dict><dict><key>IOEFIDevicePathType</key><string>MediaFilePath</string><key>Path"
             "</key><string>\\EFI\\OC\\OpenCore.efi</string></dict></array>", argv[1], argv[2]);
    signal(SIGTERM, onTerm);
    routed = routing();
    fprintf(stderr, "bootguard: watching (internal OpenCore on %s %s; boot-variable routing %s%s)\n", argv[2], argv[1],
            routed ? "on" : "off", routed ? ": efi-boot-device only" : "");
    while (!stop) { check(); sleep(1); }
    check();                                     /* last look at shutdown/restart */
    return 0;
}
