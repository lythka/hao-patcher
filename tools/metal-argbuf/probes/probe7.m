// probe7.m: texture kinds through an argument buffer on the HD 4000 (iCBE wrapper declares a 2D range at slots AGB_TEX_SLOT..).
// Each texture is filled with one known value; the argument buffer holds handle 0 (slot 16).
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "../src/argbuf_config.h"
static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"constexpr sampler S(filter::nearest);\n"
"struct A2 { texture2d<float> t [[id(0)]]; };           kernel void k2(constant A2 &a [[buffer(0)]], device float4 *o [[buffer(1)]]) { o[0] = a.t.sample(S, float2(0.5), level(0)); }\n"
"struct A3 { texture3d<float> t [[id(0)]]; };           kernel void k3(constant A3 &a [[buffer(0)]], device float4 *o [[buffer(1)]]) { o[0] = a.t.sample(S, float3(0.5), level(0)); }\n"
"struct AC { texturecube<float> t [[id(0)]]; };         kernel void kc(constant AC &a [[buffer(0)]], device float4 *o [[buffer(1)]]) { o[0] = a.t.sample(S, float3(1, 0.2, 0.1), level(0)); }\n"
"struct AA { texture2d_array<float> t [[id(0)]]; };     kernel void ka(constant AA &a [[buffer(0)]], device float4 *o [[buffer(1)]]) { o[0] = a.t.sample(S, float2(0.5), 1, level(0)); }\n"
"struct AH { texture2d<half> t [[id(0)]]; };            kernel void kh(constant AH &a [[buffer(0)]], device float4 *o [[buffer(1)]]) { o[0] = float4(a.t.sample(S, float2(0.5), level(0))); }\n"
"struct AU { texture2d<ushort> t [[id(0)]]; };          kernel void ku(constant AU &a [[buffer(0)]], device float4 *o [[buffer(1)]]) { o[0] = float4(a.t.read(uint2(0, 0))); }\n"
"struct AD { depth2d<float> t [[id(0)]]; };             kernel void kd(constant AD &a [[buffer(0)]], device float4 *o [[buffer(1)]]) { o[0] = float4(a.t.sample(S, float2(0.5), level(0))); }\n"
"struct A1 { texture1d<float> t [[id(0)]]; };           kernel void k1(constant A1 &a [[buffer(0)]], device float4 *o [[buffer(1)]]) { o[0] = a.t.sample(S, 0.5); }\n"
"struct AY { texturecube_array<float> t [[id(0)]]; };   kernel void ky(constant AY &a [[buffer(0)]], device float4 *o [[buffer(1)]]) { o[0] = a.t.sample(S, float3(1, 0.2, 0.1), 1, level(0)); }\n";

static id<MTLTexture> make(id<MTLDevice> d, MTLTextureType type, MTLPixelFormat fmt, const void *px, size_t bpp) {
    MTLTextureDescriptor *td = [MTLTextureDescriptor new];
    td.textureType = type; td.pixelFormat = fmt; td.width = 2; td.height = type == MTLTextureType1D ? 1 : 2;
    td.depth = type == MTLTextureType3D ? 2 : 1; td.usage = MTLTextureUsageShaderRead;
    td.arrayLength = (type == MTLTextureType2DArray || type == MTLTextureTypeCubeArray) ? 2 : 1;
    if (fmt == MTLPixelFormatDepth32Float) td.storageMode = MTLStorageModePrivate;
    id<MTLTexture> t = [d newTextureWithDescriptor:td];
    if (!t || fmt == MTLPixelFormatDepth32Float) return t;
    int slices = (type == MTLTextureTypeCube ? 6 : type == MTLTextureTypeCubeArray ? 12 : (int)td.arrayLength);
    unsigned char row[64]; for (int i = 0; i < 2; i++) memcpy(row + i * bpp, px, bpp);
    unsigned char img[256]; for (int i = 0; i < 8; i++) memcpy(img + i * bpp, px, bpp);
    for (int s = 0; s < slices; s++)
        [t replaceRegion:MTLRegionMake3D(0, 0, 0, 2, td.height, td.depth) mipmapLevel:0 slice:s withBytes:img
             bytesPerRow:2 * bpp bytesPerImage:2 * bpp * td.height];
    (void)row; return t;
}
int main(void) { @autoreleasepool {
    id<MTLDevice> d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
    NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
    if (!lib) { printf("library FAILED %s\n", e.localizedDescription.UTF8String); return 1; }
    float f4[4] = { 0.25f, 0.5f, 0.75f, 1.0f };
    uint16_t h4[4] = { 0x3400, 0x3800, 0x3a00, 0x3c00 };          // half 0.25 0.5 0.75 1
    uint16_t u4[4] = { 7, 8, 9, 10 };
    struct { const char *k; MTLTextureType type; MTLPixelFormat fmt; const void *px; size_t bpp; float w[4]; } T[] = {
        { "k2", MTLTextureType2D, MTLPixelFormatRGBA32Float, f4, 16, { .25f, .5f, .75f, 1 } },
        { "k3", MTLTextureType3D, MTLPixelFormatRGBA32Float, f4, 16, { .25f, .5f, .75f, 1 } },
        { "kc", MTLTextureTypeCube, MTLPixelFormatRGBA32Float, f4, 16, { .25f, .5f, .75f, 1 } },
        { "ka", MTLTextureType2DArray, MTLPixelFormatRGBA32Float, f4, 16, { .25f, .5f, .75f, 1 } },
        { "kh", MTLTextureType2D, MTLPixelFormatRGBA16Float, h4, 8, { .25f, .5f, .75f, 1 } },
        { "ku", MTLTextureType2D, MTLPixelFormatRGBA16Uint, u4, 8, { 7, 8, 9, 10 } },
        { "kd", MTLTextureType2D, MTLPixelFormatDepth32Float, NULL, 4, { 0, 0, 0, 0 } },   // cleared private depth: 0
        { "k1", MTLTextureType1D, MTLPixelFormatRGBA32Float, f4, 16, { .25f, .5f, .75f, 1 } },
        { "ky", MTLTextureTypeCubeArray, MTLPixelFormatRGBA32Float, f4, 16, { .25f, .5f, .75f, 1 } },
    };
    int fails = 0;
    for (size_t i = 0; i < sizeof T / sizeof *T; i++) {
        id<MTLComputePipelineState> p = [d newComputePipelineStateWithFunction:[lib newFunctionWithName:@(T[i].k)] error:&e];
        if (!p) { printf("%s: pipeline FAILED %s\n", T[i].k, e.localizedDescription.UTF8String); fails++; continue; }
        id<MTLTexture> t = make(d, T[i].type, T[i].fmt, T[i].px, T[i].bpp);
        if (!t) { printf("%s: no texture\n", T[i].k); fails++; continue; }
        uint64_t ab[1] = { 0 };
        id<MTLBuffer> abuf = [d newBufferWithBytes:ab length:8 options:MTLResourceStorageModeShared];
        id<MTLBuffer> out = [d newBufferWithLength:16 options:MTLResourceStorageModeShared]; memset(out.contents, 0xff, 16);
        id<MTLCommandBuffer> cb = [q commandBuffer]; id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
        [ce setComputePipelineState:p]; [ce setBuffer:abuf offset:0 atIndex:0]; [ce setBuffer:out offset:0 atIndex:1];
        [ce setTexture:t atIndex:AGB_TEX_SLOT];
        [ce dispatchThreads:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)]; [ce endEncoding]; [cb commit]; [cb waitUntilCompleted];
        float *o = out.contents; int ok = cb.status == MTLCommandBufferStatusCompleted;
        for (int c = 0; c < 4; c++) if (o[c] != T[i].w[c]) ok = 0;
        printf("%s: %s got (%g %g %g %g) want (%g %g %g %g)%s\n", T[i].k, ok ? "CORRECT" : "WRONG", o[0], o[1], o[2], o[3],
               T[i].w[0], T[i].w[1], T[i].w[2], T[i].w[3], cb.error ? " (command buffer error)" : "");
        fails += !ok;
    }
    return fails ? 3 : 0;
} }
