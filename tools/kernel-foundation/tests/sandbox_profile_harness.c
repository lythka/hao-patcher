/* Host harness for src/sandbox_profile.h: the same transform IntelCommpage runs in the kernel.
 * usage: harness TARGET_OPS DONOR_OPS TARGET_NAMES FILTER_MAX DROP,DROP,... IN OUT [MIG_FILTER MIG_MAP,...]
 * (MIG_MAP: donor index per target kernel-mig-routine index, 65535 for none)
 * prints "<status> <reason> <new size>" (status 0 pass, 1 adapted, 2 refused); OUT holds the result bytes. */
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/sandbox_profile.h"

int main(int argc, char **argv) {
    if (argc != 8 && argc != 10) {
        fprintf(stderr, "usage: %s TARGET_OPS DONOR_OPS TARGET_NAMES FILTER_MAX DROPS IN OUT [MIG_FILTER MIG_MAP]\n", argv[0]);
        return 2;
    }
    static unsigned short mig[1024];
    unsigned migs = 0, mig_filter = 0;
    if (argc == 10) {
        mig_filter = (unsigned)strtoul(argv[8], 0, 0);
        for (char *s = argv[9]; *s && migs < 1024; ) {
            mig[migs++] = (unsigned short)strtoul(s, &s, 0);
            if (*s == ',') s++;
        }
    }
    unsigned short drop[256];
    unsigned drops = 0;
    for (char *s = argv[5]; *s && drops < 256; ) {
        drop[drops++] = (unsigned short)strtoul(s, &s, 0);
        if (*s == ',') s++;
    }
    struct sandbox_op_layout layout = {
        (unsigned)strtoul(argv[1], 0, 0), (unsigned)strtoul(argv[2], 0, 0), (unsigned)strtoul(argv[3], 0, 0),
        (unsigned)strtoul(argv[4], 0, 0), drops, drop, mig_filter, migs, mig};
    FILE *in = fopen(argv[6], "rb");
    if (!in) { perror(argv[6]); return 2; }
    fseek(in, 0, SEEK_END);
    long size = ftell(in);
    fseek(in, 0, SEEK_SET);
    unsigned char *data = malloc(size ? size : 1);
    if (fread(data, 1, size, in) != (size_t)size) { perror("read"); return 2; }
    fclose(in);
    size_t out_size;
    unsigned reason;
    int status = sandbox_profile_adapt(data, size, &layout, &out_size, &reason);
    FILE *out = fopen(argv[7], "wb");
    if (!out || fwrite(data, 1, out_size, out) != out_size) { perror(argv[7]); return 2; }
    fclose(out);
    printf("%d %u %zu\n", status, reason, out_size);
    return 0;
}
