// probe.m: HD 4000 argument-buffer spikes (no argument encoder is created; that call aborts on this driver).
//   T1  compile + pipeline for a kernel taking an argument buffer (texture + device pointer)
//   T2  array<texture2d<float>, 8> indexed per thread by a value loaded from a buffer: compile, run, check
// Build (host): clang -arch x86_64 -fobjc-arc -framework Metal -framework Foundation probe.m -o probe && codesign -s - probe
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct AB { texture2d<float> t [[id(0)]]; device float4 *b [[id(1)]]; };\n"
"kernel void t1(constant AB &ab [[buffer(0)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = ab.t.read(uint2(0, 0)) + ab.b[0]; }\n"
"kernel void t2(array<texture2d<float>, 8> texs [[texture(0)]], device const uint *idx [[buffer(0)]],\n"
"               device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = texs[idx[gid]].read(uint2(0, 0)); }\n";

static id<MTLComputePipelineState> make_pipe(id<MTLDevice> d, id<MTLLibrary> lib, NSString *name) {
    NSError *e = nil;
    id<MTLFunction> f = [lib newFunctionWithName:name];
    if (!f) { printf("%s: no function\n", name.UTF8String); return nil; }
    id<MTLComputePipelineState> p = [d newComputePipelineStateWithFunction:f error:&e];
    printf("%s: pipeline %s%s%s\n", name.UTF8String, p ? "OK" : "FAILED", e ? " error: " : "",
           e ? e.localizedDescription.UTF8String : "");
    return p;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        id<MTLDevice> d = MTLCreateSystemDefaultDevice();
        if (!d) { printf("no Metal device\n"); return 1; }
        printf("device: %s class %s argumentBuffersSupport %lu\n", d.name.UTF8String,
               NSStringFromClass([d class]).UTF8String, (unsigned long)d.argumentBuffersSupport);
        NSError *e = nil;
        id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
        if (!lib) { printf("library FAILED: %s\n", e.localizedDescription.UTF8String); return 1; }
        if (e) printf("library warnings: %s\n", e.localizedDescription.UTF8String);
        make_pipe(d, lib, @"t1");
        id<MTLComputePipelineState> p2 = make_pipe(d, lib, @"t2");
        if (!p2) return 2;

        // T2: 8 one-pixel textures, texture i holds (i, 10i, 100i, 1); thread g picks texture idx[g] = (g*5+3)%8
        enum { N = 64 };
        NSMutableArray<id<MTLTexture>> *texs = [NSMutableArray array];
        for (int i = 0; i < 8; i++) {
            MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float
                                                                                          width:1 height:1 mipmapped:NO];
            td.usage = MTLTextureUsageShaderRead;
            id<MTLTexture> t = [d newTextureWithDescriptor:td];
            float px[4] = { (float)i, 10.0f * i, 100.0f * i, 1.0f };
            [t replaceRegion:MTLRegionMake2D(0, 0, 1, 1) mipmapLevel:0 withBytes:px bytesPerRow:16];
            [texs addObject:t];
        }
        uint32_t idx[N];
        for (int g = 0; g < N; g++) idx[g] = (uint32_t)((g * 5 + 3) % 8);
        id<MTLBuffer> bi = [d newBufferWithBytes:idx length:sizeof idx options:MTLResourceStorageModeShared];
        id<MTLBuffer> bo = [d newBufferWithLength:N * 16 options:MTLResourceStorageModeShared];
        memset(bo.contents, 0xff, N * 16);
        id<MTLCommandQueue> q = [d newCommandQueue];
        id<MTLCommandBuffer> cb = [q commandBuffer];
        id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
        [ce setComputePipelineState:p2];
        for (int i = 0; i < 8; i++) [ce setTexture:texs[i] atIndex:i];
        [ce setBuffer:bi offset:0 atIndex:0];
        [ce setBuffer:bo offset:0 atIndex:1];
        [ce dispatchThreads:MTLSizeMake(N, 1, 1) threadsPerThreadgroup:MTLSizeMake(16, 1, 1)];
        [ce endEncoding];
        [cb commit];
        [cb waitUntilCompleted];
        printf("t2: command buffer status %ld%s%s\n", (long)cb.status, cb.error ? " error: " : "",
               cb.error ? cb.error.localizedDescription.UTF8String : "");
        float *o = bo.contents; int bad = 0;
        for (int g = 0; g < N; g++) {
            float i = (float)idx[g];
            if (o[4*g] != i || o[4*g+1] != 10*i || o[4*g+2] != 100*i || o[4*g+3] != 1) {
                if (bad++ < 8) printf("  t2 mismatch g=%d idx=%u got (%g %g %g %g)\n", g, idx[g], o[4*g], o[4*g+1], o[4*g+2], o[4*g+3]);
            }
        }
        printf("t2: %d/%d threads correct\n", N - bad, N);
        return bad ? 3 : 0;
    }
}
