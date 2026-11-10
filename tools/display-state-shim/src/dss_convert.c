#include "dss_convert.h"
#include <string.h>

static uint32_t rd32(const uint8_t *p)
{
    uint32_t v;
    memcpy(&v, p, 4);   // little-endian on both architectures; unaligned on the client side
    return v;
}

enum dss_result dss_check_x86(const uint8_t *in, uint32_t len, size_t *need)
{
    if (!in || len < DSS_HEADER_LEN + 4) return DSS_SHORT;
    uint32_t count = rd32(in + DSS_HEADER_LEN);
    if (count > DSS_MAX_DISPLAYS) return DSS_BAD_COUNT;
    if (count) {
        if (len < DSS_HEADER_LEN + 8) return DSS_SHORT;
        if (rd32(in + DSS_HEADER_LEN + 4) != DSS_X86_DISPLAY_VERSION) return DSS_BAD_VERSION;
    }
    if (len == UINT32_MAX) return DSS_TOO_BIG;
    if (need) *need = (size_t)len + 1;
    return DSS_X86;
}

void dss_convert_x86(uint8_t *out, const uint8_t *in, uint32_t len)
{
    memcpy(out, in, DSS_HEADER_LEN);
    out[DSS_HEADER_LEN] = 0;   // no sync-session UUID
    memcpy(out + DSS_HEADER_LEN + 1, in + DSS_HEADER_LEN, len - DSS_HEADER_LEN);
}

unsigned dss_display_count(const uint8_t *client, uint32_t len)
{
    if (!client || len < DSS_HEADER_LEN + 5 || client[DSS_HEADER_LEN] != 0) return 0;
    return rd32(client + DSS_HEADER_LEN + 1);
}

const char *dss_result_name(enum dss_result r)
{
    switch (r) {
    case DSS_X86: return "x86_64 layout";
    case DSS_SHORT: return "too short";
    case DSS_BAD_COUNT: return "display count out of range";
    case DSS_BAD_VERSION: return "first display version is not 3";
    case DSS_TOO_BIG: return "too big";
    }
    return "?";
}
