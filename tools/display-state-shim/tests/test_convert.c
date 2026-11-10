// Converter checks on buffers laid out like the 26A428 x86_64 CoreDisplay reply on a MacBookPro9,2:
// header, count 9, first display version 3, display 0x042731c0.
#include "../src/dss_convert.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL %s:%d ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } else printf("ok   " __VA_ARGS__), printf("\n"); } while (0)
static void put32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
int main(void)
{
    uint8_t x86[96] = {0};
    put32(x86 + 0, 0); uint64_t h = 0x18; memcpy(x86 + 4, &h, 8);
    put32(x86 + 12, 8); put32(x86 + 16, 1); put32(x86 + 20, 0); put32(x86 + 24, 0); put32(x86 + 28, 1);
    put32(x86 + 32, 9); put32(x86 + 36, 3); put32(x86 + 40, 0x042731c0);
    for (int i = 44; i < 96; i++) x86[i] = (uint8_t)i;
    size_t need = 0;
    CHECK(dss_check_x86(x86, sizeof x86, &need) == DSS_X86 && need == sizeof x86 + 1, "measured layout is recognised, +1 byte");
    uint8_t *out = malloc(need);
    dss_convert_x86(out, x86, sizeof x86);
    CHECK(!memcmp(out, x86, 32), "header unchanged");
    CHECK(out[32] == 0, "flag 0 inserted at 0x20");
    CHECK(!memcmp(out + 33, x86 + 32, sizeof x86 - 32), "the rest shifted by one byte, unchanged");
    uint32_t v; memcpy(&v, out + 33, 4); CHECK(v == 9, "client reads count 9 after the flag");
    memcpy(&v, out + 37, 4); CHECK(v == 3, "client reads display version 3");
    memcpy(&v, out + 41, 4); CHECK(v == 0x042731c0, "client reads display 0x042731c0");
    CHECK(dss_display_count(out, (uint32_t)need) == 9, "dss_display_count");
    uint8_t zero[36] = {0};
    CHECK(dss_check_x86(zero, sizeof zero, &need) == DSS_X86 && need == 37, "count 0 converts");
    CHECK(dss_check_x86(x86, 35, &need) == DSS_SHORT, "short buffer passes through");
    CHECK(dss_check_x86(NULL, 0, &need) == DSS_SHORT, "NULL passes through");
    uint8_t bad[48]; memcpy(bad, x86, 48); put32(bad + 32, 1000);
    CHECK(dss_check_x86(bad, sizeof bad, &need) == DSS_BAD_COUNT, "absurd count passes through");
    memcpy(bad, x86, 48); put32(bad + 36, 10);
    CHECK(dss_check_x86(bad, sizeof bad, &need) == DSS_BAD_VERSION, "display version 10 (the arm64e encoder's) passes through");
    // the arm64e server's reply as an Apple silicon Mac returns it: flag 0 at 0x20, then count 5 unaligned
    uint8_t arm[64] = {0}; arm[32] = 0; put32(arm + 33, 5); put32(arm + 37, 10);
    CHECK(dss_check_x86(arm, sizeof arm, &need) != DSS_X86, "the client layout is not converted again");
    free(out);
    printf("%s: %d failures\n", fails ? "FAIL" : "PASS", fails);
    return fails != 0;
}
