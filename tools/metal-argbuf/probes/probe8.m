// probe8.m: highest texture / buffer slots the HD 4000 driver binds for a compute pipeline (plain arguments).
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
int main(void) { @autoreleasepool {
    id<MTLDevice> d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
    int tslots[] = { 16, 30, 31, 32, 40, 64, 96, 127 }, bslots[] = { 23, 30 };
    for (int i = 0; i < 8; i++) {
        NSString *src = [NSString stringWithFormat:@"#include <metal_stdlib>\nusing namespace metal;\n"
            "kernel void k(texture2d<float> t [[texture(%d)]], device float4 *o [[buffer(1)]]) { o[0] = t.read(uint2(0)); }\n", tslots[i]];
        NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
        id<MTLComputePipelineState> p = lib ? [d newComputePipelineStateWithFunction:[lib newFunctionWithName:@"k"] error:&e] : nil;
        if (!p) { printf("texture(%d): %s\n", tslots[i], e.localizedDescription.UTF8String); continue; }
        MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
        id<MTLTexture> t = [d newTextureWithDescriptor:td]; float px[4] = { 1, 2, 3, (float)tslots[i] };
        [t replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:px bytesPerRow:16];
        id<MTLBuffer> o = [d newBufferWithLength:16 options:MTLResourceStorageModeShared]; memset(o.contents, 0xff, 16);
        id<MTLCommandBuffer> cb = [q commandBuffer]; id<MTLComputeCommandEncoder> ce = [cb computeCommandEncoder];
        [ce setComputePipelineState:p]; [ce setTexture:t atIndex:tslots[i]]; [ce setBuffer:o offset:0 atIndex:1];
        [ce dispatchThreads:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)]; [ce endEncoding]; [cb commit]; [cb waitUntilCompleted];
        float *f = o.contents; printf("texture(%d): %s (%g %g %g %g)\n", tslots[i], f[3] == tslots[i] ? "bound" : "NOT bound", f[0], f[1], f[2], f[3]);
    }
    (void)bslots; return 0;
} }
