#include <metal_stdlib>
using namespace metal;
// the two Q2 host-test kernels (add1.metal, more.metal), unchanged
kernel void add1(device float *a [[buffer(0)]], uint i [[thread_position_in_grid]]) { a[i] = a[i] + 1.0f; }
kernel void saxpy_long_name(device const float4 *x [[buffer(0)]], device float4 *y [[buffer(1)]],
                            constant float &a [[buffer(2)]], uint i [[thread_position_in_grid]]) {
  y[i] = fma(float4(a), x[i], y[i]) + sin(x[i]) * 0.5f;
}
