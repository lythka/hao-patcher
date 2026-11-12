// igil_tool IN OUT: run igil_rewrite on a file (host tests). Exit 0 rewritten, 1 unchanged.
#include "igil_rewrite.h"
#include "../argbuf_config.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: igil_tool IN OUT\n"); return 2; }
    FILE *f = fopen(argv[1], "rb"); if (!f) return 2; fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    char *s = malloc(n + 1); fread(s, 1, n, f); s[n] = 0; fclose(f);
    igil_config c = { AGB_TEX_SLOT, AGB_TEX_COUNT, AGB_BUF_SLOT, AGB_BUF_COUNT, AGB_BUF_BTI }; igil_stats st;
    char *r = igil_rewrite(s, &c, &st);
    printf("textures %d buffers %d tex_base %d buf_base %d\n", st.textures, st.buffers, st.tex_base, st.buf_base);
    f = fopen(argv[2], "wb"); fputs(r ? r : s, f); fclose(f);
    return r ? 0 : 1;
}
