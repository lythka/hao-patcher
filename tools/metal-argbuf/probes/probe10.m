// probe10.m: argument buffers through the real Metal API on the HD 4000 (iCBE wrapper + libargbuf).
// Run: DYLD_INSERT_LIBRARIES=/private/var/tmp/argbuf/libargbuf.dylib AGB_LOG=1 ./probe10
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <dlfcn.h>
static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct A1 { texture2d<float> t [[id(0)]]; device float4 *b [[id(1)]]; };\n"
"kernel void tA(constant A1 &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = ab.t.read(uint2(0, 0)) + ab.b[0]; }\n"
"struct A2 { device float4 *p [[id(0)]]; device float4 *q [[id(1)]]; };\n"
"kernel void tB(constant A2 &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = ab.p[gid & 3] + ab.q[1]; }\n"
"struct Inner { device float4 *b [[id(0)]]; texture2d<float> a [[id(1)]]; };\n"
"struct Outer { constant Inner &in [[id(0)]]; texture2d<float> t [[id(1)]]; };\n"
"kernel void tC(constant Outer &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = ab.in.b[gid & 3] + ab.in.a.read(uint2(0, 0)) + ab.t.read(uint2(0, 0)); }\n"
"struct A4 { uint k [[id(0)]]; device float4 *p [[id(1)]]; float4 v [[id(2)]]; };\n"
"kernel void tD(constant A4 &ab [[buffer(0)]], constant A1 &ab2 [[buffer(2)]], device float4 *out [[buffer(1)]],\n"
"               uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = float4(ab.k) + ab.p[0] + ab.v + ab2.t.read(uint2(0, 0)) + ab2.b[1]; }\n";

static id<MTLDevice> d;
static id<MTLBuffer> fbuf(float base, int n) {
    float v[64]; for (int i = 0; i < 4 * n; i++) v[i] = base + i;
    return [d newBufferWithBytes:v length:16 * n options:MTLResourceStorageModeShared];
}
static id<MTLTexture> ftex(float base) {
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
    id<MTLTexture> t = [d newTextureWithDescriptor:td]; float px[4] = { base, base + 1, base + 2, base + 3 };
    [t replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:px bytesPerRow:16]; return t;
}
static id<MTLBuffer> abuf(id<MTLArgumentEncoder> e) {
    printf("  encoder %s encodedLength %lu alignment %lu\n", object_getClassName(e), (unsigned long)e.encodedLength, (unsigned long)e.alignment);
    id<MTLBuffer> b = [d newBufferWithLength:MAX(e.encodedLength, 16) options:MTLResourceStorageModeShared];
    memset(b.contents, 0xcd, b.length); [e setArgumentBuffer:b offset:0]; return b;
}
enum { N = 32 };
static int check(const char *name, id<MTLBuffer> out, void (^want)(int g, float w[4])) {
    float *o = out.contents; int bad = 0;
    for (int g = 0; g < N; g++) { float w[4]; want(g, w);
        for (int c = 0; c < 4; c++) if (o[4 * g + c] != w[c]) { if (bad++ < 4) printf("  g=%d c=%d got %g want %g\n", g, c, o[4 * g + c], w[c]); } }
    printf("%s: %s (%d bad of %d)\n", name, bad ? "WRONG" : "CORRECT", bad, N * 4); return bad != 0;
}
static id<MTLBuffer> run(id<MTLCommandQueue> q, id<MTLComputePipelineState> p, void (^bind)(id<MTLComputeCommandEncoder>)) {
    id<MTLBuffer> out = [d newBufferWithLength:N * 16 options:MTLResourceStorageModeShared]; memset(out.contents, 0xff, N * 16);
    id<MTLCommandBuffer> cb = [q commandBuffer]; id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
    [ce setComputePipelineState:p]; bind(ce); [ce setBuffer:out offset:0 atIndex:1];
    [ce dispatchThreads:MTLSizeMake(N, 1, 1) threadsPerThreadgroup:MTLSizeMake(16, 1, 1)]; [ce endEncoding]; [cb commit]; [cb waitUntilCompleted];
    if (cb.status != MTLCommandBufferStatusCompleted) printf("  command buffer status %ld %s\n", (long)cb.status, cb.error.localizedDescription.UTF8String);
    return out;
}
static MTLArgumentDescriptor *D(MTLDataType t, NSUInteger i) {
    MTLArgumentDescriptor *a = [MTLArgumentDescriptor argumentDescriptor]; a.dataType = t; a.index = i; a.access = MTLBindingAccessReadOnly; return a;
}
int main(void) { @autoreleasepool {
    d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
    void (*install)(void) = dlsym(RTLD_DEFAULT, "agb_install"); if (install) install(); else printf("libargbuf not loaded\n");
    NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
    if (!lib) { printf("library FAILED %s\n", e.localizedDescription.UTF8String); return 1; }
    id<MTLComputePipelineState> P[4]; const char *names[4] = { "tA", "tB", "tC", "tD" };
    for (int i = 0; i < 4; i++) {
        P[i] = [d newComputePipelineStateWithFunction:[lib newFunctionWithName:@(names[i])] error:&e];
        printf("%s pipeline %s %s\n", names[i], P[i] ? "OK" : "FAILED", P[i] ? "" : e.localizedDescription.UTF8String); if (!P[i]) return 2;
    }
    int fails = 0;
    float *(^F)(id<MTLBuffer>) = ^float *(id<MTLBuffer> b) { return (float *)b.contents; };

    // A: descriptor-built encoder (texture + device pointer)
    { id<MTLArgumentEncoder> enc = [d newArgumentEncoderWithArguments:@[ D(MTLDataTypeTexture, 0), D(MTLDataTypePointer, 1) ]];
      id<MTLTexture> t = ftex(1); id<MTLBuffer> A = fbuf(100, 4), ab = abuf(enc);
      [enc setTexture:t atIndex:0]; [enc setBuffer:A offset:0 atIndex:1];
      printf("  AB words %llx %llx\n", ((unsigned long long *)ab.contents)[0], ((unsigned long long *)ab.contents)[1]);
      id<MTLBuffer> out = run(q, P[0], ^(id<MTLComputeCommandEncoder> ce) { [ce setBuffer:ab offset:0 atIndex:0]; [ce useResource:t usage:MTLResourceUsageRead]; [ce useResource:A usage:MTLResourceUsageRead]; });
      float *a = F(A); fails += check("A descriptor encoder", out, ^(int g, float w[4]) { for (int c = 0; c < 4; c++) w[c] = 1 + c + a[c]; }); }

    // B: reflection-built encoder (function newArgumentEncoderWithBufferIndex:), two pointers, offset 16
    { id<MTLArgumentEncoder> enc = [[lib newFunctionWithName:@"tB"] newArgumentEncoderWithBufferIndex:0];
      id<MTLBuffer> A = fbuf(100, 4), B = fbuf(1000, 4), ab = abuf(enc);
      [enc setBuffer:A offset:0 atIndex:0]; [enc setBuffer:B offset:16 atIndex:1];
      id<MTLBuffer> out = run(q, P[1], ^(id<MTLComputeCommandEncoder> ce) { [ce setBuffer:ab offset:0 atIndex:0]; });
      float *a = F(A), *b = F(B); fails += check("B reflection encoder", out, ^(int g, float w[4]) { for (int c = 0; c < 4; c++) w[c] = a[4 * (g & 3) + c] + b[8 + c]; }); }

    // C: nested argument buffer (newArgumentEncoderForBufferAtIndex:)
    { id<MTLArgumentEncoder> enc = [[lib newFunctionWithName:@"tC"] newArgumentEncoderWithBufferIndex:0];
      id<MTLArgumentEncoder> inner = [enc newArgumentEncoderForBufferAtIndex:0];
      id<MTLBuffer> ab = abuf(enc), ib = abuf(inner), A = fbuf(100, 4); id<MTLTexture> ta = ftex(10), tt = ftex(20000);
      [inner setBuffer:A offset:0 atIndex:0]; [inner setTexture:ta atIndex:1];
      [enc setBuffer:ib offset:0 atIndex:0]; [enc setTexture:tt atIndex:1];
      id<MTLBuffer> out = run(q, P[2], ^(id<MTLComputeCommandEncoder> ce) { [ce setBuffer:ab offset:0 atIndex:0]; });
      float *a = F(A); fails += check("C nested argument buffer", out, ^(int g, float w[4]) { for (int c = 0; c < 4; c++) w[c] = a[4 * (g & 3) + c] + 10 + c + 20000 + c; }); }

    // D: constants (constantDataAtIndex:) + a second argument buffer at buffer(2) in the same dispatch, AB at offset 256
    { id<MTLArgumentEncoder> enc = [[lib newFunctionWithName:@"tD"] newArgumentEncoderWithBufferIndex:0];
      id<MTLArgumentEncoder> enc2 = [d newArgumentEncoderWithArguments:@[ D(MTLDataTypeTexture, 0), D(MTLDataTypePointer, 1) ]];
      id<MTLBuffer> ab = abuf(enc), A = fbuf(100, 4), B = fbuf(5000, 4); id<MTLTexture> t = ftex(300);
      *(uint32_t *)[enc constantDataAtIndex:0] = 7; float vv[4] = { 0.5, 1.5, 2.5, 3.5 }; memcpy([enc constantDataAtIndex:2], vv, 16); float *v = vv;
      [enc setBuffer:A offset:32 atIndex:1];
      id<MTLBuffer> ab2 = [d newBufferWithLength:512 options:MTLResourceStorageModeShared];
      [enc2 setArgumentBuffer:ab2 offset:256]; [enc2 setTexture:t atIndex:0]; [enc2 setBuffer:B offset:0 atIndex:1];
      id<MTLBuffer> out = run(q, P[3], ^(id<MTLComputeCommandEncoder> ce) { [ce setBuffer:ab offset:0 atIndex:0]; [ce setBuffer:ab2 offset:256 atIndex:2]; });
      float *a = F(A), *b = F(B); fails += check("D constants + second argument buffer", out, ^(int g, float w[4]) {
          for (int c = 0; c < 4; c++) w[c] = 7 + a[8 + c] + v[c] + 300 + c + b[4 + c]; }); }
    return fails ? 3 : 0;
} }
