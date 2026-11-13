// Rosetta probe for tests/test_abi.py (needs a built bundle: its Metal27 + shim through DYLD_FRAMEWORK_PATH).
// Builds descriptors with known values through 27's public API, then asks each for its private struct twice:
// directly (27's layout expected) and through probe.dylib, which sits in a folder named like a Metal driver bundle
// (the shim translates only for callers inside "...GraphicsMTLDriver.bundle/"). Prints one JSON object.
//   abi_probe PROBE_DYLIB
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <dlfcn.h>
#include <objc/message.h>

#ifdef PROBE_DYLIB
// called the way the driver calls it: an ordinary call into objc_msgSend from code in the "driver" image
__attribute__((visibility("default"), disable_tail_calls)) void *hao_probe_private(id obj, SEL sel) {
  void *p = ((void *(*)(id, SEL))objc_msgSend)(obj, sel);
  __asm__ volatile("" ::: "memory");
  return p;
}
#else
static NSString *hex(const void *p, size_t n) {
  NSMutableString *s = [NSMutableString string];
  for (size_t i = 0; i < n; i++) [s appendFormat:@"%02x", ((const uint8_t *)p)[i]];
  return s;
}
int main(int argc, char **argv) {
  @autoreleasepool {
    if (argc < 2) return 2;
    void *h = dlopen(argv[1], RTLD_NOW);
    void *(*probe)(id, SEL) = h ? dlsym(h, "hao_probe_private") : NULL;
    if (!probe) { fprintf(stderr, "no probe: %s\n", dlerror()); return 2; }
    NSMutableDictionary *out = [NSMutableDictionary dictionary];

    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:64 height:32 mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    td.storageMode = MTLStorageModeManaged;
    td.cpuCacheMode = MTLCPUCacheModeWriteCombined;
    SEL dp = sel_registerName("descriptorPrivate"), udp = sel_registerName("_descriptorPrivate");
    out[@"texture"] = @{@"direct": hex(((void *(*)(id, SEL))objc_msgSend)(td, dp), 216), @"driver": hex(probe(td, dp), 192)};

    MTLRenderPipelineDescriptor *rp = [MTLRenderPipelineDescriptor new];
    rp.rasterizationEnabled = NO;
    rp.alphaToCoverageEnabled = YES;
    rp.rasterSampleCount = 4;
    rp.label = @"probe";
    MTLVertexDescriptor *vd = [MTLVertexDescriptor vertexDescriptor];
    rp.vertexDescriptor = vd;
    rp.depthAttachmentPixelFormat = MTLPixelFormatDepth32Float;
    MTLRenderPipelineColorAttachmentDescriptor *ca = rp.colorAttachments[0];
    ca.pixelFormat = MTLPixelFormatBGRA8Unorm;
    ca.blendingEnabled = YES;
    ca.writeMask = MTLColorWriteMaskRed | MTLColorWriteMaskAlpha;
    ca.destinationAlphaBlendFactor = MTLBlendFactorOneMinusSourceAlpha;
    ca.rgbBlendOperation = MTLBlendOperationSubtract;
    ca.sourceRGBBlendFactor = MTLBlendFactorSourceAlpha;
    out[@"pipeline"] = @{@"direct": hex(((void *(*)(id, SEL))objc_msgSend)(rp, udp), 736), @"driver": hex(probe(rp, udp), 600),
                         @"label": [NSString stringWithFormat:@"%p", (__bridge void *)rp.label],
                         @"vertexDescriptor": [NSString stringWithFormat:@"%p", *(void **)((uint8_t *)probe(rp, udp) + 256)],
                         @"vertexDescriptorDirect": [NSString stringWithFormat:@"%p", *(void **)((uint8_t *)((void *(*)(id, SEL))objc_msgSend)(rp, udp) + 272)]};
    out[@"attachment"] = @{@"direct": hex(((void *(*)(id, SEL))objc_msgSend)(ca, udp), 8), @"driver": hex(probe(ca, udp), 8)};
    NSData *j = [NSJSONSerialization dataWithJSONObject:out options:NSJSONWritingSortedKeys error:nil];
    fwrite(j.bytes, 1, j.length, stdout);
  }
  return 0;
}
#endif
