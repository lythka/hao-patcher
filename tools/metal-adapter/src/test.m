// metal-adapter-test (x86_64): one compute kernel on the GPU, checked against a CPU reference.
//   metal-adapter-test KERNEL METALLIB      KERNEL = add1 | saxpy_long_name | source (MSL compiled at run time)
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
  if (argc < 3) { fprintf(stderr, "usage: metal-adapter-test add1|saxpy_long_name|source METALLIB\n"); return 2; }
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
  if (getenv("HAO_COMPILE_ONLY")) return result("COMPILE-OK", props);

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
