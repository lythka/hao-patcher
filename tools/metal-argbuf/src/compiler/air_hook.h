// air_hook.h: IR-level argument-buffer legalization inside MTLCompilerService (see air_hook.c, air_pass.c)
#pragma once
void air_hook_install(void);      // rebind the plugin's PassManager::run import (iCBE wrapper constructor)
void air_transform_init(void);    // resolve the LLVM C API from GPUCompiler 3802's libLLVM
void air_transform(void *module); // legalize one llvm::Module* before the IGIL target's passes
// The plugin's own LLVM (GPUCompiler 3802). MTLCompilerService also loads 31001's newer LLVM (the metallib
// downgrader), so symbols must never be resolved with RTLD_DEFAULT.
void *air_llvm(void);
// IGIL TextureTypeEncode wrapper: the pass zeroes every texture/sampler call with an operand it cannot trace to the
// function's own texture/sampler arguments (argument-buffer handles); the wrapper restores those uses afterwards.
typedef _Bool (*air_pass_run)(void *pass, void *function);
extern air_pass_run air_tte_orig;
_Bool air_tte_run(void *pass, void *function);
