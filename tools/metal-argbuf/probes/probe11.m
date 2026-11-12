// probe11.m: argument buffers in a render pipeline (vertex + fragment) on the HD 4000 (iCBE wrapper + libargbuf).
// Run: DYLD_INSERT_LIBRARIES=/private/var/tmp/argbuf/libargbuf.dylib ./probe11
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <dlfcn.h>
static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct VA { device const float4 *pos [[id(0)]]; constant float4 *scale [[id(1)]]; };\n"
"struct FA { texture2d<float> t [[id(0)]]; constant float4 *k [[id(1)]]; texture2d<float> u [[id(2)]]; };\n"
"struct VO { float4 p [[position]]; };\n"
"vertex VO vs(uint vid [[vertex_id]], constant VA &va [[buffer(3)]]) { VO o; o.p = va.pos[vid] * va.scale[0]; return o; }\n"
"fragment float4 fs(VO in [[stage_in]], constant FA &fa [[buffer(5)]]) {\n"
"  return fa.t.read(uint2(0, 0)) + fa.k[1] + fa.u.read(uint2(0, 0)); }\n";
int main(void) { @autoreleasepool {
    id<MTLDevice> d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
    void (*install)(void) = dlsym(RTLD_DEFAULT, "agb_install"); if (install) install(); else printf("libargbuf not loaded\n");
    NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
    if (!lib) { printf("library FAILED %s\n", e.localizedDescription.UTF8String); return 1; }
    MTLRenderPipelineDescriptor *rd = [MTLRenderPipelineDescriptor new];
    rd.vertexFunction = [lib newFunctionWithName:@"vs"]; rd.fragmentFunction = [lib newFunctionWithName:@"fs"];
    rd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA32Float;
    id<MTLRenderPipelineState> p = [d newRenderPipelineStateWithDescriptor:rd error:&e];
    printf("render pipeline %s %s\n", p ? "OK" : "FAILED", p ? "" : e.localizedDescription.UTF8String); if (!p) return 2;
    enum { W = 8 };
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:W height:W mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead; td.storageMode = MTLStorageModeManaged;
    id<MTLTexture> rt = [d newTextureWithDescriptor:td];
    MTLTextureDescriptor *sd = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
    id<MTLTexture> t = [d newTextureWithDescriptor:sd], u = [d newTextureWithDescriptor:sd];
    float tp[4] = { 1, 2, 3, 4 }, up[4] = { 1000, 2000, 3000, 4000 };
    [t replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:tp bytesPerRow:16];
    [u replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:up bytesPerRow:16];
    // full-screen triangle, scaled by 2 (positions stored at half size)
    float pos[12] = { -0.5, -0.5, 0, 0.5,   1.5, -0.5, 0, 0.5,   -0.5, 1.5, 0, 0.5 }, sc[4] = { 2, 2, 1, 2 };
    float kk[8] = { 0, 0, 0, 0, 10, 20, 30, 40 };
    id<MTLBuffer> P = [d newBufferWithBytes:pos length:sizeof pos options:MTLResourceStorageModeShared];
    id<MTLBuffer> S = [d newBufferWithBytes:sc length:sizeof sc options:MTLResourceStorageModeShared];
    id<MTLBuffer> K = [d newBufferWithBytes:kk length:sizeof kk options:MTLResourceStorageModeShared];
    id<MTLArgumentEncoder> ve = [rd.vertexFunction newArgumentEncoderWithBufferIndex:3], fe = [rd.fragmentFunction newArgumentEncoderWithBufferIndex:5];
    id<MTLBuffer> vab = [d newBufferWithLength:256 options:MTLResourceStorageModeShared], fab = [d newBufferWithLength:256 options:MTLResourceStorageModeShared];
    [ve setArgumentBuffer:vab offset:0]; [ve setBuffer:P offset:0 atIndex:0]; [ve setBuffer:S offset:0 atIndex:1];
    [fe setArgumentBuffer:fab offset:64]; [fe setTexture:t atIndex:0]; [fe setBuffer:K offset:0 atIndex:1]; [fe setTexture:u atIndex:2];
    MTLRenderPassDescriptor *pd = [MTLRenderPassDescriptor renderPassDescriptor];
    pd.colorAttachments[0].texture = rt; pd.colorAttachments[0].loadAction = MTLLoadActionClear;
    pd.colorAttachments[0].clearColor = MTLClearColorMake(-1, -1, -1, -1); pd.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLCommandBuffer> cb = [q commandBuffer]; id<MTLRenderCommandEncoder> re = [cb renderCommandEncoderWithDescriptor:pd];
    [re setRenderPipelineState:p]; [re setVertexBuffer:vab offset:0 atIndex:3]; [re setFragmentBuffer:fab offset:64 atIndex:5];
    [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [re endEncoding];
    id<MTLBlitCommandEncoder> bl = [cb blitCommandEncoder]; [bl synchronizeResource:rt]; [bl endEncoding];
    [cb commit]; [cb waitUntilCompleted];
    printf("status %ld %s\n", (long)cb.status, cb.error ? cb.error.localizedDescription.UTF8String : "");
    float px[W * W * 4]; [rt getBytes:px bytesPerRow:W * 16 fromRegion:MTLRegionMake2D(0, 0, W, W) mipmapLevel:0];
    float want[4] = { 1 + 10 + 1000, 2 + 20 + 2000, 3 + 30 + 3000, 4 + 40 + 4000 }; int bad = 0;
    for (int i = 0; i < W * W; i++) for (int c = 0; c < 4; c++) if (px[4 * i + c] != want[c]) { if (bad++ < 4) printf("  px %d c %d got %g want %g\n", i, c, px[4 * i + c], want[c]); }
    printf("render vertex+fragment argument buffers: %s (%d bad of %d)\n", bad ? "WRONG" : "CORRECT", bad, W * W * 4);
    return bad ? 3 : 0;
} }
