/* BLAKE2b (RFC 7693, unkeyed) behind blake2b.h, vectorized with SSE2 + SSSE3 (written for ssdpush;
 * same digests as blake2b.c).
 *
 * The 4x4 state v0..v15 is held as eight 128-bit rows of two 64-bit lanes:
 *   a = (v0 v1)(v2 v3)   b = (v4 v5)(v6 v7)   c = (v8 v9)(v10 v11)   d = (v12 v13)(v14 v15)
 * so one vector instruction does the work of two G functions. The column step works on the rows as
 * they are; for the diagonal step b, c, d are rotated by one, two and three lanes (alignr / swap) so
 * that the diagonals line up as columns, and rotated back afterwards. Rotations by 32 are 32-bit
 * shuffles, by 24 and 16 byte shuffles (pshufb), by 63 an add + shift.
 *
 * The hash exists twice, plain SSSE3 and with the AVX (VEX, 3-operand) encoding, and the second is
 * used when the CPU and OS support AVX (657 vs 593 MiB/s on a MacBookPro9,2's i5-3210M; the table-driven
 * portable code: 406). Build with -mssse3. */
#include <cpuid.h>
#include <string.h>
#include <emmintrin.h>
#include <tmmintrin.h>
#include "blake2b.h"

static const uint64_t IV[8] = {
    0x6A09E667F3BCC908ULL, 0xBB67AE8584CAA73BULL, 0x3C6EF372FE94F82BULL, 0xA54FF53A5F1D36F1ULL,
    0x510E527FADE682D1ULL, 0x9B05688C2B3E6C1FULL, 0x1F83D9ABFB41BD6BULL, 0x5BE0CD19137E2179ULL};

#define LOADU(p) _mm_loadu_si128((const __m128i *)(const void *)(p))
#define STOREU(p, x) _mm_storeu_si128((__m128i *)(void *)(p), x)

/* rotate each 64-bit lane right */
#define ROTR32(x) _mm_shuffle_epi32((x), _MM_SHUFFLE(2, 3, 0, 1))
#define ROTR24(x) _mm_shuffle_epi8((x), r24)
#define ROTR16(x) _mm_shuffle_epi8((x), r16)
#define ROTR63(x) _mm_xor_si128(_mm_srli_epi64((x), 63), _mm_add_epi64((x), (x)))

/* two G functions per half: rows (al ah)(bl bh)(cl ch)(dl dh), message words xl xh / yl yh */
#define G2(xl, xh, yl, yh)                                                              \
    do {                                                                                \
        al = _mm_add_epi64(_mm_add_epi64(al, bl), xl); ah = _mm_add_epi64(_mm_add_epi64(ah, bh), xh); \
        dl = ROTR32(_mm_xor_si128(dl, al));             dh = ROTR32(_mm_xor_si128(dh, ah));             \
        cl = _mm_add_epi64(cl, dl);                     ch = _mm_add_epi64(ch, dh);                     \
        bl = ROTR24(_mm_xor_si128(bl, cl));             bh = ROTR24(_mm_xor_si128(bh, ch));             \
        al = _mm_add_epi64(_mm_add_epi64(al, bl), yl); ah = _mm_add_epi64(_mm_add_epi64(ah, bh), yh); \
        dl = ROTR16(_mm_xor_si128(dl, al));             dh = ROTR16(_mm_xor_si128(dh, ah));             \
        cl = _mm_add_epi64(cl, dl);                     ch = _mm_add_epi64(ch, dh);                     \
        bl = ROTR63(_mm_xor_si128(bl, cl));             bh = ROTR63(_mm_xor_si128(bh, ch));             \
    } while (0)

/* (b: v4 v5 v6 v7 -> v5 v6 v7 v4, c: two lanes, d: v12..v15 -> v15 v12 v13 v14) and back */
#define DIAG()                                                                          \
    do {                                                                                \
        __m128i t0 = _mm_alignr_epi8(bh, bl, 8), t1 = _mm_alignr_epi8(bl, bh, 8);       \
        bl = t0; bh = t1;                                                               \
        t0 = cl; cl = ch; ch = t0;                                                      \
        t0 = _mm_alignr_epi8(dl, dh, 8); t1 = _mm_alignr_epi8(dh, dl, 8);               \
        dl = t0; dh = t1;                                                               \
    } while (0)
#define UNDIAG()                                                                        \
    do {                                                                                \
        __m128i t0 = _mm_alignr_epi8(bl, bh, 8), t1 = _mm_alignr_epi8(bh, bl, 8);       \
        bl = t0; bh = t1;                                                               \
        t0 = cl; cl = ch; ch = t0;                                                      \
        t0 = _mm_alignr_epi8(dh, dl, 8); t1 = _mm_alignr_epi8(dl, dh, 8);               \
        dl = t0; dh = t1;                                                               \
    } while (0)

/* message pair (lane 0 = m[i], lane 1 = m[j]) from the block loaded as eight vectors M[k] = (m[2k] m[2k+1]);
 * i and j are constants, so each call folds to one instruction (or none) */
static inline __attribute__((always_inline)) __m128i mp(const __m128i *M, int i, int j) {
    __m128i a = M[i >> 1], b = M[j >> 1];
    if (!(i & 1) && !(j & 1)) return _mm_unpacklo_epi64(a, b);                 /* (a.0 b.0) */
    if ((i & 1) && (j & 1)) return _mm_unpackhi_epi64(a, b);                   /* (a.1 b.1) */
    if (!(i & 1)) return (i >> 1) == (j >> 1) ? a                               /* (a.0 b.1) */
        : _mm_castpd_si128(_mm_move_sd(_mm_castsi128_pd(b), _mm_castsi128_pd(a)));
    return _mm_alignr_epi8(b, a, 8);                                            /* (a.1 b.0) */
}
#define MP(i, j) mp(M, i, j)

#define ROUND(s0, s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11, s12, s13, s14, s15)       \
    do {                                                                                \
        G2(MP(s0, s2), MP(s4, s6), MP(s1, s3), MP(s5, s7));                             \
        DIAG();                                                                         \
        G2(MP(s8, s10), MP(s12, s14), MP(s9, s11), MP(s13, s15));                       \
        UNDIAG();                                                                       \
    } while (0)

static inline __attribute__((always_inline))
void compress_body(uint64_t h[8], const uint8_t blk[128], uint64_t t0, uint64_t t1, int last) {
    const __m128i r24 = _mm_setr_epi8(3, 4, 5, 6, 7, 0, 1, 2, 11, 12, 13, 14, 15, 8, 9, 10);
    const __m128i r16 = _mm_setr_epi8(2, 3, 4, 5, 6, 7, 0, 1, 10, 11, 12, 13, 14, 15, 8, 9);
    __m128i M[8];
    for (int k = 0; k < 8; k++) M[k] = LOADU(blk + 16 * k);   /* x86 is little-endian */
    __m128i al = LOADU(h), ah = LOADU(h + 2), bl = LOADU(h + 4), bh = LOADU(h + 6);
    __m128i cl = LOADU(IV), ch = LOADU(IV + 2);
    __m128i dl = _mm_xor_si128(LOADU(IV + 4), _mm_set_epi64x((long long)t1, (long long)t0));
    __m128i dh = _mm_xor_si128(LOADU(IV + 6), _mm_set_epi64x(0, last ? -1LL : 0));
    ROUND(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15);
    ROUND(14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3);
    ROUND(11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4);
    ROUND(7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8);
    ROUND(9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13);
    ROUND(2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9);
    ROUND(12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11);
    ROUND(13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10);
    ROUND(6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5);
    ROUND(10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0);
    ROUND(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15);
    ROUND(14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3);
    STOREU(h, _mm_xor_si128(LOADU(h), _mm_xor_si128(al, cl)));
    STOREU(h + 2, _mm_xor_si128(LOADU(h + 2), _mm_xor_si128(ah, ch)));
    STOREU(h + 4, _mm_xor_si128(LOADU(h + 4), _mm_xor_si128(bl, dl)));
    STOREU(h + 6, _mm_xor_si128(LOADU(h + 6), _mm_xor_si128(bh, dh)));
}

static inline __attribute__((always_inline))
void hash_body(uint8_t *out, size_t outlen, const uint8_t *in, size_t inlen) {
    uint64_t h[8], t0 = 0, t1 = 0;
    for (int i = 0; i < 8; i++) h[i] = IV[i];
    h[0] ^= 0x01010000ULL ^ (uint64_t)outlen;
    /* all full blocks except the last block of input are compressed with last=0 */
    while (inlen > 128) {
        t0 += 128; if (t0 < 128) t1++;
        compress_body(h, in, t0, t1, 0);
        in += 128; inlen -= 128;
    }
    uint8_t blk[128] = {0};
    memcpy(blk, in, inlen);
    t0 += inlen; if (t0 < inlen) t1++;
    compress_body(h, blk, t0, t1, 1);
    for (size_t i = 0; i < outlen; i++) out[i] = (uint8_t)(h[i >> 3] >> (8 * (i & 7)));
}

static void hash_ssse3(uint8_t *out, size_t outlen, const uint8_t *in, size_t inlen) {
    hash_body(out, outlen, in, inlen);
}
__attribute__((target("avx")))
static void hash_avx(uint8_t *out, size_t outlen, const uint8_t *in, size_t inlen) {
    hash_body(out, outlen, in, inlen);
}

static void (*hash)(uint8_t *, size_t, const uint8_t *, size_t) = hash_ssse3;

__attribute__((constructor)) static void pick(void) {
#ifdef BLAKE2B_NO_AVX   /* tests: force the SSSE3 path */
    return;
#endif
    unsigned a, b, c, d, lo, hi;
    if (!__get_cpuid(1, &a, &b, &c, &d) || !(c & bit_AVX) || !(c & bit_OSXSAVE)) return;
    __asm__("xgetbv" : "=a"(lo), "=d"(hi) : "c"(0));
    if ((lo & 6) == 6) hash = hash_avx;   /* OS saves XMM and YMM state */
}

int blake2b_uses_avx(void) { return hash == hash_avx; }

void blake2b(uint8_t *out, size_t outlen, const void *in, size_t inlen) {
    hash(out, outlen, in, inlen);
}
