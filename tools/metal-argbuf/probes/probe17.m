// probe17.m: ASTC 8x8 textures on the HD 4000 (libargbuf's astc.m: created as RGBA8, decoded at upload). Encodes a
// test image with libate, uploads it with replaceRegion and with a blit, reads the textures back and compares them to
// libate's own decode of the same blocks.
// Run: ./probe17 with libargbuf loaded by the driver (or DYLD_INSERT_LIBRARIES=.../libargbuf.dylib)
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <AppleTextureEncoder.h>
#import <dlfcn.h>
enum { W = 64, H = 48 };
int main(void) { @autoreleasepool {
    id<MTLDevice> d = MTLCreateSystemDefaultDevice(); id<MTLCommandQueue> q = [d newCommandQueue];
    void (*install)(void) = dlsym(RTLD_DEFAULT, "agb_install"); if (install) install();
    static uint8_t img[W * H * 4], ref[W * H * 4], got[W * H * 4];
    for (int y = 0; y < H; y++) for (int x = 0; x < W; x++) {
        uint8_t *p = img + 4 * (y * W + x); p[0] = x * 4; p[1] = y * 5; p[2] = (x ^ y) * 3; p[3] = 255;
    }
    at_encoder_t e = at_encoder_create(at_texel_format_rgba8_unorm, at_alpha_not_premultiplied, at_block_format_astc_8x8_ldr,
                                       at_alpha_not_premultiplied, NULL);
    enum { BX = W / 8, BY = H / 8 };
    static uint8_t blocks[BX * BY * 16] __attribute__((aligned(16)));
    at_texel_region_t src = { img, { W, H, 1 }, W * 4, W * H * 4 };
    at_block_buffer_t dst = { blocks, BX * 16, BX * BY * 16 };
    float err = at_encoder_compress_texels(e, &src, &dst, 0.0f, at_flags_default);
    at_texel_region_t rr = { ref, { W, H, 1 }, W * 4, W * H * 4 };
    at_encoder_decompress_texels(e, &dst, &rr, at_flags_default);
    printf("encoded %dx%d to ASTC 8x8 (error %g)\n", W, H, err);
    int fails = 0;
    for (int path = 0; path < 2; path++) {
        MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatASTC_8x8_LDR width:W height:H mipmapped:NO];
        td.storageMode = MTLStorageModeManaged; td.usage = MTLTextureUsageShaderRead;
        id<MTLTexture> t = [d newTextureWithDescriptor:td];
        printf("%s: texture %s pixelFormat %lu\n", path ? "blit" : "replaceRegion", t ? "OK" : "nil", (unsigned long)t.pixelFormat);
        if (!t) { fails++; continue; }
        id<MTLCommandBuffer> cb = [q commandBuffer];
        if (path == 0) [t replaceRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0 withBytes:blocks bytesPerRow:BX * 16];
        else {
            id<MTLBuffer> b = [d newBufferWithBytes:blocks length:sizeof blocks options:MTLResourceStorageModeShared];
            id<MTLBlitCommandEncoder> bl = [cb blitCommandEncoder];
            [bl copyFromBuffer:b sourceOffset:0 sourceBytesPerRow:BX * 16 sourceBytesPerImage:sizeof blocks sourceSize:MTLSizeMake(W, H, 1)
                     toTexture:t destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0, 0, 0)];
            [bl endEncoding];
        }
        id<MTLBlitCommandEncoder> sy = [cb blitCommandEncoder]; [sy synchronizeResource:t]; [sy endEncoding];
        [cb commit]; [cb waitUntilCompleted];
        [t getBytes:got bytesPerRow:W * 4 fromRegion:MTLRegionMake2D(0, 0, W, H) mipmapLevel:0];
        int bad = 0; for (int k = 0; k < W * H * 4; k++) bad += got[k] != ref[k];
        printf("%s: %s (%d of %d bytes differ from libate's decode)\n", path ? "blit" : "replaceRegion", bad ? "WRONG" : "CORRECT", bad, W * H * 4);
        fails += bad != 0;
    }
    return fails ? 3 : 0;
} }
