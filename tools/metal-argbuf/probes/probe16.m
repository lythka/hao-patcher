// probe16.m: an argument buffer with an inline struct (constants + a texture inside it), encoded through the real API
// (reflection encoder; nested ids are parent id + member id). Kernel source: kernels/k17.metal, copied here.
// Run: DYLD_INSERT_LIBRARIES=/private/var/tmp/argbuf/libargbuf.dylib ./probe16   (or with libargbuf in the driver)
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <dlfcn.h>
static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct Inl { float3 a; float b; texture2d<float> t; uint c; };\n"
"struct AB { uint k [[id(0)]]; Inl s [[id(1)]]; float2 v [[id(10)]]; texture2d<float> u [[id(11)]]; };\n"
"kernel void kin(constant AB &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = float4(ab.k) + float4(ab.s.a, ab.s.b) + float4(ab.s.c) + float4(ab.v, 0, 0)\n"
"           + ab.s.t.read(uint2(0)) + ab.u.read(uint2(0)); }\n";
int main(void) { @autoreleasepool {
    id<MTLDevice> d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
    void (*install)(void) = dlsym(RTLD_DEFAULT, "agb_install"); if (install) install();
    NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
    if (!lib) { printf("library FAILED %s\n", e.localizedDescription.UTF8String); return 1; }
    id<MTLFunction> f = [lib newFunctionWithName:@"kin"];
    id<MTLComputePipelineState> p = [d newComputePipelineStateWithFunction:f error:&e];
    printf("pipeline %s %s\n", p ? "OK" : "FAILED", p ? "" : e.localizedDescription.UTF8String); if (!p) return 2;
    id<MTLArgumentEncoder> enc = [f newArgumentEncoderWithBufferIndex:0];
    printf("encodedLength %lu alignment %lu (want 80, 16)\n", (unsigned long)enc.encodedLength, (unsigned long)enc.alignment);
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
    id<MTLTexture> t = [d newTextureWithDescriptor:td], u = [d newTextureWithDescriptor:td];
    float tp[4] = { 100, 200, 300, 400 }, up[4] = { 1000, 2000, 3000, 4000 };
    [t replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:tp bytesPerRow:16];
    [u replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:up bytesPerRow:16];
    id<MTLBuffer> ab = [d newBufferWithLength:256 options:MTLResourceStorageModeShared];
    [enc setArgumentBuffer:ab offset:0];
    *(uint32_t *)[enc constantDataAtIndex:0] = 1;                                   // k
    float a[3] = { 2, 3, 4 }; memcpy([enc constantDataAtIndex:1], a, sizeof a);     // s.a  (id 1 + 0)
    *(float *)[enc constantDataAtIndex:2] = 5;                                      // s.b  (id 1 + 1)
    [enc setTexture:t atIndex:3];                                                   // s.t  (id 1 + 2)
    *(uint32_t *)[enc constantDataAtIndex:4] = 6;                                   // s.c  (id 1 + 3)
    float v[2] = { 7, 8 }; memcpy([enc constantDataAtIndex:10], v, sizeof v);       // v
    [enc setTexture:u atIndex:11];                                                  // u
    enum { N = 16 };
    id<MTLBuffer> out = [d newBufferWithLength:N * 16 options:MTLResourceStorageModeShared]; memset(out.contents, 0xff, N * 16);
    id<MTLCommandBuffer> cb = [q commandBuffer]; id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
    [ce setComputePipelineState:p]; [ce setBuffer:ab offset:0 atIndex:0]; [ce setBuffer:out offset:0 atIndex:1];
    [ce dispatchThreads:MTLSizeMake(N, 1, 1) threadsPerThreadgroup:MTLSizeMake(16, 1, 1)]; [ce endEncoding]; [cb commit]; [cb waitUntilCompleted];
    float want[4] = { 1 + 2 + 6 + 7 + 100 + 1000, 1 + 3 + 6 + 8 + 200 + 2000, 1 + 4 + 6 + 300 + 3000, 1 + 5 + 6 + 400 + 4000 };
    float *o = out.contents; int bad = 0;
    for (int g = 0; g < N; g++) for (int c = 0; c < 4; c++) if (o[4 * g + c] != want[c]) { if (bad++ < 4) printf("  g=%d c=%d got %g want %g\n", g, c, o[4 * g + c], want[c]); }
    printf("inline struct in an argument buffer: %s (%d bad of %d)\n", bad ? "WRONG" : "CORRECT", bad, N * 4);
    return bad ? 3 : 0;
} }
