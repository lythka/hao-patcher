// Execute the actual helper IR on the CPU with explicit lane/width/shuffle mocks.
#include <stdint.h>
#include <stdio.h>
#include <string.h>
extern float air_simd_shuffle_rotate_down_f32(float, uint16_t);
extern uint16_t air_get_simdgroup_size_i16(void);
extern uint64_t air_insert_bits_u_i64(uint64_t, uint64_t, uint32_t, uint32_t);
extern uint64_t air_extract_bits_u_i64(uint64_t, uint32_t, uint32_t);
static uint32_t lane, width, lanes[64];
uint32_t mock_lane(void) { return lane; }
uint32_t mock_width(void) { return width; }
uint32_t mock_shuffle(uint32_t bits, uint32_t source) {
  (void)bits;
  return source < width ? lanes[source] : 0xdeadbeef;
}
static uint64_t random64(void) {
  static uint64_t state = UINT64_C(0xd1e5a7b93c462801);
  state ^= state << 13; state ^= state >> 7; state ^= state << 17;
  return state;
}
int main(void) {
  uint64_t rotations = 0, bit_cases = 0;
  for (width = 1; width <= 65535; ++width)
    if (air_get_simdgroup_size_i16() != width) return 1;
  const uint32_t widths[] = {8, 16, 32, 64};
  for (unsigned w = 0; w < 4; ++w) {
    width = widths[w];
    for (unsigned i = 0; i < width; ++i) lanes[i] = (uint32_t)random64();
    lanes[0] = 0x80000000; lanes[1] = 0x7fc00123; lanes[2] = 0xff800000;
    for (lane = 0; lane < width; ++lane) {
      float input; memcpy(&input, &lanes[lane], 4);
      for (uint32_t delta = 0; delta <= 65535; ++delta) {
        float result = air_simd_shuffle_rotate_down_f32(input, (uint16_t)delta);
        uint32_t actual; memcpy(&actual, &result, 4);
        if (actual != lanes[(lane + delta) % width]) return 2;
        ++rotations;
      }
    }
  }
  for (unsigned offset = 0; offset <= 64; ++offset)
    for (unsigned count = 0; count <= 64 - offset; ++count)
      for (unsigned trial = 0; trial < 64; ++trial) {
        uint64_t base = random64(), insert = random64(), want_insert = base, want_extract = 0;
        // Bit-by-bit reference avoids the helper's mask/shift construction.
        for (unsigned bit = 0; bit < count; ++bit) {
          uint64_t mask = UINT64_C(1) << (offset + bit);
          if ((insert >> bit) & 1) want_insert |= mask; else want_insert &= ~mask;
          if ((base >> (offset + bit)) & 1) want_extract |= UINT64_C(1) << bit;
        }
        if (air_insert_bits_u_i64(base, insert, offset, count) != want_insert) return 3;
        if (air_extract_bits_u_i64(base, offset, count) != want_extract) return 4;
        ++bit_cases;
      }
  printf("{\"rotation_cases\":%llu,\"bit_cases\":%llu,\"width_cases\":65535,\"passed\":true}\n",
         (unsigned long long)rotations, (unsigned long long)bit_cases);
  return 0;
}
