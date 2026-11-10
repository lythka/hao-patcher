// ripatch IN OUT PATCH [flags]: apply a RIDIFF10 image patch with the host's libParallelCompression RawImagePatch
// (no root). IN "-" = full-replacement patch (no base image). The library is loaded at run time with dlopen (it is
// in the dyld shared cache, nothing is linked at build time). Struct layout recovered with `dyld_info -disassemble`:
// 0x10 in, 0x18 out, 0x20 patch, 0x28 flags, 0x2c threads, 0x30 verbosity. A missing symbol stops with an error.
#include <dlfcn.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
struct opts { uint64_t r0, r1; const char *in, *out, *patch; uint32_t flags, nthreads, verbose, pad; uint64_t r7, r8, r9, r10; };
int main(int argc, char **argv) {
  if (argc < 4) { fprintf(stderr, "usage: ripatch IN OUT PATCH [flags]\n"); return 2; }
  void *h = dlopen("/usr/lib/libParallelCompression.dylib", RTLD_NOW);
  if (!h) { fprintf(stderr, "ripatch: dlopen libParallelCompression: %s\n", dlerror()); return 3; }
  int (*RawImagePatch)(struct opts *) = (int (*)(struct opts *))dlsym(h, "RawImagePatch");
  if (!RawImagePatch) { fprintf(stderr, "ripatch: no RawImagePatch in libParallelCompression on this host\n"); return 3; }
  struct opts o; memset(&o, 0, sizeof o);
  o.in = strcmp(argv[1], "-") ? argv[1] : NULL; o.out = argv[2]; o.patch = argv[3]; o.verbose = 3;
  if (argc > 4) o.flags = (uint32_t)strtoul(argv[4], 0, 0);
  int r = RawImagePatch(&o);
  fprintf(stderr, "RawImagePatch returned %d\n", r);
  return r ? 1 : 0;
}
