// air_pass.c: argument-buffer legalization of AIR for the HD 4000 IGIL target, run in MTLCompilerService on the
// module generateIGIL is about to compile (air_hook.c). Uses the LLVM C API of GPUCompiler 3802's libLLVM.
//
// Graphics functions (vertex/fragment) only; compute already compiles. The IGIL target lowers graphics with a 32-bit
// pointer data layout and its constant-buffer load pass (IGILair_ldst_handling) has no pointer-typed load, so an
// argument buffer in a vertex or fragment function crashes the compiler (intrinsic id 0), and member offsets would be
// computed with 4-byte pointers while Metal lays argument buffers out with 8-byte ones. Two rewrites:
//   1. a GEP into constant memory (addrspace 2) whose source type contains pointers is replaced by an i8 GEP with the
//      byte offset of AIR's 64-bit layout;
//   2. a load of a pointer from constant memory becomes a volatile <2 x i32> load + bitcast i64 + inttoptr (volatile
//      so instcombine does not fold it back into a pointer load).
// ARGBUF_LLVM_OPTIONS (host harness) passes options such as -print-after-all to LLVM.
// ARGBUF_IR_DUMP_DIR=DIR (host harness) prints each module before and after to DIR/N.before.ll, DIR/N.after.ll.
#include "air_hook.h"
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syslog.h>

typedef void *V; typedef void *T; typedef void *B; typedef void *M; typedef void *BB; typedef void *TD;
enum { K_VOID = 0, K_INT = 8, K_FUNC = 9, K_STRUCT = 10, K_ARRAY = 11, K_POINTER = 12, K_VECTOR = 13 };   // LLVMTypeKind
enum { OP_GEP = 29, OP_LOAD = 27 };                                                                    // LLVMOpcode

#define API(X) \
    X(void, LLVMPrintModuleToFile, M, const char *, char **) \
    X(V, LLVMGetFirstFunction, M) X(V, LLVMGetNextFunction, V) \
    X(BB, LLVMGetFirstBasicBlock, V) X(BB, LLVMGetNextBasicBlock, BB) \
    X(V, LLVMGetFirstInstruction, BB) X(V, LLVMGetNextInstruction, V) \
    X(int, LLVMGetInstructionOpcode, V) X(V, LLVMGetOperand, V, unsigned) X(int, LLVMGetNumOperands, V) \
    X(T, LLVMTypeOf, V) X(int, LLVMGetTypeKind, T) X(T, LLVMGetElementType, T) X(unsigned, LLVMGetPointerAddressSpace, T) \
    X(unsigned, LLVMCountStructElementTypes, T) X(T, LLVMStructGetTypeAtIndex, T, unsigned) X(unsigned, LLVMGetArrayLength, T) \
    X(void, LLVMSetVolatile, V, int) X(void, LLVMSetAlignment, V, unsigned) \
    X(V, LLVMBuildLoad, B, V, const char *) X(V, LLVMBuildBitCast, B, V, T, const char *) \
    X(V, LLVMBuildIntToPtr, B, V, T, const char *) X(V, LLVMBuildGEP, B, V, V *, unsigned, const char *) \
    X(V, LLVMBuildMul, B, V, V, const char *) X(V, LLVMBuildAdd, B, V, V, const char *) \
    X(V, LLVMBuildSExtOrBitCast, B, V, T, const char *) X(V, LLVMBuildZExtOrBitCast, B, V, T, const char *) \
    X(B, LLVMCreateBuilderInContext, void *) X(void, LLVMPositionBuilderBefore, B, V) X(void, LLVMDisposeBuilder, B) \
    X(void, LLVMReplaceAllUsesWith, V, V) X(void, LLVMInstructionEraseFromParent, V) \
    X(void *, LLVMGetModuleContext, M) X(T, LLVMVectorType, T, unsigned) X(T, LLVMPointerType, T, unsigned) \
    X(T, LLVMInt8TypeInContext, void *) X(T, LLVMInt32TypeInContext, void *) X(T, LLVMInt64TypeInContext, void *) \
    X(V, LLVMConstInt, T, unsigned long long, int) X(V, LLVMIsAConstantInt, V) X(long long, LLVMConstIntGetSExtValue, V) \
    X(TD, LLVMCreateTargetData, const char *) X(unsigned long long, LLVMOffsetOfElement, TD, T, unsigned) \
    X(unsigned long long, LLVMABISizeOfType, TD, T) X(unsigned, LLVMGetNamedMetadataNumOperands, M, const char *) \
    X(void, LLVMGetNamedMetadataOperands, M, const char *, V *) X(V, LLVMMDNode, V *, unsigned) \
    X(unsigned, LLVMGetMDNodeNumOperands, V) X(void, LLVMGetMDNodeOperands, V, V *) X(V, LLVMIsAFunction, V) \
    X(void *, LLVMGetFirstUse, V) X(void *, LLVMGetNextUse, void *) X(V, LLVMGetUser, void *) X(void, LLVMSetOperand, V, unsigned, V) \
    X(V, LLVMIsAConstantAggregateZero, V) X(V, LLVMIsACallInst, V) X(const char *, LLVMGetStructName, T)
#define DECL(R, N, ...) static R (*N)(__VA_ARGS__);
API(DECL)
#undef DECL

// AIR's own data layout (64-bit pointers): what Metal's argument encoders and the compute target use
static const char *AIR64 = "e-p:64:64:64-i1:8:8-i8:8:8-i16:16:16-i32:32:32-i64:64:64-f32:32:32-f64:64:64-v16:16:16-"
                           "v24:32:32-v32:32:32-v48:64:64-v64:64:64-v96:128:128-v128:128:128-v192:256:256-v256:256:256-"
                           "v512:512:512-v1024:1024:1024-n8:16:32";
static TD td64;
static int ready;

void air_transform_init(void) {
    int ok = 1; void *llvm = air_llvm();
    if (!llvm) { syslog(LOG_ERR, "icbe_wrap: GPUCompiler 3802 libLLVM not loaded"); return; }
#define LOAD(R, N, ...) if (!(N = (R (*)(__VA_ARGS__))dlsym(llvm, #N))) { syslog(LOG_ERR, "icbe_wrap: no %s", #N); ok = 0; }
    API(LOAD)
#undef LOAD
    if (ok) td64 = LLVMCreateTargetData(AIR64);
    ready = ok && td64;
    // ARGBUF_LLVM_OPTIONS="-print-after-all ..." (host harness): LLVM options for the IGIL target's passes
    const char *opts = getenv("ARGBUF_LLVM_OPTIONS");
    void (*parse)(int, const char *const *, const char *) = opts ? (void (*)(int, const char *const *, const char *))dlsym(llvm, "LLVMParseCommandLineOptions") : NULL;
    if (parse) {
        static char buf[1024]; static const char *argv[32]; int argc = 0;
        snprintf(buf, sizeof buf, "%s", opts); argv[argc++] = "argbuf";
        for (char *t = strtok(buf, " "); t && argc < 31; t = strtok(NULL, " ")) argv[argc++] = t;
        parse(argc, argv, "argbuf");
    }
}

static int contains_pointer(T t, int depth) {
    if (depth > 16) return 0;
    switch (LLVMGetTypeKind(t)) {
    case K_POINTER: return 1;
    case K_ARRAY: case K_VECTOR: return contains_pointer(LLVMGetElementType(t), depth + 1);
    case K_STRUCT:
        for (unsigned i = 0; i < LLVMCountStructElementTypes(t); i++) if (contains_pointer(LLVMStructGetTypeAtIndex(t, i), depth + 1)) return 1;
        return 0;
    default: return 0;
    }
}

// The functions of the module's graphics entry points (air.vertex / air.fragment named metadata: operand 0 of each
// node is the function).
static int is_graphics(M m, V fn) {
    static const char *kinds[] = { "air.vertex", "air.fragment" };
    for (int k = 0; k < 2; k++) {
        unsigned n = LLVMGetNamedMetadataNumOperands(m, kinds[k]); if (!n || n > 64) continue;
        V nodes[64]; LLVMGetNamedMetadataOperands(m, kinds[k], nodes);
        for (unsigned i = 0; i < n; i++) {
            unsigned no = LLVMGetMDNodeNumOperands(nodes[i]); if (!no || no > 64) continue;
            V ops[64]; LLVMGetMDNodeOperands(nodes[i], ops);
            if (ops[0] == fn) return 1;
        }
    }
    return 0;
}

// 1. GEP over constant memory with pointer-containing types -> i8 GEP with the 64-bit layout's offset
static int fix_gep(void *ctx, B b, V gep) {
    V base = LLVMGetOperand(gep, 0);
    T pt = LLVMTypeOf(base);
    if (LLVMGetTypeKind(pt) != K_POINTER || LLVMGetPointerAddressSpace(pt) != 2) return 0;
    T t = LLVMGetElementType(pt);
    if (!contains_pointer(t, 0)) return 0;
    T i64 = LLVMInt64TypeInContext(ctx), i8p = LLVMPointerType(LLVMInt8TypeInContext(ctx), 2);
    LLVMPositionBuilderBefore(b, gep);
    long long cst = 0; V dyn = NULL;
    int n = LLVMGetNumOperands(gep);
    for (int i = 1; i < n; i++) {
        V idx = LLVMGetOperand(gep, (unsigned)i);
        if (i == 1) {   // steps over whole objects of the source type
            if (LLVMIsAConstantInt(idx)) cst += LLVMConstIntGetSExtValue(idx) * (long long)LLVMABISizeOfType(td64, t);
            else { V s = LLVMBuildMul(b, LLVMBuildSExtOrBitCast(b, idx, i64, ""), LLVMConstInt(i64, LLVMABISizeOfType(td64, t), 0), "");
                   dyn = dyn ? LLVMBuildAdd(b, dyn, s, "") : s; }
            continue;
        }
        int k = LLVMGetTypeKind(t);
        if (k == K_STRUCT) {
            unsigned f = (unsigned)LLVMConstIntGetSExtValue(idx);
            cst += (long long)LLVMOffsetOfElement(td64, t, f);
            t = LLVMStructGetTypeAtIndex(t, f);
        } else if (k == K_ARRAY || k == K_VECTOR) {
            T e = LLVMGetElementType(t);
            if (LLVMIsAConstantInt(idx)) cst += LLVMConstIntGetSExtValue(idx) * (long long)LLVMABISizeOfType(td64, e);
            else { V s = LLVMBuildMul(b, LLVMBuildSExtOrBitCast(b, idx, i64, ""), LLVMConstInt(i64, LLVMABISizeOfType(td64, e), 0), "");
                   dyn = dyn ? LLVMBuildAdd(b, dyn, s, "") : s; }
            t = e;
        } else return 0;   // not expected after the first index
    }
    V off = LLVMConstInt(i64, (unsigned long long)cst, 1);
    if (dyn) off = LLVMBuildAdd(b, dyn, off, "");
    V raw = LLVMBuildBitCast(b, base, i8p, "ab.raw");
    V p = LLVMBuildGEP(b, raw, &off, 1, "ab.member");
    V cast = LLVMBuildBitCast(b, p, LLVMTypeOf(gep), "ab.ptr");
    LLVMReplaceAllUsesWith(gep, cast);
    LLVMInstructionEraseFromParent(gep);
    return 1;
}

// 2. pointer load from constant memory -> volatile <2 x i32> load + bitcast i64 + inttoptr
static int fix_load(void *ctx, B b, V load) {
    T rt = LLVMTypeOf(load);
    if (LLVMGetTypeKind(rt) != K_POINTER) return 0;
    V addr = LLVMGetOperand(load, 0);
    if (LLVMGetPointerAddressSpace(LLVMTypeOf(addr)) != 2) return 0;
    T v2 = LLVMVectorType(LLVMInt32TypeInContext(ctx), 2);
    LLVMPositionBuilderBefore(b, load);
    V a = LLVMBuildBitCast(b, addr, LLVMPointerType(v2, 2), "ab.addr");
    V w = LLVMBuildLoad(b, a, "ab.words");
    LLVMSetVolatile(w, 1); LLVMSetAlignment(w, 8);
    V i = LLVMBuildBitCast(b, w, LLVMInt64TypeInContext(ctx), "ab.bits");
    V p = LLVMBuildIntToPtr(b, i, rt, "ab.value");
    LLVMReplaceAllUsesWith(load, p);
    LLVMInstructionEraseFromParent(load);
    return 1;
}

static void dump(M m, int n, const char *kind) {
    const char *dir = getenv("ARGBUF_IR_DUMP_DIR"); if (!dir) return;
    char p[1024]; snprintf(p, sizeof p, "%s/%d.%s.ll", dir, n, kind);
    char *err = NULL; LLVMPrintModuleToFile(m, p, &err);
}

void air_transform(void *module) {
    if (!ready || !module) return;
    static int seq; int n = __atomic_add_fetch(&seq, 1, __ATOMIC_RELAXED);
    M m = module; void *ctx = LLVMGetModuleContext(m);
    dump(m, n, "before");
    B b = LLVMCreateBuilderInContext(ctx);
    int geps = 0, loads = 0;
    for (V fn = LLVMGetFirstFunction(m); fn; fn = LLVMGetNextFunction(fn)) {
        if (!LLVMGetFirstBasicBlock(fn) || !is_graphics(m, fn)) continue;
        for (BB bb = LLVMGetFirstBasicBlock(fn); bb; bb = LLVMGetNextBasicBlock(bb))
            for (V in = LLVMGetFirstInstruction(bb), next; in; in = next) {
                next = LLVMGetNextInstruction(in);
                if (LLVMGetInstructionOpcode(in) == OP_GEP) geps += fix_gep(ctx, b, in);
            }
        for (BB bb = LLVMGetFirstBasicBlock(fn); bb; bb = LLVMGetNextBasicBlock(bb))
            for (V in = LLVMGetFirstInstruction(bb), next; in; in = next) {
                next = LLVMGetNextInstruction(in);
                if (LLVMGetInstructionOpcode(in) == OP_LOAD) loads += fix_load(ctx, b, in);
            }
    }
    LLVMDisposeBuilder(b);
    if (geps || loads) syslog(LOG_NOTICE, "icbe_wrap: AIR argument buffers: %d member addresses, %d pointer loads", geps, loads);
    dump(m, n, "after");
}

// ---- IGIL TextureTypeEncode wrapper ------------------------------------------------------------------------------
// The pass builds a map from the function's own texture/sampler arguments; when the function has any, a texture or
// sampler call with an operand outside that map (an argument-buffer handle) has all its uses replaced by
// zeroinitializer (IGILTextureTypeEncode::runOnFunction, ConstantAggregateZero::get + replaceAllUsesWith). Record
// the uses of every call taking a texture/sampler, run the pass, and give back the uses it zeroed.
static int texture_or_sampler(T t) {
    if (LLVMGetTypeKind(t) != K_POINTER) return 0;
    T e = LLVMGetElementType(t);
    if (LLVMGetTypeKind(e) != K_STRUCT) return 0;
    const char *n = LLVMGetStructName(e);
    return n && (strstr(n, "_texture") || strstr(n, "_sampler"));
}
typedef struct { V user, call; unsigned idx; } use_rec;
_Bool air_tte_run(void *pass, void *fn) {
    if (!ready || getenv("ARGBUF_TTE_DISABLE")) return air_tte_orig(pass, fn);
    size_t n = 0, cap = 64; use_rec *r = malloc(cap * sizeof *r);
    for (BB bb = LLVMGetFirstBasicBlock(fn); bb; bb = LLVMGetNextBasicBlock(bb))
        for (V in = LLVMGetFirstInstruction(bb); in; in = LLVMGetNextInstruction(in)) {
            if (!LLVMIsACallInst(in) || LLVMGetTypeKind(LLVMTypeOf(in)) == K_VOID) continue;
            int tex = 0, no = LLVMGetNumOperands(in);
            for (int k = 0; k < no && !tex; k++) tex = texture_or_sampler(LLVMTypeOf(LLVMGetOperand(in, (unsigned)k)));
            if (!tex) continue;
            for (void *u = LLVMGetFirstUse(in); u; u = LLVMGetNextUse(u)) {
                V user = LLVMGetUser(u); int uo = LLVMGetNumOperands(user);
                for (int k = 0; k < uo; k++) if (LLVMGetOperand(user, (unsigned)k) == in) {
                    if (n == cap) r = realloc(r, (cap *= 2) * sizeof *r);
                    r[n++] = (use_rec){ user, in, (unsigned)k };
                }
            }
        }
    _Bool changed = air_tte_orig(pass, fn);
    int restored = 0;
    for (size_t i = 0; i < n; i++) {
        V now = LLVMGetOperand(r[i].user, r[i].idx);
        if (now != r[i].call && LLVMIsAConstantAggregateZero(now)) { LLVMSetOperand(r[i].user, r[i].idx, r[i].call); restored++; }
    }
    free(r);
    if (restored) syslog(LOG_NOTICE, "icbe_wrap: IGILTextureTypeEncode: restored %d uses of argument-buffer texture/sampler calls", restored);
    return changed || restored;
}
