// dss_convert: the one layout change between 27.0's x86_64 display-state encoder and its client decoder.
//
// Both layouts start with the same 32-byte header (u32, u64, u32 x5). The client then reads a u8 flag (1: a
// 16-byte sync-session UUID follows) before the u32 display count; the x86_64 encoder writes the count directly.
// Each display record then starts with its u32 display version, which the 26A428 x86_64 encoder always writes as 3.
// Everything after the header is identical, so the conversion inserts one 0x00 byte at offset 0x20.
#ifndef DSS_CONVERT_H
#define DSS_CONVERT_H
#include <stddef.h>
#include <stdint.h>

#define DSS_HEADER_LEN 32u
#define DSS_X86_DISPLAY_VERSION 3u
#define DSS_MAX_DISPLAYS 64u

enum dss_result {
    DSS_X86 = 0,        // the x86_64 layout: convert
    DSS_SHORT,          // shorter than header + count (+ the first display's version when the count is not 0)
    DSS_BAD_COUNT,      // the count at 0x20 is above DSS_MAX_DISPLAYS: not this layout
    DSS_BAD_VERSION,    // the first display's version is not 3: not this build's encoder
    DSS_TOO_BIG,        // the converted length would not fit a u32
};

// Classify IN (LEN bytes). For DSS_X86, *NEED is the converted length (LEN + 1).
enum dss_result dss_check_x86(const uint8_t *in, uint32_t len, size_t *need);

// Write the client layout of IN (LEN bytes, DSS_X86) to OUT (LEN + 1 bytes).
void dss_convert_x86(uint8_t *out, const uint8_t *in, uint32_t len);

// The display count of a buffer in the client layout with flag 0 (0 if too short).
unsigned dss_display_count(const uint8_t *client, uint32_t len);

const char *dss_result_name(enum dss_result r);
#endif
