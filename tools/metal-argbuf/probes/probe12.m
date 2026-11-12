// probe12.m: samplers stored in argument buffers on the HD 4000 (iCBE wrapper with the AIR hook + libargbuf).
// A 2x1 texture (texel 0 = 0, texel 1 = 1) sampled at x = 0.375: linear filtering gives 0.25, nearest gives 0, so the
// result shows which sampler state the GPU used.
// Run: DYLD_INSERT_LIBRARIES=/private/var/tmp/argbuf/libargbuf.dylib ./probe12
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <dlfcn.h>
static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct AS { texture2d<float> t [[id(0)]]; sampler s [[id(1)]]; };\n"
"kernel void s1(constant AS &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = ab.t.sample(ab.s, float2(0.375, 0.5), level(0)); }\n"
"kernel void s2(texture2d<float> tx [[texture(2)]], sampler sm [[sampler(3)]], constant AS &ab [[buffer(0)]],\n"
"               device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = ab.t.sample(ab.s, float2(0.375, 0.5), level(0)) + 10 * tx.sample(sm, float2(0.375, 0.5), level(0)); }\n"
"kernel void s3(constant AS &a [[buffer(0)]], constant AS &b [[buffer(2)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = a.t.sample(a.s, float2(0.375, 0.5), level(0)) + 100 * b.t.sample(b.s, float2(0.375, 0.5), level(0)); }\n";
static id<MTLDevice> d;
enum { N = 16 };
static id<MTLSamplerState> sampler(BOOL linear) {
    MTLSamplerDescriptor *sd = [MTLSamplerDescriptor new];
    sd.minFilter = sd.magFilter = linear ? MTLSamplerMinMagFilterLinear : MTLSamplerMinMagFilterNearest;
    sd.sAddressMode = sd.tAddressMode = MTLSamplerAddressModeClampToEdge; sd.supportArgumentBuffers = YES;
    return [d newSamplerStateWithDescriptor:sd];
}
static id<MTLBuffer> run(id<MTLCommandQueue> q, id<MTLComputePipelineState> p, void (^bind)(id<MTLComputeCommandEncoder>)) {
    id<MTLBuffer> out = [d newBufferWithLength:N * 16 options:MTLResourceStorageModeShared]; memset(out.contents, 0xff, N * 16);
    id<MTLCommandBuffer> cb = [q commandBuffer]; id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
    [ce setComputePipelineState:p]; bind(ce); [ce setBuffer:out offset:0 atIndex:1];
    [ce dispatchThreads:MTLSizeMake(N, 1, 1) threadsPerThreadgroup:MTLSizeMake(16, 1, 1)]; [ce endEncoding]; [cb commit]; [cb waitUntilCompleted];
    if (cb.status != MTLCommandBufferStatusCompleted) printf("  command buffer status %ld\n", (long)cb.status);
    return out;
}
static int check(const char *name, id<MTLBuffer> out, float want) {
    float *o = out.contents; int bad = 0;
    for (int g = 0; g < N; g++) if (fabsf(o[4 * g] - want) > 1e-3f) { if (bad++ < 3) printf("  g=%d got %g want %g\n", g, o[4 * g], want); }
    printf("%s: %s (x = %g, want %g)\n", name, bad ? "WRONG" : "CORRECT", o[0], want); return bad != 0;
}
int main(void) { @autoreleasepool {
    d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
    void (*install)(void) = dlsym(RTLD_DEFAULT, "agb_install"); if (install) install(); else printf("libargbuf not loaded\n");
    NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
    if (!lib) { printf("library FAILED %s\n", e.localizedDescription.UTF8String); return 1; }
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:2 height:1 mipmapped:NO];
    id<MTLTexture> tex = [d newTextureWithDescriptor:td], tex2 = [d newTextureWithDescriptor:td];
    float px[8] = { 0, 0, 0, 0, 1, 1, 1, 1 };
    [tex replaceRegion:MTLRegionMake2D(0, 0, 2, 1) mipmapLevel:0 withBytes:px bytesPerRow:32];
    [tex2 replaceRegion:MTLRegionMake2D(0, 0, 2, 1) mipmapLevel:0 withBytes:px bytesPerRow:32];
    id<MTLSamplerState> lin = sampler(YES), near = sampler(NO);
    int fails = 0;
    id<MTLFunction> f1 = [lib newFunctionWithName:@"s1"], f2 = [lib newFunctionWithName:@"s2"], f3 = [lib newFunctionWithName:@"s3"];
    id<MTLComputePipelineState> p1 = [d newComputePipelineStateWithFunction:f1 error:&e], p2 = [d newComputePipelineStateWithFunction:f2 error:&e],
                                p3 = [d newComputePipelineStateWithFunction:f3 error:&e];
    printf("pipelines %s %s %s\n", p1 ? "OK" : "FAILED", p2 ? "OK" : "FAILED", p3 ? "OK" : "FAILED"); if (!p1 || !p2 || !p3) return 2;
    for (int which = 0; which < 2; which++) {   // s1: AB sampler linear, then nearest
        id<MTLArgumentEncoder> enc = [f1 newArgumentEncoderWithBufferIndex:0];
        id<MTLBuffer> ab = [d newBufferWithLength:64 options:MTLResourceStorageModeShared];
        [enc setArgumentBuffer:ab offset:0]; [enc setTexture:tex atIndex:0]; [enc setSamplerState:which ? near : lin atIndex:1];
        id<MTLBuffer> out = run(q, p1, ^(id<MTLComputeCommandEncoder> ce) { [ce setBuffer:ab offset:0 atIndex:0]; });
        fails += check(which ? "s1 argument-buffer sampler (nearest)" : "s1 argument-buffer sampler (linear)", out, which ? 0 : 0.25f);
    }
    {   // s2: own texture + own sampler (nearest) alongside an AB texture + AB sampler (linear)
        id<MTLArgumentEncoder> enc = [f2 newArgumentEncoderWithBufferIndex:0];
        id<MTLBuffer> ab = [d newBufferWithLength:64 options:MTLResourceStorageModeShared];
        [enc setArgumentBuffer:ab offset:0]; [enc setTexture:tex atIndex:0]; [enc setSamplerState:lin atIndex:1];
        id<MTLBuffer> out = run(q, p2, ^(id<MTLComputeCommandEncoder> ce) {
            [ce setBuffer:ab offset:0 atIndex:0]; [ce setTexture:tex2 atIndex:2]; [ce setSamplerState:near atIndex:3]; });
        fails += check("s2 own sampler + argument-buffer sampler", out, 0.25f + 10 * 0);
        id<MTLBuffer> out2 = run(q, p2, ^(id<MTLComputeCommandEncoder> ce) {
            [ce setBuffer:ab offset:0 atIndex:0]; [ce setTexture:tex2 atIndex:2]; [ce setSamplerState:lin atIndex:3]; });
        fails += check("s2 own sampler linear too", out2, 0.25f + 10 * 0.25f);
    }
    {   // s3: two argument buffers with different samplers
        id<MTLArgumentEncoder> enc = [f3 newArgumentEncoderWithBufferIndex:0];
        id<MTLBuffer> a = [d newBufferWithLength:64 options:MTLResourceStorageModeShared], b = [d newBufferWithLength:64 options:MTLResourceStorageModeShared];
        [enc setArgumentBuffer:a offset:0]; [enc setTexture:tex atIndex:0]; [enc setSamplerState:near atIndex:1];
        [enc setArgumentBuffer:b offset:0]; [enc setTexture:tex atIndex:0]; [enc setSamplerState:lin atIndex:1];
        id<MTLBuffer> out = run(q, p3, ^(id<MTLComputeCommandEncoder> ce) { [ce setBuffer:a offset:0 atIndex:0]; [ce setBuffer:b offset:0 atIndex:2]; });
        fails += check("s3 two argument buffers, different samplers", out, 0 + 100 * 0.25f);
    }
    return fails ? 3 : 0;
} }
