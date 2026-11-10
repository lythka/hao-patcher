/* CPU stand-ins exercise the authored IR, not Intel hardware semantics. */
#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern float air_simd_min_f32(float);
extern float air_simd_max_f32(float);
static float values[64];
static uint64_t active, reads, cases;
static unsigned mask_calls;

uint64_t mock_mask(void) { ++mask_calls; return active; }
uint32_t mock_shuffle(uint32_t unused, uint32_t lane) {
    (void)unused;
    assert(lane < 64 && (active & (UINT64_C(1) << lane)));
    reads |= UINT64_C(1) << lane;
    uint32_t bits;
    memcpy(&bits, &values[lane], sizeof bits);
    return bits;
}
float mock_min(float a, float b) { return fminf(a, b); }
float mock_max(float a, float b) { return fmaxf(a, b); }

static void check(uint64_t mask) {
    active = mask;
    float low = INFINITY, high = -INFINITY;
    for (unsigned i = 0; i < 64; ++i) {
        /* Poison all inactive lanes. The shuffle also refuses those reads. */
        values[i] = (mask & (UINT64_C(1) << i)) ?
                    (float)((int)((i * 73 + mask) % 1009) - 504) : NAN;
        if (mask & (UINT64_C(1) << i)) {
            if (values[i] < low) low = values[i];
            if (values[i] > high) high = values[i];
        }
    }
    for (unsigned i = 0; i < 64; ++i) if (mask & (UINT64_C(1) << i)) {
        reads = 0; mask_calls = 0;
        assert(air_simd_min_f32(values[i]) == low);
        assert(reads == mask && mask_calls == 1);
        reads = 0; mask_calls = 0;
        assert(air_simd_max_f32(values[i]) == high);
        assert(reads == mask && mask_calls == 1);
        ++cases;
    }
}

int main(void) {
    for (uint64_t mask = 1; mask < 65536; ++mask) check(mask);
    for (unsigned i = 0; i < 64; ++i) check(UINT64_C(1) << i);
    check(UINT64_MAX);
    check(UINT64_C(0xaaaaaaaaaaaaaaaa));
    check(UINT64_C(0x8000000000000001));
    uint64_t seed = UINT64_C(0x123456789abcdef);
    for (unsigned i = 0; i < 10000; ++i) {
        seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
        check(seed);
    }
    printf("{\"passed\":true,\"active_lane_cases\":%llu,"
           "\"scope\":\"finite non-NaN values; CPU intrinsic stand-ins\"}\n",
           (unsigned long long)cases);
}
