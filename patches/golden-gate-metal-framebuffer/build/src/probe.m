#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <math.h>
#include <sys/sysctl.h>

static void require(BOOL ok, NSString *message) {
    if (!ok) @throw [NSException exceptionWithName:@"ProbeFailure" reason:message userInfo:nil];
}
static NSString *sysString(const char *key) {
    char value[256] = {0}; size_t size = sizeof(value);
    return sysctlbyname(key, value, &size, NULL, 0) ? @"unknown" : @(value);
}
static id<MTLRenderPipelineState> pipeline(id<MTLDevice> d, id<MTLLibrary> lib,
        NSString *fragment, NSUInteger attachment, MTLPixelFormat format, NSUInteger samples,
        MTLRenderPipelineReflection **reflection) {
    MTLRenderPipelineDescriptor *desc = [MTLRenderPipelineDescriptor new];
    desc.vertexFunction = [lib newFunctionWithName:@"fullscreen"];
    if(getenv("FB_PROBE_IDENTITIES") && [fragment hasPrefix:@"fetch"]) {
        desc.vertexFunction=[lib newFunctionWithName:@"fullscreen_identity"];
        desc.fragmentFunction=[lib newFunctionWithName:@"fetch_identity"];
    } else if(getenv("FB_PROBE_CONSTANTS") && [fragment hasPrefix:@"fetch"]) {
        MTLFunctionConstantValues *constants=[MTLFunctionConstantValues new];
        uint32_t index=(uint32_t)attachment;BOOL enabled=YES;
        [constants setConstantValue:&index type:MTLDataTypeUInt atIndex:0];
        [constants setConstantValue:&enabled type:MTLDataTypeBool atIndex:1];
        NSError *error=nil;desc.fragmentFunction=[lib newFunctionWithName:@"fetch_dynamic" constantValues:constants error:&error];
        require(desc.fragmentFunction!=nil,error.description?:@"Specialization failed");
    } else desc.fragmentFunction = [lib newFunctionWithName:fragment];
    require(desc.vertexFunction && desc.fragmentFunction, @"Missing shader function");
    desc.colorAttachments[attachment].pixelFormat = format;
    desc.rasterSampleCount = samples;
    NSError *error = nil;
    id<MTLRenderPipelineState> ps;
    const char *factory=getenv("FB_PROBE_FACTORY");
    if(factory && !strcmp(factory,"async")) {
        dispatch_semaphore_t done=dispatch_semaphore_create(0);
        __block id<MTLRenderPipelineState> made;__block NSError *failure;
        __block MTLRenderPipelineReflection *info;
        [d newRenderPipelineStateWithDescriptor:desc options:MTLPipelineOptionArgumentInfo
            completionHandler:^(id<MTLRenderPipelineState> value,MTLRenderPipelineReflection *r,NSError *e){
                made=value;info=r;failure=e;dispatch_semaphore_signal(done);
            }];
        require(!dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,30*NSEC_PER_SEC)),@"Async factory timeout");
        ps=made;error=failure;if(reflection)*reflection=info;
    } else ps = [d newRenderPipelineStateWithDescriptor:desc
        options:reflection ? MTLPipelineOptionArgumentInfo : MTLPipelineOptionNone reflection:reflection error:&error];
    require(ps != nil, error.description ?: @"Pipeline creation failed");
    return ps;
}
static MTLRenderPassDescriptor *pass(id<MTLTexture> t, NSUInteger attachment, MTLLoadAction load) {
    MTLRenderPassDescriptor *p = [MTLRenderPassDescriptor renderPassDescriptor];
    p.colorAttachments[attachment].texture = t;
    const char *subresource=getenv("FB_PROBE_SUBRESOURCE");
    if(subresource && !strcmp(subresource,"mip"))p.colorAttachments[attachment].level=1;
    if(subresource && !strcmp(subresource,"slice"))p.colorAttachments[attachment].slice=2;
    p.colorAttachments[attachment].loadAction = load;
    p.colorAttachments[attachment].storeAction = MTLStoreActionStore;
    p.colorAttachments[attachment].clearColor = MTLClearColorMake(.125, .25, .375, .5);
    return p;
}
static id<MTLTexture> readView(id<MTLTexture> texture) {
    const char *subresource=getenv("FB_PROBE_SUBRESOURCE");if(!subresource)return texture;
    return [texture newTextureViewWithPixelFormat:texture.pixelFormat textureType:MTLTextureType2D
        levels:NSMakeRange(!strcmp(subresource,"mip")?1:0,1) slices:NSMakeRange(!strcmp(subresource,"slice")?2:0,1)];
}
static char parentKey;
static id<MTLRenderCommandEncoder> beginPass(id<MTLCommandBuffer> cb,MTLRenderPassDescriptor *desc) {
    if(!getenv("FB_PROBE_PARALLEL"))return [cb renderCommandEncoderWithDescriptor:desc];
    id<MTLParallelRenderCommandEncoder> parent=[cb parallelRenderCommandEncoderWithDescriptor:desc];
    id<MTLRenderCommandEncoder> e=[parent renderCommandEncoder];objc_setAssociatedObject(e,&parentKey,parent,OBJC_ASSOCIATION_RETAIN_NONATOMIC);return e;
}
static void endPass(id<MTLRenderCommandEncoder> e) {
    [e endEncoding];id parent=objc_getAssociatedObject(e,&parentKey);[parent endEncoding];
}
static id<MTLRenderCommandEncoder> draw(id<MTLRenderCommandEncoder> e, NSString *form, id<MTLBuffer> indices,id<MTLRenderPipelineState> ps) {
    if ([form isEqual:@"strip"])
        [e drawPrimitives:MTLPrimitiveTypeTriangleStrip vertexStart:0 vertexCount:6];
    else if ([form isEqual:@"indexed-strip"])
        [e drawIndexedPrimitives:MTLPrimitiveTypeTriangleStrip indexCount:6 indexType:MTLIndexTypeUInt16
            indexBuffer:indices indexBufferOffset:0];
    else if ([form isEqual:@"indexed-instanced"])
        [e drawIndexedPrimitives:MTLPrimitiveTypeTriangle indexCount:3 indexType:MTLIndexTypeUInt16
            indexBuffer:indices indexBufferOffset:0 instanceCount:4 baseVertex:0 baseInstance:7];
    else if ([form isEqual:@"offset-indexed"])
        [e drawIndexedPrimitives:MTLPrimitiveTypeTriangle indexCount:12 indexType:MTLIndexTypeUInt16
            indexBuffer:indices indexBufferOffset:6 instanceCount:1 baseVertex:3 baseInstance:0];
    else if ([form isEqual:@"offset-direct"])
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:3 vertexCount:12 instanceCount:1 baseInstance:7];
    else if ([form isEqual:@"indexed"])
        [e drawIndexedPrimitives:MTLPrimitiveTypeTriangle indexCount:12 indexType:MTLIndexTypeUInt16
                    indexBuffer:indices indexBufferOffset:0];
    else if ([form isEqual:@"instanced"])
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3 instanceCount:4];
    else if ([form isEqual:@"overlap"])
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:12];
    else if ([form isEqual:@"draws"])
        for (int i = 0; i < 4; ++i) {
            id parent=objc_getAssociatedObject(e,&parentKey);
            if(i && parent) {
                [e endEncoding];e=[parent renderCommandEncoder];[e setRenderPipelineState:ps];
                objc_setAssociatedObject(e,&parentKey,parent,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
            }
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        }
    else
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    return e;
}
static float quantize(float x, MTLPixelFormat format) {
    if (format == MTLPixelFormatRGBA8Unorm) return roundf(fminf(1, fmaxf(0, x)) * 255) / 255;
    if (format == MTLPixelFormatRGBA16Float || format == MTLPixelFormatRG16Float) return (float)(__fp16)x;
    return x;
}
static NSDictionary *run(id<MTLDevice> d, id<MTLLibrary> lib, NSString *output,
        NSString *mode, NSString *load, NSString *form, NSUInteger attachment,
        MTLPixelFormat format, NSUInteger samples) {
    NSString *name = [NSString stringWithFormat:@"%@-%@-%@-a%lu-f%lu-s%lu", mode, load, form,
                      (unsigned long)attachment, (unsigned long)format, (unsigned long)samples];
    NSMutableDictionary *row = [@{@"case":name, @"mode":mode, @"load":load, @"form":form,
        @"attachment":@(attachment), @"format":@(format), @"samples":@(samples)} mutableCopy];
    @try {
        BOOL lowered = [mode isEqual:@"lowered-control"];
        BOOL control = lowered || [mode isEqual:@"texture-control"], seeded = ![load isEqual:@"clear"];
        NSUInteger draws = [form isEqual:@"one"] ? 1 : 4;
        MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:16 height:16 mipmapped:NO];
        td.storageMode = MTLStorageModePrivate;
        td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
        td.sampleCount = samples;
        td.textureType = samples == 1 ? MTLTextureType2D : MTLTextureType2DMultisample;
        const char *subresource=getenv("FB_PROBE_SUBRESOURCE");
        if(subresource && !strcmp(subresource,"mip")){td.width=32;td.height=32;td.mipmapLevelCount=2;}
        if(subresource && !strcmp(subresource,"slice")){td.textureType=MTLTextureType2DArray;td.arrayLength=3;}
        if(subresource)td.usage|=MTLTextureUsagePixelFormatView;
        id<MTLTexture> a = [d newTextureWithDescriptor:td], b = control ? [d newTextureWithDescriptor:td] : nil;
        require(a != nil && (!control || b != nil), @"Texture allocation failed");
        NSString *fn = [NSString stringWithFormat:@"%@%lu", control && !lowered ? (samples == 1 ? @"texture" : @"multisample") : @"fetch", (unsigned long)attachment];
        MTLRenderPipelineReflection *reflection = nil;
        id<MTLRenderPipelineState> ps = pipeline(d, lib, fn, attachment, format, samples, lowered ? &reflection : NULL);
        NSUInteger textureIndex = 0;
        if (lowered) {
            BOOL found = NO;
            for (MTLArgument *arg in reflection.fragmentArguments) {
                if (arg.type == MTLArgumentTypeTexture && [arg.name isEqual:[NSString stringWithFormat:@"__fbcompat_fb_attachment_%lu", (unsigned long)attachment]]) {
                    textureIndex = arg.index; found = YES;
                }
            }
            require(found, @"Lowered attachment texture missing from pipeline reflection");
            row[@"reflected_texture_index"] = @(textureIndex);
        }
        id<MTLRenderPipelineState> writer = seeded ? pipeline(d, lib, [NSString stringWithFormat:@"initial%lu", (unsigned long)attachment], attachment, format, samples, NULL) : nil;
        uint16_t ix[] = {0,1,2,0,1,2,0,1,2,0,1,2,0,1,2};
        id<MTLBuffer> indices = [d newBufferWithBytes:ix length:sizeof(ix) options:MTLResourceStorageModeShared];
        id<MTLCommandQueue> q = [d newCommandQueue];
        id<MTLCommandBuffer> cb = getenv("FB_PROBE_UNRETAINED") ? [q commandBufferWithUnretainedReferences] : [q commandBuffer];
        require(cb != nil, @"Command buffer allocation failed");
        id<MTLRenderCommandEncoder> e = beginPass(cb,pass(a, attachment, MTLLoadActionClear));
        require(e != nil, @"Initial encoder allocation failed");
        if (seeded) {
            [e setRenderPipelineState:writer];
            [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        }
        if (control) {
            endPass(e);
            // Independent reference: each primitive reads a distinct, fully stored texture.
            for (NSUInteger i = 0; i < draws; ++i) {
                e = beginPass(cb,pass(b, attachment, MTLLoadActionDontCare));
                require(e != nil, @"Control encoder allocation failed");
                [e setRenderPipelineState:ps]; [e setFragmentTexture:readView(a) atIndex:textureIndex];
                [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
                endPass(e); id<MTLTexture> swap = a; a = b; b = swap;
            }
        } else {
            if ([load isEqual:@"load"]) {
                endPass(e);
                e = beginPass(cb,pass(a, attachment, MTLLoadActionLoad));
                require(e != nil, @"Load encoder allocation failed");
            }
            [e setRenderPipelineState:ps]; e=draw(e, form, indices,ps); endPass(e);
        }
        NSError *error = nil;
        id<MTLComputePipelineState> reader = [d newComputePipelineStateWithFunction:[lib newFunctionWithName:samples == 1 ? @"read_single" : @"read_samples"] error:&error];
        require(reader != nil, error.description ?: @"Readback pipeline failed");
        NSUInteger length = 16 * 16 * samples * 4 * sizeof(float);
        id<MTLBuffer> out = [d newBufferWithLength:length options:MTLResourceStorageModeShared];
        require(out != nil, @"Readback allocation failed");
        memset(out.contents, 0xcd, length);
        id<MTLComputeCommandEncoder> c = [cb computeCommandEncoder];
        require(c != nil, @"Readback encoder failed");
        __attribute__((objc_precise_lifetime)) id<MTLTexture> readable=readView(a);
        [c setComputePipelineState:reader]; [c setTexture:readable atIndex:0]; [c setBuffer:out offset:0 atIndex:0];
        [c dispatchThreadgroups:MTLSizeMake(2,2,1) threadsPerThreadgroup:MTLSizeMake(8,8,1)]; [c endEncoding];
        [cb commit]; [cb waitUntilCompleted];
        require(cb.status == MTLCommandBufferStatusCompleted, cb.error.description ?: @"GPU command failed");
        require([[NSData dataWithBytes:out.contents length:length] writeToFile:[output stringByAppendingPathComponent:[name stringByAppendingString:@".f32"]] atomically:YES], @"Saving pixels failed");
        float *actual = out.contents; NSUInteger bad = 0; float maxError = 0;
        for (NSUInteger y = 0; y < 16; ++y) for (NSUInteger x = 0; x < 16; ++x)
            for (NSUInteger s = 0; s < samples; ++s) for (NSUInteger ch = 0; ch < 4; ++ch) {
                float want = .125f * (ch + 1) + (seeded ? ((x + 3*y) % 4) / 32.f + s / 32.f : 0);
                want = quantize(want, format);
                for (NSUInteger k = 0; k < draws; ++k) {
                    float identity=0;
                    if(getenv("FB_PROBE_IDENTITIES")) {
                        NSUInteger baseVertex=[form hasPrefix:@"offset"]?3:0;
                        NSUInteger baseInstance=([form isEqual:@"offset-direct"] || [form isEqual:@"indexed-instanced"])?7:0;
                        NSUInteger primitive=([form isEqual:@"one"] || [form isEqual:@"draws"] || [form containsString:@"instanced"])?0:k;
                        identity=(baseVertex+baseInstance+primitive)/1024.f;
                    }
                    want = quantize(want + .0625f + s / 64.f + identity, format);
                }
                if (format == MTLPixelFormatRG16Float && ch >= 2) want = ch == 3 ? 1 : 0;
                float got = actual[((y*16+x)*samples+s)*4+ch], delta = fabsf(want - got);
                // UNORM -> float conversion can round by one ULP in the texture unit.
                float tolerance = format == MTLPixelFormatRGBA8Unorm ? 0x1p-23f : 0;
                if (!isfinite(got) || delta > tolerance) ++bad;
                maxError = fmaxf(maxError, delta);
            }
        row[@"status"] = bad ? @"mismatch" : @"pass";
        row[@"mismatched_components"] = @(bad); row[@"max_error"] = @(maxError);
        row[@"compared_components"] = @(16*16*samples*4);
    } @catch (NSException *exception) {
        row[@"status"] = @"error"; row[@"error"] = exception.reason ?: exception.name;
    }
    return row;
}
int main(int argc, const char **argv) { @autoreleasepool {
    if (argc < 4 || argc > 5) { fprintf(stderr, "usage: probe LIBRARY OUTPUT native|texture-control|lowered-control|all [1|4]\n"); return 2; }
    NSString *output = @(argv[2]), *mode = @(argv[3]);
    if (![@[@"native", @"texture-control", @"lowered-control", @"all"] containsObject:mode]) return 2;
    NSUInteger onlySamples = argc == 5 ? strtoul(argv[4], NULL, 10) : 0;
    if (argc == 5 && onlySamples != 1 && onlySamples != 4) return 2;
    NSError *error = nil;
    if (![[NSFileManager defaultManager] createDirectoryAtPath:output withIntermediateDirectories:NO attributes:nil error:&error]) {
        fprintf(stderr, "%s\n", error.description.UTF8String); return 2;
    }
    id<MTLDevice> d = MTLCreateSystemDefaultDevice();
    if (!d) return 2;
    id<MTLLibrary> lib;
    if(getenv("FB_PROBE_SOURCE")) {
        NSString *source=[NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:&error];
        MTLCompileOptions *options=[MTLCompileOptions new];options.languageVersion=MTLLanguageVersion3_0;
        options.preprocessorMacros=@{@"FB_FUNCTION_CONSTANTS":@1};
        options.fastMathEnabled=NO;
        lib=source?[d newLibraryWithSource:source options:options error:&error]:nil;
    } else lib = [d newLibraryWithURL:[NSURL fileURLWithPath:@(argv[1])] error:&error];
    if (!lib) { fprintf(stderr, "%s\n", error.description.UTF8String); return 2; }
    NSDictionary *identity = @{@"device":d.name, @"registry_id":@(d.registryID), @"boot":sysString("kern.bootsessionuuid"),
        @"os_build":sysString("kern.osversion"), @"library":@(argv[1]), @"pid":@(getpid())};
    [[NSJSONSerialization dataWithJSONObject:identity options:NSJSONWritingPrettyPrinted error:NULL]
        writeToFile:[output stringByAppendingPathComponent:@"identity.json"] atomically:YES];
    NSMutableArray *rows = [NSMutableArray new]; NSUInteger failed = 0;
    NSArray *modes = [mode isEqual:@"all"] ? @[@"native", @"texture-control"] : @[mode];
    for (NSString *m in modes) for (NSNumber *a in @[@0,@1,@2])
        for (NSNumber *f in @[@(MTLPixelFormatRGBA8Unorm),@(MTLPixelFormatRG16Float),@(MTLPixelFormatRGBA16Float),@(MTLPixelFormatRGBA32Float)])
        for (NSNumber *s in @[@1,@4]) for (NSString *load in @[@"clear",@"load",@"previous-draw"])
        for (NSString *form in @[@"one",@"draws",@"overlap",@"indexed",@"instanced",@"strip",@"indexed-strip",@"indexed-instanced",@"offset-indexed",@"offset-direct"]) { @autoreleasepool {
            if (![d supportsTextureSampleCount:s.unsignedIntegerValue]) continue;
            if(getenv("FB_PROBE_SUBRESOURCE") && s.unsignedIntegerValue!=1)continue;
            if(getenv("FB_PROBE_IDENTITIES") && (a.unsignedIntegerValue!=0 || f.unsignedIntegerValue!=MTLPixelFormatRGBA32Float))continue;
            if (onlySamples && s.unsignedIntegerValue != onlySamples) continue;
            NSDictionary *row = run(d, lib, output, m, load, form, a.unsignedIntegerValue, f.unsignedIntegerValue, s.unsignedIntegerValue);
            [rows addObject:row]; failed += ![row[@"status"] isEqual:@"pass"];
            printf("%s %s\n", [row[@"case"] UTF8String], [row[@"status"] UTF8String]); fflush(stdout);
            [[NSJSONSerialization dataWithJSONObject:rows options:NSJSONWritingPrettyPrinted error:NULL]
                writeToFile:[output stringByAppendingPathComponent:@"results.json"] atomically:YES];
        }}
    printf("cases=%lu failures=%lu\n", (unsigned long)rows.count, (unsigned long)failed);
    return failed ? 1 : 0;
}}
