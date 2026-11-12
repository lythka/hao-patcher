// argbuf.m: native Metal argument buffers (tier 1) for OCLP's HD 4000 driver (MTLIGAccelDevice on MetalOld).
//
// MetalOld's MTLIOAccelDevice answers newIndirectArgumentBufferLayoutWithStructType: with nil and
// newIndirectArgumentEncoderWithLayout: with abort(); the HD 4000 driver overrides neither. This adds both to the
// driver's device class. The shader side (the iCBE wrapper's IGIL rewrite) makes every argument-buffer access go
// through fixed binding-table ranges (argbuf_config.h); this side writes the matching values into argument buffers
// and, at each draw/dispatch, binds the resources each bound argument buffer refers to into those ranges -- the
// per-draw binding-table step Gen7 needs (it has no bindless resource heap).
//
// Encoded values (the front-end layout the IGIL target reads; see findings.md):
//   texture member  -> 64-bit, low word = texture handle h  (resource bound at texture slot AGB_TEX_SLOT + h)
//   buffer member   -> ((AGB_BUF_BTI + h) << 32) | offset    (buffer bound at buffer slot AGB_BUF_SLOT + h, offset 0)
//   sampler member  -> 64-bit sampler handle h               (sampler bound at sampler slot AGB_SAMP_SLOT + h)
// Texture and buffer handles are fixed per (layout structure, member): two instances of the same layout bound in the
// same draw would collide (logged). Sampler handles are per distinct sampler state object.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <os/lock.h>
#import <os/log.h>
#import <dlfcn.h>
#import "../argbuf_config.h"

#define AGB_STAGES 3          // 0 compute, 1 vertex, 2 fragment
#define AGB_API_BUFFERS 31    // client buffer indices tracked per stage
#define AGB_MAX_DEPTH 4       // nested argument buffers followed at bind time

enum { K_NONE, K_TEX, K_BUF, K_SAMP, K_CONST, K_OTHER };
typedef struct { uint32_t offset; uint8_t kind; int32_t handle; } AGBSlot;

static os_log_t g_log;
static os_unfair_lock g_lock = OS_UNFAIR_LOCK_INIT;
static BOOL g_stderr;
static void agb_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void agb_log(const char *fmt, ...) {
    char msg[512]; va_list ap; va_start(ap, fmt); vsnprintf(msg, sizeof msg, fmt, ap); va_end(ap);
    os_log_error(g_log, "argbuf: %{public}s", msg);
    if (g_stderr) fprintf(stderr, "argbuf: %s\n", msg);
}

static MTLStructType *(*createStructType)(NSArray<MTLArgumentDescriptor *> *);
static char tableKey, stateKey;

// ---------------------------------------------------------------------------------------------------- sizes
static void scalarInfo(MTLDataType t, NSUInteger *size, NSUInteger *comps, NSUInteger *cols) {
    *cols = 1;
    switch (t) {
#define V(base, sz) case base: *size = sz; *comps = 1; return; case base##2: *size = sz; *comps = 2; return; \
                    case base##3: *size = sz; *comps = 3; return; case base##4: *size = sz; *comps = 4; return;
    V(MTLDataTypeFloat, 4) V(MTLDataTypeHalf, 2) V(MTLDataTypeInt, 4) V(MTLDataTypeUInt, 4) V(MTLDataTypeShort, 2)
    V(MTLDataTypeUShort, 2) V(MTLDataTypeChar, 1) V(MTLDataTypeUChar, 1) V(MTLDataTypeBool, 1) V(MTLDataTypeLong, 8)
    V(MTLDataTypeULong, 8)
#undef V
#define M(base, sz, c, r) case base##c##x##r: *size = sz; *comps = r; *cols = c; return;
    M(MTLDataTypeFloat, 4, 2, 2) M(MTLDataTypeFloat, 4, 2, 3) M(MTLDataTypeFloat, 4, 2, 4) M(MTLDataTypeFloat, 4, 3, 2)
    M(MTLDataTypeFloat, 4, 3, 3) M(MTLDataTypeFloat, 4, 3, 4) M(MTLDataTypeFloat, 4, 4, 2) M(MTLDataTypeFloat, 4, 4, 3)
    M(MTLDataTypeFloat, 4, 4, 4) M(MTLDataTypeHalf, 2, 2, 2) M(MTLDataTypeHalf, 2, 2, 3) M(MTLDataTypeHalf, 2, 2, 4)
    M(MTLDataTypeHalf, 2, 3, 2) M(MTLDataTypeHalf, 2, 3, 3) M(MTLDataTypeHalf, 2, 3, 4) M(MTLDataTypeHalf, 2, 4, 2)
    M(MTLDataTypeHalf, 2, 4, 3) M(MTLDataTypeHalf, 2, 4, 4)
#undef M
    default: *size = 8; *comps = 1; return;   // resources, pointers and anything unknown: 8 bytes
    }
}
static void typeSize(MTLDataType t, NSUInteger *size, NSUInteger *align) {
    NSUInteger s, c, cols; scalarInfo(t, &s, &c, &cols);
    NSUInteger vec = s * (c == 3 ? 4 : c);
    *size = vec * cols; *align = vec;
}

// ---------------------------------------------------------------------------------------------------- layout
@interface AGBLayout : NSObject {
@public
    NSMutableData *slots;                 // AGBSlot[]
    NSMutableDictionary<NSNumber *, NSNumber *> *slotForIndex;
    NSMutableDictionary<NSNumber *, AGBLayout *> *nested; // slot -> layout of the argument buffer it points to
    NSUInteger length, align;
    NSString *key;
    __weak id device;
}
@end

static NSMutableDictionary<NSString *, NSData *> *g_handles; // layout key -> int32 handle per slot
static int32_t g_nextTex, g_nextBuf;

static MTLArgumentDescriptor *descriptor(MTLDataType type, NSUInteger index, NSUInteger len, MTLTextureType tt, MTLBindingAccess access) {
    MTLArgumentDescriptor *d = [MTLArgumentDescriptor argumentDescriptor];
    d.dataType = type; d.index = index; d.arrayLength = len; d.textureType = tt; d.access = access; return d;
}

@implementation AGBLayout
- (instancetype)init {
    if ((self = [super init])) {
        slots = [NSMutableData new]; slotForIndex = [NSMutableDictionary new]; nested = [NSMutableDictionary new]; align = 8;
    }
    return self;
}
- (AGBSlot *)slot:(NSUInteger)i { return (AGBSlot *)slots.mutableBytes + i; }
- (NSUInteger)count { return slots.length / sizeof(AGBSlot); }
- (void)addSlot:(NSUInteger)index offset:(NSUInteger)offset kind:(uint8_t)kind {
    AGBSlot s = { (uint32_t)offset, kind, -1 };
    slotForIndex[@(index)] = @([self count]);
    [slots appendBytes:&s length:sizeof s];
}
- (NSInteger)slotForIndex:(NSUInteger)index {
    NSNumber *n = slotForIndex[@(index)]; return n ? n.integerValue : -1;
}
static uint8_t kindOf(MTLDataType t) {
    switch (t) {
    case MTLDataTypeTexture: return K_TEX;
    case MTLDataTypePointer: return K_BUF;
    case MTLDataTypeSampler: return K_SAMP;
    case MTLDataTypeStruct: case MTLDataTypeArray: case MTLDataTypeNone: return K_OTHER;
    case MTLDataTypeRenderPipeline: case MTLDataTypeComputePipeline: case MTLDataTypeIndirectCommandBuffer:
    case MTLDataTypeVisibleFunctionTable: case MTLDataTypeIntersectionFunctionTable:
    case MTLDataTypePrimitiveAccelerationStructure: case MTLDataTypeInstanceAccelerationStructure: return K_OTHER;
    default: return K_CONST;
    }
}
// Rebuild argument descriptors from any struct type (reflection types carry no resource offsets) and let MetalOld's
// own MTLCreateStructTypeFromArgumentDescriptors pack them: that packing is what the IGIL target reads.
- (void)setStructType:(MTLStructType *)st withDevice:(id)dev {
    device = dev;
    NSArray<MTLStructMember *> *members = [st.members sortedArrayUsingComparator:^NSComparisonResult(MTLStructMember *a, MTLStructMember *b) {
        return a.argumentIndex < b.argumentIndex ? NSOrderedAscending : a.argumentIndex > b.argumentIndex ? NSOrderedDescending : NSOrderedSame;
    }];
    NSMutableArray *descs = [NSMutableArray new];
    NSMutableString *k = [NSMutableString stringWithString:@"{"];
    NSMutableDictionary<NSNumber *, AGBLayout *> *nestedByIndex = [NSMutableDictionary new];
    for (MTLStructMember *m in members) {
        MTLDataType t = m.dataType; NSUInteger len = 0; MTLTextureType tt = MTLTextureType2D;
        if (t == MTLDataTypeArray) {
            MTLArrayType *a = m.arrayType; t = a.elementType; len = a.arrayLength;
            if (t == MTLDataTypeTexture) tt = a.elementTextureReferenceType.textureType;
            if (t == MTLDataTypeStruct || t == MTLDataTypeArray) agb_log("member %lu: arrays of %s are not supported", (unsigned long)m.argumentIndex, t == MTLDataTypeStruct ? "structs" : "arrays");
        } else if (t == MTLDataTypeTexture) {
            tt = m.textureReferenceType.textureType;
        } else if (t == MTLDataTypePointer) {
            MTLPointerType *p = m.pointerType;
            if (p.elementIsArgumentBuffer && p.elementStructType) {
                AGBLayout *n = [AGBLayout new]; [n setStructType:p.elementStructType withDevice:dev];
                nestedByIndex[@(m.argumentIndex)] = n;
            }
        } else if (t == MTLDataTypeStruct) {
            agb_log("member %lu: inline structs are not supported", (unsigned long)m.argumentIndex);
        }
        [descs addObject:descriptor(t, m.argumentIndex, len, tt, MTLBindingAccessReadOnly)];
        [k appendFormat:@"%lu:%lu:%lu:%lu%@;", (unsigned long)m.argumentIndex, (unsigned long)t, (unsigned long)len,
            (unsigned long)tt, nestedByIndex[@(m.argumentIndex)] ? nestedByIndex[@(m.argumentIndex)]->key : @""];
    }
    [k appendString:@"}"]; key = k;
    MTLStructType *packed = descs.count ? createStructType(descs) : nil;
    NSUInteger end = 0;
    for (MTLStructMember *m in packed.members) {
        MTLDataType t = m.dataType; NSUInteger n = 1, stride = 8;
        if (t == MTLDataTypeArray) { n = m.arrayType.arrayLength; stride = m.arrayType.stride; t = m.arrayType.elementType; }
        uint8_t kind = kindOf(t);
        NSUInteger size, al; typeSize(t, &size, &al);
        if (kind != K_CONST) { size = 8; al = 8; }
        if (!stride) stride = size;
        for (NSUInteger e = 0; e < n; e++) {
            [self addSlot:m.argumentIndex + e offset:m.offset + e * stride kind:kind];
            if (kind == K_CONST && e) break;   // a constant array is one index (base); elements by pointer math
        }
        AGBLayout *nl = nestedByIndex[@(m.argumentIndex)];
        if (nl) nested[@([self slotForIndex:m.argumentIndex])] = nl;
        end = MAX(end, m.offset + (n - 1) * stride + size);
        align = MAX(align, al);
    }
    length = (end + align - 1) / align * align;
    [self assignHandles];
}
- (void)assignHandles {
    os_unfair_lock_lock(&g_lock);
    NSData *h = g_handles[key];
    if (!h) {
        NSMutableData *m = [NSMutableData dataWithLength:[self count] * sizeof(int32_t)];
        int32_t *v = m.mutableBytes;
        for (NSUInteger i = 0; i < [self count]; i++) {
            AGBSlot *s = [self slot:i]; v[i] = -1;
            if (s->kind == K_TEX) v[i] = g_nextTex < AGB_TEX_COUNT ? g_nextTex++ : -1;
            else if (s->kind == K_BUF) v[i] = g_nextBuf < AGB_BUF_COUNT ? g_nextBuf++ : -1;
            if ((s->kind == K_TEX || s->kind == K_BUF) && v[i] < 0)
                agb_log("out of %s handles (layout %s, slot %lu)", s->kind == K_TEX ? "texture" : "buffer", key.UTF8String, (unsigned long)i);
        }
        g_handles[key] = h = m;
    }
    os_unfair_lock_unlock(&g_lock);
    const int32_t *v = h.bytes;
    for (NSUInteger i = 0; i < [self count]; i++) [self slot:i]->handle = v[i];
}
- (NSUInteger)encodedLength { return length; }
- (NSUInteger)alignment { return align; }
- (BOOL)bufferLayoutMatchesFrontEndLayout { return YES; }
- (id)device { return device; }
- (NSString *)description { return [NSString stringWithFormat:@"<AGBLayout %@ length %lu>", key, (unsigned long)length]; }
@end

// ---------------------------------------------------------------------------------------------------- instances
// What one encoded argument buffer (buffer, base offset) refers to; kept on the MTLBuffer for draw-time binding.
@interface AGBInstance : NSObject {
@public
    AGBLayout *layout;
    NSMutableArray *res;        // resource per slot (NSNull when unset)
    NSMutableData *offs;        // NSUInteger per slot: buffer offset, or sampler handle
}
@end
@implementation AGBInstance
- (instancetype)initWithLayout:(AGBLayout *)l {
    if ((self = [super init])) {
        layout = l; NSUInteger n = [l count];
        res = [NSMutableArray arrayWithCapacity:n]; for (NSUInteger i = 0; i < n; i++) [res addObject:NSNull.null];
        offs = [NSMutableData dataWithLength:n * sizeof(NSUInteger)];
    }
    return self;
}
@end

@interface AGBTable : NSObject { @public os_unfair_lock lock; NSMutableDictionary<NSNumber *, AGBInstance *> *byOffset; }
@end
@implementation AGBTable
- (instancetype)init { if ((self = [super init])) { lock = OS_UNFAIR_LOCK_INIT; byOffset = [NSMutableDictionary new]; } return self; }
@end

static AGBTable *tableOf(id buffer, BOOL create) {
    AGBTable *t = objc_getAssociatedObject(buffer, &tableKey);
    if (t || !create) return t;
    os_unfair_lock_lock(&g_lock);
    t = objc_getAssociatedObject(buffer, &tableKey);
    if (!t) { t = [AGBTable new]; objc_setAssociatedObject(buffer, &tableKey, t, OBJC_ASSOCIATION_RETAIN); }
    os_unfair_lock_unlock(&g_lock);
    return t;
}
static AGBInstance *instanceAt(id buffer, NSUInteger offset) {
    AGBTable *t = tableOf(buffer, NO); if (!t) return nil;
    os_unfair_lock_lock(&t->lock); AGBInstance *i = t->byOffset[@(offset)]; os_unfair_lock_unlock(&t->lock);
    return i;
}

static NSMutableArray *g_samplers;
static int32_t samplerHandle(id sampler) {
    if (!sampler) return 0;
    os_unfair_lock_lock(&g_lock);
    NSUInteger i = [g_samplers indexOfObjectIdenticalTo:sampler];
    if (i == NSNotFound) {
        if (g_samplers.count < AGB_SAMP_COUNT) { [g_samplers addObject:sampler]; i = g_samplers.count - 1; }
        else { i = 0; agb_log("out of sampler handles"); }
    }
    os_unfair_lock_unlock(&g_lock);
    return (int32_t)i;
}

// ---------------------------------------------------------------------------------------------------- encoder
@interface AGBEncoder : NSObject <MTLArgumentEncoder>
@end
@implementation AGBEncoder {
    AGBLayout *_layout;
    id<MTLDevice> _device;
    id<MTLBuffer> _buffer;
    NSUInteger _base;
    uint8_t *_dst;
    AGBInstance *_inst;
}
@synthesize label;
- (instancetype)initWithLayout:(AGBLayout *)layout device:(id<MTLDevice>)device {
    if ((self = [super init])) { _layout = layout; _device = device; }
    return self;
}
- (id<MTLDevice>)device { return _device; }
- (id)layout { return _layout; }
- (NSUInteger)encodedLength { return _layout->length; }
- (NSUInteger)alignment { return _layout->align; }
- (void)setArgumentBuffer:(id<MTLBuffer>)buffer offset:(NSUInteger)offset {
    _buffer = buffer; _base = offset; _dst = buffer ? (uint8_t *)buffer.contents + offset : NULL; _inst = nil;
    if (!buffer) return;
    AGBTable *t = tableOf(buffer, YES);
    os_unfair_lock_lock(&t->lock);
    AGBInstance *i = t->byOffset[@(offset)];
    if (!i || i->layout != _layout) t->byOffset[@(offset)] = i = [[AGBInstance alloc] initWithLayout:_layout];
    os_unfair_lock_unlock(&t->lock);
    _inst = i;
}
- (void)setArgumentBuffer:(id<MTLBuffer>)buffer startOffset:(NSUInteger)startOffset arrayElement:(NSUInteger)element {
    static dispatch_once_t once; dispatch_once(&once, ^{ agb_log("arrays of argument buffers are encoded but bound only by element offset"); });
    [self setArgumentBuffer:buffer offset:startOffset + element * _layout->length];
}
- (AGBSlot *)slotAt:(NSUInteger)index kind:(uint8_t)kind slot:(NSInteger *)outSlot {
    NSInteger s = [_layout slotForIndex:index];
    if (s < 0 || !_dst) { agb_log("index %lu: %s", (unsigned long)index, s < 0 ? "no such member" : "no argument buffer set"); return NULL; }
    AGBSlot *slot = [_layout slot:s];
    if (slot->kind != kind) { agb_log("index %lu: member kind %d, set as %d", (unsigned long)index, slot->kind, kind); return NULL; }
    *outSlot = s; return slot;
}
- (void)record:(NSInteger)s resource:(id)r offset:(NSUInteger)o {
    AGBTable *t = tableOf(_buffer, NO);
    if (t) os_unfair_lock_lock(&t->lock);
    _inst->res[s] = r ?: NSNull.null; ((NSUInteger *)_inst->offs.mutableBytes)[s] = o;
    if (t) os_unfair_lock_unlock(&t->lock);
}
- (void)setBuffer:(id<MTLBuffer>)buffer offset:(NSUInteger)offset atIndex:(NSUInteger)index {
    NSInteger s; AGBSlot *slot = [self slotAt:index kind:K_BUF slot:&s]; if (!slot) return;
    uint64_t v = buffer && slot->handle >= 0 ? ((uint64_t)(AGB_BUF_BTI + slot->handle) << 32) | (uint32_t)offset : 0;
    memcpy(_dst + slot->offset, &v, 8);
    [self record:s resource:buffer offset:offset];
}
- (void)setBuffers:(id<MTLBuffer> const[])buffers offsets:(NSUInteger const[])offsets withRange:(NSRange)range {
    for (NSUInteger i = 0; i < range.length; i++) [self setBuffer:buffers[i] offset:offsets[i] atIndex:range.location + i];
}
- (void)setTexture:(id<MTLTexture>)texture atIndex:(NSUInteger)index {
    NSInteger s; AGBSlot *slot = [self slotAt:index kind:K_TEX slot:&s]; if (!slot) return;
    uint64_t v = slot->handle >= 0 ? (uint32_t)slot->handle : 0;
    memcpy(_dst + slot->offset, &v, 8);
    [self record:s resource:texture offset:0];
}
- (void)setTextures:(id<MTLTexture> const[])textures withRange:(NSRange)range {
    for (NSUInteger i = 0; i < range.length; i++) [self setTexture:textures[i] atIndex:range.location + i];
}
- (void)setSamplerState:(id<MTLSamplerState>)sampler atIndex:(NSUInteger)index {
    NSInteger s; AGBSlot *slot = [self slotAt:index kind:K_SAMP slot:&s]; if (!slot) return;
    int32_t h = samplerHandle(sampler); uint64_t v = (uint32_t)h;
    memcpy(_dst + slot->offset, &v, 8);
    [self record:s resource:sampler offset:(NSUInteger)h];
}
- (void)setSamplerStates:(id<MTLSamplerState> const[])samplers withRange:(NSRange)range {
    for (NSUInteger i = 0; i < range.length; i++) [self setSamplerState:samplers[i] atIndex:range.location + i];
}
- (void *)constantDataAtIndex:(NSUInteger)index {
    NSInteger s = [_layout slotForIndex:index];
    if (s < 0 || !_dst) { agb_log("constantDataAtIndex %lu: %s", (unsigned long)index, s < 0 ? "no such member" : "no argument buffer set"); return NULL; }
    return _dst + [_layout slot:s]->offset;
}
- (id<MTLArgumentEncoder>)newArgumentEncoderForBufferAtIndex:(NSUInteger)index {
    NSInteger s = [_layout slotForIndex:index];
    AGBLayout *n = s >= 0 ? _layout->nested[@(s)] : nil;
    if (!n) { agb_log("newArgumentEncoderForBufferAtIndex %lu: not an argument-buffer pointer", (unsigned long)index); return nil; }
    return [[AGBEncoder alloc] initWithLayout:n device:_device];
}
#define UNSUPPORTED(sig) sig { agb_log("unsupported: %s", sel_getName(_cmd)); }
UNSUPPORTED(- (void)setRenderPipelineState:(id)p atIndex:(NSUInteger)i)
UNSUPPORTED(- (void)setRenderPipelineStates:(id const[])p withRange:(NSRange)r)
UNSUPPORTED(- (void)setComputePipelineState:(id)p atIndex:(NSUInteger)i)
UNSUPPORTED(- (void)setComputePipelineStates:(id const[])p withRange:(NSRange)r)
UNSUPPORTED(- (void)setIndirectCommandBuffer:(id)b atIndex:(NSUInteger)i)
UNSUPPORTED(- (void)setIndirectCommandBuffers:(id const[])b withRange:(NSRange)r)
UNSUPPORTED(- (void)setAccelerationStructure:(id)a atIndex:(NSUInteger)i)
UNSUPPORTED(- (void)setVisibleFunctionTable:(id)t atIndex:(NSUInteger)i)
UNSUPPORTED(- (void)setVisibleFunctionTables:(id const[])t withRange:(NSRange)r)
UNSUPPORTED(- (void)setIntersectionFunctionTable:(id)t atIndex:(NSUInteger)i)
UNSUPPORTED(- (void)setIntersectionFunctionTables:(id const[])t withRange:(NSRange)r)
#undef UNSUPPORTED
@end

// ---------------------------------------------------------------------------------------------------- draw-time binding
@interface AGBEncState : NSObject {
@public
    __unsafe_unretained id bufs[AGB_STAGES][AGB_API_BUFFERS];
    NSUInteger offs[AGB_STAGES][AGB_API_BUFFERS];
    __unsafe_unretained id lastTex[AGB_STAGES][AGB_TEX_COUNT];
    __unsafe_unretained id lastBuf[AGB_STAGES][AGB_BUF_COUNT];
    __unsafe_unretained id lastSamp[AGB_STAGES][AGB_SAMP_COUNT];
}
@end
@implementation AGBEncState
@end
static AGBEncState *stateOf(id enc, BOOL create) {
    AGBEncState *s = objc_getAssociatedObject(enc, &stateKey);
    if (!s && create) { s = [AGBEncState new]; objc_setAssociatedObject(enc, &stateKey, s, OBJC_ASSOCIATION_RETAIN_NONATOMIC); }
    return s;
}

typedef void (*SetBufferIMP)(id, SEL, id, NSUInteger, NSUInteger);
typedef void (*SetBuffersIMP)(id, SEL, const id *, const NSUInteger *, NSRange);
typedef void (*SetOffsetIMP)(id, SEL, NSUInteger, NSUInteger);
typedef void (*SetBytesIMP)(id, SEL, const void *, NSUInteger, NSUInteger);
static SetBufferIMP o_setBuffer[AGB_STAGES];
static SEL s_setBuffer[AGB_STAGES], s_setTexture[AGB_STAGES], s_setSampler[AGB_STAGES];

static void track(id enc, int stage, NSUInteger index, id buffer, NSUInteger offset) {
    if (index >= AGB_API_BUFFERS) return;
    AGBEncState *s = stateOf(enc, buffer != nil);
    if (!s) return;
    s->bufs[stage][index] = buffer; s->offs[stage][index] = offset;
}

static void bindInstance(id enc, AGBEncState *st, int stage, AGBInstance *inst, int depth) {
    AGBLayout *l = inst->layout; NSUInteger n = [l count];
    const NSUInteger *offs = inst->offs.bytes;
    for (NSUInteger i = 0; i < n; i++) {
        AGBSlot *slot = [l slot:i];
        id r = inst->res[i]; if (r == NSNull.null) r = nil;
        if (slot->kind == K_TEX && slot->handle >= 0) {
            if (st->lastTex[stage][slot->handle] != r) {
                ((void (*)(id, SEL, id, NSUInteger))objc_msgSend)(enc, s_setTexture[stage], r, AGB_TEX_SLOT + slot->handle);
                st->lastTex[stage][slot->handle] = r;
            }
        } else if (slot->kind == K_BUF && slot->handle >= 0) {
            if (st->lastBuf[stage][slot->handle] != r) {
                o_setBuffer[stage](enc, s_setBuffer[stage], r, 0, AGB_BUF_SLOT + slot->handle);
                st->lastBuf[stage][slot->handle] = r;
            }
            if (r && depth < AGB_MAX_DEPTH) { AGBInstance *ni = instanceAt(r, offs[i]); if (ni) bindInstance(enc, st, stage, ni, depth + 1); }
        } else if (slot->kind == K_SAMP && r) {
            NSUInteger h = offs[i];
            if (h < AGB_SAMP_COUNT && st->lastSamp[stage][h] != r) {
                ((void (*)(id, SEL, id, NSUInteger))objc_msgSend)(enc, s_setSampler[stage], r, AGB_SAMP_SLOT + h);
                st->lastSamp[stage][h] = r;
            }
        }
    }
}

typedef struct { SEL sel; IMP imp; int render; } AGBHook;
static AGBHook g_hooks[64];
static int g_nhooks;

IMP agb_pre(id self, SEL _cmd);
IMP agb_pre(id self, SEL _cmd) {
    IMP orig = NULL; int render = 0;
    for (int i = 0; i < g_nhooks; i++) if (g_hooks[i].sel == _cmd) { orig = g_hooks[i].imp; render = g_hooks[i].render; break; }
    if (!orig) { agb_log("no original for %s", sel_getName(_cmd)); abort(); }
    AGBEncState *st = stateOf(self, NO);
    if (!st) return orig;
    @autoreleasepool {
        for (int stage = render ? 1 : 0; stage <= (render ? 2 : 0); stage++)
            for (int i = 0; i < AGB_API_BUFFERS; i++) {
                id b = st->bufs[stage][i]; if (!b) continue;
                AGBInstance *inst = instanceAt(b, st->offs[stage][i]);
                if (inst) bindInstance(self, st, stage, inst, 0);
            }
    }
    return orig;
}
extern void agb_tramp(void);

// ---------------------------------------------------------------------------------------------------- install
static IMP replace(Class c, SEL sel, IMP imp) {
    Method m = class_getInstanceMethod(c, sel);
    if (!m) { agb_log("%s lacks %s", class_getName(c), sel_getName(sel)); return NULL; }
    IMP orig = method_getImplementation(m);
    if (!class_addMethod(c, sel, imp, method_getTypeEncoding(m))) method_setImplementation(class_getInstanceMethod(c, sel), imp);
    return orig;
}

static void hookStage(Class c, int stage, NSString *prefix) {
    // prefix: @"" (compute) / @"Vertex" / @"Fragment"
    s_setBuffer[stage] = NSSelectorFromString([NSString stringWithFormat:@"set%@Buffer:offset:atIndex:", prefix]);
    s_setTexture[stage] = NSSelectorFromString([NSString stringWithFormat:@"set%@Texture:atIndex:", prefix]);
    s_setSampler[stage] = NSSelectorFromString([NSString stringWithFormat:@"set%@SamplerState:atIndex:", prefix]);
    SEL sb = s_setBuffer[stage];
    __block SetBufferIMP ob = NULL;
    ob = (SetBufferIMP)replace(c, sb, imp_implementationWithBlock(^(id enc, id buffer, NSUInteger offset, NSUInteger index) {
        ob(enc, sb, buffer, offset, index); track(enc, stage, index, buffer, offset);
    }));
    o_setBuffer[stage] = ob;
    SEL sbs = NSSelectorFromString([NSString stringWithFormat:@"set%@Buffers:offsets:withRange:", prefix]);
    __block SetBuffersIMP obs = NULL;
    obs = (SetBuffersIMP)replace(c, sbs, imp_implementationWithBlock(^(id enc, const id *buffers, const NSUInteger *offsets, NSRange r) {
        obs(enc, sbs, buffers, offsets, r);
        for (NSUInteger i = 0; i < r.length; i++) track(enc, stage, r.location + i, buffers[i], offsets[i]);
    }));
    SEL so = NSSelectorFromString([NSString stringWithFormat:@"set%@BufferOffset:atIndex:", prefix]);
    __block SetOffsetIMP oo = NULL;
    oo = (SetOffsetIMP)replace(c, so, imp_implementationWithBlock(^(id enc, NSUInteger offset, NSUInteger index) {
        oo(enc, so, offset, index);
        AGBEncState *s = stateOf(enc, NO); if (s && index < AGB_API_BUFFERS) s->offs[stage][index] = offset;
    }));
    SEL sy = NSSelectorFromString([NSString stringWithFormat:@"set%@Bytes:length:atIndex:", prefix]);
    __block SetBytesIMP oy = NULL;
    oy = (SetBytesIMP)replace(c, sy, imp_implementationWithBlock(^(id enc, const void *bytes, NSUInteger length, NSUInteger index) {
        oy(enc, sy, bytes, length, index); track(enc, stage, index, nil, 0);
    }));
}

static void hookDraws(Class c, int render) {
    unsigned n = 0; Method *ms = class_copyMethodList(c, &n);
    for (unsigned i = 0; i < n; i++) {
        const char *name = sel_getName(method_getName(ms[i]));
        if (strncmp(name, "draw", 4) && strncmp(name, "dispatch", 8)) continue;
        if (g_nhooks == (int)(sizeof g_hooks / sizeof g_hooks[0])) { agb_log("too many draw selectors"); break; }
        g_hooks[g_nhooks++] = (AGBHook){ method_getName(ms[i]), method_getImplementation(ms[i]), render };
        method_setImplementation(ms[i], (IMP)agb_tramp);
    }
    free(ms);
}

void agb_install(void);
void agb_install(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{ @autoreleasepool {
        Class dev = NSClassFromString(@"MTLIGAccelDevice");
        Class cce = NSClassFromString(@"MTLIGAccelComputeCommandEncoder");
        Class rce = NSClassFromString(@"MTLIGAccelRenderCommandEncoder");
        createStructType = dlsym(RTLD_DEFAULT, "MTLCreateStructTypeFromArgumentDescriptors");
        if (!dev || !cce || !rce || !createStructType) { agb_log("not installed: driver classes or MetalOld entry point missing"); return; }
        g_handles = [NSMutableDictionary new]; g_samplers = [NSMutableArray new];
        class_addProtocol(AGBEncoder.class, @protocol(MTLArgumentEncoder));
        class_replaceMethod(dev, @selector(newIndirectArgumentBufferLayoutWithStructType:),
            imp_implementationWithBlock(^id(id d, MTLStructType *st) __attribute__((ns_returns_retained)) { return [AGBLayout new]; }), "@@:@");
        class_replaceMethod(dev, @selector(newIndirectArgumentEncoderWithLayout:),
            imp_implementationWithBlock(^id(id d, id layout) __attribute__((ns_returns_retained)) {
                if (![layout isKindOfClass:AGBLayout.class]) { agb_log("foreign layout %s", object_getClassName(layout)); return nil; }
                return [[AGBEncoder alloc] initWithLayout:layout device:d];
            }), "@@:@");
        hookStage(cce, 0, @"");
        hookStage(rce, 1, @"Vertex");
        hookStage(rce, 2, @"Fragment");
        hookDraws(cce, 0);
        hookDraws(rce, 1);
        agb_log("installed: textures %d..%d, buffers %d..%d (BTI %d), samplers %d..%d, %d draw/dispatch selectors",
                AGB_TEX_SLOT, AGB_TEX_SLOT + AGB_TEX_COUNT - 1, AGB_BUF_SLOT, AGB_BUF_SLOT + AGB_BUF_COUNT - 1, AGB_BUF_BTI,
                AGB_SAMP_SLOT, AGB_SAMP_SLOT + AGB_SAMP_COUNT - 1, g_nhooks);
    }});
}

__attribute__((constructor)) static void init(void) {
    g_log = os_log_create("com.hao.argbuf", "device");
    g_stderr = getenv("AGB_LOG") != NULL;
    if (getenv("AGB_DISABLE")) return;
    // Loaded by the driver bundle: its classes are present. Loaded into a test program: the program calls agb_install
    // after creating the device.
    if (NSClassFromString(@"MTLIGAccelDevice")) agb_install();
}
