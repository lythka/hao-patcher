// GPU-client loader: also required for cached translated shaders. Never loads
// the compiler, LLVM, or sampler-query (which must follow frontend specialization).
#include <stdio.h>
#include <stddef.h>
#include <mach/mach_vm.h>
#include "image_identity.h"
#include "sampler_upload.c"
const char *compat_gpu_client_abi(void){return SAMPLER_CLIENT_ABI;}
int compat_gpu_client_ready(void){return sampler_upload_ready;}
