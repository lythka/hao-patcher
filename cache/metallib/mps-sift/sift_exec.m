// sift_exec: run MPSImage's SIFT feature extraction on deterministic inputs and write raw outputs.
// Builds for the host's Apple GPU (arm64, original MPS) and for the HD 4000 (x86_64, linked with
// cache/shaders/src/replay.m's bridge so the private MetalOld compiler route works in process).
//
//   sift_exec api    OUT CASES.json      MPSImageSIFT through its own encode method (Apple's dispatch,
//                                        constants, arguments); keypoints + count + dispatch log + timings
//   sift_exec kernel OUT CASES.json LIB  one extraction kernel dispatched directly with MPS's own
//                                        recorded arguments (captured from an MPSImageSIFT encode on this
//                                        device) on identical pyramid inputs; keypoints + state + timings
//
//   sift_exec block  OUT CASES.json PROBE  block_probe.metal's 2x1 block reads on dumped DoG textures
//
// CASES.json is written by gen.py (inputs next to it). Every output is a raw file; results.json lists
// what ran, the functions MPS created (name and function constants) and GPU times.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <MetalPerformanceShaders/MetalPerformanceShaders.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <mach/mach_time.h>

#define MSG(T, ...) ((T)objc_msgSend)(__VA_ARGS__)

static BOOL sameMultiset(NSArray *a, NSArray *b) {
  NSCountedSet *x = [NSCountedSet setWithArray:a], *y = [NSCountedSet setWithArray:b];
  if (a.count != b.count || x.count != y.count) return NO;
  for (NSData *record in x) if ([x countForObject:record] != [y countForObject:record]) return NO;
  return YES;
}

static NSMutableDictionary *gFunctionKeys;   // function pointer -> "name|constants"
static NSMutableDictionary *gFunctionRaw;    // "name|constants" -> the constant values' own description
static NSMutableDictionary *gPipelineKeys;   // pipeline pointer -> function key
static NSMutableArray *gDispatches;          // dispatch records of the current encode
static NSMutableDictionary *gPending;        // bindings since the last dispatch
static NSMutableArray *gSamplers;
static NSString *gCurrentPipeline;
static BOOL gRecord;
static NSMutableDictionary *gTextures;       // pointer -> texture bound while recording (pyramid dumps)

static NSString *ptr(id o) { return [NSString stringWithFormat:@"%p", (__bridge void *)o]; }
static NSString *hex(const void *p, NSUInteger n) {
  NSMutableString *s = [NSMutableString stringWithCapacity:n * 2];
  for (NSUInteger i = 0; i < n; i++) [s appendFormat:@"%02x", ((const unsigned char *)p)[i]];
  return s;
}
static NSString *constants(MTLFunctionConstantValues *v) {
  if (!v) return @"";
  // MTLFunctionConstantValuesInternal describes itself as "index: N; type: T; value: (V)" lines.
  NSMutableArray *parts = [NSMutableArray array];
  NSRegularExpression *re = [NSRegularExpression regularExpressionWithPattern:@"index: (\\d+); type: MTLDataType(\\w+); value: \\(([^)]*)\\)" options:0 error:nil];
  NSString *d = v.description;
  for (NSTextCheckingResult *m in [re matchesInString:d options:0 range:NSMakeRange(0, d.length)])
    [parts addObject:[NSString stringWithFormat:@"%@.%@=%@", [d substringWithRange:[m rangeAtIndex:1]], [d substringWithRange:[m rangeAtIndex:2]], [d substringWithRange:[m rangeAtIndex:3]]]];
  return [parts componentsJoinedByString:@";"];
}

static void swz(Class c, SEL s, IMP n, IMP *o) {
  // One saved original per selector: a second class with a different implementation is not hooked.
  Method m = class_getInstanceMethod(c, s);
  if (!m || method_getImplementation(m) == n) return;
  if (*o) { fprintf(stderr, "[hook] %s on %s not hooked (second implementation)\n", sel_getName(s), class_getName(c)); return; }
  *o = method_setImplementation(m, n);
}
static IMP o_fncv, o_fndesc, o_pso1, o_pso2, o_pso3, o_samp, o_setpso, o_setbuf, o_setbytes, o_settex, o_setsamp, o_disp, o_dispthreads, o_tgmem;
static id h_fncv(id s, SEL c, NSString *n, MTLFunctionConstantValues *v, NSError **e) {
  id f = ((id (*)(id, SEL, id, id, NSError **))o_fncv)(s, c, n, v, e);
  if (f) { NSString *k = [NSString stringWithFormat:@"%@|%@", n, constants(v)]; gFunctionKeys[ptr(f)] = k; gFunctionRaw[k] = v.description ?: @""; }
  return f;
}
static id h_fndesc(id s, SEL c, MTLFunctionDescriptor *d, NSError **e) {
  id f = ((id (*)(id, SEL, id, NSError **))o_fndesc)(s, c, d, e);
  if (f) { NSString *k = [NSString stringWithFormat:@"%@|%@", d.name, constants(d.constantValues)]; gFunctionKeys[ptr(f)] = k; gFunctionRaw[k] = d.constantValues.description ?: @""; }
  return f;
}
static NSString *fkey(id<MTLFunction> f) { return gFunctionKeys[ptr(f)] ?: [NSString stringWithFormat:@"%@|?", f.name]; }
static id h_pso1(id s, SEL c, id f, NSError **e) {
  id p = ((id (*)(id, SEL, id, NSError **))o_pso1)(s, c, f, e);
  if (p) gPipelineKeys[ptr(p)] = fkey(f);
  return p;
}
static id h_pso2(id s, SEL c, id f, NSUInteger o, id *r, NSError **e) {
  id p = ((id (*)(id, SEL, id, NSUInteger, id *, NSError **))o_pso2)(s, c, f, o, r, e);
  if (p) gPipelineKeys[ptr(p)] = fkey(f);
  return p;
}
static id h_pso3(id s, SEL c, MTLComputePipelineDescriptor *d, NSUInteger o, id *r, NSError **e) {
  id p = ((id (*)(id, SEL, id, NSUInteger, id *, NSError **))o_pso3)(s, c, d, o, r, e);
  if (p) gPipelineKeys[ptr(p)] = fkey(d.computeFunction);
  return p;
}
static NSDictionary *samplerDict(MTLSamplerDescriptor *d) {
  return @{@"normalized": @(d.normalizedCoordinates), @"s": @(d.sAddressMode), @"t": @(d.tAddressMode), @"r": @(d.rAddressMode),
           @"min": @(d.minFilter), @"mag": @(d.magFilter), @"mip": @(d.mipFilter), @"lodMin": @(d.lodMinClamp),
           @"lodMax": @(d.lodMaxClamp), @"aniso": @(d.maxAnisotropy), @"compare": @(d.compareFunction),
           @"border": @(d.borderColor), @"argbuf": @(d.supportArgumentBuffers), @"lodAverage": @(d.lodAverage)};
}
static NSMutableDictionary *gSamplerDescs;
static id h_samp(id s, SEL c, MTLSamplerDescriptor *d) {
  id r = ((id (*)(id, SEL, id))o_samp)(s, c, d);
  if (r) gSamplerDescs[ptr(r)] = samplerDict(d);
  return r;
}
static void h_setpso(id s, SEL c, id p) {
  if (gRecord) { gCurrentPipeline = gPipelineKeys[ptr(p)] ?: @"?"; gPending = [NSMutableDictionary dictionary]; }
  ((void (*)(id, SEL, id))o_setpso)(s, c, p);
}
static void h_setbuf(id s, SEL c, id<MTLBuffer> b, NSUInteger off, NSUInteger i) {
  if (gRecord) gPending[[NSString stringWithFormat:@"buffer%lu", i]] = @{@"length": @(b.length), @"offset": @(off), @"id": ptr(b)};
  ((void (*)(id, SEL, id, NSUInteger, NSUInteger))o_setbuf)(s, c, b, off, i);
}
static void h_setbytes(id s, SEL c, const void *p, NSUInteger l, NSUInteger i) {
  if (gRecord) gPending[[NSString stringWithFormat:@"bytes%lu", i]] = hex(p, l);
  ((void (*)(id, SEL, const void *, NSUInteger, NSUInteger))o_setbytes)(s, c, p, l, i);
}
static void h_settex(id s, SEL c, id<MTLTexture> t, NSUInteger i) {
  if (gRecord) {
    gPending[[NSString stringWithFormat:@"texture%lu", i]] = t ? @{@"format": @(t.pixelFormat), @"type": @(t.textureType), @"width": @(t.width), @"height": @(t.height), @"array": @(t.arrayLength), @"mips": @(t.mipmapLevelCount), @"id": ptr(t)} : @{};
    if (t && gTextures) gTextures[ptr(t)] = t;
  }
  ((void (*)(id, SEL, id, NSUInteger))o_settex)(s, c, t, i);
}
static void h_setsamp(id s, SEL c, id t, NSUInteger i) {
  if (gRecord) gPending[[NSString stringWithFormat:@"sampler%lu", i]] = gSamplerDescs[ptr(t)] ?: @{@"unknown": ptr(t)};
  ((void (*)(id, SEL, id, NSUInteger))o_setsamp)(s, c, t, i);
}
static void h_tgmem(id s, SEL c, NSUInteger l, NSUInteger i) {
  if (gRecord) gPending[[NSString stringWithFormat:@"tgmem%lu", i]] = @(l);
  ((void (*)(id, SEL, NSUInteger, NSUInteger))o_tgmem)(s, c, l, i);
}
static void record(NSString *kind, MTLSize g, MTLSize t) {
  NSMutableDictionary *d = [gPending mutableCopy] ?: [NSMutableDictionary dictionary];
  d[@"function"] = gCurrentPipeline ?: @"?";
  d[@"kind"] = kind;
  d[@"grid"] = @[@(g.width), @(g.height), @(g.depth)];
  d[@"threads"] = @[@(t.width), @(t.height), @(t.depth)];
  [gDispatches addObject:d];
}
static void h_disp(id s, SEL c, MTLSize g, MTLSize t) {
  if (gRecord) record(@"threadgroups", g, t);
  ((void (*)(id, SEL, MTLSize, MTLSize))o_disp)(s, c, g, t);
}
static void h_dispthreads(id s, SEL c, MTLSize g, MTLSize t) {
  if (gRecord) record(@"threads", g, t);
  ((void (*)(id, SEL, MTLSize, MTLSize))o_dispthreads)(s, c, g, t);
}

static void install(id<MTLDevice> d, id<MTLCommandQueue> q) {
  gFunctionKeys = [NSMutableDictionary dictionary]; gFunctionRaw = [NSMutableDictionary dictionary]; gPipelineKeys = [NSMutableDictionary dictionary];
  gSamplerDescs = [NSMutableDictionary dictionary]; gDispatches = [NSMutableArray array];
  NSError *e = nil;
  id<MTLLibrary> l = [d newLibraryWithSource:@"kernel void k(){}" options:nil error:&e];
  Class lc = l ? object_getClass(l) : objc_getClass("_MTLLibrary");
  swz(lc, @selector(newFunctionWithName:constantValues:error:), (IMP)h_fncv, &o_fncv);
  swz(lc, @selector(newFunctionWithDescriptor:error:), (IMP)h_fndesc, &o_fndesc);
  Class dc = object_getClass(d);
  swz(dc, @selector(newComputePipelineStateWithFunction:error:), (IMP)h_pso1, &o_pso1);
  swz(dc, @selector(newComputePipelineStateWithFunction:options:reflection:error:), (IMP)h_pso2, &o_pso2);
  swz(dc, @selector(newComputePipelineStateWithDescriptor:options:reflection:error:), (IMP)h_pso3, &o_pso3);
  swz(dc, @selector(newSamplerStateWithDescriptor:), (IMP)h_samp, &o_samp);
  NSMutableSet *classes = [NSMutableSet set];
  for (int k = 0; k < 2; k++) {
    id<MTLCommandBuffer> b = [q commandBuffer];
    id<MTLComputeCommandEncoder> c = k ? [b computeCommandEncoderWithDispatchType:MTLDispatchTypeConcurrent] : [b computeCommandEncoder];
    [classes addObject:NSStringFromClass(object_getClass(c))]; [c endEncoding];
  }
  for (NSString *n in classes) {
    Class ec = NSClassFromString(n);
    swz(ec, @selector(setComputePipelineState:), (IMP)h_setpso, &o_setpso);
    swz(ec, @selector(setBuffer:offset:atIndex:), (IMP)h_setbuf, &o_setbuf);
    swz(ec, @selector(setBytes:length:atIndex:), (IMP)h_setbytes, &o_setbytes);
    swz(ec, @selector(setTexture:atIndex:), (IMP)h_settex, &o_settex);
    swz(ec, @selector(setSamplerState:atIndex:), (IMP)h_setsamp, &o_setsamp);
    swz(ec, @selector(dispatchThreadgroups:threadsPerThreadgroup:), (IMP)h_disp, &o_disp);
    swz(ec, @selector(dispatchThreads:threadsPerThreadgroup:), (IMP)h_dispthreads, &o_dispthreads);
    swz(ec, @selector(setThreadgroupMemoryLength:atIndex:), (IMP)h_tgmem, &o_tgmem);
  }
}

static double now(void) {
  static mach_timebase_info_data_t tb;
  if (!tb.denom) mach_timebase_info(&tb);
  return (double)mach_absolute_time() * tb.numer / tb.denom / 1e9;
}
static NSData *load(NSString *dir, NSString *name) {
  NSData *d = [NSData dataWithContentsOfFile:[dir stringByAppendingPathComponent:name]];
  if (!d) { fprintf(stderr, "missing input %s\n", name.UTF8String); exit(3); }
  return d;
}
static id newSIFT(id<MTLDevice> d, NSDictionary *c) {
  id desc = [NSClassFromString(@"MPSImageSIFTDescriptor") new];
  if (c[@"layersPerOctave"]) MSG(void (*)(id, SEL, NSUInteger), desc, @selector(setLayersPerOctave:), [c[@"layersPerOctave"] unsignedIntegerValue]);
  if (c[@"octaves"]) MSG(void (*)(id, SEL, NSUInteger), desc, @selector(setOctaves:), [c[@"octaves"] unsignedIntegerValue]);
  if (c[@"maximumKeyPoints"]) MSG(void (*)(id, SEL, NSUInteger), desc, @selector(setMaximumKeyPoints:), [c[@"maximumKeyPoints"] unsignedIntegerValue]);
  id s = MSG(id (*)(id, SEL, id, id), [NSClassFromString(@"MPSImageSIFT") alloc], @selector(initWithDevice:descriptor:), d, desc);
  MSG(void (*)(id, SEL, BOOL), s, @selector(setFusedKeypointOrientation:), [c[@"fused"] boolValue]);
  MSG(void (*)(id, SEL, BOOL), s, @selector(setUseCompressedTexture:), [c[@"compressed"] boolValue]);
  return s;
}
static id<MTLTexture> sourceTexture(id<MTLDevice> d, NSDictionary *c, NSString *dir) {
  NSUInteger w = [c[@"width"] unsignedIntegerValue], h = [c[@"height"] unsignedIntegerValue], bpp = [c[@"bytesPerPixel"] unsignedIntegerValue];
  MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:[c[@"format"] unsignedIntegerValue] width:w height:h mipmapped:NO];
  td.usage = MTLTextureUsageShaderRead; td.storageMode = MTLStorageModeShared;
  id<MTLTexture> t = [d newTextureWithDescriptor:td];
  NSData *px = load(dir, c[@"source"]);
  if (px.length != w * h * bpp) { fprintf(stderr, "bad source size\n"); exit(3); }
  [t replaceRegion:MTLRegionMake2D(0, 0, w, h) mipmapLevel:0 withBytes:px.bytes bytesPerRow:w * bpp];
  return t;
}
static NSDictionary *gpuTime(id<MTLCommandBuffer> b) {
  return @{@"status": @(b.status), @"gpu_s": @(b.GPUEndTime - b.GPUStartTime), @"error": b.error ? b.error.description : [NSNull null]};
}
static void writeOut(NSString *out, NSString *name, const void *p, NSUInteger n) {
  [[NSData dataWithBytes:p length:n] writeToFile:[out stringByAppendingPathComponent:name] atomically:YES];
}
static NSUInteger keypointBytes(id<MTLBuffer> count, NSUInteger stride, NSUInteger capacity) {
  uint32_t n = *(uint32_t *)count.contents;
  return MIN((NSUInteger)n, capacity) * stride;
}

enum { kStride = 36, kCapacity = 20000 };

// Copy one bound texture (all slices, mip 0) to a file after the command buffer that wrote it completed.
static NSUInteger dumpTexture(id<MTLDevice> d, id<MTLCommandQueue> q, id<MTLTexture> t, NSString *path) {
  NSUInteger bpp = 2, row = t.width * bpp, image = row * t.height, layers = t.arrayLength;
  id<MTLBuffer> b = [d newBufferWithLength:image * layers options:MTLResourceStorageModeShared];
  id<MTLCommandBuffer> cb = [q commandBuffer];
  id<MTLBlitCommandEncoder> be = [cb blitCommandEncoder];
  for (NSUInteger l = 0; l < layers; l++)
    [be copyFromTexture:t sourceSlice:l sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(t.width, t.height, 1)
               toBuffer:b destinationOffset:l * image destinationBytesPerRow:row destinationBytesPerImage:image];
  [be endEncoding]; [cb commit]; [cb waitUntilCompleted];
  [[NSData dataWithBytes:b.contents length:b.length] writeToFile:path atomically:YES];
  return b.length;
}

static int api(NSString *out, NSArray *cases, NSString *dir, id<MTLDevice> d, id<MTLCommandQueue> q) {
  NSMutableArray *results = [NSMutableArray array];
  for (NSDictionary *c in cases) {
    @autoreleasepool {
      NSString *name = c[@"name"]; NSUInteger repeats = [c[@"repeats"] unsignedIntegerValue] ?: 5;
      fprintf(stderr, "CASE api %s\n", name.UTF8String);
      id<MTLTexture> src = sourceTexture(d, c, dir);
      double t0 = now(); id sift = newSIFT(d, c); double tinit = now() - t0;
      id<MTLBuffer> kp = [d newBufferWithLength:kStride * kCapacity options:MTLResourceStorageModeShared];
      id<MTLBuffer> cnt = [d newBufferWithLength:16 options:MTLResourceStorageModeShared];
      NSMutableArray *runs = [NSMutableArray array]; NSArray *dispatches = nil; NSData *first = nil; BOOL stable = YES;
      for (NSUInteger r = 0; r < repeats; r++) {
        memset(kp.contents, 0xAB, kp.length); memset(cnt.contents, 0xAB, cnt.length);
        gDispatches = [NSMutableArray array]; gRecord = (r == 0);
        gTextures = (r == 0 && [c[@"dumpPyramids"] boolValue]) ? [NSMutableDictionary dictionary] : nil;
        id<MTLCommandBuffer> b = [q commandBuffer];
        double e0 = now();
        MSG(void (*)(id, SEL, id, id, id, NSUInteger, id, NSUInteger), sift,
            @selector(encodeToCommandBuffer:sourceTexture:keypointsBuffer:keypointBufferOffset:keypointCountBuffer:keypointCountBufferOffset:),
            b, src, kp, 0, cnt, 0);
        double encode = now() - e0; gRecord = NO;
        [b commit]; [b waitUntilCompleted];
        NSMutableDictionary *row = [gpuTime(b) mutableCopy]; row[@"encode_s"] = @(encode); row[@"wall_s"] = @(now() - e0);
        uint32_t n = *(uint32_t *)cnt.contents; row[@"count"] = @(n);
        [runs addObject:row];
        if (r == 0) {
          dispatches = gDispatches;
          first = [NSData dataWithBytes:kp.contents length:keypointBytes(cnt, kStride, kCapacity)];
          writeOut(out, [name stringByAppendingString:@".keypoints"], first.bytes, first.length);
          writeOut(out, [name stringByAppendingString:@".count"], cnt.contents, 4);
          NSUInteger octave = 0;
          for (NSMutableDictionary *x in gDispatches) {
            if (!gTextures || ![x[@"function"] hasPrefix:@"ExtractFeaturePoints"]) continue;
            for (NSString *slot in @[@"texture0", @"texture1"]) {
              id<MTLTexture> t = gTextures[x[slot][@"id"]];
              if (!t || (t.pixelFormat != MTLPixelFormatR16Unorm && t.pixelFormat != MTLPixelFormatR16Snorm)) continue;
              NSString *file = [NSString stringWithFormat:@"%@.o%lu.%@.bin", name, (unsigned long)octave, slot];
              dumpTexture(d, q, t, [out stringByAppendingPathComponent:file]);
              x[[slot stringByAppendingString:@"_dump"]] = file;
            }
            octave++;
          }
          gTextures = nil;
        } else {
          // Atomic appends may reorder; compare as a multiset of 36-byte records.
          NSData *now_ = [NSData dataWithBytes:kp.contents length:keypointBytes(cnt, kStride, kCapacity)];
          if (now_.length != first.length) stable = NO;
          else {
            NSMutableArray *a = [NSMutableArray array], *bb = [NSMutableArray array];
            for (NSUInteger i = 0; i < first.length; i += kStride) {
              [a addObject:[first subdataWithRange:NSMakeRange(i, kStride)]];
              [bb addObject:[now_ subdataWithRange:NSMakeRange(i, kStride)]];
            }
            if (!sameMultiset(a, bb)) stable = NO;
          }
        }
        if (b.status != MTLCommandBufferStatusCompleted) break;
      }
      [results addObject:@{@"case": name, @"mode": @"api", @"init_s": @(tinit), @"runs": runs, @"repeat_identical": @(stable), @"dispatches": dispatches ?: @[]}];
      // A failed command buffer on the HD 4000 is a GPU stall of seconds on a shared desktop: stop at the first.
      if ([[runs.lastObject objectForKey:@"status"] integerValue] != MTLCommandBufferStatusCompleted) {
        fprintf(stderr, "STOP api: %s failed\n", name.UTF8String);
        [[NSJSONSerialization dataWithJSONObject:results options:NSJSONWritingPrettyPrinted error:nil] writeToFile:[out stringByAppendingPathComponent:@"api-results.json"] atomically:YES];
        return 1;
      }
      [[NSJSONSerialization dataWithJSONObject:results options:NSJSONWritingPrettyPrinted error:nil] writeToFile:[out stringByAppendingPathComponent:@"api-results.json"] atomically:YES];
      [[NSJSONSerialization dataWithJSONObject:gFunctionRaw options:NSJSONWritingPrettyPrinted error:nil] writeToFile:[out stringByAppendingPathComponent:@"functions.json"] atomically:YES];
    }
  }
  return 0;
}

static NSData *unhex(NSString *s) {
  NSMutableData *d = [NSMutableData dataWithLength:s.length / 2];
  for (NSUInteger i = 0; i < d.length; i++) ((unsigned char *)d.mutableBytes)[i] = (unsigned char)strtoul([[s substringWithRange:NSMakeRange(i * 2, 2)] UTF8String], NULL, 16);
  return d;
}

// Capture MPS's own dispatches for one source image on this device; return the octave-th dispatch of a
// function whose name starts with prefix (MPS dispatches the extraction kernel once per octave).
static NSMutableDictionary *gCaptures;       // capture source -> {all, status}: one MPS encode per source
static NSDictionary *captureDispatch(id<MTLDevice> d, id<MTLCommandQueue> q, NSDictionary *c, NSString *dir, NSString *prefix, NSUInteger octave) {
  NSDictionary *cached = gCaptures[c[@"source"]];
  if (cached) {
    NSUInteger k = 0;
    for (NSDictionary *x in cached[@"all"])
      if ([x[@"function"] hasPrefix:prefix] && k++ == octave) return @{@"dispatch": x, @"all": cached[@"all"], @"status": cached[@"status"]};
    return cached;
  }
  id<MTLTexture> src = sourceTexture(d, c, dir);
  id sift = newSIFT(d, c);
  id<MTLBuffer> kp = [d newBufferWithLength:kStride * kCapacity options:MTLResourceStorageModeShared];
  id<MTLBuffer> cnt = [d newBufferWithLength:16 options:MTLResourceStorageModeShared];
  gDispatches = [NSMutableArray array]; gRecord = YES;
  id<MTLCommandBuffer> b = [q commandBuffer];
  MSG(void (*)(id, SEL, id, id, id, NSUInteger, id, NSUInteger), sift,
      @selector(encodeToCommandBuffer:sourceTexture:keypointsBuffer:keypointBufferOffset:keypointCountBuffer:keypointCountBufferOffset:),
      b, src, kp, 0, cnt, 0);
  gRecord = NO; [b commit]; [b waitUntilCompleted];
  if (!gCaptures) gCaptures = [NSMutableDictionary dictionary];
  gCaptures[c[@"source"]] = @{@"all": gDispatches, @"status": @(b.status)};
  NSUInteger k = 0;
  for (NSDictionary *x in gDispatches)
    if ([x[@"function"] hasPrefix:prefix] && k++ == octave) return @{@"dispatch": x, @"all": gDispatches, @"status": @(b.status)};
  return @{@"all": gDispatches, @"status": @(b.status)};
}

static MTLFunctionConstantValues *parseConstants(NSString *s) {
  MTLFunctionConstantValues *v = [MTLFunctionConstantValues new];
  for (NSString *part in [s componentsSeparatedByString:@";"]) {
    if (!part.length) continue;
    NSArray *kv = [part componentsSeparatedByString:@"="]; NSArray *it = [kv[0] componentsSeparatedByString:@"."];
    NSUInteger index = [it[0] integerValue]; NSString *type = it[1];
    if ([type isEqual:@"UInt"]) { uint32_t x = (uint32_t)strtoul([kv[1] UTF8String], NULL, 10); [v setConstantValue:&x type:MTLDataTypeUInt atIndex:index]; }
    else if ([type isEqual:@"Int"]) { int32_t x = (int32_t)strtol([kv[1] UTF8String], NULL, 10); [v setConstantValue:&x type:MTLDataTypeInt atIndex:index]; }
    else if ([type isEqual:@"Bool"]) { BOOL x = [kv[1] intValue] != 0; [v setConstantValue:&x type:MTLDataTypeBool atIndex:index]; }
    else { fprintf(stderr, "constant type %s\n", type.UTF8String); exit(3); }
  }
  return v;
}

static id<MTLTexture> arrayTexture(id<MTLDevice> d, MTLPixelFormat f, NSUInteger w, NSUInteger h, NSUInteger layers, NSData *px) {
  MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:f width:w height:h mipmapped:NO];
  td.textureType = MTLTextureType2DArray; td.arrayLength = layers; td.usage = MTLTextureUsageShaderRead; td.storageMode = MTLStorageModeShared;
  id<MTLTexture> t = [d newTextureWithDescriptor:td];
  if (px.length != w * h * layers * 2) { fprintf(stderr, "bad pyramid size\n"); exit(3); }
  for (NSUInteger l = 0; l < layers; l++)
    [t replaceRegion:MTLRegionMake2D(0, 0, w, h) mipmapLevel:0 slice:l withBytes:(const char *)px.bytes + l * w * h * 2 bytesPerRow:w * 2 bytesPerImage:w * h * 2];
  return t;
}

static int kernel(NSString *out, NSArray *cases, NSString *dir, NSString *libPath, id<MTLDevice> d, id<MTLCommandQueue> q) {
  NSError *e = nil;
  double l0 = now();
  id<MTLLibrary> lib = [d newLibraryWithURL:[NSURL fileURLWithPath:libPath] error:&e];
  if (!lib) { fprintf(stderr, "library: %s\n", e.description.UTF8String); return 3; }
  double tlib = now() - l0;
  NSMutableArray *results = [NSMutableArray array];
  NSMutableDictionary *pipelines = [NSMutableDictionary dictionary];
  NSMutableSet *failed = [NSMutableSet set];
  for (NSDictionary *c in cases) {
    @autoreleasepool {
      NSString *name = c[@"name"], *function = c[@"function"];
      NSUInteger repeats = [c[@"repeats"] unsignedIntegerValue] ?: 20;
      fprintf(stderr, "CASE kernel %s %s\n", name.UTF8String, function.UTF8String);
      NSMutableDictionary *row = [@{@"case": name, @"mode": @"kernel", @"function": function, @"library_s": @(tlib)} mutableCopy];
      // MPS's own arguments for this size: grid, threadgroup, parameters, sampler and constants.
      NSDictionary *cap = c[@"recordedDispatch"] ?
        @{@"dispatch": c[@"recordedDispatch"], @"all": @[], @"status": @(MTLCommandBufferStatusCompleted)} :
        captureDispatch(d, q, c[@"capture"], dir, c[@"captureFunction"] ?: function, [c[@"octave"] unsignedIntegerValue]);
      NSDictionary *x = cap[@"dispatch"];
      row[@"capture_status"] = cap[@"status"];
      if ([cap[@"status"] integerValue] != MTLCommandBufferStatusCompleted) {
        fprintf(stderr, "STOP kernel: MPS capture for %s failed\n", name.UTF8String);
        row[@"error"] = @"capture failed"; [results addObject:row]; break;
      }
      row[@"captured_functions"] = [[NSOrderedSet orderedSetWithArray:[cap[@"all"] valueForKey:@"function"]] array];
      if (!x) {
        // MPS on this device does not dispatch this kernel: use the recorded fallback (the Apple reference's).
        x = c[@"fallback"]; row[@"arguments"] = @"fallback";
        if (!x) { row[@"error"] = @"MPS did not dispatch this kernel and no fallback was given"; [results addObject:row]; continue; }
      } else row[@"arguments"] = @"captured";
      row[@"dispatch"] = x;
      NSString *fc = c[@"constants"] ?: [[x[@"function"] componentsSeparatedByString:@"|"] lastObject];
      row[@"constants"] = fc;
      NSString *pkey = [NSString stringWithFormat:@"%@|%@", function, fc];
      // A failed command buffer on the HD 4000 is a GPU stall of seconds: skip the rest of that specialization.
      if ([failed containsObject:pkey]) { row[@"error"] = @"skipped: this specialization failed earlier"; [results addObject:row]; continue; }
      id<MTLComputePipelineState> p = pipelines[pkey];
      if (!p) {
        double p0 = now();
        id<MTLFunction> f = [lib newFunctionWithName:function constantValues:parseConstants(fc) error:&e];
        if (f) p = [d newComputePipelineStateWithFunction:f error:&e];
        row[@"pipeline_s"] = @(now() - p0); row[@"pipeline_cold"] = @YES;
        if (!p) { row[@"error"] = e.description ?: @"pipeline"; [results addObject:row]; continue; }
        pipelines[pkey] = p;
      } else row[@"pipeline_cold"] = @NO;
      row[@"threadExecutionWidth"] = @(p.threadExecutionWidth); row[@"maxThreads"] = @(p.maxTotalThreadsPerThreadgroup);
      NSUInteger w = [c[@"width"] unsignedIntegerValue], h = [c[@"height"] unsignedIntegerValue];
      id<MTLTexture> dog = arrayTexture(d, [c[@"dogFormat"] unsignedIntegerValue], w, h, [c[@"dogLayers"] unsignedIntegerValue], load(dir, c[@"dog"]));
      id<MTLTexture> gauss = arrayTexture(d, [c[@"gaussFormat"] unsignedIntegerValue], w, h, [c[@"gaussLayers"] unsignedIntegerValue], load(dir, c[@"gauss"]));
      NSDictionary *sd = x[@"sampler0"];
      MTLSamplerDescriptor *s = [MTLSamplerDescriptor new];
      s.normalizedCoordinates = [sd[@"normalized"] boolValue]; s.sAddressMode = [sd[@"s"] unsignedIntegerValue];
      s.tAddressMode = [sd[@"t"] unsignedIntegerValue]; s.rAddressMode = [sd[@"r"] unsignedIntegerValue];
      s.minFilter = [sd[@"min"] unsignedIntegerValue]; s.magFilter = [sd[@"mag"] unsignedIntegerValue]; s.mipFilter = [sd[@"mip"] unsignedIntegerValue];
      s.lodMinClamp = [sd[@"lodMin"] floatValue]; s.lodMaxClamp = [sd[@"lodMax"] floatValue];
      id<MTLSamplerState> sampler = [d newSamplerStateWithDescriptor:s];
      // Parameters: this device's own MPS capture; the reference's are kept for comparison.
      NSData *params = unhex(x[@"bytes0"]);
      if (c[@"params"]) row[@"params_equal_reference"] = @([x[@"bytes0"] isEqual:c[@"params"]]);
      id<MTLBuffer> kp = [d newBufferWithLength:kStride * kCapacity options:MTLResourceStorageModeShared];
      id<MTLBuffer> state = [d newBufferWithLength:64 options:MTLResourceStorageModeShared];
      MTLSize grid = MTLSizeMake([x[@"grid"][0] unsignedIntegerValue], [x[@"grid"][1] unsignedIntegerValue], [x[@"grid"][2] unsignedIntegerValue]);
      MTLSize tg = MTLSizeMake([x[@"threads"][0] unsignedIntegerValue], [x[@"threads"][1] unsignedIntegerValue], [x[@"threads"][2] unsignedIntegerValue]);
      row[@"executed_grid"] = @[@(grid.width), @(grid.height), @(grid.depth)];
      row[@"executed_threads"] = @[@(tg.width), @(tg.height), @(tg.depth)];
      NSMutableArray *runs = [NSMutableArray array]; NSData *first = nil; BOOL stable = YES;
      for (NSUInteger r = 0; r < repeats; r++) {
        memset(kp.contents, 0xAB, kp.length); memset(state.contents, 0, state.length);
        id<MTLCommandBuffer> b = [q commandBuffer];
        id<MTLComputeCommandEncoder> ce = [b computeCommandEncoder];
        [ce setComputePipelineState:p];
        [ce setTexture:dog atIndex:0]; [ce setTexture:gauss atIndex:1];
        [ce setBytes:params.bytes length:params.length atIndex:0];
        [ce setBuffer:kp offset:0 atIndex:1]; [ce setBuffer:state offset:0 atIndex:2];
        if (x[@"tgmem0"]) [ce setThreadgroupMemoryLength:[x[@"tgmem0"] unsignedIntegerValue] atIndex:0];
        [ce setSamplerState:sampler atIndex:0];
        if ([x[@"kind"] isEqual:@"threads"]) [ce dispatchThreads:grid threadsPerThreadgroup:tg];
        else [ce dispatchThreadgroups:grid threadsPerThreadgroup:tg];
        [ce endEncoding];
        double e0 = now(); [b commit]; [b waitUntilCompleted];
        NSMutableDictionary *t = [gpuTime(b) mutableCopy]; t[@"wall_s"] = @(now() - e0); t[@"count"] = @(*(uint32_t *)state.contents);
        [runs addObject:t];
        if (b.status != MTLCommandBufferStatusCompleted) { [failed addObject:pkey]; break; }
        // The first word of the 64-byte state buffer is the keypoint counter MPS's initialize kernel clears.
        NSUInteger n = MIN((NSUInteger)*(uint32_t *)state.contents, (NSUInteger)kCapacity) * kStride;
        NSData *cur = [NSData dataWithBytes:kp.contents length:n];
        if (r == 0) {
          first = cur;
          writeOut(out, [name stringByAppendingString:@".keypoints"], cur.bytes, cur.length);
          writeOut(out, [name stringByAppendingString:@".state"], state.contents, state.length);
        } else if (cur.length != first.length) stable = NO;
        else {
          NSMutableArray *a = [NSMutableArray array], *bb = [NSMutableArray array];
          for (NSUInteger i = 0; i < first.length; i += kStride) {
            [a addObject:[first subdataWithRange:NSMakeRange(i, kStride)]];
            [bb addObject:[cur subdataWithRange:NSMakeRange(i, kStride)]];
          }
          if (!sameMultiset(a, bb)) stable = NO;
        }
      }
      row[@"runs"] = runs; row[@"repeat_identical"] = @(stable);
      [results addObject:row];
      [[NSJSONSerialization dataWithJSONObject:results options:NSJSONWritingPrettyPrinted error:nil] writeToFile:[out stringByAppendingPathComponent:@"kernel-results.json"] atomically:YES];
      [[NSJSONSerialization dataWithJSONObject:gFunctionRaw options:NSJSONWritingPrettyPrinted error:nil] writeToFile:[out stringByAppendingPathComponent:@"functions.json"] atomically:YES];
    }
  }
  [[NSJSONSerialization dataWithJSONObject:results options:NSJSONWritingPrettyPrinted error:nil]
    writeToFile:[out stringByAppendingPathComponent:@"kernel-results.json"] atomically:YES];
  for (NSDictionary *row in results) {
    if (row[@"error"] || ![row[@"runs"] count]) return 1;
    for (NSDictionary *run in row[@"runs"])
      if ([run[@"status"] integerValue] != MTLCommandBufferStatusCompleted ||
          [run[@"count"] unsignedIntegerValue] > kCapacity) return 1;
  }
  return 0;
}

// The kernel's block reads in isolation (block_probe.metal) on a dumped MPS DoG texture: every coordinate
// from (-3,-4) to (W+2,H+1), every layer, the four row offsets; outputs six float4 per coordinate.
static int block(NSString *out, NSArray *cases, NSString *dir, NSString *libPath, id<MTLDevice> d, id<MTLCommandQueue> q) {
  NSError *e = nil;
  id<MTLLibrary> lib = [d newLibraryWithURL:[NSURL fileURLWithPath:libPath] error:&e];
  if (!lib) { fprintf(stderr, "library: %s\n", e.description.UTF8String); return 3; }
  double p0 = now();
  id<MTLComputePipelineState> p = [d newComputePipelineStateWithFunction:[lib newFunctionWithName:@"probe"] error:&e];
  if (!p) { fprintf(stderr, "pipeline: %s\n", e.description.UTF8String); return 4; }
  double tpipe = now() - p0;
  NSMutableArray *results = [NSMutableArray array];
  for (NSDictionary *c in cases) {
    @autoreleasepool {
      NSString *name = c[@"name"];
      fprintf(stderr, "CASE block %s\n", name.UTF8String);
      int w = [c[@"width"] intValue], h = [c[@"height"] intValue], layers = [c[@"layers"] intValue];
      id<MTLTexture> t = arrayTexture(d, [c[@"format"] unsignedIntegerValue], w, h, layers, load(dir, c[@"texture"]));
      NSDictionary *sd = c[@"sampler"];
      MTLSamplerDescriptor *sdesc = [MTLSamplerDescriptor new];
      sdesc.normalizedCoordinates = [sd[@"normalized"] boolValue]; sdesc.sAddressMode = [sd[@"s"] unsignedIntegerValue];
      sdesc.tAddressMode = [sd[@"t"] unsignedIntegerValue]; sdesc.rAddressMode = [sd[@"r"] unsignedIntegerValue];
      sdesc.minFilter = [sd[@"min"] unsignedIntegerValue]; sdesc.magFilter = [sd[@"mag"] unsignedIntegerValue]; sdesc.mipFilter = [sd[@"mip"] unsignedIntegerValue];
      id<MTLSamplerState> sampler = [d newSamplerStateWithDescriptor:sdesc];
      NSMutableData *coords = [NSMutableData data];
      for (int l = 0; l < layers; l++) for (int o = 0; o < 4; o++) for (int y = -4; y <= h + 1; y++) for (int x = -3; x <= w + 2; x++) {
        int v[4] = {x, y, l, o}; [coords appendBytes:v length:16];
      }
      NSUInteger n = coords.length / 16, groups = (n + 31) / 32;
      while (coords.length < groups * 32 * 16) [coords appendBytes:(const char *)coords.bytes + (n - 1) * 16 length:16];
      id<MTLBuffer> cb = [d newBufferWithBytes:coords.bytes length:coords.length options:MTLResourceStorageModeShared];
      id<MTLBuffer> ob = [d newBufferWithLength:groups * 32 * 6 * 16 options:MTLResourceStorageModeShared];
      id<MTLCommandBuffer> b = [q commandBuffer];
      id<MTLComputeCommandEncoder> ce = [b computeCommandEncoder];
      [ce setComputePipelineState:p]; [ce setTexture:t atIndex:0]; [ce setSamplerState:sampler atIndex:0];
      [ce setBuffer:cb offset:0 atIndex:0]; [ce setBuffer:ob offset:0 atIndex:1];
      [ce dispatchThreadgroups:MTLSizeMake(groups, 1, 1) threadsPerThreadgroup:MTLSizeMake(32, 1, 1)];
      [ce endEncoding]; [b commit]; [b waitUntilCompleted];
      writeOut(out, [name stringByAppendingString:@".coords"], coords.bytes, n * 16);
      writeOut(out, [name stringByAppendingString:@".block"], ob.contents, n * 6 * 16);
      NSMutableDictionary *row = [gpuTime(b) mutableCopy];
      row[@"case"] = name; row[@"mode"] = @"block"; row[@"coordinates"] = @(n); row[@"pipeline_s"] = @(tpipe);
      [results addObject:row];
      [[NSJSONSerialization dataWithJSONObject:results options:NSJSONWritingPrettyPrinted error:nil] writeToFile:[out stringByAppendingPathComponent:@"block-results.json"] atomically:YES];
      if (b.status != MTLCommandBufferStatusCompleted) return 1;
    }
  }
  return 0;
}

int main(int argc, const char **argv) {
  @autoreleasepool {
    if (argc < 4) { fprintf(stderr, "usage: sift_exec api|kernel|block OUT CASES.json [LIB]\n"); return 2; }
    id<MTLDevice> d = MTLCreateSystemDefaultDevice();
    if (!d) { fprintf(stderr, "no Metal device\n"); return 3; }
    id<MTLCommandQueue> q = [d newCommandQueue];
    install(d, q);
    NSString *out = @(argv[2]), *casesPath = @(argv[3]);
    [[NSFileManager defaultManager] createDirectoryAtPath:out withIntermediateDirectories:YES attributes:nil error:nil];
    NSArray *cases = [NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:casesPath] options:0 error:nil];
    if (!cases) { fprintf(stderr, "cases\n"); return 3; }
    NSString *dir = [casesPath stringByDeletingLastPathComponent];
    NSString *env = [NSString stringWithFormat:@"{\"device\":\"%@\",\"arch\":\"%s\"}", d.name,
#if defined(__x86_64__)
                     "x86_64"
#else
                     "arm64"
#endif
    ];
    [env writeToFile:[out stringByAppendingPathComponent:@"device.json"] atomically:YES encoding:NSUTF8StringEncoding error:nil];
    printf("%s\n", env.UTF8String);
    if (!strcmp(argv[1], "api")) return api(out, cases, dir, d, q);
    if (!strcmp(argv[1], "kernel") && argc == 5) return kernel(out, cases, dir, @(argv[4]), d, q);
    if (!strcmp(argv[1], "block") && argc == 5) return block(out, cases, dir, @(argv[4]), d, q);
    return 2;
  }
}
