#include <metal_stdlib>
using namespace metal;
extern TYPE2 matrix_op(TYPE2,TYPE2,TYPE2);
kernel void probe(device const float2 *a [[buffer(0)]],device const float2 *b [[buffer(1)]],device const float2 *c [[buffer(2)]],device float2 *o [[buffer(3)]],threadgroup MEMTYPE2 *shared [[threadgroup(0)]],uint3 lid [[thread_position_in_threadgroup]],uint3 gid [[threadgroup_position_in_grid]],ushort lane [[thread_index_in_simdgroup]]) {
 uint i=gid.x*32+lid.x;shared[lid.x]=MEMTYPE2(a[i]);shared[32+lid.x]=MEMTYPE2(b[i]);threadgroup_barrier(mem_flags::mem_threadgroup);
 TYPE2 av=TYPE2(shared[lane]),bv=TYPE2(shared[32+lane]);o[i]=float2(matrix_op(av,bv,TYPE2(c[i])));
}
