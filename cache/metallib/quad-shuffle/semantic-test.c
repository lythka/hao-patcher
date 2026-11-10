/* Execute the actual helper IR against a lane-exchange model, comparing bits. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef float float4 __attribute__((ext_vector_type(4)));
typedef uint32_t uint4 __attribute__((ext_vector_type(4)));
typedef _Float16 half3 __attribute__((ext_vector_type(3)));
typedef _Float16 half4 __attribute__((ext_vector_type(4)));
typedef float float3 __attribute__((ext_vector_type(3)));
extern float air_quad_shuffle_down_f32(float, uint16_t);
extern float4 air_quad_shuffle_down_v4f32(float4, uint16_t);
extern uint32_t air_quad_shuffle_down_u_i32(uint32_t, uint16_t);
extern uint4 air_quad_shuffle_down_u_v4i32(uint4, uint16_t);
extern float air_quad_broadcast_f32(float, uint16_t);
extern half3 air_quad_shuffle_xor_v3f16(half3, uint16_t);
extern _Float16 air_quad_shuffle_xor_f16(_Float16, uint16_t);
extern void test_quad_xor_half2(uint16_t *, uint16_t, uint16_t *);
extern half4 air_quad_shuffle_xor_v4f16(half4, uint16_t);
extern float3 air_quad_broadcast_v3f32(float3, uint16_t);
static uint32_t values[32][4], lane, width;
static uint16_t halves[32][4];
static int half_mode;
static uint64_t comparisons;

uint32_t test_lane(void) { return lane; }
uint32_t test_shuffle(uint32_t value, uint32_t source) {
    assert(source < width);
    if (half_mode) {
        for (unsigned c = 0; c < 4; ++c)
            if (value == halves[lane][c]) return halves[source][c];
        assert(!"helper changed the half bits");
    }
    for (unsigned c = 0; c < 4; ++c)
        if (value == values[lane][c]) return values[source][c];
    assert(!"helper changed the input bits");
    return 0;
}

static void check(uint16_t delta) {
    /* Independent quad-local reference, including unchanged upper lanes. */
    unsigned local = lane % 4;
    unsigned source = delta < 4 - local ? lane + delta : lane;
    float input, output;
    uint32_t bits;
    memcpy(&input, &values[lane][0], sizeof input);
    output = air_quad_shuffle_down_f32(input, delta);
    memcpy(&bits, &output, sizeof bits);
    assert(bits == values[source][0]);
    assert(air_quad_shuffle_down_u_i32(values[lane][0], delta) == values[source][0]);
    float4 fv;
    uint4 uv;
    memcpy(&fv, values[lane], sizeof fv);
    memcpy(&uv, values[lane], sizeof uv);
    fv = air_quad_shuffle_down_v4f32(fv, delta);
    uv = air_quad_shuffle_down_u_v4i32(uv, delta);
    uint32_t fb[4], ub[4];
    memcpy(fb, &fv, sizeof fb);
    memcpy(ub, &uv, sizeof ub);
    for (unsigned c = 0; c < 4; ++c) {
        assert(fb[c] == values[source][c]);
        assert(ub[c] == values[source][c]);
    }
    comparisons += 10;
}

int main(void) {
    const uint32_t special[] = {0, 0x80000000, 0x7f800000, 0xff800000,
        0x7fc00001, 0x7f800001, 1, 0x80000001};
    for (unsigned l = 0; l < 32; ++l)
        for (unsigned c = 0; c < 4; ++c)
            values[l][c] = special[(l + c) % 8];
    /* Every ushort delta, every lane, every supported SIMD width. */
    for (width = 8; width <= 32; width *= 2)
        for (lane = 0; lane < width; ++lane)
            for (unsigned delta = 0; delta <= UINT16_MAX; ++delta)
                check((uint16_t)delta);
    /* Check defined results for every active mask within a quad. */
    width = 32;
    for (unsigned mask = 1; mask < 16; ++mask)
        for (lane = 0; lane < width; ++lane)
            for (unsigned delta = 0; delta <= 4; ++delta) {
                unsigned local = lane % 4;
                unsigned source = delta < 4 - local ? local + delta : local;
                if ((mask & (1u << local)) && (mask & (1u << source)))
                    check((uint16_t)delta);
            }
    /* Every half bit pattern (including NaNs/subnormals), all valid selectors,
       every lane and width. The shuffle itself must do no floating arithmetic. */
    for (width = 8; width <= 32; width *= 2)
        for (unsigned seed = 0; seed <= UINT16_MAX; ++seed) {
            for (unsigned l = 0; l < width; ++l)
                for (unsigned c = 0; c < 4; ++c)
                    halves[l][c] = (uint16_t)(seed + l * 4 + c);
            for (lane = 0; lane < width; ++lane)
                for (unsigned selector = 0; selector < 4; ++selector) {
                    half_mode = 1;
                    half3 input = {0};
                    memcpy(&input, halves[lane], 6);
                    half3 output = air_quad_shuffle_xor_v3f16(input, selector);
                    uint16_t bits[3];
                    memcpy(bits, &output, 6);
                    for (unsigned c = 0; c < 3; ++c)
                        assert(bits[c] == halves[lane ^ selector][c]);
                    _Float16 h;
                    memcpy(&h, halves[lane], 2);
                    h = air_quad_shuffle_xor_f16(h, selector);
                    uint16_t hb;
                    memcpy(&hb, &h, 2);
                    assert(hb == halves[lane ^ selector][0]);
                    uint16_t hb2[2];
                    test_quad_xor_half2(halves[lane], selector, hb2);
                    for (unsigned c = 0; c < 2; ++c)
                        assert(hb2[c] == halves[lane ^ selector][c]);
                    half4 h4;
                    memcpy(&h4, halves[lane], 8);
                    h4 = air_quad_shuffle_xor_v4f16(h4, selector);
                    uint16_t hb4[4];
                    memcpy(hb4, &h4, 8);
                    for (unsigned c = 0; c < 4; ++c)
                        assert(hb4[c] == halves[lane ^ selector][c]);
                    half_mode = 0;
                    float value, result;
                    uint32_t fb;
                    memcpy(&value, &values[lane][0], 4);
                    result = air_quad_broadcast_f32(value, selector);
                    memcpy(&fb, &result, 4);
                    assert(fb == values[(lane / 4) * 4 + selector][0]);
                    float3 fv = {0};
                    memcpy(&fv, values[lane], 12);
                    fv = air_quad_broadcast_v3f32(fv, selector);
                    uint32_t fb3[3];
                    memcpy(fb3, &fv, 12);
                    for (unsigned c = 0; c < 3; ++c)
                        assert(fb3[c] == values[(lane / 4) * 4 + selector][c]);
                    comparisons += 14;
                }
        }
    printf("%llu bit comparisons passed\n", (unsigned long long)comparisons);
}
