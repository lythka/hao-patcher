// IO80211 functions 27.x callers bind to that OCLP's Modern Wireless wrapper (with its IO80211Old.dylib) gets wrong:
// - seven it exports as data (an 8-byte pointer to a CFString of the function's own name), so a call lands in the
//   wrapper's __DATA and faults (airportd SIGBUS in WiFiPolicy, -[WiFiUsageLinkSession eventDictionary:] ->
//   apple80211_authtype_uppertoStr);
// - one it does not export at all (Apple80211Get20MhzChannelIndexBitmap, bound by WiFiAnalytics), which dyld
//   cannot bind to anything once the wrapper overrides the cached IO80211.
// Ported from the x86_64 IO80211 of 26A428 (1585.79, UUID 79BE178F) and 26B131 (1587.10, UUID CA44E6CB), the
// RosettaOS dyld caches 27.x's WiFiPolicy/CoreWiFi/WiFiAnalytics are linked with. A full 32-bit sweep of both
// (tests/io80211-expected-BUILD-x86_64.txt) differs in one point: 26B131 maps 0xe0822410 to -3998. All eight are leaf
// functions of 32-bit arguments. coverage.py checks a build's clients against this library and OCLP's (README
// "IO80211"). This library re-exports OCLP's wrapper (IO80211OCLP.dylib), so every other IO80211 name resolves as
// before.
#include <stdint.h>

#define EXPORT __attribute__((visibility("default")))

EXPORT const char *apple80211_apmodetoStr(int mode) {
    switch (mode) {
    case 0: return "UNKNOWN";
    case 1: return "IBSS";
    case 2: return "INFRA";
    case 3: return "ANY";
    default: return "unknown";
    }
}

// Tahoe's 25G332 table has 13 entries (no ORBI, STEERING, ORBI_TRI_BAND, P2P_REAL_TIME_MOBILE, so 7..12 name
// different profiles); 27.0's callers use 27.0's numbering.
EXPORT const char *apple80211_roam_profile_typetoStr(int type) {
    switch (type) {
    case 0: return "UNKNOWN";
    case 1: return "SINGLE_BAND_SINGLE_AP";
    case 2: return "DUAL_BAND_SINGLE_AP";
    case 3: return "MULTI_AP";
    case 4: return "DISABLED";
    case 5: return "SUPPRESSION";
    case 6: return "SLEEP";
    case 7: return "ORBI";
    case 8: return "STEERING";
    case 9: return "TRI_BAND_SINGLE_AP";
    case 10: return "TRI_BAND_MULTI_AP";
    case 11: return "DUAL_BAND_NO_5G_SINGLE_AP";
    case 12: return "DUAL_BAND_NO_5G_MULTI_AP";
    case 13: return "ORBI_TRI_BAND";
    case 14: return "UNKNOWN_NO_6G";
    case 15: return "P2P_REAL_TIME_MOBILE";
    case 16: return "COUNT";
    default: return "unknown";
    }
}

EXPORT const char *apple80211_authtype_uppertoStr(int authtype) {
    switch (authtype) {
    case 0x0: return "NONE";
    case 0x1: return "WPA";
    case 0x2: return "WPA_PSK";
    case 0x4: return "WPA2";
    case 0x8: return "WPA2_PSK";
    case 0x10: return "FT_PSK";
    case 0x20: return "LEAP";
    case 0x40: return "8021X";
    case 0x80: return "FT_8021X";
    case 0x100: return "WPS";
    case 0x200: return "WAPI";
    case 0x400: return "SHA256_PSK";
    case 0x800: return "SHA256_8021X";
    case 0x1000: return "WPA3_SAE";
    case 0x2000: return "FT_SAE";
    case 0x4000: return "SHA384_8021X";
    case 0x8000: return "SHA384_FT_8021X";
    case 0xffff: return "UNKNOWN";
    case 0x10000: return "AKM_24";
    case 0x20000: return "AKM_25";
    default: return "unknown";
    }
}

EXPORT const char *apple80211_roam_status_bss_flagstoStr(int flag) {
    switch (flag) {
    case 0x1: return "NEIGHBOR_REPORT_SUPPORTED";
    case 0x2: return "FAST_BSS_TRANSITION_SUPPORTED";
    case 0x4: return "BSS_TRANSITION_MGMT_SUPPORTED";
    case 0x8: return "ADAPTIVE_FBT_SUPPORTED";
    case 0x10: return "FASTLANE_SUPPORTED";
    case 0x20: return "NETWORK_ASSURANCE_SUPPORTED";
    case 0x40: return "WNM_SCORE_STATUS";
    case 0x80: return "ROAM_TO_COLOCATED_AP";
    default: return "unknown";
    }
}

EXPORT const char *convertPhyTypeToString(int phy) {
    switch (phy) {
    case 0x2: return "11A";
    case 0x4: return "11B";
    case 0x8: return "11G";
    case 0x10: return "11N";
    case 0x80: return "11AC";
    case 0x100: return "11AX";
    case 0x200: return "11BE";
    default: return "unknown";
    }
}

// errno values and IOReturn codes to Apple80211 errors (-3900...); anything else passes through.
// Tahoe's 25G332 table stops at 0xe0822409; 27.0 adds 0xe082240a..0xe082240f, 27.2.1 0xe0822410.
EXPORT int convertToApple80211Error(int error) {
    switch ((unsigned)error) {
    case 16: return -3951;          // EBUSY
    case 22: return -3949;          // EINVAL
    case 37: return -3952;          // EALREADY
    case 0xe00002bd: return -3901;  // kIOReturnNoMemory
    case 0xe00002f0: return -3947;  // kIOReturnNotFound
    case 0xe0820442: return -3950;
    case 0xe0822401: return -3948;
    case 0xe0822402: return -3947;
    case 0xe0822407: return -3945;
    case 0xe0822409: return -3899;
    case 0xe082240a: return -3946;
    case 0xe082240b: return -3993;
    case 0xe082240c: return -3994;
    case 0xe082240d: return -3995;
    case 0xe082240e: return -3996;
    case 0xe082240f: return -3997;
    case 0xe0822410: return -3998;  // 26B131; 26A428 passes it through
    default: return error;
    }
}

// An IEEE 802.11 association status code (and 1000.. driver codes) to an association result. A second argument of
// 2 always gives 21; otherwise the status alone decides, unknown codes give 0xffff.
EXPORT int convertAssocStatusToAssocResult(int status, int kind) {
    if (kind == 2) return 21;
    switch (status) {
    case 0: return 1;
    case 1: case 201: return 2;
    case 10: return 3;
    case 11: return 4;
    case 12: return 5;
    case 13: return 6;
    case 14: return 7;
    case 15: return 8;
    case 16: return 9;
    case 17: return 10;
    case 18: return 11;
    case 25: return 12;
    case 26: return 13;
    case 40: case 213: return 14;
    case 41: return 15;
    case 42: return 16;
    case 43: case 220: return 17;
    case 44: return 18;
    case 45: return 19;
    case 46: case 224: return 20;
    case 202: return 21;
    case 215: case 216: case 1014: return 22;
    case 27: return 23;
    case 28: return 24;
    case 1000: return 25;
    case 1001: return 26;
    case 1002: return 27;
    case 1003: return 28;
    case 1004: return 29;
    case 1005: return 30;
    case 1006: return 31;
    case 1007: return 32;
    case 1008: return 33;
    case 1009: return 34;
    case 1010: return 35;
    case 1011: return 36;
    case 126: case 1012: return 37;
    case 1013: return 38;
    case 1015: return 39;
    case 1016: return 40;
    case 2: case 3: case 31: case 39: case 47: case 49: case 50: case 56: case 57: case 59: case 60: case 61: case 62:
    case 63: case 64: case 65: case 67: case 68: case 73: case 74: case 75: case 77: case 78: case 80: case 81: case 85:
    case 86: case 87: case 88: case 89: case 94: case 95: case 96: case 97: case 98: case 100: case 101: case 102:
    case 105: case 106: case 107: case 108: case 109: case 111: case 113: case 116: case 117: case 118: case 119:
    case 120: case 121: case 122: case 123: case 128: case 129: return 41;
    case 5: case 214: return 42;
    case 6: return 43;
    case 7: return 44;
    case 19: return 45;
    case 23: return 46;
    case 24: return 47;
    case 30: return 48;
    case 32: return 49;
    case 33: return 50;
    case 34: return 51;
    case 35: return 52;
    case 37: return 53;
    case 38: return 54;
    case 51: return 55;
    case 52: return 56;
    case 53: case 249: return 57;
    case 54: return 58;
    case 55: return 59;
    case 58: return 60;
    case 72: return 61;
    case 76: return 62;
    case 79: return 63;
    case 82: return 64;
    case 83: return 65;
    case 84: return 66;
    case 92: return 67;
    case 93: return 68;
    case 99: return 69;
    case 103: return 70;
    case 104: return 71;
    case 110: return 72;
    case 112: return 73;
    case 125: return 74;
    case 0xffff: return 0;
    default: return 0xffff;
    }
}

// The bit of a 20 MHz channel in its band's index bitmap: 2.4 GHz channel N is bit N, 5 GHz (N + 24) / 4, 6 GHz N / 4
// (channel flags 0x8, 0x10, 0x2000, tested in that order; only the low byte of a 5 or 6 GHz channel counts). 0 unless
// the 20 MHz flag (0x2) is set. Identical code in 26A428 and 26B131.
EXPORT uint64_t Apple80211Get20MhzChannelIndexBitmap(uint32_t channel, uint32_t flags) {
    unsigned bit;
    if (!(flags & 0x2)) return 0;
    if (flags & 0x8) bit = channel;
    else if (flags & 0x10) bit = ((channel & 0xff) + 24) >> 2;
    else if (flags & 0x2000) bit = (channel & 0xff) >> 2;
    else return 0;
    return (uint64_t)1 << (bit & 63);
}
