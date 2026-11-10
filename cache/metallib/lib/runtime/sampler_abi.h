#ifndef COMPAT_SAMPLER_ABI_H
#define COMPAT_SAMPLER_ABI_H
// Private constant channel: packed S/T enums, separate from the driver's S query.
#include <stdlib.h>
#include <string.h>
#define SAMPLER_AXIS_CHANNEL 0x5354U
// This is an explicit cross-process deployment contract, not a readiness probe.
// Set it in BOTH the compiler and GPU client, and use a fresh private shader cache.
#define SAMPLER_CLIENT_ABI "axis-st-v1"
static inline int sampler_abi_opted_in(void){
 const char *abi=getenv("COMPAT_CLIENT_ABI");
 return abi && !strcmp(abi,SAMPLER_CLIENT_ABI);
}
#endif
