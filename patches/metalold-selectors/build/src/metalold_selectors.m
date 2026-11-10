// metalold_selectors.m: the macOS 26 Metal selectors that OCLP's legacy Metal stack (MetalOld 306 + the HD 4000
// driver) does not answer, added to MetalOld's base classes at load time with the answer a device without the
// feature gives. Loaded by the GPU driver bundle (LC_LOAD_WEAK_DYLIB added by patches/metalold-selectors), so it is
// in the process exactly when the legacy device classes are.
//
// Rules of this file (patches/metalold-selectors/build/README.md "How it works"):
//   - a method is added only when the class, with its superclasses, does not answer the selector (never an override);
//   - a capability query answers NO, a factory for an object kind this stack has answers nil (with an NSError where
//     the call takes one), a renamed selector forwards to its pre-rename name, a residency-set call is a no-op (the
//     device never hands out a residency set), a dynamic-attribute-stride encoder call forwards to the static variant
//     (the device answers supportsDynamicAttributeStride NO, so a caller honouring it passes MTLAttributeStrideStatic);
//   - nothing here claims a capability the hardware lacks.
// Kill switch: METALOLD_SELECTORS=0 in the process environment.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <os/log.h>

typedef struct { NSUInteger width, height, depth; } MOSSize;       // MTLSize
typedef struct { uint64_t _impl; } MOSResourceID;                    // MTLResourceID
static const NSInteger MOSShaderValidationDefault = 0;               // MTLShaderValidationDefault
static const NSInteger MOSShaderValidationDisabled = 2;              // MTLShaderValidationDisabled

// The pre-rename / pre-feature selectors of MetalOld 306 the shim forwards to, declared so the compiler emits the
// right call (objc_msgSend_stret for MTLSize).
@protocol MOSLegacy <NSObject>
- (NSUInteger)length;
- (NSUInteger)resourceOptions;
- (id)newBufferWithLength:(NSUInteger)length options:(NSUInteger)options;
- (NSUInteger)currentAllocatedSize;
- (NSUInteger)size;
- (id)newIOHandleWithURL:(NSURL *)url error:(NSError **)error;
- (id)newIOHandleWithURL:(NSURL *)url compressionMethod:(NSInteger)method error:(NSError **)error;
- (id)newTextureWithDescriptor:(id)descriptor iosurface:(void *)iosurface plane:(NSUInteger)plane;
- (id)newSharedEvent;
- (id)newEvent;
- (MOSSize)sparseTileSizeWithTextureType:(NSUInteger)type pixelFormat:(NSUInteger)format sampleCount:(NSUInteger)samples;
- (id)newTextureViewWithPixelFormat:(NSUInteger)format textureType:(NSUInteger)type levels:(NSRange)levels slices:(NSRange)slices;
- (void)encodeSignalEvent:(id)event value:(uint64_t)value;
- (void)setVertexBuffer:(id)buffer offset:(NSUInteger)offset atIndex:(NSUInteger)index;
- (void)setVertexBuffers:(const id *)buffers offsets:(const NSUInteger *)offsets withRange:(NSRange)range;
- (void)setVertexBufferOffset:(NSUInteger)offset atIndex:(NSUInteger)index;
- (void)setVertexBytes:(const void *)bytes length:(NSUInteger)length atIndex:(NSUInteger)index;
- (void)setBuffer:(id)buffer offset:(NSUInteger)offset atIndex:(NSUInteger)index;
- (void)setBuffers:(const id *)buffers offsets:(const NSUInteger *)offsets withRange:(NSRange)range;
- (void)setBufferOffset:(NSUInteger)offset atIndex:(NSUInteger)index;
- (void)setBytes:(const void *)bytes length:(NSUInteger)length atIndex:(NSUInteger)index;
- (void)setKernelBuffer:(id)buffer offset:(NSUInteger)offset atIndex:(NSUInteger)index;
@end

static os_log_t mos_log(void) {
    static os_log_t log;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ log = os_log_create("metalold-selectors", "shim"); });
    return log;
}

static BOOL answers(id self, SEL sel) { return [self respondsToSelector:sel]; }

static id unsupported(NSError **error, SEL sel) {
    if (error) *error = [NSError errorWithDomain:NSCocoaErrorDomain code:NSFeatureUnsupportedError userInfo:@{
        NSLocalizedDescriptionKey: [NSString stringWithFormat:@"%s is not supported by this Metal device", sel_getName(sel)]}];
    return nil;
}

static const void *MOSShaderValidationKey = &MOSShaderValidationKey;
static const void *MOSForwardProgressUsageKey = &MOSForwardProgressUsageKey;
static const void *MOSWritableHeapsKey = &MOSWritableHeapsKey;

// ---- _MTLDevice (MTLDevice, MTLDeviceSPI) ------------------------------------------------------------------------
@interface MOSDevice : NSObject @end
@implementation MOSDevice
// capability queries: the hardware or the legacy driver lacks every one of these
- (BOOL)supportsDynamicAttributeStride { return NO; }
- (BOOL)supportsBfloat16Buffers { return NO; }
- (BOOL)supportsBufferWithAddressRanges { return NO; }
- (BOOL)supportsHeapWithAddressRanges { return NO; }
- (BOOL)supportsColorSpaceConversionMatrixSelection { return NO; }
- (BOOL)supportsConditionalLoadStore { return NO; }
- (BOOL)supportsConditionalTileDispatch { return NO; }
- (BOOL)supportsDepthBoundsTesting { return NO; }
- (BOOL)supportsDeviceCoherency { return NO; }
- (BOOL)supportsExtendedIndirectRenderCommand { return NO; }
- (BOOL)supportsExtendedVertexFormats { return NO; }
- (BOOL)supportsFP8 { return NO; }
- (BOOL)supportsGlobalVariableBindingInDylibs { return NO; }
- (BOOL)supportsGlobalVariableBindings { return NO; }
- (BOOL)supportsMXU { return NO; }
- (BOOL)supportsMeshRenderDynamicLibraries { return NO; }
- (BOOL)supportsPerPlaneCompression { return NO; }
- (BOOL)supportsPlacementSparse { return NO; }
- (BOOL)supportsRayTracingCurves { return NO; }
- (BOOL)supportsRayTracingDirectIntersectionResultAccess { return NO; }
- (BOOL)supportsRayTracingGPUTableUpdateBuffers { return NO; }
- (BOOL)supportsRayTracingIndirectInstanceAccelerationStructureBuild { return NO; }
- (BOOL)supportsRayTracingMatrixLayout { return NO; }
- (BOOL)supportsRayTracingMultiLevelInstancing { return NO; }
- (BOOL)supportsRayTracingPerComponentMotionInterpolation { return NO; }
- (BOOL)supportsRayTracingTraversalMetrics { return NO; }
- (BOOL)supportsResourceDetachBacking { return NO; }
- (BOOL)supportsTextureAccessPatterns { return NO; }
- (BOOL)supportsVirtualSubstreams { return NO; }
// Tahoe: requiresBFloat16Emulation = !supportsFamily(MTLGPUFamilyApple9)
- (BOOL)requiresBFloat16Emulation { return YES; }
// Tahoe: supportsArgumentBuffers asks the feature table; every Metal device has argument buffers tier 1, which
// MetalOld reports through argumentBuffersSupport (MTLArgumentBuffersTier1 = 0, so the tier itself is not a flag)
- (BOOL)supportsArgumentBuffers { return answers(self, @selector(argumentBuffersSupport)); }
// compilation concurrency (Metal 3.1): no concurrency promise from the legacy compiler service
- (NSUInteger)maximumConcurrentCompilationTaskCount { return 1; }
- (NSUInteger)maxConcurrentExecutingCompilationTasks { return 1; }   // Tahoe forwards to maximumConcurrentCompilationTaskCount
- (BOOL)shouldMaximizeConcurrentCompilation { return NO; }
- (void)setShouldMaximizeConcurrentCompilation:(BOOL)value { (void)value; }
- (void)setCompilerProcessesCount:(int)count { (void)count; }
- (BOOL)areWritableHeapsEnabled { return [objc_getAssociatedObject(self, MOSWritableHeapsKey) boolValue]; }
- (void)setWritableHeapsEnabled:(BOOL)value { objc_setAssociatedObject(self, MOSWritableHeapsKey, @(value), OBJC_ASSOCIATION_RETAIN_NONATOMIC); }
// ray tracing limits: no acceleration structures on this stack
- (NSUInteger)maxAccelerationStructureLevels { return 0; }
- (NSUInteger)maxAccelerationStructureTraversalDepth { return 0; }
- (NSUInteger)latestSupportedGenericBVHVersion { return 0; }
// object kinds this stack does not have: nil, with the error where the call takes one
- (id)newResidencySetWithDescriptor:(id)descriptor error:(NSError **)error { (void)descriptor; return unsupported(error, _cmd); }
- (id)newLogStateWithDescriptor:(id)descriptor error:(NSError **)error { (void)descriptor; return unsupported(error, _cmd); }
- (id)newLibraryWithCIFiltersForComputePipeline:(id)pipeline imageFilterFunctionInfo:(id)info error:(NSError **)error { (void)pipeline; (void)info; return unsupported(error, _cmd); }
- (id)newLibraryWithGraphs:(id)graphs functions:(id)functions error:(NSError **)error { (void)graphs; (void)functions; return unsupported(error, _cmd); }
- (id)newLibraryWithGraphsSPI:(id)graphs functions:(id)functions error:(NSError **)error { (void)graphs; (void)functions; return unsupported(error, _cmd); }
- (id)newMotionEstimationPipelineWithDescriptor:(id)descriptor { (void)descriptor; return nil; }
- (id)newResourceGroupFromResources:(const id *)resources count:(NSUInteger)count { (void)resources; (void)count; return nil; }
- (id)newIndirectArgumentEncoderWithArguments:(id)arguments { (void)arguments; return nil; }
- (id)newBufferWithLength:(NSUInteger)length options:(NSUInteger)options gpuAddress:(uint64_t)address { (void)length; (void)options; (void)address; return nil; }
- (id)newBufferWithBytes:(const void *)bytes length:(NSUInteger)length options:(NSUInteger)options gpuAddress:(uint64_t)address { (void)bytes; (void)length; (void)options; (void)address; return nil; }
- (id)newBufferWithBytesNoCopy:(void *)bytes length:(NSUInteger)length options:(NSUInteger)options gpuAddress:(uint64_t)address deallocator:(id)deallocator { (void)bytes; (void)length; (void)options; (void)address; (void)deallocator; return nil; }
- (id)newTextureWithBytesNoCopy:(void *)bytes length:(NSUInteger)length descriptor:(id)descriptor deallocator:(id)deallocator { (void)bytes; (void)length; (void)descriptor; (void)deallocator; return nil; }
- (id)newTextureLayoutWithDescriptor:(id)descriptor isHeapOrBufferBacked:(BOOL)backed { (void)descriptor; (void)backed; return nil; }
// a buffer from a descriptor (driver SPI, CMImaging/CMCapture): the length and options the 306 descriptor carries
- (id)newBufferWithDescriptor:(id)descriptor {
    if (!descriptor || !answers(descriptor, @selector(length)) || !answers(descriptor, @selector(resourceOptions))) return nil;
    return [(id<MOSLegacy>)self newBufferWithLength:[(id<MOSLegacy>)descriptor length] options:[(id<MOSLegacy>)descriptor resourceOptions]];
}
// renamed or extended selectors: forward to the MetalOld 306 name
- (id)newIOFileHandleWithURL:(NSURL *)url error:(NSError **)error {
    if (!answers(self, @selector(newIOHandleWithURL:error:))) return unsupported(error, _cmd);
    return [(id<MOSLegacy>)self newIOHandleWithURL:url error:error];
}
- (id)newIOFileHandleWithURL:(NSURL *)url compressionMethod:(NSInteger)method error:(NSError **)error {
    if (!answers(self, @selector(newIOHandleWithURL:compressionMethod:error:))) return unsupported(error, _cmd);
    return [(id<MOSLegacy>)self newIOHandleWithURL:url compressionMethod:method error:error];
}
- (id)newTextureWithDescriptor:(id)descriptor iosurface:(void *)iosurface plane:(NSUInteger)plane slice:(NSUInteger)slice {
    // slices of an IOSurface are a later addition; MetalOld binds plane 0.. of a 2D surface only
    if (slice != 0 || !answers(self, @selector(newTextureWithDescriptor:iosurface:plane:))) return nil;
    return [(id<MOSLegacy>)self newTextureWithDescriptor:descriptor iosurface:iosurface plane:plane];
}
- (id)newSharedEventWithOptions:(NSUInteger)options { (void)options; return answers(self, @selector(newSharedEvent)) ? [(id<MOSLegacy>)self newSharedEvent] : nil; }
- (id)newEventWithOptions:(NSUInteger)options { (void)options; return answers(self, @selector(newEvent)) ? [(id<MOSLegacy>)self newEvent] : nil; }
- (MOSSize)sparseTileSizeWithTextureType:(NSUInteger)type pixelFormat:(NSUInteger)format sampleCount:(NSUInteger)samples sparsePageSize:(NSInteger)pageSize {
    (void)pageSize;   // one page size on this stack; the device reports no sparse tiles anyway
    if (answers(self, @selector(sparseTileSizeWithTextureType:pixelFormat:sampleCount:)))
        return [(id<MOSLegacy>)self sparseTileSizeWithTextureType:type pixelFormat:format sampleCount:samples];
    return (MOSSize){0, 0, 0};
}
- (MOSSize)tileSizeWithSparsePageSize:(NSInteger)pageSize textureType:(NSUInteger)type pixelFormat:(NSUInteger)format sampleCount:(NSUInteger)samples {
    return [self sparseTileSizeWithTextureType:type pixelFormat:format sampleCount:samples sparsePageSize:pageSize];
}
@end

// ---- _MTLCommandQueue: residency sets never exist on this stack, so there is nothing to add or remove ------------
@interface MOSCommandQueue : NSObject @end
@implementation MOSCommandQueue
- (void)addResidencySet:(id)set { (void)set; }
- (void)addResidencySets:(const id *)sets count:(NSUInteger)count { (void)sets; (void)count; }
- (void)removeResidencySet:(id)set { (void)set; }
- (void)removeResidencySets:(const id *)sets count:(NSUInteger)count { (void)sets; (void)count; }
@end

// ---- _MTLCommandBuffer ------------------------------------------------------------------------------------------
@interface MOSCommandBuffer : NSObject @end
@implementation MOSCommandBuffer
- (void)useResidencySet:(id)set { (void)set; }
- (void)useResidencySets:(const id *)sets count:(NSUInteger)count { (void)sets; (void)count; }
// the agent mask selects GPU agents of a multi-agent device; this device has one, so the plain signal is the same
- (void)encodeSignalEvent:(id)event value:(uint64_t)value agentMask:(uint64_t)mask {
    (void)mask;
    if (answers(self, @selector(encodeSignalEvent:value:))) [(id<MOSLegacy>)self encodeSignalEvent:event value:value];
}
@end

// ---- _MTLCommandEncoder (render and compute encoders): dynamic attribute stride ----------------------------------
// The device answers supportsDynamicAttributeStride NO. A caller honouring that passes MTLAttributeStrideStatic and
// these are then exactly the static calls; another stride is a contract violation the static call cannot honour.
@interface MOSCommandEncoder : NSObject @end
@implementation MOSCommandEncoder
- (void)useResidencySet:(id)set { (void)set; }
- (void)useResidencySets:(const id *)sets count:(NSUInteger)count { (void)sets; (void)count; }
- (void)setVertexBuffer:(id)buffer offset:(NSUInteger)offset attributeStride:(NSUInteger)stride atIndex:(NSUInteger)index {
    (void)stride;
    if (answers(self, @selector(setVertexBuffer:offset:atIndex:))) [(id<MOSLegacy>)self setVertexBuffer:buffer offset:offset atIndex:index];
}
- (void)setVertexBuffers:(const id *)buffers offsets:(const NSUInteger *)offsets attributeStrides:(const NSUInteger *)strides withRange:(NSRange)range {
    (void)strides;
    if (answers(self, @selector(setVertexBuffers:offsets:withRange:))) [(id<MOSLegacy>)self setVertexBuffers:buffers offsets:offsets withRange:range];
}
- (void)setVertexBufferOffset:(NSUInteger)offset attributeStride:(NSUInteger)stride atIndex:(NSUInteger)index {
    (void)stride;
    if (answers(self, @selector(setVertexBufferOffset:atIndex:))) [(id<MOSLegacy>)self setVertexBufferOffset:offset atIndex:index];
}
- (void)setVertexBytes:(const void *)bytes length:(NSUInteger)length attributeStride:(NSUInteger)stride atIndex:(NSUInteger)index {
    (void)stride;
    if (answers(self, @selector(setVertexBytes:length:atIndex:))) [(id<MOSLegacy>)self setVertexBytes:bytes length:length atIndex:index];
}
- (void)setBuffer:(id)buffer offset:(NSUInteger)offset attributeStride:(NSUInteger)stride atIndex:(NSUInteger)index {
    (void)stride;
    if (answers(self, @selector(setBuffer:offset:atIndex:))) [(id<MOSLegacy>)self setBuffer:buffer offset:offset atIndex:index];
}
- (void)setBuffers:(const id *)buffers offsets:(const NSUInteger *)offsets attributeStrides:(const NSUInteger *)strides withRange:(NSRange)range {
    (void)strides;
    if (answers(self, @selector(setBuffers:offsets:withRange:))) [(id<MOSLegacy>)self setBuffers:buffers offsets:offsets withRange:range];
}
- (void)setBufferOffset:(NSUInteger)offset attributeStride:(NSUInteger)stride atIndex:(NSUInteger)index {
    (void)stride;
    if (answers(self, @selector(setBufferOffset:atIndex:))) [(id<MOSLegacy>)self setBufferOffset:offset atIndex:index];
}
- (void)setBytes:(const void *)bytes length:(NSUInteger)length attributeStride:(NSUInteger)stride atIndex:(NSUInteger)index {
    (void)stride;
    if (answers(self, @selector(setBytes:length:atIndex:))) [(id<MOSLegacy>)self setBytes:bytes length:length atIndex:index];
}
@end

// ---- indirect command buffers: the same stride variants on their command objects -------------------------------
@interface MOSIndirectRenderCommand : NSObject @end
@implementation MOSIndirectRenderCommand
- (void)setVertexBuffer:(id)buffer offset:(NSUInteger)offset attributeStride:(NSUInteger)stride atIndex:(NSUInteger)index {
    (void)stride;
    if (answers(self, @selector(setVertexBuffer:offset:atIndex:))) [(id<MOSLegacy>)self setVertexBuffer:buffer offset:offset atIndex:index];
}
@end
@interface MOSIndirectComputeCommand : NSObject @end
@implementation MOSIndirectComputeCommand
- (void)setKernelBuffer:(id)buffer offset:(NSUInteger)offset attributeStride:(NSUInteger)stride atIndex:(NSUInteger)index {
    (void)stride;
    if (answers(self, @selector(setKernelBuffer:offset:atIndex:))) [(id<MOSLegacy>)self setKernelBuffer:buffer offset:offset atIndex:index];
}
@end

// ---- textures (MTLIOAccelTexture is MetalOld's texture base): lossless compression views ------------------------
// Textures are never compressed on this stack, so the plain view of the same level and slice is the compressed view.
@interface MOSTexture : NSObject @end
@implementation MOSTexture
- (id)newCompressedTextureViewWithPixelFormat:(NSUInteger)format textureType:(NSUInteger)type level:(NSUInteger)level slice:(NSUInteger)slice {
    if (!answers(self, @selector(newTextureViewWithPixelFormat:textureType:levels:slices:))) return nil;
    return [(id<MOSLegacy>)self newTextureViewWithPixelFormat:format textureType:type levels:NSMakeRange(level, 1) slices:NSMakeRange(slice, 1)];
}
@end

// ---- pipeline states and descriptors: shader validation (macOS 15) does not exist on this stack ------------------
// Tahoe's _MTLRenderPipelineState answers MTLShaderValidationDisabled; its descriptors store the request.
@interface MOSPipelineState : NSObject @end
@implementation MOSPipelineState
- (NSInteger)shaderValidation { return MOSShaderValidationDisabled; }
@end
@interface MOSPipelineDescriptor : NSObject @end
@implementation MOSPipelineDescriptor
- (NSInteger)shaderValidation { id v = objc_getAssociatedObject(self, MOSShaderValidationKey); return v ? [v integerValue] : MOSShaderValidationDefault; }
- (void)setShaderValidation:(NSInteger)value { objc_setAssociatedObject(self, MOSShaderValidationKey, @(value), OBJC_ASSOCIATION_RETAIN_NONATOMIC); }
// macOS 27 (26A428): MPSCore's MPSKey_Compile sets forwardProgressUsage on every compute pipeline descriptor, so
// WindowServer's desktop effects pass aborts without it. A scheduling hint the legacy driver predates:
// kept and returned like shaderValidation, default 0.
- (NSUInteger)forwardProgressUsage { id v = objc_getAssociatedObject(self, MOSForwardProgressUsageKey); return v ? [v unsignedIntegerValue] : 0; }
- (void)setForwardProgressUsage:(NSUInteger)value { objc_setAssociatedObject(self, MOSForwardProgressUsageKey, @(value), OBJC_ASSOCIATION_RETAIN_NONATOMIC); }
@end

// ---- depth stencil states: no bindless resource IDs on this stack ------------------------------------------------
@interface MOSDepthStencilState : NSObject @end
@implementation MOSDepthStencilState
- (MOSResourceID)gpuResourceID { return (MOSResourceID){0}; }
@end

// ---- shared events: Tahoe's _MTLSharedEvent returns itself as its IOSurface-shareable event ----------------------
@interface MOSSharedEvent : NSObject @end
@implementation MOSSharedEvent
- (id)IOSurfaceSharedEvent { return self; }
@end

// ---- heaps: MTLAllocation (macOS 15) allocatedSize; Tahoe's _MTLHeap answers with currentAllocatedSize ------------
@interface MOSHeap : NSObject @end
@implementation MOSHeap
- (NSUInteger)allocatedSize {
    if (answers(self, @selector(currentAllocatedSize))) return [(id<MOSLegacy>)self currentAllocatedSize];
    return answers(self, @selector(size)) ? [(id<MOSLegacy>)self size] : 0;
}
@end

// ---- MTLArchitecture: NSCopying (macOS 14); the object is an immutable name ---------------------------------------
@interface MOSArchitecture : NSObject @end
@implementation MOSArchitecture
- (id)copyWithZone:(NSZone *)zone { (void)zone; return self; }
@end

// ---- attach: every donor method the target class does not already answer -----------------------------------------
static const struct { const char *target; const char *donor; } targets[] = {
    {"_MTLDevice", "MOSDevice"},
    {"_MTLCommandQueue", "MOSCommandQueue"},
    {"_MTLCommandBuffer", "MOSCommandBuffer"},
    {"_MTLCommandEncoder", "MOSCommandEncoder"},
    {"_MTLIndirectRenderCommand", "MOSIndirectRenderCommand"},
    {"_MTLIndirectComputeCommand", "MOSIndirectComputeCommand"},
    {"MTLIOAccelTexture", "MOSTexture"},
    {"_MTLRenderPipelineState", "MOSPipelineState"},
    {"_MTLComputePipelineState", "MOSPipelineState"},
    {"MTLRenderPipelineDescriptor", "MOSPipelineDescriptor"},
    {"MTLComputePipelineDescriptor", "MOSPipelineDescriptor"},
    {"MTLTileRenderPipelineDescriptor", "MOSPipelineDescriptor"},
    {"MTLMeshRenderPipelineDescriptor", "MOSPipelineDescriptor"},
    {"_MTLDepthStencilState", "MOSDepthStencilState"},
    {"_MTLSharedEvent", "MOSSharedEvent"},
    {"_MTLHeap", "MOSHeap"},
    {"MTLArchitecture", "MOSArchitecture"},
};

static unsigned attach(Class target, Class donor) {
    unsigned n = 0, added = 0;
    Method *methods = class_copyMethodList(donor, &n);
    for (unsigned i = 0; i < n; i++) {
        SEL sel = method_getName(methods[i]);
        if (class_getInstanceMethod(target, sel)) continue;     // answered by the class or a superclass: keep it
        if (class_addMethod(target, sel, method_getImplementation(methods[i]), method_getTypeEncoding(methods[i]))) added++;
    }
    free(methods);
    return added;
}

// Attach to the classes named PREFIX + target. The constructor uses the empty prefix; the host test (tests/fake_stack.m)
// names its stand-ins with a prefix because the host's own Metal is in the process under the real names.
unsigned metalold_selectors_attach_prefixed(const char *prefix) {
    unsigned total = 0;
    for (size_t i = 0; i < sizeof targets / sizeof targets[0]; i++) {
        char name[128];
        snprintf(name, sizeof name, "%s%s", prefix, targets[i].target);
        Class target = objc_getClass(name), donor = objc_getClass(targets[i].donor);
        if (!target || !donor) continue;
        unsigned added = attach(target, donor);
        total += added;
        os_log_debug(mos_log(), "%{public}s: %u selectors added", name, added);
    }
    return total;
}

static BOOL attached_at_load;
BOOL metalold_selectors_attached_at_load(void) { return attached_at_load; }

__attribute__((constructor)) static void metalold_selectors_init(void) {
    const char *off = getenv("METALOLD_SELECTORS");
    if (off && strcmp(off, "0") == 0) return;
    unsigned total = metalold_selectors_attach_prefixed("");
    attached_at_load = YES;
    os_log_info(mos_log(), "%u Metal selectors added to the legacy stack", total);
}
