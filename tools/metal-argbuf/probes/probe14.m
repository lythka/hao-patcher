// probe14.m: render-stage argument buffers one stage at a time (vertex buffer, fragment buffer, vertex texture), to
// measure which slots each stage can use. Run with AGB_HANDLE_BASE=0,N to put the buffer handles at slots AGB_BUF_SLOT+N...
// Run: DYLD_INSERT_LIBRARIES=/private/var/tmp/argbuf/libargbuf.dylib AGB_HANDLE_BASE=0,N ./probe14
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <dlfcn.h>
static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct VA { device const float4 *pos [[id(0)]]; };\n"
"struct FA { constant float4 *k [[id(0)]]; };\n"
"struct VO { float4 p [[position]]; float4 c; };\n"
"struct VT { texture2d<float> t [[id(0)]]; };\n"
"vertex VO vtex(uint vid [[vertex_id]], constant float4 *pos [[buffer(3)]], constant VT &vt [[buffer(4)]])\n"
"{ VO o; o.p = pos[vid]; o.c = vt.t.read(uint2(0, 0)); return o; }\n"
"fragment float4 fcol(VO in [[stage_in]]) { return in.c; }\n"
"vertex VO vab(uint vid [[vertex_id]], constant VA &va [[buffer(3)]]) { VO o; o.p = va.pos[vid]; o.c = 0; return o; }\n"
"vertex VO vplain(uint vid [[vertex_id]], constant float4 *pos [[buffer(3)]]) { VO o; o.p = pos[vid]; o.c = 0; return o; }\n"
"fragment float4 fab(VO in [[stage_in]], constant FA &fa [[buffer(5)]]) { return fa.k[1]; }\n"
"fragment float4 fplain(VO in [[stage_in]]) { return float4(7, 8, 9, 10); }\n";
static id<MTLDevice> d;
static int draw(id<MTLLibrary> lib, id<MTLCommandQueue> q, int mode) {
    BOOL vstage = mode != 1;
    MTLRenderPipelineDescriptor *rd = [MTLRenderPipelineDescriptor new];
    rd.vertexFunction = [lib newFunctionWithName:mode == 2 ? @"vtex" : vstage ? @"vab" : @"vplain"];
    rd.fragmentFunction = [lib newFunctionWithName:mode == 2 ? @"fcol" : vstage ? @"fplain" : @"fab"];
    rd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA32Float;
    NSError *e = nil; id<MTLRenderPipelineState> p = [d newRenderPipelineStateWithDescriptor:rd error:&e];
    if (!p) { printf("mode %d pipeline FAILED %s\n", mode, e.localizedDescription.UTF8String); return 1; }
    enum { W = 4 };
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:W height:W mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget; td.storageMode = MTLStorageModeManaged;
    id<MTLTexture> rt = [d newTextureWithDescriptor:td];
    float pos[12] = { -1, -1, 0, 1, 3, -1, 0, 1, -1, 3, 0, 1 }, kk[8] = { 0, 0, 0, 0, 10, 20, 30, 40 };
    id<MTLBuffer> P = [d newBufferWithBytes:pos length:sizeof pos options:MTLResourceStorageModeShared];
    id<MTLBuffer> K = [d newBufferWithBytes:kk length:sizeof kk options:MTLResourceStorageModeShared];
    id<MTLBuffer> ab = [d newBufferWithLength:64 options:MTLResourceStorageModeShared];
    id<MTLArgumentEncoder> enc = mode == 2 ? [rd.vertexFunction newArgumentEncoderWithBufferIndex:4]
                               : vstage ? [rd.vertexFunction newArgumentEncoderWithBufferIndex:3] : [rd.fragmentFunction newArgumentEncoderWithBufferIndex:5];
    [enc setArgumentBuffer:ab offset:0];
    MTLTextureDescriptor *sd = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
    id<MTLTexture> tx = [d newTextureWithDescriptor:sd]; float tp[4] = { 5, 6, 7, 8 };
    [tx replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:tp bytesPerRow:16];
    if (mode == 2) [enc setTexture:tx atIndex:0]; else [enc setBuffer:vstage ? P : K offset:0 atIndex:0];
    MTLRenderPassDescriptor *pd = [MTLRenderPassDescriptor renderPassDescriptor];
    pd.colorAttachments[0].texture = rt; pd.colorAttachments[0].loadAction = MTLLoadActionClear;
    pd.colorAttachments[0].clearColor = MTLClearColorMake(-1, -1, -1, -1); pd.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLCommandBuffer> cb = [q commandBuffer]; id<MTLRenderCommandEncoder> re = [cb renderCommandEncoderWithDescriptor:pd];
    [re setRenderPipelineState:p];
    if (mode == 2) { [re setVertexBuffer:P offset:0 atIndex:3]; [re setVertexBuffer:ab offset:0 atIndex:4]; }
    else if (vstage) [re setVertexBuffer:ab offset:0 atIndex:3]; else { [re setVertexBuffer:P offset:0 atIndex:3]; [re setFragmentBuffer:ab offset:0 atIndex:5]; }
    [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [re endEncoding];
    id<MTLBlitCommandEncoder> bl = [cb blitCommandEncoder]; [bl synchronizeResource:rt]; [bl endEncoding];
    [cb commit]; [cb waitUntilCompleted];
    float px[4]; [rt getBytes:px bytesPerRow:W * 16 fromRegion:MTLRegionMake2D(1, 1, 1, 1) mipmapLevel:0];
    float want[3][4] = { { 7, 8, 9, 10 }, { 10, 20, 30, 40 }, { 5, 6, 7, 8 } };
    int ok = !memcmp(px, want[mode], sizeof px);
    static const char *what[] = { "vertex stage argument-buffer buffer", "fragment stage argument-buffer buffer", "vertex stage argument-buffer texture" };
    printf("%s: %s (got %g %g %g %g)\n", what[mode], ok ? "CORRECT" : "WRONG", px[0], px[1], px[2], px[3]);
    return !ok;
}
int main(void) { @autoreleasepool {
    d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
    void (*install)(void) = dlsym(RTLD_DEFAULT, "agb_install"); if (install) install(); else printf("libargbuf not loaded\n");
    NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
    if (!lib) { printf("library FAILED %s\n", e.localizedDescription.UTF8String); return 1; }
    int fails = draw(lib, q, 0);
    fails += draw(lib, q, 1);
    fails += draw(lib, q, 2);
    return fails ? 3 : 0;
} }
