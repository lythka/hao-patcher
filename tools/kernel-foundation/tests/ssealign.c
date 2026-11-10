// ssealign [N]: legacy-SSE aligned forms on misaligned memory (movapd load, mulpd/addpd m128, movaps store), checked
// against unaligned moves; with N, times N rounds (each round 4 misaligned operands).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static double buf[16] __attribute__((aligned(64)));
static void round_trip(double *p) {           // p is 8 mod 16
    __asm__ volatile(
        "movapd 0(%0), %%xmm0\n\t"             // load
        "mulpd 16(%0), %%xmm0\n\t"             // arithmetic m128
        "addpd 32(%0), %%xmm0\n\t"
        "movaps %%xmm0, 48(%0)\n\t"            // store
        :: "r"(p) : "xmm0", "memory");
}
int main(int argc, char **argv) {
    double *p = (double *)((char *)buf + 8);
    for (int i = 0; i < 8; i++) p[i] = i + 1.5;
    round_trip(p);
    double want0 = p[0] * p[2] + p[4], want1 = p[1] * p[3] + p[5];
    int ok = p[6] == want0 && p[7] == want1;
    printf("misaligned movapd/mulpd/addpd/movaps: %s (%g %g, want %g %g)\n", ok ? "OK" : "WRONG", p[6], p[7], want0, want1);
    if (argc > 1) {
        long n = atol(argv[1]); struct timespec a, b; clock_gettime(CLOCK_MONOTONIC, &a);
        for (long i = 0; i < n; i++) round_trip(p);
        clock_gettime(CLOCK_MONOTONIC, &b);
        double us = ((b.tv_sec - a.tv_sec) * 1e6 + (b.tv_nsec - a.tv_nsec) / 1e3) / (n * 4.0);
        printf("%ld rounds, %.2f us per misaligned operand\n", n, us);
    }
    return ok ? 0 : 1;
}
