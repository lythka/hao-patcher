// The build table and the process gate, without CoreDisplay: this file supplies the wrapped function.
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <mach/mach.h>
int _CGXGetDisplaySystemState(mach_port_t port, uint64_t seed, uint8_t **data, uint32_t *len);
#include "../src/dss_shim.c"

static unsigned replies;
int _CGXGetDisplaySystemState(mach_port_t port, uint64_t seed, uint8_t **data, uint32_t *len)
{
    (void)port; (void)seed;
    static uint8_t reply[40] = {0};
    *data = reply; *len = sizeof reply; replies++;
    return 0;
}

static const char *find(const struct dss_build *builds, size_t n, const uint8_t uuid[16])
{
    for (size_t i = 0; i < n; i++) if (!memcmp(builds[i].uuid, uuid, 16)) return builds[i].build;
    return NULL;
}

int main(void)
{
    const uint8_t s0[16] = {0x02,0xA9,0xA2,0x02,0x2F,0x39,0x32,0x04,0x86,0xDE,0xA0,0x9E,0xA6,0xAE,0x8D,0xEE};
    const uint8_t c0[16] = {0x61,0xC8,0x2C,0x50,0xC9,0x1D,0x30,0x28,0x93,0x3E,0xFC,0x20,0xD2,0xAB,0x40,0xFA};
    const uint8_t s1[16] = {0x21,0xAC,0xAB,0xFA,0xB6,0xCC,0x3E,0xC9,0xA3,0xCF,0x6D,0x4D,0x6B,0x63,0x7F,0x5A};
    const uint8_t c1[16] = {0x8B,0x08,0xB2,0x49,0x43,0x0F,0x35,0x65,0xA3,0x75,0x86,0x27,0x27,0x74,0xC7,0x77};
    size_t ns = sizeof skylight_builds / sizeof *skylight_builds, nc = sizeof coredisplay_builds / sizeof *coredisplay_builds;
    assert(!strcmp(find(skylight_builds, ns, s0), "26A428") && !strcmp(find(coredisplay_builds, nc, c0), "26A428"));
    assert(!strcmp(find(skylight_builds, ns, s1), "26B131") && !strcmp(find(coredisplay_builds, nc, c1), "26B131"));
    assert(!find(skylight_builds, ns, c0) && !find(coredisplay_builds, nc, s0));
    char text[37];
    assert(!loaded_build("/SkyLight.framework/Versions/A/SkyLight", skylight_builds, ns, text) && !*text);
    // Not the window server: the reply passes through unchanged.
    uint8_t *data = NULL; uint32_t len = 0;
    assert(dss_shim_get(0, 0, &data, &len) == 0 && len == 40 && replies == 1 && !gate());
    puts("PASS: build table and gate");
}
