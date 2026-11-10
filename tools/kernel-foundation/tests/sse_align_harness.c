/* Host harness for src/sse_align.h: harness HEX... prints "ok base index scale disp store length" or "no" per input. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/sse_align.h"
int main(int argc, char **argv) {
    for (int a = 1; a < argc; a++) {
        unsigned char code[16]; unsigned n = 0;
        for (const char *h = argv[a]; h[0] && h[1] && n < 16; h += 2) { char b[3] = {h[0], h[1], 0}; code[n++] = (unsigned char)strtoul(b, 0, 16); }
        struct sse_align_insn d;
        if (sse_align_decode(code, n, &d)) printf("ok %d %d %u %lld %d %u\n", d.base, d.index, d.scale, d.disp, d.store, d.length);
        else printf("no\n");
    }
    return 0;
}
