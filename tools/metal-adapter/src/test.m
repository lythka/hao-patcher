// metal-adapter-test (x86_64): one compute kernel on the GPU checked against a CPU reference, or one draw read back.
//   metal-adapter-test KERNEL METALLIB      KERNEL = add1 | saxpy_long_name | source (MSL compiled at run time)
//                                                  | render-color | render-texture | render-source (see run_render)
// Whatever Metal the process links (/System/.../Metal) is used: OCLP's wrapper + MetalOld normally, the bundle's
// Metal27 when DYLD_FRAMEWORK_PATH points at the bundle's fw/. Prints the Metal images loaded, the device, the
// pipeline (threadExecutionWidth, maxTotalThreadsPerThreadgroup) and one line
//   RESULT kernel=K status=PASS|FAIL|COMPILE-OK|SETUP-ERROR|NO-DEVICE|NO-LIBRARY|NO-PIPELINE|GPU-ERROR|CRASH|TIMEOUT ...
// then releases everything and prints TEARDOWN kernel=K status=OK|CRASH|TIMEOUT.
// Env: HAO_DEVICE=auto (default: MTLCreateSystemDefaultDevice, then manual) | default | manual
//      (manual = load the IOAccelerator's MetalPluginName bundle and -initWithAcceleratorPort: on the service).
//      Host replay only: HAO_ACCEL_PORT (fake port), HAO_DEVICE_CLASS, HAO_DRIVER_BUNDLE.
//      HAO_COMPILE_ONLY=1 stops after the pipeline. HAO_TIMEOUT seconds (default 120).
//      HAO_EXPECT=adapter|control: SETUP-ERROR unless the bundle shim is (adapter) / is not (control) loaded.
// Crashes and timeouts are caught and reported (no crash report is written); exit 0 only on PASS / COMPILE-OK.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOKit/IOKitLib.h>
#include <execinfo.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <math.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <signal.h>
#include <unistd.h>

static const char *gKernel = "?";
static volatile int gTeardown;  // set once RESULT is printed: what follows is releasing the objects
static void say(const char *s) { write(2, s, strlen(s)); }
static void on_fatal(int sig) {
  char line[256];
  snprintf(line, sizeof line, "[test] CRASH signal %d (%s)%s\n", sig, strsignal(sig), gTeardown ? " during teardown" : ""); say(line);
  void *a[64]; int k = backtrace(a, 64); backtrace_symbols_fd(a, k, 2);
  if (gTeardown) snprintf(line, sizeof line, "TEARDOWN kernel=%s status=CRASH signal=%d\n", gKernel, sig);
  else snprintf(line, sizeof line, "RESULT kernel=%s status=CRASH signal=%d\n", gKernel, sig);
  write(1, line, strlen(line));
  _exit(gTeardown ? 100 + sig : 128 + sig);
}
static void on_alarm(int sig) {
  (void)sig; char line[256];
  if (gTeardown) snprintf(line, sizeof line, "TEARDOWN kernel=%s status=TIMEOUT\n", gKernel);
  else snprintf(line, sizeof line, "RESULT kernel=%s status=TIMEOUT\n", gKernel);
  write(1, line, strlen(line));
  _exit(124);
}
static int result(const char *status, NSString *detail) {
  char line[2048];
  snprintf(line, sizeof line, "RESULT kernel=%s status=%s %s\n", gKernel, status, detail ? detail.UTF8String : "");
  fflush(stderr); printf("%s", line); fflush(stdout);
  gTeardown = 1;
  return (!strcmp(status, "PASS") || !strcmp(status, "COMPILE-OK")) ? 0 : 1;
}

static void uuid_of(const struct mach_header *h, char out[37]) {
  const uint8_t *p = (const uint8_t *)((const struct mach_header_64 *)h + 1); out[0] = 0;
  for (uint32_t i = 0; i < h->ncmds; i++) {
    const struct load_command *lc = (const void *)p;
    if (lc->cmd == LC_UUID) { const uint8_t *u = ((const struct uuid_command *)lc)->uuid;
      snprintf(out, 37, "%02X%02X%02X%02X-%02X%02X-%02X%02X-%02X%02X-%02X%02X%02X%02X%02X%02X", u[0], u[1], u[2], u[3], u[4],
               u[5], u[6], u[7], u[8], u[9], u[10], u[11], u[12], u[13], u[14], u[15]); return; }
    p += lc->cmdsize; }
}
static void print_images(const char *when) {
  for (uint32_t i = 0; i < _dyld_image_count(); i++) {
    const char *n = _dyld_get_image_name(i);
    if (!n || !(strstr(n, "/Metal.framework/") || strstr(n, "MetalOld") || strstr(n, "HaoMetalShim") ||
                strstr(n, "MTLCompiler") || strstr(n, "GPUCompiler.framework/Versions/3802/Libraries/libLLVM") ||
                strstr(n, "GPUCompiler.framework/Versions/31001/Libraries/libLLVM") || strstr(n, "MTLDriver") ||
                strstr(n, "libMTLIntelCompilerPlugin") || strstr(n, "libigdmd")))
      continue;
    char u[37]; uuid_of(_dyld_get_image_header(i), u);
    fprintf(stderr, "[test] image(%s) %s uuid %s\n", when, n, u);
  }
}

static id<MTLDevice> manual_device(void) {
  const char *portEnv = getenv("HAO_ACCEL_PORT");
  io_service_t svc = 0; NSString *bundlePath = nil, *className = nil;
  if (portEnv) {  // host replay: a fake port served by the replay interposer
    svc = (io_service_t)strtoul(portEnv, NULL, 0);
    bundlePath = @(getenv("HAO_DRIVER_BUNDLE") ?: ""); className = @(getenv("HAO_DEVICE_CLASS") ?: "MTLIGAccelDevice");
  } else {
    io_iterator_t it = 0;
    if (IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOAccelerator"), &it) != KERN_SUCCESS) {
      fprintf(stderr, "[test] manual: no IOAccelerator services\n"); return nil; }
    io_service_t s;
    while ((s = IOIteratorNext(it))) {
      CFTypeRef pn = IORegistryEntryCreateCFProperty(s, CFSTR("MetalPluginName"), kCFAllocatorDefault, 0);
      CFTypeRef pc = IORegistryEntryCreateCFProperty(s, CFSTR("MetalPluginClassName"), kCFAllocatorDefault, 0);
      io_name_t cls = ""; IOObjectGetClass(s, cls);
      fprintf(stderr, "[test] manual: IOAccelerator %s MetalPluginName=%s MetalPluginClassName=%s\n", cls,
              pn ? [(__bridge NSString *)pn UTF8String] : "-", pc ? [(__bridge NSString *)pc UTF8String] : "-");
      if (!svc && pn && pc && CFGetTypeID(pn) == CFStringGetTypeID() && CFGetTypeID(pc) == CFStringGetTypeID()) {
        svc = s; bundlePath = [NSString stringWithFormat:@"/System/Library/Extensions/%@.bundle", (__bridge NSString *)pn];
        className = [(__bridge NSString *)pc copy];
      } else IOObjectRelease(s);
      if (pn) CFRelease(pn);
      if (pc) CFRelease(pc);
    }
    IOObjectRelease(it);
    if (!svc) { fprintf(stderr, "[test] manual: no IOAccelerator with a Metal plugin\n"); return nil; }
  }
  NSBundle *b = [NSBundle bundleWithPath:bundlePath]; NSError *e = nil;
  if (![b loadAndReturnError:&e]) { fprintf(stderr, "[test] manual: bundle %s: %s\n", bundlePath.UTF8String, e.description.UTF8String); return nil; }
  Class C = NSClassFromString(className);
  fprintf(stderr, "[test] manual: bundle %s class %s -> %p, port %#x\n", bundlePath.UTF8String, className.UTF8String, (__bridge void *)C, svc);
  if (!C) return nil;
  id dev = ((id (*)(id, SEL, unsigned))objc_msgSend)([C alloc], sel_registerName("initWithAcceleratorPort:"), svc);
  if (!dev) { fprintf(stderr, "[test] manual: initWithAcceleratorPort: returned nil\n"); return nil; }
  static const char *inits[] = {"initLimits", "initFeatureQueries"};
  for (int k = 0; k < 2; k++)
    if ([dev respondsToSelector:sel_registerName(inits[k])]) ((void (*)(id, SEL))objc_msgSend)(dev, sel_registerName(inits[k]));
  fprintf(stderr, "[test] manual: device created, initLimits + initFeatureQueries called\n");
  return dev;
}

static id<MTLDevice> make_device(NSString **how) {
  const char *mode = getenv("HAO_DEVICE") ?: "auto";
  if (getenv("HAO_ACCEL_PORT")) mode = "manual";
  if (strcmp(mode, "manual")) {
    id<MTLDevice> d = MTLCreateSystemDefaultDevice();
    fprintf(stderr, "[test] MTLCreateSystemDefaultDevice -> %p\n", (__bridge void *)d);
    if (d) { *how = @"default"; return d; }
    NSArray *all = MTLCopyAllDevices();
    fprintf(stderr, "[test] MTLCopyAllDevices -> %lu device(s)\n", (unsigned long)all.count);
    if (all.count) { *how = @"copyall"; return all[0]; }
    if (!strcmp(mode, "default")) return nil;
  }
  *how = @"manual";
  return manual_device();
}

static NSString *const kSource =
  @"#include <metal_stdlib>\nusing namespace metal;\n"
   "kernel void scale2(device float *a [[buffer(0)]], uint i [[thread_position_in_grid]]) { a[i] = a[i] * 2.0f + 0.5f; }\n";

static int run(char **argv);
int main(int argc, char **argv) {
  setvbuf(stdout, NULL, _IOLBF, 0);
  if (argc < 3) { fprintf(stderr, "usage: metal-adapter-test add1|saxpy_long_name|source|render-color|render-texture|render-source METALLIB\n"); return 2; }
  gKernel = argv[1];
  static char altstack[1 << 16]; stack_t ss = {.ss_sp = altstack, .ss_size = sizeof altstack};
  sigaltstack(&ss, NULL);
  struct sigaction sa = {.sa_handler = on_fatal, .sa_flags = SA_ONSTACK | SA_RESETHAND};
  static const int sigs[] = {SIGSEGV, SIGBUS, SIGILL, SIGABRT, SIGFPE, SIGTRAP, SIGSYS};
  for (int k = 0; k < 7; k++) sigaction(sigs[k], &sa, NULL);
  signal(SIGALRM, on_alarm);
  alarm(getenv("HAO_TIMEOUT") ? (unsigned)atoi(getenv("HAO_TIMEOUT")) : 120);

  fprintf(stderr, "[test] pid %d kernel %s metallib %s\n", getpid(), argv[1], argv[2]);
  static const char *envs[] = {"DYLD_FRAMEWORK_PATH", "DYLD_INSERT_LIBRARIES", "HAO_METAL27_INTERNAL",
                               "MTL_MONOLITHIC_COMPILER", "HAO_DEVICE", "HAO_DOWNGRADE_TEMPLATE"};
  for (int k = 0; k < 6; k++) fprintf(stderr, "[test] env %s=%s\n", envs[k], getenv(envs[k]) ?: "(unset)");
  print_images("start");
  int rc;
  @autoreleasepool { rc = run(argv); }  // pipeline, library, queue, buffers and device are released here
  printf("TEARDOWN kernel=%s status=OK\n", gKernel);
  return rc;
}

static int image_loaded(const char *needle) {
  for (uint32_t i = 0; i < _dyld_image_count(); i++) { const char *n = _dyld_get_image_name(i); if (n && strstr(n, needle)) return 1; }
  return 0;
}
static int compile_only(void) { const char *c = getenv("HAO_COMPILE_ONLY"); return c && *c; }

// MTLStorageModeManaged and synchronizeResource: are deprecated in the macOS 27 SDK (no effect on Apple silicon); on
// the Intel GPU they are the way to read a texture back.
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
// ---- render: one draw into a 64x64 RGBA8 texture, read back and checked pixel by pixel
//   render-color    lib's tri_vertex + solid_fragment: the lower-left half triangle (px < py) in the constant colour
//                   (setFragmentBytes), the rest the clear colour; the diagonal px == py is not checked
//   render-texture  tri_vertex + tex_fragment: a triangle covering the target samples a 4x4 texture (nearest,
//                   setFragmentTexture + setFragmentSamplerState): pixel (x, y) = texel (x / 16, y / 16)
//   render-source   as render-color, with the shaders compiled from MSL at run time
static NSString *const kRenderSource =
  @"#include <metal_stdlib>\nusing namespace metal;\nstruct VOut { float4 pos [[position]]; };\n"
   "vertex VOut src_vertex(device const float2 *pos [[buffer(0)]], uint vid [[vertex_id]]) {\n"
   "  VOut o; o.pos = float4(pos[vid], 0.0f, 1.0f); return o; }\n"
   "fragment float4 src_fragment(VOut in [[stage_in]], constant float4 &color [[buffer(0)]]) { return color; }\n";
enum { RW = 64, RH = 64, TW = 4 };
static void texel(unsigned x, unsigned y, uint8_t out[4]) {
  out[0] = (uint8_t)(20 + 60 * x); out[1] = (uint8_t)(20 + 60 * y); out[2] = (uint8_t)(200 - 25 * (x + y)); out[3] = 255;
}

static int run_render(id<MTLDevice> dev, NSString *how, char **argv) {
  const char *k = argv[1];
  int tex = !strcmp(k, "render-texture"), src = !strcmp(k, "render-source");
  if (!tex && !src && strcmp(k, "render-color")) return result("SETUP-ERROR", @"unknown render case");
  NSError *e = nil;
  id<MTLLibrary> lib = src ? [dev newLibraryWithSource:kRenderSource options:nil error:&e]
                           : [dev newLibraryWithURL:[NSURL fileURLWithPath:@(argv[2])] error:&e];
  fprintf(stderr, "[test] library %s error=%s\n", lib ? "OK" : "FAIL", e ? e.localizedDescription.UTF8String : "-");
  if (!lib) return result("NO-LIBRARY", e.localizedDescription);
  NSString *vn = src ? @"src_vertex" : @"tri_vertex", *fn = src ? @"src_fragment" : tex ? @"tex_fragment" : @"solid_fragment";
  id<MTLFunction> vf = [lib newFunctionWithName:vn], ff = [lib newFunctionWithName:fn];
  if (!vf || !ff) return result("NO-LIBRARY", [NSString stringWithFormat:@"no function %@", vf ? fn : vn]);
  MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
  pd.vertexFunction = vf; pd.fragmentFunction = ff;
  pd.colorAttachments[0].pixelFormat = MTLPixelFormatRGBA8Unorm;
  e = nil;
  id<MTLRenderPipelineState> pso = [dev newRenderPipelineStateWithDescriptor:pd error:&e];
  print_images("pipeline");
  if (!pso) return result("NO-PIPELINE", e.localizedDescription);
  fprintf(stderr, "[test] pipeline OK class=%s\n", class_getName([(id)pso class]));
  NSString *props = [NSString stringWithFormat:@"device=\"%@\" via=%@ pipeline=%s", dev.name, how, class_getName([(id)pso class])];
  if (compile_only()) return result("COMPILE-OK", props);

  MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:RW height:RH mipmapped:NO];
  td.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead; td.storageMode = MTLStorageModeManaged;
  id<MTLTexture> target = [dev newTextureWithDescriptor:td];
  if (!target) return result("GPU-ERROR", [props stringByAppendingString:@" no render target"]);
  id<MTLTexture> st = nil; id<MTLSamplerState> ss = nil;
  if (tex) {
    MTLTextureDescriptor *sd = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:TW height:TW mipmapped:NO];
    sd.usage = MTLTextureUsageShaderRead; sd.storageMode = MTLStorageModeManaged;
    st = [dev newTextureWithDescriptor:sd];
    uint8_t px[TW * TW * 4];
    for (unsigned y = 0; y < TW; y++) for (unsigned x = 0; x < TW; x++) texel(x, y, px + (y * TW + x) * 4);
    [st replaceRegion:MTLRegionMake2D(0, 0, TW, TW) mipmapLevel:0 withBytes:px bytesPerRow:TW * 4];
    MTLSamplerDescriptor *smp = [MTLSamplerDescriptor new];
    smp.minFilter = smp.magFilter = MTLSamplerMinMagFilterNearest;
    smp.sAddressMode = smp.tAddressMode = MTLSamplerAddressModeClampToEdge;
    ss = [dev newSamplerStateWithDescriptor:smp];
    if (!st || !ss) return result("GPU-ERROR", [props stringByAppendingString:@" no texture or sampler"]);
  }
  id<MTLCommandQueue> q = [dev newCommandQueue];
  if (!q) return result("GPU-ERROR", [props stringByAppendingString:@" no command queue"]);
  MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
  rp.colorAttachments[0].texture = target;
  rp.colorAttachments[0].loadAction = MTLLoadActionClear;
  rp.colorAttachments[0].storeAction = MTLStoreActionStore;
  rp.colorAttachments[0].clearColor = MTLClearColorMake(0.0, 0.0, 1.0, 1.0);  // (0, 0, 255, 255)
  id<MTLCommandBuffer> cb = [q commandBuffer];
  id<MTLRenderCommandEncoder> enc = [cb renderCommandEncoderWithDescriptor:rp];
  [enc setRenderPipelineState:pso];
  static const float half[6] = {-1, -1, 1, -1, -1, 1}, full[6] = {-1, -1, 3, -1, -1, 3};
  [enc setVertexBytes:tex ? full : half length:sizeof half atIndex:0];
  const float color[4] = {1.0f, 0.2f, 0.6f, 1.0f};                           // (255, 51, 153, 255)
  if (tex) { [enc setFragmentTexture:st atIndex:0]; [enc setFragmentSamplerState:ss atIndex:0]; }
  else [enc setFragmentBytes:color length:sizeof color atIndex:0];
  [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
  [enc endEncoding];
  id<MTLBlitCommandEncoder> bl = [cb blitCommandEncoder];
  [bl synchronizeResource:target];
  [bl endEncoding];
  [cb commit];
  [cb waitUntilCompleted];
  fprintf(stderr, "[test] command buffer status=%ld error=%s\n", (long)cb.status, cb.error ? cb.error.description.UTF8String : "-");
  if (cb.status != MTLCommandBufferStatusCompleted || cb.error)
    return result("GPU-ERROR", [NSString stringWithFormat:@"%@ cbstatus=%ld error=\"%@\"", props, (long)cb.status, cb.error.localizedDescription]);

  static uint8_t out[RW * RH * 4];
  [target getBytes:out bytesPerRow:RW * 4 fromRegion:MTLRegionMake2D(0, 0, RW, RH) mipmapLevel:0];
  NSUInteger bad = 0, checked = 0, fx = 0, fy = 0, inside = 0; int maxd = 0; uint8_t got[4] = {0}, want0[4] = {0};
  for (unsigned y = 0; y < RH; y++) for (unsigned x = 0; x < RW; x++) {
    uint8_t want[4];
    if (tex) texel(x / (RW / TW), y / (RH / TW), want);
    else if (x == y) continue;  // on the triangle's edge: either colour is right
    else if (x < y) { want[0] = 255; want[1] = 51; want[2] = 153; want[3] = 255; inside++; }
    else { want[0] = 0; want[1] = 0; want[2] = 255; want[3] = 255; }
    const uint8_t *p = out + (y * RW + x) * 4; int d = 0;
    for (int c = 0; c < 4; c++) { int dc = abs((int)p[c] - (int)want[c]); if (dc > d) d = dc; }
    checked++;
    if (d > maxd) maxd = d;
    if (d > 1) { if (!bad) { fx = x; fy = y; memcpy(got, p, 4); memcpy(want0, want, 4); } bad++; }
  }
  fprintf(stderr, "[test] pixel(0,63)=%u,%u,%u,%u pixel(63,0)=%u,%u,%u,%u\n", out[(63 * RW) * 4], out[(63 * RW) * 4 + 1],
          out[(63 * RW) * 4 + 2], out[(63 * RW) * 4 + 3], out[63 * 4], out[63 * 4 + 1], out[63 * 4 + 2], out[63 * 4 + 3]);
  NSString *check = [NSString stringWithFormat:@"%@ checked=%lu bad=%lu maxdiff=%d%@", props, (unsigned long)checked,
                     (unsigned long)bad, maxd, tex ? @"" : [NSString stringWithFormat:@" inside=%lu", (unsigned long)inside]];
  if (bad) return result("FAIL", [check stringByAppendingFormat:@" first_bad=%lu,%lu got=%u,%u,%u,%u want=%u,%u,%u,%u",
                                  (unsigned long)fx, (unsigned long)fy, got[0], got[1], got[2], got[3], want0[0], want0[1], want0[2], want0[3]]);
  return result("PASS", check);
}

static int run(char **argv) {
  // HAO_EXPECT=adapter|control: refuse to report a result from the wrong Metal (e.g. DYLD_* stripped on the way)
  const char *expect = getenv("HAO_EXPECT");
  int shim = image_loaded("/libHaoMetalShim.dylib"), old = image_loaded("MetalOld");
  fprintf(stderr, "[test] setup: shim %s, MetalOld %s, expect %s\n", shim ? "loaded" : "absent", old ? "loaded" : "absent", expect ?: "-");
  if (expect && !strcmp(expect, "adapter") && (!shim || old))
    return result("SETUP-ERROR", @"adapter run without the bundle's Metal27 + shim (or with MetalOld)");
  if (expect && !strcmp(expect, "control") && shim)
    return result("SETUP-ERROR", @"control run with the bundle's shim loaded");
  NSString *how = nil;
  id<MTLDevice> dev = make_device(&how);
  print_images("device");
  if (!dev) return result("NO-DEVICE", nil);
  fprintf(stderr, "[test] device name=\"%s\" class=%s via=%s registryID=%#llx lowPower=%d unifiedMemory=%d "
          "maxThreadsPerThreadgroup=%lux%lux%lu\n", dev.name.UTF8String, class_getName([(id)dev class]), how.UTF8String,
          (unsigned long long)dev.registryID, dev.isLowPower, dev.hasUnifiedMemory, (unsigned long)dev.maxThreadsPerThreadgroup.width,
          (unsigned long)dev.maxThreadsPerThreadgroup.height, (unsigned long)dev.maxThreadsPerThreadgroup.depth);
  if ([(id)dev respondsToSelector:sel_registerName("llvmVersion")])
    fprintf(stderr, "[test] device llvmVersion %ld\n", (long)((NSInteger (*)(id, SEL))objc_msgSend)((id)dev, sel_registerName("llvmVersion")));
  if (!strncmp(argv[1], "render-", 7)) return run_render(dev, how, argv);

  NSError *e = nil;
  id<MTLLibrary> lib = nil; NSString *fname = @(argv[1]);
  if (!strcmp(argv[1], "source")) {
    lib = [dev newLibraryWithSource:kSource options:nil error:&e]; fname = @"scale2";
  } else {
    lib = [dev newLibraryWithURL:[NSURL fileURLWithPath:@(argv[2])] error:&e];
  }
  fprintf(stderr, "[test] library %s error=%s\n", lib ? "OK" : "FAIL", e ? e.localizedDescription.UTF8String : "-");
  if (!lib) return result("NO-LIBRARY", e.localizedDescription);
  id<MTLFunction> fn = [lib newFunctionWithName:fname];
  if (!fn) return result("NO-LIBRARY", [@"no function " stringByAppendingString:fname]);
  e = nil;
  id<MTLComputePipelineState> pso = [dev newComputePipelineStateWithFunction:fn error:&e];
  print_images("pipeline");
  if (!pso) return result("NO-PIPELINE", e.localizedDescription);
  NSUInteger tew = pso.threadExecutionWidth, maxT = pso.maxTotalThreadsPerThreadgroup;
  fprintf(stderr, "[test] pipeline OK class=%s threadExecutionWidth=%lu maxTotalThreadsPerThreadgroup=%lu\n",
          class_getName([(id)pso class]), (unsigned long)tew, (unsigned long)maxT);
  NSString *props = [NSString stringWithFormat:@"device=\"%@\" via=%@ threadExecutionWidth=%lu maxTotalThreadsPerThreadgroup=%lu",
                     dev.name, how, (unsigned long)tew, (unsigned long)maxT];
  if (compile_only()) return result("COMPILE-OK", props);

  NSUInteger tg = 64; while (tg > 1 && tg > maxT) tg >>= 1;
  id<MTLCommandQueue> q = [dev newCommandQueue];
  if (!q) return result("GPU-ERROR", [props stringByAppendingString:@" no command queue"]);
  id<MTLCommandBuffer> cb = [q commandBuffer];
  id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
  [enc setComputePipelineState:pso];
  const NSUInteger N = 1024, N4 = 256; const float A = 2.5f;
  id<MTLBuffer> b0 = nil, b1 = nil;
  if (!strcmp(argv[1], "saxpy_long_name")) {
    b0 = [dev newBufferWithLength:N4 * 16 options:MTLResourceStorageModeShared];
    b1 = [dev newBufferWithLength:N4 * 16 options:MTLResourceStorageModeShared];
    float *x = b0.contents, *y = b1.contents;
    for (NSUInteger i = 0; i < N4 * 4; i++) { x[i] = (float)((int)(i % 37) - 18) * 0.125f; y[i] = (float)(i % 11) - 5.0f; }
    [enc setBuffer:b0 offset:0 atIndex:0]; [enc setBuffer:b1 offset:0 atIndex:1]; [enc setBytes:&A length:4 atIndex:2];
    [enc dispatchThreadgroups:MTLSizeMake(N4 / tg, 1, 1) threadsPerThreadgroup:MTLSizeMake(tg, 1, 1)];
  } else {
    b0 = [dev newBufferWithLength:N * 4 options:MTLResourceStorageModeShared];
    float *a = b0.contents; for (NSUInteger i = 0; i < N; i++) a[i] = (float)i;
    [enc setBuffer:b0 offset:0 atIndex:0];
    [enc dispatchThreadgroups:MTLSizeMake(N / tg, 1, 1) threadsPerThreadgroup:MTLSizeMake(tg, 1, 1)];
  }
  [enc endEncoding];
  [cb commit];
  [cb waitUntilCompleted];
  fprintf(stderr, "[test] command buffer status=%ld error=%s (threadgroup %lu)\n", (long)cb.status,
          cb.error ? cb.error.description.UTF8String : "-", (unsigned long)tg);
  if (cb.status != MTLCommandBufferStatusCompleted || cb.error)
    return result("GPU-ERROR", [NSString stringWithFormat:@"%@ cbstatus=%ld error=\"%@\"", props, (long)cb.status, cb.error.localizedDescription]);

  NSUInteger bad = 0, first = NSUIntegerMax; double maxerr = 0; double got0 = 0, want0 = 0;
  if (!strcmp(argv[1], "saxpy_long_name")) {
    const float *y = b1.contents;
    for (NSUInteger i = 0; i < N4 * 4; i++) {
      double x = (double)((int)(i % 37) - 18) * 0.125, y0 = (double)(i % 11) - 5.0;
      double want = fma((double)A, x, y0) + sin(x) * 0.5, err = fabs(y[i] - want);
      if (err > maxerr) maxerr = err;
      if (err > 2e-3 * (1 + fabs(want))) { if (first == NSUIntegerMax) { first = i; got0 = y[i]; want0 = want; } bad++; }
    }
  } else {
    const float *a = b0.contents;
    for (NSUInteger i = 0; i < N; i++) {
      double want = !strcmp(argv[1], "source") ? (double)i * 2.0 + 0.5 : (double)i + 1.0, err = fabs(a[i] - want);
      if (err > maxerr) maxerr = err;
      if (a[i] != (float)want) { if (first == NSUIntegerMax) { first = i; got0 = a[i]; want0 = want; } bad++; }
    }
    fprintf(stderr, "[test] out[0..3] = %g %g %g %g\n", a[0], a[1], a[2], a[3]);
  }
  NSUInteger total = !strcmp(argv[1], "saxpy_long_name") ? N4 * 4 : N;
  NSString *check = [NSString stringWithFormat:@"%@ checked=%lu bad=%lu maxerr=%.3g", props, (unsigned long)total, (unsigned long)bad, maxerr];
  if (bad) return result("FAIL", [check stringByAppendingFormat:@" first_bad=%lu got=%g want=%g", (unsigned long)first, got0, want0]);
  return result("PASS", check);
}

#ifdef HOST_R38_EXPORTS
// Host-only build (never in the bundle): the r38 replay runtime's patched MetalOld dlsym()s these from the main
// executable for its in-process compiler; forward them to $RP_MTLCOMPILER (as cache/shaders' replay does).
#include <dlfcn.h>
static void *gMC; static void *mc(const char *s) { if (!gMC) gMC = dlopen(getenv("RP_MTLCOMPILER"), RTLD_NOW | RTLD_GLOBAL); return gMC ? dlsym(gMC, s) : NULL; }
__attribute__((visibility("default"), used)) void *MTLCodeGenServiceCreate(void) { return ((void *(*)(void))mc("MTLCodeGenServiceCreate"))(); }
__attribute__((visibility("default"), used)) void MTLCodeGenServiceDestroy(void *s) { ((void (*)(void *))mc("MTLCodeGenServiceDestroy"))(s); }
__attribute__((visibility("default"), used)) int MTLCodeGenServiceSetPluginPath(void *s, const char *p, const void *t, size_t n) { return ((int (*)(void *, const char *, const void *, size_t))mc("MTLCodeGenServiceSetPluginPath"))(s, p, t, n); }
__attribute__((visibility("default"), used)) void MTLCodeGenServiceBuildRequest(void *s, unsigned a, unsigned b, const void *d, size_t n, void (^cb)(unsigned, const void *, size_t, const char *)) {
  ((void (*)(void *, unsigned, unsigned, const void *, size_t, void (^)(unsigned, const void *, size_t, const char *)))mc("MTLCodeGenServiceBuildRequest"))(s, a, b, d, n, cb); }
#endif
