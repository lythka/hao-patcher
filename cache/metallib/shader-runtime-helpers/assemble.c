// Assemble new helper IR with the exact legacy compiler's LLVM C API.
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
typedef void *Ref;
#define LOAD(ret, name, args) ret (*name) args = dlsym(handle, "LLVM" #name); \
  if (!name) { fprintf(stderr, "missing LLVM%s\n", #name); return 3; }
int main(int argc, char **argv) {
  if (argc != 4) return 2;
  void *handle = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (!handle) { fprintf(stderr, "%s\n", dlerror()); return 3; }
  LOAD(Ref, ContextCreate, (void));
  LOAD(int, CreateMemoryBufferWithContentsOfFile, (const char *, Ref *, char **));
  LOAD(int, ParseIRInContext, (Ref, Ref, Ref *, char **));
  LOAD(int, VerifyModule, (Ref, int, char **));
  LOAD(int, WriteBitcodeToFile, (Ref, const char *));
  Ref context = ContextCreate(), buffer = 0, module = 0;
  char *error = 0;
  if (CreateMemoryBufferWithContentsOfFile(argv[2], &buffer, &error) ||
      ParseIRInContext(context, buffer, &module, &error) ||
      VerifyModule(module, 2, &error)) {
    fprintf(stderr, "%s\n", error ? error : "invalid IR"); return 4;
  }
  return WriteBitcodeToFile(module, argv[3]) ? 5 : 0;
}
