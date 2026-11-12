// probe4.m: argument buffers with buffer members on the HD 4000 GPU (iCBE wrapper installed). Handles by hand:
// texture handle k = texture slot 16+k; buffer pointer = (40+h)<<32 | byte offset (binding-table index 40+h), h = buffer slot 23+h.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct AB1 { texture2d<float> t [[id(0)]]; device float4 *b [[id(1)]]; };\n"
"struct AB5 { device float4 *p [[id(0)]]; device float4 *q [[id(1)]]; };\n"
"kernel void t1(constant AB1 &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = ab.t.read(uint2(0, 0)) + ab.b[0]; }\n"
"kernel void t5(constant AB5 &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = ab.p[gid & 3] + ab.q[1]; }\n"
"kernel void t7(constant AB5 &ab [[buffer(0)]], constant float4 &k [[buffer(2)]], texture2d<float> tx [[texture(0)]],\n"
"               device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = ab.p[gid & 3] + ab.q[1] + k + tx.read(uint2(0, 0)); }\n";
static uint64_t P(int h, uint32_t off) { return ((uint64_t)(40 + h) << 32) | off; }
static id<MTLBuffer> fbuf(id<MTLDevice> d, float base, int n) {
    float v[64]; for (int i = 0; i < 4 * n; i++) v[i] = base + i; return [d newBufferWithBytes:v length:16 * n options:MTLResourceStorageModeShared];
}
int main(void) { @autoreleasepool {
    id<MTLDevice> d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
    NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
    if (!lib) { printf("library FAILED %s\n", e.localizedDescription.UTF8String); return 1; }
    int fails = 0; enum { N = 32 };
    for (int which = 0; which < 3; which++) {
        NSString *fn = which == 2 ? @"t7" : which ? @"t5" : @"t1";
        id<MTLComputePipelineState> p = [d newComputePipelineStateWithFunction:[lib newFunctionWithName:fn] error:&e];
        printf("%s pipeline %s %s\n", fn.UTF8String, p ? "OK" : "FAILED", e ? e.localizedDescription.UTF8String : ""); if (!p) { fails++; continue; }
        id<MTLBuffer> A = fbuf(d, 100, 4), B = fbuf(d, 1000, 4);
        id<MTLTexture> tex = nil; float px[4] = { 1, 2, 3, 4 };
        uint64_t ab[2];
        id<MTLBuffer> kb = nil; { float kv[4] = { 10000, 20000, 30000, 40000 }; kb = [d newBufferWithBytes:kv length:16 options:MTLResourceStorageModeShared]; }
        if (which != 1) {
            MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
            tex = [d newTextureWithDescriptor:td]; [tex replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:px bytesPerRow:16];
            if (which == 0) { ab[0] = 0; ab[1] = P(0, 0); }   // t -> slot 16, b -> A (slot 23)
            else { ab[0] = P(0, 0); ab[1] = P(1, 16); }
        } else { ab[0] = P(0, 0); ab[1] = P(1, 16); }        // p -> A (slot 23), q -> B+16 bytes (slot 24)
        id<MTLBuffer> abuf = [d newBufferWithBytes:ab length:sizeof ab options:MTLResourceStorageModeShared];
        id<MTLBuffer> out = [d newBufferWithLength:N*16 options:MTLResourceStorageModeShared]; memset(out.contents, 0xff, N*16);
        id<MTLCommandBuffer> cb = [q commandBuffer]; id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
        [ce setComputePipelineState:p]; [ce setBuffer:abuf offset:0 atIndex:0]; [ce setBuffer:out offset:0 atIndex:1];
        [ce setBuffer:A offset:0 atIndex:40]; [ce setBuffer:B offset:0 atIndex:41]; if (tex) [ce setTexture:tex atIndex:which == 2 ? 0 : 16];
        [ce setBuffer:kb offset:0 atIndex:2];
        [ce dispatchThreads:MTLSizeMake(N,1,1) threadsPerThreadgroup:MTLSizeMake(16,1,1)]; [ce endEncoding]; [cb commit]; [cb waitUntilCompleted];
        printf("%s status %ld %s\n", fn.UTF8String, (long)cb.status, cb.error ? cb.error.localizedDescription.UTF8String : "");
        float *o = out.contents, *a = A.contents, *b = B.contents; int bad = 0;
        for (int g = 0; g < N; g++) for (int c = 0; c < 4; c++) {
            float w = which ? a[4*(g&3)+c] + b[4*2+c] + (which == 2 ? 10000 * (c + 1) + px[c] : 0) : px[c] + a[c];
            if (o[4*g+c] != w) { if (bad++ < 4) printf("  g=%d c=%d got %g want %g\n", g, c, o[4*g+c], w); }
        }
        printf("%s: %s (%d bad of %d)\n", fn.UTF8String, bad ? "WRONG" : "CORRECT", bad, N*4); fails += bad != 0;
    }
    return fails ? 3 : 0;
} }
