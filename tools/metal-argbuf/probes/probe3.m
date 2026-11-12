// probe3.m: texture-only argument buffer on the HD 4000 GPU with the iCBE wrapper. Two textures in the argument
// buffer, handles written by hand (handle k = slot AGB_TEX_SLOT+k); each thread reads one of them (non-uniform member choice).
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "../src/argbuf_config.h"
static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct AB { texture2d<float> a [[id(0)]]; texture2d<float> b [[id(1)]]; };\n"
"kernel void t3(constant AB &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = (gid & 1) ? ab.b.read(uint2(0, 0)) : ab.a.read(uint2(0, 0)); }\n";
int main(void) { @autoreleasepool {
    id<MTLDevice> d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
    NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
    if (!lib) { printf("library FAILED %s\n", e.localizedDescription.UTF8String); return 1; }
    id<MTLComputePipelineState> p = [d newComputePipelineStateWithFunction:[lib newFunctionWithName:@"t3"] error:&e];
    printf("t3 pipeline %s %s\n", p ? "OK" : "FAILED", e ? e.localizedDescription.UTF8String : ""); if (!p) return 2;
    id<MTLTexture> tx[2];
    for (int i = 0; i < 2; i++) {
        MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
        tx[i] = [d newTextureWithDescriptor:td]; float px[4] = { 10.0f*(i+1), 20.0f*(i+1), 30.0f*(i+1), 1 };
        [tx[i] replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:px bytesPerRow:16];
    }
    uint64_t ab[2] = { 0, 1 };      // a -> handle 0 (slot 16), b -> handle 1 (slot 17)
    id<MTLBuffer> abuf = [d newBufferWithBytes:ab length:sizeof ab options:MTLResourceStorageModeShared];
    enum { N = 32 }; id<MTLBuffer> out = [d newBufferWithLength:N*16 options:MTLResourceStorageModeShared]; memset(out.contents, 0xff, N*16);
    id<MTLCommandBuffer> cb = [q commandBuffer]; id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
    [ce setComputePipelineState:p]; [ce setBuffer:abuf offset:0 atIndex:0]; [ce setBuffer:out offset:0 atIndex:1];
    [ce setTexture:tx[0] atIndex:AGB_TEX_SLOT]; [ce setTexture:tx[1] atIndex:AGB_TEX_SLOT + 1];
    [ce dispatchThreads:MTLSizeMake(N,1,1) threadsPerThreadgroup:MTLSizeMake(16,1,1)]; [ce endEncoding]; [cb commit]; [cb waitUntilCompleted];
    printf("status %ld %s\n", (long)cb.status, cb.error ? cb.error.localizedDescription.UTF8String : "");
    float *o = out.contents; int bad = 0;
    for (int g = 0; g < N; g++) { int i = g & 1; float w[4] = { 10.0f*(i+1), 20.0f*(i+1), 30.0f*(i+1), 1 };
        for (int c = 0; c < 4; c++) if (o[4*g+c] != w[c]) { if (bad++ < 4) printf("  g=%d c=%d got %g want %g\n", g, c, o[4*g+c], w[c]); } }
    printf("t3: %s (%d bad values of %d)\n", bad ? "WRONG" : "CORRECT", bad, N*4);
    return bad ? 3 : 0;
} }
