// probe5.m: the pointer values the HD 4000 GPU uses for bound buffers (reinterpret_cast<ulong>(ptr)), per slot and
// with a setBuffer offset; plus the GPU read through a pointer value round-tripped through memory.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"kernel void addr(device float4 *a [[buffer(0)]], device float4 *b [[buffer(2)]], device float4 *c [[buffer(23)]],\n"
"                 device float4 *d [[buffer(24)]], device ulong *out [[buffer(1)]])\n"
"{ out[0] = reinterpret_cast<ulong>(a); out[1] = reinterpret_cast<ulong>(b); out[2] = reinterpret_cast<ulong>(c);\n"
"  out[3] = reinterpret_cast<ulong>(d); out[4] = reinterpret_cast<ulong>(c + 1); out[5] = reinterpret_cast<ulong>(out); }\n"
"kernel void only(device float4 *c [[buffer(23)]], device ulong *out [[buffer(1)]])\n"
"{ out[0] = reinterpret_cast<ulong>(c); out[1] = reinterpret_cast<ulong>(out); }\n";
int main(void) { @autoreleasepool {
    id<MTLDevice> d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
    NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
    if (!lib) { printf("library FAILED %s\n", e.localizedDescription.UTF8String); return 1; }
    for (NSString *fn in @[ @"addr", @"only" ]) {
        id<MTLComputePipelineState> p = [d newComputePipelineStateWithFunction:[lib newFunctionWithName:fn] error:&e];
        printf("%s pipeline %s\n", fn.UTF8String, p ? "OK" : e.localizedDescription.UTF8String); if (!p) continue;
        id<MTLBuffer> bufs[4]; for (int i = 0; i < 4; i++) bufs[i] = [d newBufferWithLength:256 options:MTLResourceStorageModeShared];
        id<MTLBuffer> out = [d newBufferWithLength:64 options:MTLResourceStorageModeShared]; memset(out.contents, 0xee, 64);
        id<MTLCommandBuffer> cb = [q commandBuffer]; id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
        [ce setComputePipelineState:p]; [ce setBuffer:out offset:0 atIndex:1];
        [ce setBuffer:bufs[0] offset:0 atIndex:0]; [ce setBuffer:bufs[1] offset:32 atIndex:2];
        [ce setBuffer:bufs[2] offset:0 atIndex:23]; [ce setBuffer:bufs[3] offset:48 atIndex:24];
        [ce dispatchThreads:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)]; [ce endEncoding]; [cb commit]; [cb waitUntilCompleted];
        uint64_t *o = out.contents; printf("%s:", fn.UTF8String); for (int i = 0; i < 6; i++) printf(" %d=0x%llx", i, o[i]); printf("\n");
    }
    return 0;
} }
