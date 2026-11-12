// probe6.m: which surface each argument-buffer pointer high word selects (iCBE wrapper declares u32..u39 at slots 23..30).
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct AB { device float4 *p [[id(0)]]; };\n"
"kernel void t6(constant AB &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = ab.p[0]; }\n";
int main(void) { @autoreleasepool {
    id<MTLDevice> d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
    NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
    id<MTLComputePipelineState> p = [d newComputePipelineStateWithFunction:[lib newFunctionWithName:@"t6"] error:&e];
    if (!p) { printf("pipeline FAILED %s\n", e.localizedDescription.UTF8String); return 1; }
    id<MTLBuffer> slots[8];
    for (int s = 0; s < 8; s++) { float v[4] = { 1000.0f * (s + 1), 0, 0, 0 }; slots[s] = [d newBufferWithBytes:v length:16 options:MTLResourceStorageModeShared]; }
    float zv[4] = { 7, 7, 7, 7 }; id<MTLBuffer> abmark = nil; (void)zv;
    for (int k = 0; k <= 40; k++) {
        uint64_t ab[1] = { (uint64_t)k << 32 };
        id<MTLBuffer> abuf = [d newBufferWithBytes:ab length:sizeof ab options:MTLResourceStorageModeShared];
        id<MTLBuffer> out = [d newBufferWithLength:16 options:MTLResourceStorageModeShared]; memset(out.contents, 0xff, 16);
        id<MTLCommandBuffer> cb = [q commandBuffer]; id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
        [ce setComputePipelineState:p]; [ce setBuffer:abuf offset:0 atIndex:0]; [ce setBuffer:out offset:0 atIndex:1];
        for (int s = 0; s < 8; s++) [ce setBuffer:slots[s] offset:0 atIndex:23 + s];
        [ce dispatchThreads:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)]; [ce endEncoding]; [cb commit]; [cb waitUntilCompleted];
        float *o = out.contents; printf("k=%d -> %g %g\n", k, o[0], o[1]);
    }
    (void)abmark; return 0;
} }
