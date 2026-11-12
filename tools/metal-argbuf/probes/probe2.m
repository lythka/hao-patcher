// probe2.m: GPU run of an argument-buffer kernel on the HD 4000 with the iCBE wrapper installed.
// The argument buffer is filled by hand (no argument encoder yet): texture handle k (slot 16+k) at offset 0,
// the data buffer's GPU address at offset 8. t0 reports the address the GPU itself uses for a bound buffer.
// Build (host): clang -arch x86_64 -mmacosx-version-min=11.0 -fobjc-arc -framework Metal -framework Foundation probe2.m -o probe2
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/message.h>

static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct AB { texture2d<float> t [[id(0)]]; device float4 *b [[id(1)]]; };\n"
"kernel void t0(device float4 *data [[buffer(0)]], device ulong *out [[buffer(1)]])\n"
"{ out[0] = reinterpret_cast<ulong>(data); }\n"
"kernel void t1(constant AB &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = ab.t.read(uint2(0, 0)) + ab.b[0]; }\n";

static id<MTLComputePipelineState> make_pipe(id<MTLDevice> d, id<MTLLibrary> lib, NSString *name) {
    NSError *e = nil; id<MTLFunction> f = [lib newFunctionWithName:name];
    id<MTLComputePipelineState> p = f ? [d newComputePipelineStateWithFunction:f error:&e] : nil;
    printf("%s: pipeline %s %s\n", name.UTF8String, p ? "OK" : "FAILED", e ? e.localizedDescription.UTF8String : "");
    return p;
}
static void run(id<MTLCommandQueue> q, void (^enc)(id<MTLComputeCommandEncoder>), const char *what) {
    id<MTLCommandBuffer> cb = [q commandBuffer]; id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
    enc(ce); [ce endEncoding]; [cb commit]; [cb waitUntilCompleted];
    printf("%s: status %ld %s\n", what, (long)cb.status, cb.error ? cb.error.localizedDescription.UTF8String : "");
}

int main(void) {
    @autoreleasepool {
        id<MTLDevice> d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
        NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
        if (!lib) { printf("library FAILED: %s\n", e.localizedDescription.UTF8String); return 1; }
        id<MTLComputePipelineState> p0 = make_pipe(d, lib, @"t0"), p1 = make_pipe(d, lib, @"t1");

        float dv[4] = { 1000, 2000, 3000, 4000 };
        id<MTLBuffer> data = [d newBufferWithBytes:dv length:sizeof dv options:MTLResourceStorageModeShared];
        uint64_t cpuAddr = [(id)data respondsToSelector:@selector(gpuAddress)] ? ((uint64_t (*)(id, SEL))objc_msgSend)(data, @selector(gpuAddress)) : 0;
        printf("data gpuAddress (CPU API) 0x%llx\n", cpuAddr);
        uint64_t gpuAddr = 0;
        if (p0) {
            id<MTLBuffer> o = [d newBufferWithLength:8 options:MTLResourceStorageModeShared];
            run(q, ^(id<MTLComputeCommandEncoder> ce) { [ce setComputePipelineState:p0]; [ce setBuffer:data offset:0 atIndex:0];
                [ce setBuffer:o offset:0 atIndex:1]; [ce dispatchThreads:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)]; }, "t0");
            gpuAddr = *(uint64_t *)o.contents; printf("data address seen by GPU 0x%llx\n", gpuAddr);
        }
        if (!p1) return 2;

        MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
        td.usage = MTLTextureUsageShaderRead;
        id<MTLTexture> tex = [d newTextureWithDescriptor:td];
        float px[4] = { 1, 2, 3, 4 };
        [tex replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:px bytesPerRow:16];

        uint64_t addrs[2] = { gpuAddr, cpuAddr };
        const char *names[2] = { "GPU-reported address", "gpuAddress API" };
        int pass = 0;
        for (int v = 0; v < 2; v++) {
            if (!addrs[v]) continue;
            uint64_t ab[2] = { 0 /* handle 0 -> slot 16 */, addrs[v] };
            id<MTLBuffer> abuf = [d newBufferWithBytes:ab length:sizeof ab options:MTLResourceStorageModeShared];
            enum { N = 32 };
            id<MTLBuffer> out = [d newBufferWithLength:N * 16 options:MTLResourceStorageModeShared];
            memset(out.contents, 0xff, N * 16);
            run(q, ^(id<MTLComputeCommandEncoder> ce) {
                [ce setComputePipelineState:p1];
                [ce setBuffer:abuf offset:0 atIndex:0]; [ce setBuffer:out offset:0 atIndex:1];
                [ce setBuffer:data offset:0 atIndex:2];            // residency for the pointed-to buffer
                [ce setTexture:tex atIndex:16];                     // argument-buffer texture slot 16 (handle 0)
                [ce dispatchThreads:MTLSizeMake(N,1,1) threadsPerThreadgroup:MTLSizeMake(16,1,1)]; }, names[v]);
            float *o = out.contents; int bad = 0;
            for (int g = 0; g < N; g++)
                for (int c = 0; c < 4; c++) if (o[4*g+c] != px[c] + dv[c]) { if (bad++ < 3) printf("  g=%d c=%d got %g want %g\n", g, c, o[4*g+c], px[c]+dv[c]); }
            printf("t1 with %s: %s (%d bad values)\n", names[v], bad ? "WRONG" : "CORRECT", bad);
            if (!bad) pass = 1;
        }
        return pass ? 0 : 3;
    }
}
