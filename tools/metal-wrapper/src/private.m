// hao's Metal wrapper: the private names 27.2.1's x86_64 code imports from Metal that the SDK headers don't declare
// and MetalOld lacks. make.py builds this file into every wrapper (with the generated stubs and runtime.m).
//
// Each implementation is the smallest that is correct for its importers. Signatures and behaviour come from 27.2.1's
// x86_64 Metal (dyld_shared_cache_x86_64, read-only) and the importers' call sites; README.md has the table of who
// calls what. Rules:
// - Pure functions (string tables, bit counts, pixel-format tests, range allocator) do what 27.2.1's do.
// - Validation helpers for the Metal debug layer push the same messages 27.2.1's push, or none where 27.2.1's read
//   private Metal 27 structures MetalOld does not have.
// - Private classes have 27.2.1's superclass and the ivars importers reach by offset, with 27.2.1's types, plus their
//   initializers and accessors. Work methods are left out: their subclasses (IOGPU's, which an Intel GPU never
//   instantiates) override them, and anything else that sends one fails loudly instead of being told "done".
// Built without ARC.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <dispatch/dispatch.h>
#import <os/lock.h>
#import <stdbool.h>
#import <stdlib.h>
#import <string.h>
#import <unistd.h>

#pragma clang diagnostic ignored "-Wobjc-missing-super-calls"

extern bool os_variant_has_internal_diagnostics(const char *subsystem);            // libsystem_darwin
extern void _MTLMessageContextPush_(void *context, int level, NSString *format, ...);  // MetalOld exports it

// --- functions ----------------------------------------------------------------------------------------------------

// MetalTools' debug layer names tensor data types in its copy-validation messages. Same table as 27.2.1.
NSString *_MTLTensorDataTypeToString(MTLTensorDataType type) {
    switch ((long)type) {
    case 3: return @"MTLTensorDataTypeFloat32";
    case 16: return @"MTLTensorDataTypeFloat16";
    case 121: return @"MTLTensorDataTypeBFloat16";
    case 29: return @"MTLTensorDataTypeInt32";
    case 33: return @"MTLTensorDataTypeUInt32";
    case 37: return @"MTLTensorDataTypeInt16";
    case 41: return @"MTLTensorDataTypeUInt16";
    case 45: return @"MTLTensorDataTypeInt8";
    case 49: return @"MTLTensorDataTypeUInt8";
    case 141: return @"MTLTensorDataTypeMetalFloat8E5M2";
    case 142: return @"MTLTensorDataTypeMetalFloat8E4M3";
    case 143: return @"MTLTensorDataTypeInt4";
    case 144: return @"MTLTensorDataTypeUInt4";
    case 145: return @"MTLTensorDataTypeMetalFloat8UE8M0";
    case 148: return @"MTLTensorDataTypeMetalFloat4E2M1";
    case 149: return @"MTLTensorDataTypeUInt2";
    case 150: return @"MTLTensorDataTypeInt2";
    default: return @"Unknown";
    }
}

// Packed (sub-byte or block-scaled) formats are 141...154, as in 27.2.1.
BOOL _MTLTensorIsPackedFormatType(MTLTensorDataType type) {
    return (unsigned long)type - 141 < 14;
}

NSString *_MTLTensorPlaneTypeToString(NSUInteger plane) {
    return plane == 0 ? @"MTLTensorPlaneTypeData" : plane == 1 ? @"MTLTensorPlaneTypeScales" : @"Unknown";
}

// Bits per element. 27.2.1 also reports unsupported types through MTLReportFailure before returning 0; this returns
// 0 without the report (the debug layer that calls it then rejects the tensor on its own size checks).
uint64_t tensorDataTypeBitCount(MTLTensorDataType type) {
    switch ((long)type) {
    case 3: case 29: case 33: return 32;
    case 16: case 37: case 41: case 121: return 16;
    case 45: case 49: case 141: case 142: case 145: case 153: case 154: return 8;
    case 146: case 147: return 6;
    case 143: case 144: case 148: return 4;
    case 151: case 152: return 3;
    case 149: case 150: return 2;
    default: return 0;
    }
}

// MetalTools' debug layer and ParavirtualizedGraphicsGPUTask ask whether a format is an RGB colour format. Same set as
// 27.2.1: pixel formats 45...48, 95...100 and 120...122.
bool isRGBPixelFormat(MTLPixelFormat format) {
    unsigned long f = format;
    return (f - 95 <= 27 && (0xE00003FUL >> (f - 95)) & 1) || f - 45 < 4;
}

NSString *MTLSparsePageSizeString(MTLSparsePageSize size) {
    switch ((long)size) {
    case 0: return @"0";
    case 101: return @"MTLSparsePageSize16";
    case 102: return @"MTLSparsePageSize64";
    case 103: return @"MTLSparsePageSize256";
    default: return @"Unknown";
    }
}

// As 27.2.1: Apple-internal diagnostics builds only.
BOOL _MTLIsInternalBuild(void) {
    return os_variant_has_internal_diagnostics("com.apple.Metal");
}

// Product of the extents (1 for rank 0). 27.2.1 reads the extents through a private accessor; the public ones give
// the same numbers for any MTLTensorExtents.
NSUInteger _MTLTensorElementCount(MTLTensorExtents *extents) {
    NSUInteger n = 1, rank = [extents rank];
    for (NSUInteger i = 0; i < rank; i++)
        n *= (NSUInteger)[extents extentAtDimensionIndex:i];
    return n;
}

BOOL _MTLTensorExtentsAreEqual(MTLTensorExtents *a, MTLTensorExtents *b) {
    NSUInteger rank = [a rank];
    if (rank != [b rank]) return NO;
    for (NSUInteger i = 0; i < rank; i++)
        if ([a extentAtDimensionIndex:i] != [b extentAtDimensionIndex:i]) return NO;
    return YES;
}

// The debug layer's check that a dispatch matches the pipeline's required threads per threadgroup. Same messages,
// levels and arguments as 27.2.1, pushed through MetalOld's message context.
void validateDispatchThreadsPerThreadgroupWithRTPTG(void *context, MTLSize threads, MTLSize required) {
    if (!(required.width | required.height | required.depth)) return;
    if (threads.width != required.width)
        _MTLMessageContextPush_(context, 8, @"threadsPerThreadgroup.width(%lu) should be %lu "
                                "(requiredThreadsPerThreadgroup.width)", threads.width, required.width);
    if (threads.height != required.height)
        _MTLMessageContextPush_(context, 8, @"threadsPerThreadgroup.height(%lu) should be %lu "
                                "(requiredThreadsPerThreadgroup.height)", threads.height, required.height);
    if (threads.depth != required.depth)
        _MTLMessageContextPush_(context, 8, @"threadsPerThreadgroup.depth(%lu) should be %lu "
                                "(requiredThreadsPerThreadgroup.depth)", threads.depth, required.depth);
}

// The debug layer's tile-size check for render passes from apps built against SDKs before fall 2025. 27.2.1's reads
// Metal 27's private render-pass structure, whose layout MetalOld's descriptors don't have, so this pushes no
// message and answers "valid"; the caller ignores the answer.
BOOL _MTLValidateRenderPassDescriptorTileProperties(void *context, NSUInteger bits, NSUInteger samples,
                                                    const void *descriptor, id device) {
    return YES;
}

// Shader validation keys MTL4 pipelines by this SHA-256 (returned in memory, as 27.2.1's MTLUINT256_t) to match
// pipelines named in its environment settings. MetalOld has no MTL4 pipelines; an all-zero hash matches none.
typedef struct { uint64_t word[4]; } HaoUInt256;
HaoUInt256 hao_generatePipelineHash(id descriptor) __asm__("__Z34generatePipelineHashWithDescriptorP22MTL4PipelineDescriptor");
HaoUInt256 hao_generatePipelineHash(id descriptor) {
    HaoUInt256 zero = {{0, 0, 0, 0}};
    return zero;
}

// Reflection for an MTL4 function from its binary; 27.2.1 returns nil when there is no data. Only Apple-GPU drivers
// call it.
id newFunctionReflectionWithFunctionDescriptor(id descriptor, id options, id data) {
    return nil;
}

// MetalOld has MTLRangeAllocator (same 40-byte layout as 27.2.1) but not these two. MetalOld's Destroy frees
// `ranges` with free(), as 27.2.1's does.
typedef struct { uint64_t start, end; } HaoRange;
typedef struct { HaoRange *ranges; uint32_t count; uint64_t capacity, increment, alignmentMask; } HaoRangeAllocator;

void MTLRangeAllocatorInitWithStartRange(HaoRangeAllocator *a, uint64_t start, uint64_t length, uint64_t alignment,
                                         uint64_t increment) {
    a->capacity = 0;
    a->increment = increment ? increment : 1;
    a->count = 0;
    a->ranges = NULL;
    a->alignmentMask = alignment ? alignment - 1 : 0;
    HaoRange *r = malloc(a->increment * sizeof *r);
    if (!r) return;
    a->ranges = r;
    a->capacity = a->increment;
    a->count = 1;
    r[0].start = start;
    r[0].end = start + length;
}

uint64_t MTLRangeAllocatorGetFreeSize(const HaoRangeAllocator *a) {
    uint64_t n = 0;
    for (uint32_t i = 0; i < a->count; i++) n += a->ranges[i].end - a->ranges[i].start + 1;
    return n;
}

// --- data ---------------------------------------------------------------------------------------------------------

NSString *const kMetalTextureWriteAccessPattern = @"kMetalTextureWriteAccessPattern";       // 27.2.1's values
NSString *const MTLPipelinePerformanceKeyThreadInvariantSpillsCount = @"Thread invariant spilled bytes";

// --- classes ------------------------------------------------------------------------------------------------------

// MetalOld's base class for labelled objects (MetalOld exports it). Its ivars are declared as MetalOld (and 27.2.1)
// lay them out, so the offsets compiled into our subclasses are already right; the runtime would slide them anyway
// when a class is realized, and an importer only reads an offset once it has an instance.
@interface _MTLObjectWithLabel : NSObject {
    NSString *_label;
    os_unfair_lock _labelLock;
}
@property (copy) NSString *label;
@end

// Function stitching: a kernel attribute, and a node that returns early when its condition holds. MetalOld has the
// stitching graph and MTLFunctionStitchingFunctionNode; the early-return node is that node with one argument, the
// condition, and an empty name, as in 27.2.1.
@interface MTLFunctionStitchingAttributeKernel : NSObject <MTLFunctionStitchingAttribute>
@end
@implementation MTLFunctionStitchingAttributeKernel
- (NSString *)formattedDescription:(NSUInteger)indent {
    return [NSString stringWithFormat:@"%@ kernel", [super description]];
}
@end

@interface MTLFunctionStitchingEarlyReturnNode : MTLFunctionStitchingFunctionNode
@property (nonatomic, retain) id<MTLFunctionStitchingNode> condition;
@end
@implementation MTLFunctionStitchingEarlyReturnNode
- (instancetype)initWithCondition:(id<MTLFunctionStitchingNode>)condition
               controlDependencies:(NSArray<MTLFunctionStitchingFunctionNode *> *)dependencies {
    return [super initWithName:@"" arguments:[NSArray arrayWithObjects:&condition count:1] controlDependencies:dependencies];
}
- (id<MTLFunctionStitchingNode>)condition { return [self arguments][0]; }
- (void)setCondition:(id<MTLFunctionStitchingNode>)condition {
    [self setArguments:[NSArray arrayWithObjects:&condition count:1]];
}
- (id)copyWithZone:(NSZone *)zone {
    return [[[self class] allocWithZone:zone] initWithName:[self name] arguments:[self arguments]
                                        controlDependencies:[self controlDependencies]];
}
@end

// Shader-log message decoder (format string plus the GPU's packed arguments). Its one importer, MetalTools' shader
// validation report reader, falls back to the undecoded format string when -decode returns nil, which this does.
@interface MTLMessageDecoder : NSObject {
    void *_decoder;
}
@end
@implementation MTLMessageDecoder
- (instancetype)initWithFormatString:(const char *)format varArgsData:(const void *)data varArgsSize:(unsigned)size
                              logger:(id)logger customContext:(id)context {
    return [super init];
}
- (instancetype)initWithFormatString:(const char *)format varArgsData:(const void *)data varArgsSize:(unsigned)size
                              logger:(id)logger {
    return [self initWithFormatString:format varArgsData:data varArgsSize:size logger:logger customContext:nil];
}
- (NSString *)decode { return nil; }
@end

// A list of GPU address ranges (ParavirtualizedGraphics). Owns a copy of the ranges, as 27.2.1's does.
typedef struct { uint64_t address, length; } HaoAddressRange;
@interface MTLResourceAddressRangeArray : NSObject <NSCopying> {
    NSUInteger _count;
    HaoAddressRange *_ranges;
}
@end
@implementation MTLResourceAddressRangeArray
- (instancetype)initWithCount:(NSUInteger)count {
    if ((self = [super init])) {
        _count = count;
        _ranges = count ? calloc(count, sizeof *_ranges) : NULL;
        if (count && !_ranges) { [self release]; return nil; }
    }
    return self;
}
- (instancetype)initWithRanges:(const HaoAddressRange *)ranges count:(NSUInteger)count {
    if ((self = [self initWithCount:count]) && count) memcpy(_ranges, ranges, count * sizeof *_ranges);
    return self;
}
- (void)dealloc { free(_ranges); [super dealloc]; }
- (NSUInteger)count { return _count; }
- (HaoAddressRange *)ranges { return _ranges; }
- (id)copyWithZone:(NSZone *)zone {
    return [[[self class] allocWithZone:zone] initWithRanges:_ranges count:_count];
}
- (BOOL)isEqual:(id)other {
    if (other == self) return YES;
    if (![other isKindOfClass:[MTLResourceAddressRangeArray class]] || [other count] != _count) return NO;
    return !_count || !memcmp(_ranges, [other ranges], _count * sizeof *_ranges);
}
- (NSUInteger)hash { return _count; }
@end

// The base of Metal 27's allocations (heaps, pipelines). IOGPU's residency sets read _allocationType (a byte).
@interface _MTLAllocation : _MTLObjectWithLabel {
    char _allocationType;
}
@end
@implementation _MTLAllocation
- (instancetype)initWithAllocationType:(char)type {
    if ((self = [super init])) _allocationType = type;
    return self;
}
@end

// Shader-log state. MetalTools' shader validation reads _logBuffer and _logBufferResidencySet. MetalOld has no
// log-state objects, so nothing on Intel makes one; the layout is 27.2.1's.
@interface _MTLLogState : NSObject {
    NSInteger _logLevel;
    NSInteger _logBufferSize;
    id _logBuffer;
    id _logBufferResidencySet;
    id _device;
    os_unfair_lock _logHandlerLock;
    os_unfair_lock _logBufferLock;
    BOOL _usedForShaderValidation;
    NSMutableArray *_handlers;
    NSMutableDictionary *defaultLoggerCache;
    NSString *metalSubsystem;
    NSString *shaderLoggingCategory;
    NSString *metalLoggingKey;
    unsigned droppedMessages;
}
@end
@implementation _MTLLogState
- (instancetype)initWithDevice:(id)device descriptor:(MTLLogStateDescriptor *)descriptor error:(NSError **)error {
    if ((self = [super init])) {
        _device = [device retain];
        _logLevel = [descriptor level];
        _logBufferSize = [descriptor bufferSize];
        _handlers = [NSMutableArray new];
    }
    return self;
}
- (void)addLogHandler:(void (^)(NSString *, NSString *, MTLLogLevel, NSString *))handler {
    os_unfair_lock_lock(&_logHandlerLock);
    [_handlers addObject:[[handler copy] autorelease]];
    os_unfair_lock_unlock(&_logHandlerLock);
}
- (void)dealloc {
    [_handlers release];
    [_logBuffer release];
    [_logBufferResidencySet release];
    [_device release];
    [super dealloc];
}
@end

// Resource view pools (Apple-GPU drivers subclass the texture view pool).
@interface _MTLResourceViewPool : _MTLObjectWithLabel {
    MTLResourceID _baseResourceID;
    NSUInteger _resourceViewCount;
    id _device;
}
@end
@implementation _MTLResourceViewPool
- (instancetype)initWithDescriptor:(MTLResourceViewPoolDescriptor *)descriptor device:(id)device {
    if ((self = [super init])) {
        _device = [device retain];
        _resourceViewCount = [descriptor resourceViewCount];
        [self setLabel:[descriptor label]];
    }
    return self;
}
- (void)dealloc { [_device release]; [super dealloc]; }
- (id)device { return _device; }
- (MTLResourceID)baseResourceID { return _baseResourceID; }
- (NSUInteger)resourceViewCount { return _resourceViewCount; }
@end

@interface _MTLTextureViewPool : _MTLResourceViewPool
@end
@implementation _MTLTextureViewPool
@end

// Indirect mesh-draw argument records (Apple-GPU drivers).
@interface _MTLIndirectDrawMeshThreadgroupsArguments : NSObject
@property (nonatomic) MTLSize threadgroupsPerGrid, threadsPerObjectThreadgroup, threadsPerMeshThreadgroup;
@end
@implementation _MTLIndirectDrawMeshThreadgroupsArguments
@end

@interface _MTLIndirectDrawMeshThreadsArguments : NSObject
@property (nonatomic) MTLSize threadsPerGrid, threadsPerObjectThreadgroup, threadsPerMeshThreadgroup;
@end
@implementation _MTLIndirectDrawMeshThreadsArguments
@end

// --- Metal 4 objects. IOGPU subclasses the allocator, command buffer, queue and the three encoders and reads the
// ivars below by offset; Apple-GPU drivers subclass the rest. An Intel GPU never creates any of them (MetalOld's
// devices make no Metal 4 objects), but the classes, superclasses and ivars must exist for those images to load.

@interface _MTL4CommandAllocator : _MTLObjectWithLabel {
    id _device;
    uint64_t _globalTraceObjectID;
    id _privateDataTable;
    struct { void *begin, *end, *cap; } _resetHandlers;        // std::vector<void (^)()> in 27.2.1
}
@end
@implementation _MTL4CommandAllocator
- (instancetype)initWithDevice:(id)device {
    if ((self = [super init])) _device = [device retain];
    return self;
}
- (instancetype)initWithDevice:(id)device descriptor:(MTL4CommandAllocatorDescriptor *)descriptor {
    if ((self = [self initWithDevice:device])) [self setLabel:[descriptor label]];
    return self;
}
- (void)dealloc { [_privateDataTable release]; [_device release]; [super dealloc]; }
- (id)device { return _device; }
- (uint64_t)globalTraceObjectID { return _globalTraceObjectID; }
- (uint64_t)allocatedSize { return 0; }
@end

@interface _MTL4CommandBuffer : _MTLObjectWithLabel {
    id _device;
    uint64_t _globalTraceObjectID;
    uint64_t _labelTraceID;
    id _commandAllocator;
    NSMutableArray *_mlCommandEncoders;
    id _logState;
    NSMutableArray *_feedbackHandlers;
    id _privateData;
    uint64_t _privateDataOffset;
    uint64_t _currentGeneration;
}
@end
@implementation _MTL4CommandBuffer
- (instancetype)initWithDevice:(id)device {
    if ((self = [super init])) _device = [device retain];
    return self;
}
- (void)dealloc {
    [_feedbackHandlers release];
    [_mlCommandEncoders release];
    [_logState release];
    [_privateData release];
    [_commandAllocator release];
    [_device release];
    [super dealloc];
}
- (id)device { return _device; }
- (uint64_t)globalTraceObjectID { return _globalTraceObjectID; }
- (uint64_t)currentGeneration { return _currentGeneration; }
- (id)commandAllocator { return _commandAllocator; }
- (id)logState { return _logState; }
- (void)setLogState:(id)state { [state retain]; [_logState release]; _logState = state; }
- (id)privateData { return _privateData; }
- (void)setPrivateData:(id)data { [data retain]; [_privateData release]; _privateData = data; }
- (uint64_t)privateDataOffset { return _privateDataOffset; }
- (void)setPrivateDataOffset:(uint64_t)offset { _privateDataOffset = offset; }
- (NSArray *)feedbackHandlers { return _feedbackHandlers; }
- (NSArray *)mlCommandEncoders { return _mlCommandEncoders; }
@end

@interface _MTL4CommandEncoder : _MTLObjectWithLabel {
    id _device;
    id _commandAllocator;
    uint64_t _globalTraceObjectID;
    uint64_t _labelTraceID;
    id _commandBuffer;
}
@end
@implementation _MTL4CommandEncoder
- (instancetype)initWithCommandAllocator:(id)allocator {
    if ((self = [super init])) {
        _commandAllocator = [allocator retain];
        _device = [allocator device];          // not retained, as 27.2.1
    }
    return self;
}
- (void)dealloc {
    _device = nil;
    [_commandBuffer release];
    [_commandAllocator release];
    [super dealloc];
}
- (id)device { return _device; }
- (id)commandAllocator { return _commandAllocator; }
- (id)commandBuffer { return _commandBuffer; }
- (void)setCommandBuffer:(id)buffer { [buffer retain]; [_commandBuffer release]; _commandBuffer = buffer; }
- (uint64_t)globalTraceObjectID { return _globalTraceObjectID; }
@end

@interface _MTL4ComputeCommandEncoder : _MTL4CommandEncoder
@end
@implementation _MTL4ComputeCommandEncoder
@end

@interface _MTL4RenderCommandEncoder : _MTL4CommandEncoder {
    NSUInteger _tileWidth;
    NSUInteger _tileHeight;
}
@end
@implementation _MTL4RenderCommandEncoder
- (NSUInteger)tileWidth { return _tileWidth; }
- (void)setTileWidth:(NSUInteger)width { _tileWidth = width; }
- (NSUInteger)tileHeight { return _tileHeight; }
- (void)setTileHeight:(NSUInteger)height { _tileHeight = height; }
@end

@interface _MTL4MachineLearningCommandEncoder : _MTL4CommandEncoder {
    id _currentPipelineState;
    id _currentArgumentTable;
    void *_dispatchList;
    id _event;
    id _anePrePowerUpEvent;
    uint64_t _agentMask;
}
@end
@implementation _MTL4MachineLearningCommandEncoder
- (uint64_t)agentMask { return _agentMask; }
@end

@interface _MTL4CommandQueue : _MTLObjectWithLabel {
    id _device;
    uint64_t _globalTraceObjectID;
    uint64_t _labelTraceID;
    int _backgroundTrackingPID;
    dispatch_queue_t _submissionQueue;
    id _lastCommittedCommandBuffer;
    uint64_t _lastCommittedCommandBufferGeneration;
    os_unfair_lock _mlCommandQueueLock;
    id _mlCommandQueue;
}
@end
@implementation _MTL4CommandQueue
- (instancetype)initWithDevice:(id)device {
    if ((self = [super init])) {
        _backgroundTrackingPID = getpid();
        _device = [device retain];
        _mlCommandQueueLock = OS_UNFAIR_LOCK_INIT;
        _submissionQueue = dispatch_queue_create("com.Metal4.SubmissionQueue",
                                                 dispatch_queue_attr_make_with_qos_class(NULL, QOS_CLASS_USER_INTERACTIVE, 0));
    }
    return self;
}
- (instancetype)initWithDescriptor:(MTL4CommandQueueDescriptor *)descriptor device:(id)device {
    if ((self = [self initWithDevice:device]) && [descriptor label]) [self setLabel:[descriptor label]];
    return self;
}
- (void)dealloc {
    [_mlCommandQueue release];
    [_lastCommittedCommandBuffer release];
    if (_submissionQueue) dispatch_release(_submissionQueue);
    [_device release];
    [super dealloc];
}
- (id)device { return _device; }
- (uint64_t)globalTraceObjectID { return _globalTraceObjectID; }
- (int)backgroundTrackingPID { return _backgroundTrackingPID; }
- (id)lastCommittedCommandBuffer { return _lastCommittedCommandBuffer; }
- (uint64_t)lastCommittedCommandBufferGeneration { return _lastCommittedCommandBufferGeneration; }
- (id)mlCommandQueue { return _mlCommandQueue; }
@end

@interface _MTL4Archive : _MTLObjectWithLabel {
    id _device;
    void *_archiveReader;                                      // std::unique_ptr<MTL4ArchiveImpl> in 27.2.1
}
@end
@implementation _MTL4Archive
- (instancetype)initWithDevice:(id)device {
    if ((self = [super init])) _device = [device retain];
    return self;
}
- (void)dealloc { [_device release]; [super dealloc]; }
@end

@interface _MTL4ArgumentTable : _MTLObjectWithLabel {
    id _device;
}
@end
@implementation _MTL4ArgumentTable
- (instancetype)initWithDescriptor:(MTL4ArgumentTableDescriptor *)descriptor device:(id)device {
    if ((self = [super init])) {
        _device = [device retain];
        [self setLabel:[descriptor label]];
    }
    return self;
}
- (void)dealloc { [_device release]; [super dealloc]; }
- (id)device { return _device; }
@end

@interface _MTL4BinaryFunction : _MTLObjectWithLabel <NSCopying> {
    NSArray *_relocations;
    NSString *_name;
    id debugInstrumentationData;
    id _reflection;
    NSUInteger _functionType;
}
@end
@implementation _MTL4BinaryFunction
- (instancetype)initWithFunctionDescriptor:(MTL4FunctionDescriptor *)descriptor {
    if ((self = [super init]) && [descriptor respondsToSelector:@selector(name)])
        _name = [[(id)descriptor name] copy];
    return self;
}
- (void)dealloc {
    [_relocations release];
    [_name release];
    [debugInstrumentationData release];
    [_reflection release];
    [super dealloc];
}
- (id)copyWithZone:(NSZone *)zone { return [self retain]; }
- (NSString *)name { return _name; }
- (id)reflection { return _reflection; }
- (NSUInteger)functionType { return _functionType; }
- (id)debugInstrumentationData { return debugInstrumentationData; }
- (NSArray *)relocations { return _relocations; }
- (void)setRelocations:(NSArray *)relocations {
    NSArray *old = _relocations;
    _relocations = [relocations copy];
    [old release];
}
@end

@interface _MTL4Compiler : _MTLObjectWithLabel {
    dispatch_queue_t _mlcompiler_queue;
    dispatch_queue_t _debugQueue;
    BOOL _shouldMaximizeConcurrentCompilation;
    id _destinationBinaryArchive;
    id _pipelineDataSetSerializer;
    id _device;
}
@end
@implementation _MTL4Compiler
- (instancetype)initWithDevice:(id)device {
    if ((self = [super init])) _device = [device retain];
    return self;
}
- (instancetype)initWithDevice:(id)device descriptor:(MTL4CompilerDescriptor *)descriptor {
    if ((self = [self initWithDevice:device])) [self setLabel:[descriptor label]];
    return self;
}
- (void)dealloc {
    [_destinationBinaryArchive release];
    [_pipelineDataSetSerializer release];
    [_device release];
    [super dealloc];
}
- (id)device { return _device; }
- (id)destinationBinaryArchive { return _destinationBinaryArchive; }
- (id)pipelineDataSetSerializer { return _pipelineDataSetSerializer; }
- (void)setPipelineDataSetSerializer:(id)serializer {
    [serializer retain]; [_pipelineDataSetSerializer release]; _pipelineDataSetSerializer = serializer;
}
- (BOOL)shouldMaximizeConcurrentCompilation { return _shouldMaximizeConcurrentCompilation; }
@end

@interface _MTL4CompilerTask : _MTLObjectWithLabel {
    id _compiler;
    NSInteger _status;
}
@end
@implementation _MTL4CompilerTask
- (instancetype)initWithCompiler:(id)compiler {
    if ((self = [super init])) _compiler = [compiler retain];
    return self;
}
- (void)dealloc { [_compiler release]; [super dealloc]; }
- (id)compiler { return _compiler; }
- (NSInteger)status { return _status; }
- (void)setStatus:(NSInteger)status { _status = status; }
@end
