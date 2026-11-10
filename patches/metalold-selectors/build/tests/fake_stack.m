// fake_stack: stand-ins for the legacy Metal classes the shim attaches to (named Fake + the real name: the host's own
// Metal holds the real names in this process), with the MetalOld 306 selectors the shim forwards to.
// usage: fake_stack LIBMETALOLD_SELECTORS_DYLIB -> prints "ok NAME" / "FAIL NAME: why" lines, exit 1 on any FAIL.
// Built x86_64 like the dylib (tests/test_shim.py), run under Rosetta.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <stdio.h>

typedef struct { NSUInteger width, height, depth; } FSize;
typedef struct { uint64_t _impl; } FResourceID;

static int failures = 0;
#define CHECK(name, cond, ...) do { if (cond) printf("ok %s\n", name); else { failures++; printf("FAIL %s: ", name); printf(__VA_ARGS__); printf("\n"); } } while (0)

@interface Fake_MTLDevice : NSObject
@property NSURL *ioURL; @property NSUInteger plane; @property NSUInteger bufferLength; @property NSUInteger bufferOptions;
@end
@implementation Fake_MTLDevice
- (BOOL)supportsRaytracing { return YES; }                                  // an existing answer the shim must keep
- (NSUInteger)argumentBuffersSupport { return 0; }                         // MTLArgumentBuffersTier1
- (id)newIOHandleWithURL:(NSURL *)url error:(NSError **)error { (void)error; self.ioURL = url; return @"iohandle"; }
- (id)newTextureWithDescriptor:(id)d iosurface:(void *)s plane:(NSUInteger)plane { (void)d; (void)s; self.plane = plane; return @"texture"; }
- (id)newSharedEvent { return @"sharedevent"; }
- (FSize)sparseTileSizeWithTextureType:(NSUInteger)t pixelFormat:(NSUInteger)f sampleCount:(NSUInteger)c { (void)t; (void)f; (void)c; return (FSize){4, 4, 1}; }
- (id)newBufferWithLength:(NSUInteger)length options:(NSUInteger)options { self.bufferLength = length; self.bufferOptions = options; return @"buffer"; }
@end
@interface FakeMTLIOAccelDevice : Fake_MTLDevice @end
@implementation FakeMTLIOAccelDevice @end
@interface FakeMTLIGAccelDevice : FakeMTLIOAccelDevice @end              // the driver's class inherits what the shim adds
@implementation FakeMTLIGAccelDevice @end

@interface FakeMTLBufferDescriptor : NSObject @end
@implementation FakeMTLBufferDescriptor
- (NSUInteger)length { return 4096; }
- (NSUInteger)resourceOptions { return 16; }
@end

@interface Fake_MTLCommandQueue : NSObject @end
@implementation Fake_MTLCommandQueue @end

@interface Fake_MTLCommandBuffer : NSObject
@property uint64_t signalled;
@end
@implementation Fake_MTLCommandBuffer
- (void)encodeSignalEvent:(id)event value:(uint64_t)value { (void)event; self.signalled = value; }
@end

@interface Fake_MTLCommandEncoder : NSObject
@property id buffer; @property NSUInteger offset; @property NSUInteger index;
@end
@implementation Fake_MTLCommandEncoder @end
@interface FakeMTLIGAccelRenderCommandEncoder : Fake_MTLCommandEncoder @end
@implementation FakeMTLIGAccelRenderCommandEncoder
- (void)setVertexBuffer:(id)buffer offset:(NSUInteger)offset atIndex:(NSUInteger)index { self.buffer = buffer; self.offset = offset; self.index = index; }
@end
@interface FakeMTLIGAccelBlitCommandEncoder : Fake_MTLCommandEncoder @end
@implementation FakeMTLIGAccelBlitCommandEncoder @end                 // answers no vertex setter: the forwarded call must not reach it

@interface FakeMTLIOAccelTexture : NSObject
@property NSRange levels; @property NSRange slices;
@end
@implementation FakeMTLIOAccelTexture
- (id)newTextureViewWithPixelFormat:(NSUInteger)f textureType:(NSUInteger)t levels:(NSRange)levels slices:(NSRange)slices { (void)f; (void)t; self.levels = levels; self.slices = slices; return @"view"; }
@end

@interface Fake_MTLRenderPipelineState : NSObject @end
@implementation Fake_MTLRenderPipelineState @end
@interface FakeMTLRenderPipelineDescriptor : NSObject @end
@implementation FakeMTLRenderPipelineDescriptor @end
@interface Fake_MTLDepthStencilState : NSObject @end
@implementation Fake_MTLDepthStencilState @end
@interface Fake_MTLSharedEvent : NSObject @end
@implementation Fake_MTLSharedEvent @end
@interface Fake_MTLHeap : NSObject @end
@implementation Fake_MTLHeap
- (NSUInteger)currentAllocatedSize { return 65536; }
@end
@interface FakeMTLArchitecture : NSObject @end
@implementation FakeMTLArchitecture @end

#define MSG(ret, obj, ...) ((ret (*)(id, SEL, ...))objc_msgSend)(obj, @selector(__VA_ARGS__))
#define SEND(ret, obj, sel, ...) ((ret (*)(id, SEL, ...))objc_msgSend)(obj, sel_registerName(sel), ##__VA_ARGS__)

int main(int argc, char **argv) { @autoreleasepool {
    if (argc < 2) { fprintf(stderr, "usage: fake_stack DYLIB\n"); return 2; }
    unsigned before = 0;
    Method *m = class_copyMethodList([Fake_MTLDevice class], &before);
    free(m);
    if (!dlopen(argv[1], RTLD_NOW)) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 1; }
    unsigned (*attach)(const char *) = dlsym(RTLD_DEFAULT, "metalold_selectors_attach_prefixed");
    BOOL (*at_load)(void) = dlsym(RTLD_DEFAULT, "metalold_selectors_attached_at_load");
    CHECK("exports attach", attach != NULL && at_load != NULL, "entry points not exported");
    printf("constructor attached: %d\n", at_load ? at_load() : -1);
    CHECK("attach adds to the stand-ins", attach("Fake") > 0, "nothing added");
    CHECK("attach is idempotent", attach("Fake") == 0, "a second attach added methods");

    FakeMTLIGAccelDevice *dev = [FakeMTLIGAccelDevice new];
    CHECK("device: supportsDynamicAttributeStride NO", [dev respondsToSelector:@selector(supportsDynamicAttributeStride)] && !SEND(BOOL, dev, "supportsDynamicAttributeStride"), "");
    CHECK("device: supportsFP8 NO", !SEND(BOOL, dev, "supportsFP8"), "");
    CHECK("device: existing supportsRaytracing kept", SEND(BOOL, dev, "supportsRaytracing") == YES, "the shim overrode an existing method");
    CHECK("device: supportsArgumentBuffers YES (tier 1 exists)", SEND(BOOL, dev, "supportsArgumentBuffers") == YES, "");
    CHECK("device: requiresBFloat16Emulation YES", SEND(BOOL, dev, "requiresBFloat16Emulation") == YES, "");
    CHECK("device: maximumConcurrentCompilationTaskCount 1", SEND(NSUInteger, dev, "maximumConcurrentCompilationTaskCount") == 1, "");
    NSError * __autoreleasing error = nil;   // the shim stores an autoreleased error through NSError **, as a method does
    id set = SEND(id, dev, "newResidencySetWithDescriptor:error:", nil, &error);
    CHECK("device: newResidencySetWithDescriptor nil + NSFeatureUnsupportedError", set == nil && error && error.code == NSFeatureUnsupportedError && [error.domain isEqual:NSCocoaErrorDomain], "set=%p code=%ld", set, (long)error.code);
    error = nil;
    NSURL *url = [NSURL fileURLWithPath:@"/tmp/x"];
    id handle = SEND(id, dev, "newIOFileHandleWithURL:error:", url, &error);
    CHECK("device: newIOFileHandleWithURL forwards to newIOHandleWithURL", [handle isEqual:@"iohandle"] && dev.ioURL == url && !error, "");
    CHECK("device: newTextureWithDescriptor:iosurface:plane:slice: slice 0 forwards", [SEND(id, dev, "newTextureWithDescriptor:iosurface:plane:slice:", nil, NULL, (NSUInteger)2, (NSUInteger)0) isEqual:@"texture"] && dev.plane == 2, "");
    CHECK("device: slice 1 answers nil", SEND(id, dev, "newTextureWithDescriptor:iosurface:plane:slice:", nil, NULL, (NSUInteger)0, (NSUInteger)1) == nil, "");
    FSize size = ((FSize (*)(id, SEL, NSUInteger, NSUInteger, NSUInteger, NSInteger))objc_msgSend_stret)(dev, sel_registerName("sparseTileSizeWithTextureType:pixelFormat:sampleCount:sparsePageSize:"), 2, 70, 1, 101);
    CHECK("device: sparseTileSize…sparsePageSize forwards (stret)", size.width == 4 && size.height == 4 && size.depth == 1, "%lu %lu %lu", size.width, size.height, size.depth);
    CHECK("device: newSharedEventWithOptions forwards", [SEND(id, dev, "newSharedEventWithOptions:", (NSUInteger)0) isEqual:@"sharedevent"], "");
    CHECK("device: newLogStateWithDescriptor nil", SEND(id, dev, "newLogStateWithDescriptor:error:", nil, NULL) == nil, "");
    id buffer = SEND(id, dev, "newBufferWithDescriptor:", [FakeMTLBufferDescriptor new]);
    CHECK("device: newBufferWithDescriptor forwards length/options", [buffer isEqual:@"buffer"] && dev.bufferLength == 4096 && dev.bufferOptions == 16, "");
    CHECK("device: newBufferWithDescriptor nil for nil", SEND(id, dev, "newBufferWithDescriptor:", nil) == nil, "");
    CHECK("device: setWritableHeapsEnabled round trip", (SEND(void, dev, "setWritableHeapsEnabled:", (BOOL)YES), SEND(BOOL, dev, "areWritableHeapsEnabled") == YES), "");

    Fake_MTLCommandQueue *queue = [Fake_MTLCommandQueue new];
    SEND(void, queue, "addResidencySet:", nil);
    SEND(void, queue, "removeResidencySets:count:", NULL, (NSUInteger)0);
    CHECK("queue: residency set calls are no-ops", YES, "");
    Fake_MTLCommandBuffer *cb = [Fake_MTLCommandBuffer new];
    SEND(void, cb, "useResidencySet:", nil);
    SEND(void, cb, "encodeSignalEvent:value:agentMask:", nil, (uint64_t)7, (uint64_t)1);
    CHECK("command buffer: encodeSignalEvent:value:agentMask: forwards", cb.signalled == 7, "signalled=%llu", cb.signalled);

    FakeMTLIGAccelRenderCommandEncoder *enc = [FakeMTLIGAccelRenderCommandEncoder new];
    SEND(void, enc, "setVertexBuffer:offset:attributeStride:atIndex:", @"b", (NSUInteger)64, NSUIntegerMax, (NSUInteger)3);
    CHECK("render encoder: attributeStride variant forwards to the static call", [enc.buffer isEqual:@"b"] && enc.offset == 64 && enc.index == 3, "");
    FakeMTLIGAccelBlitCommandEncoder *blit = [FakeMTLIGAccelBlitCommandEncoder new];
    SEND(void, blit, "setVertexBuffer:offset:attributeStride:atIndex:", @"b", (NSUInteger)0, NSUIntegerMax, (NSUInteger)0);
    CHECK("blit encoder: attributeStride variant does nothing without the static call", blit.buffer == nil, "");
    SEND(void, enc, "useResidencySet:", nil);
    CHECK("encoder: useResidencySet no-op", YES, "");

    FakeMTLIOAccelTexture *tex = [FakeMTLIOAccelTexture new];
    id view = SEND(id, tex, "newCompressedTextureViewWithPixelFormat:textureType:level:slice:", (NSUInteger)70, (NSUInteger)2, (NSUInteger)3, (NSUInteger)5);
    CHECK("texture: compressed view is the plain view of that level and slice", [view isEqual:@"view"] && NSEqualRanges(tex.levels, NSMakeRange(3, 1)) && NSEqualRanges(tex.slices, NSMakeRange(5, 1)), "");

    CHECK("pipeline state: shaderValidation Disabled", SEND(NSInteger, [Fake_MTLRenderPipelineState new], "shaderValidation") == 2, "");
    FakeMTLRenderPipelineDescriptor *desc = [FakeMTLRenderPipelineDescriptor new];
    CHECK("descriptor: shaderValidation Default", SEND(NSInteger, desc, "shaderValidation") == 0, "");
    SEND(void, desc, "setShaderValidation:", (NSInteger)1);
    CHECK("descriptor: shaderValidation round trip", SEND(NSInteger, desc, "shaderValidation") == 1, "");
    FResourceID rid = ((FResourceID (*)(id, SEL))objc_msgSend)([Fake_MTLDepthStencilState new], sel_registerName("gpuResourceID"));
    CHECK("depth stencil: gpuResourceID zero", rid._impl == 0, "");
    Fake_MTLSharedEvent *event = [Fake_MTLSharedEvent new];
    CHECK("shared event: IOSurfaceSharedEvent is self", SEND(id, event, "IOSurfaceSharedEvent") == event, "");
    CHECK("heap: allocatedSize is currentAllocatedSize", SEND(NSUInteger, [Fake_MTLHeap new], "allocatedSize") == 65536, "");
    FakeMTLArchitecture *arch = [FakeMTLArchitecture new];
    CHECK("architecture: copyWithZone is self", SEND(id, arch, "copyWithZone:", NULL) == arch, "");

    unsigned after = 0;
    m = class_copyMethodList([Fake_MTLDevice class], &after);
    free(m);
    CHECK("device class gained methods", after > before, "%u -> %u", before, after);
    printf("%s: %d failures\n", failures ? "FAILED" : "PASSED", failures);
    return failures ? 1 : 0;
} }
