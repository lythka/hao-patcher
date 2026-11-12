// astc.m: ASTC textures on the HD 4000 (Gen7 has no ASTC sampler support).
//
// Weather's WeatherMaps loads KTX map textures in ASTC 8x8 (MTLPixelFormatASTC_8x8_LDR) and MetalOld's descriptor
// validation aborts the process. An ASTC texture is created as RGBA8 (sRGB variants as RGBA8 sRGB) and tagged; data
// uploaded into it (replaceRegion, blit copyFromBuffer:...toTexture:) is decoded with the system's Apple Texture
// Encoder (libate, at_encoder_decompress_texels) at upload time. Sampling, mipmap generation and views then work on
// the RGBA8 texture natively. The texture reports pixelFormat RGBA8.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#import <os/lock.h>
#import <AppleTextureEncoder.h>

void agb_astc_install(Class device, Class heap, Class blit, void (*log)(const char *, ...));

static void (*astc_log)(const char *, ...);
static char astcKey, hookedKey;

@interface AGBAstc : NSObject { @public at_encoder_t enc; NSUInteger bw, bh; MTLPixelFormat outFormat; } @end
@implementation AGBAstc @end

// ASTC sRGB formats are 186..200, LDR 204..218 (LDR = sRGB + 18); libate decodes LDR blocks.
static int is_astc(MTLPixelFormat pf) { return (pf >= 186 && pf <= 200) || (pf >= 204 && pf <= 218); }
static AGBAstc *info_for(MTLPixelFormat pf) {
    static NSMutableDictionary<NSNumber *, AGBAstc *> *cache; static os_unfair_lock lock = OS_UNFAIR_LOCK_INIT;
    os_unfair_lock_lock(&lock);
    if (!cache) cache = [NSMutableDictionary new];
    AGBAstc *i = cache[@(pf)];
    if (!i) {
        int srgb = pf <= 200; MTLPixelFormat ldr = srgb ? pf + 18 : pf;
        for (int b = 0; b < 64 && !i; b++) {
            if (at_block_format_to_MTLPixelFormat((at_block_format_t)b) != ldr) continue;
            at_encoder_t e = at_encoder_create(at_texel_format_rgba8_unorm, at_alpha_not_premultiplied, (at_block_format_t)b,
                                               at_alpha_not_premultiplied, NULL);
            if (!e) break;
            i = [AGBAstc new]; i->enc = e;
            at_size_t d = at_encoder_get_block_dimensions(e); i->bw = d.x; i->bh = d.y;
            i->outFormat = srgb ? MTLPixelFormatRGBA8Unorm_sRGB : MTLPixelFormatRGBA8Unorm;
            cache[@(pf)] = i;
        }
        if (!i) astc_log("ASTC pixel format %lu: no decoder", (unsigned long)pf);
    }
    os_unfair_lock_unlock(&lock);
    return i;
}

// Decode `depth` slices of ASTC blocks into tightly packed RGBA8; returns malloc'd texels or NULL.
static uint8_t *decode(AGBAstc *i, const void *blocks, NSUInteger bpr, NSUInteger bpi, MTLSize size) {
    NSUInteger by = (size.height + i->bh - 1) / i->bh, outRow = size.width * 4, outImg = outRow * size.height;
    if (!bpi) bpi = bpr * by;
    uint8_t *out = malloc(outImg * MAX(size.depth, (NSUInteger)1));
    void *aligned = NULL;
    for (NSUInteger z = 0; z < MAX(size.depth, (NSUInteger)1); z++) {
        const uint8_t *src = (const uint8_t *)blocks + z * bpi;
        if ((uintptr_t)src & 15) {   // libate wants 16-byte aligned blocks
            if (!aligned && posix_memalign(&aligned, 16, bpr * by)) { free(out); return NULL; }
            memcpy(aligned, src, bpr * by); src = aligned;
        }
        at_block_buffer_t sb = { (void *)src, bpr, bpr * by };
        at_texel_region_t dr = { out + z * outImg, { (uint32_t)size.width, (uint32_t)size.height, 1 }, outRow, outImg };
        at_error_t err = at_encoder_decompress_texels(i->enc, &sb, &dr, at_flags_default);
        if (err != at_error_success) { astc_log("ASTC decode failed (%d)", (int)err); free(aligned); free(out); return NULL; }
    }
    free(aligned);
    return out;
}

typedef void (*ReplaceIMP)(id, SEL, MTLRegion, NSUInteger, NSUInteger, const void *, NSUInteger, NSUInteger);
static ReplaceIMP o_replace;
static void hook_texture_class(Class c) {
    if (objc_getAssociatedObject(c, &hookedKey)) return;
    objc_setAssociatedObject(c, &hookedKey, @YES, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    SEL s = @selector(replaceRegion:mipmapLevel:slice:withBytes:bytesPerRow:bytesPerImage:);
    Method m = class_getInstanceMethod(c, s); if (!m) { astc_log("%s lacks replaceRegion", class_getName(c)); return; }
    o_replace = (ReplaceIMP)method_getImplementation(m);
    method_setImplementation(m, imp_implementationWithBlock(^(id tex, MTLRegion r, NSUInteger level, NSUInteger slice, const void *bytes,
                                                               NSUInteger bpr, NSUInteger bpi) {
        AGBAstc *i = objc_getAssociatedObject(tex, &astcKey);
        if (!i || !bytes) { o_replace(tex, s, r, level, slice, bytes, bpr, bpi); return; }
        uint8_t *t = decode(i, bytes, bpr, bpi, r.size);
        if (!t) return;
        o_replace(tex, s, r, level, slice, t, r.size.width * 4, r.size.width * 4 * r.size.height);
        free(t);
    }));
}

typedef id (*NewTexIMP)(id, SEL, MTLTextureDescriptor *);
static void hook_new_texture(Class c) {
    SEL s = @selector(newTextureWithDescriptor:);
    Method m = class_getInstanceMethod(c, s); if (!m) return;
    __block NewTexIMP orig = (NewTexIMP)method_getImplementation(m);
    IMP imp = imp_implementationWithBlock(^id(id owner, MTLTextureDescriptor *td) __attribute__((ns_returns_retained)) {
        AGBAstc *i = td && is_astc(td.pixelFormat) ? info_for(td.pixelFormat) : nil;
        if (!i) return orig(owner, s, td);
        MTLTextureDescriptor *c2 = [td copy]; c2.pixelFormat = i->outFormat;
        id t = orig(owner, s, c2);
        if (t) { objc_setAssociatedObject(t, &astcKey, i, OBJC_ASSOCIATION_RETAIN_NONATOMIC); hook_texture_class(object_getClass(t)); }
        return t;
    });
    if (!class_addMethod(c, s, imp, method_getTypeEncoding(m))) method_setImplementation(class_getInstanceMethod(c, s), imp);
}

typedef void (*BlitIMP)(id, SEL, id, NSUInteger, NSUInteger, NSUInteger, MTLSize, id, NSUInteger, NSUInteger, MTLOrigin);
typedef void (*BlitOptIMP)(id, SEL, id, NSUInteger, NSUInteger, NSUInteger, MTLSize, id, NSUInteger, NSUInteger, MTLOrigin, MTLBlitOption);
static char tempsKey;
// The decoded copy lives in a new shared buffer kept alive with the encoder.
static id<MTLBuffer> decoded_buffer(id enc, id<MTLBuffer> src, NSUInteger off, NSUInteger bpr, NSUInteger bpi, MTLSize size, AGBAstc *i) {
    const uint8_t *p = src.contents;
    if (!p) { astc_log("ASTC blit from a buffer without CPU contents"); return nil; }
    uint8_t *t = decode(i, p + off, bpr, bpi, size); if (!t) return nil;
    NSUInteger len = size.width * 4 * size.height * MAX(size.depth, (NSUInteger)1);
    id<MTLBuffer> b = [src.device newBufferWithBytes:t length:len options:MTLResourceStorageModeShared];
    free(t);
    NSMutableArray *temps = objc_getAssociatedObject(enc, &tempsKey);
    if (!temps) { temps = [NSMutableArray new]; objc_setAssociatedObject(enc, &tempsKey, temps, OBJC_ASSOCIATION_RETAIN_NONATOMIC); }
    if (b) [temps addObject:b];
    return b;
}
static void hook_blit(Class c) {
    SEL s1 = @selector(copyFromBuffer:sourceOffset:sourceBytesPerRow:sourceBytesPerImage:sourceSize:toTexture:destinationSlice:destinationLevel:destinationOrigin:);
    SEL s2 = @selector(copyFromBuffer:sourceOffset:sourceBytesPerRow:sourceBytesPerImage:sourceSize:toTexture:destinationSlice:destinationLevel:destinationOrigin:options:);
    Method m1 = class_getInstanceMethod(c, s1), m2 = class_getInstanceMethod(c, s2);
    if (m1) {
        __block BlitIMP o1 = (BlitIMP)method_getImplementation(m1);
        method_setImplementation(m1, imp_implementationWithBlock(^(id enc, id<MTLBuffer> buf, NSUInteger off, NSUInteger bpr, NSUInteger bpi, MTLSize size,
                                                                  id<MTLTexture> tex, NSUInteger slice, NSUInteger level, MTLOrigin o) {
            AGBAstc *i = tex ? objc_getAssociatedObject(tex, &astcKey) : nil;
            id<MTLBuffer> d = i ? decoded_buffer(enc, buf, off, bpr, bpi, size, i) : nil;
            if (d) o1(enc, s1, d, 0, size.width * 4, size.width * 4 * size.height, size, tex, slice, level, o);
            else if (!i) o1(enc, s1, buf, off, bpr, bpi, size, tex, slice, level, o);
        }));
    }
    if (m2) {
        __block BlitOptIMP o2 = (BlitOptIMP)method_getImplementation(m2);
        method_setImplementation(m2, imp_implementationWithBlock(^(id enc, id<MTLBuffer> buf, NSUInteger off, NSUInteger bpr, NSUInteger bpi, MTLSize size,
                                                                  id<MTLTexture> tex, NSUInteger slice, NSUInteger level, MTLOrigin o, MTLBlitOption opt) {
            AGBAstc *i = tex ? objc_getAssociatedObject(tex, &astcKey) : nil;
            id<MTLBuffer> d = i ? decoded_buffer(enc, buf, off, bpr, bpi, size, i) : nil;
            if (d) o2(enc, s2, d, 0, size.width * 4, size.width * 4 * size.height, size, tex, slice, level, o, opt);
            else if (!i) o2(enc, s2, buf, off, bpr, bpi, size, tex, slice, level, o, opt);
        }));
    }
}

void agb_astc_install(Class device, Class heap, Class blit, void (*log)(const char *, ...)) {
    astc_log = log;
    if (getenv("AGB_ASTC_DISABLE")) return;
    hook_new_texture(device);
    if (heap) hook_new_texture(heap);
    if (blit) hook_blit(blit);
}
