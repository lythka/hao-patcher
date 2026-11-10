/*
 * Misaligned legacy-SSE memory operands in user code (IntelCommpage's #GP fix-up; host harness tests/sse_align_harness.c).
 *
 * Legacy SSE m128 alignment is enforced on Intel; a misaligned operand raises #GP(0). VectorKit's camera code in
 * 26A428 reads matrices with movapd/mulpd/addpd at 8 mod 16, so without this fix-up every map faults on its first frame.
 *
 * This decodes the faulting instruction just enough to find its memory operand: legacy prefixes, REX, the 0F (0F 38,
 * 0F 3A) opcode, ModRM, SIB, displacement and immediate. It accepts only packed SSE/SSE2/SSSE3/SSE4.1 instructions whose m128
 * operand must be aligned (the list below), addressed by a general base register in 64-bit mode. Everything else
 * (VEX, scalar and m64 forms, unaligned moves, RIP-relative, FS/GS, 0x67, no base, base RSP, base == index) is left to
 * the normal #GP. The includer provides nothing; only fixed-width integer arithmetic is used.
 */
#ifndef SSE_ALIGN_H
#define SSE_ALIGN_H

struct sse_align_insn {
    int base, index;          /* x86 register numbers 0-15 (rax..r15); index -1 if none */
    unsigned scale;           /* 1, 2, 4, 8 */
    long long disp;
    int store;                /* the instruction writes its m128 operand */
    unsigned length, xmm, operation, immediate; /* full length, ModRM.reg, table index, imm8 */
};

/* One explicit table feeds both the decoder and the build-time register-form thunks.
 * kind: 0 computes XMM, 1 loads, 2 stores. No GPR/flags destinations or implicit GPR operands. */
struct sse_align_op { unsigned prefix, map, opcode, immediate, kind; };
static const struct sse_align_op sse_align_ops[] = {
#define SSE_OP(name, prefix, map, opcode, immediate, kind) {prefix, map, opcode, immediate, kind},
#include "sse_ops.def"
#undef SSE_OP
};

/* 1 and *out filled when code[0..n) is an accepted instruction; 0 otherwise. */
static inline int sse_align_decode(const unsigned char *code, unsigned n, struct sse_align_insn *out) {
    unsigned i = 0, rex = 0;
    int p66 = 0;
    for (; i < n && i < 4; i++) {
        unsigned b = code[i];
        if (b == 0x66) p66 = 1;
        else if (b == 0x2E || b == 0x3E || b == 0x26 || b == 0x36) continue;
        else if (b == 0x64 || b == 0x65 || b == 0x67 || b == 0xF0 || b == 0xF2 || b == 0xF3) return 0;
        else break;
    }
    if (i < n && (code[i] & 0xF0) == 0x40) rex = code[i++];
    if (i + 2 > n || code[i] != 0x0F) return 0;
    i++;
    unsigned op = code[i++], map = 0;
    if (op == 0x38 || op == 0x3A) {
        map = op;
        if (i >= n) return 0;
        op = code[i++];
    }
    unsigned operation;
    for (operation = 0; operation < sizeof(sse_align_ops)/sizeof(sse_align_ops[0]); operation++) {
        const struct sse_align_op *entry = &sse_align_ops[operation];
        if (entry->prefix == (unsigned)p66 && entry->map == map && entry->opcode == op) break;
    }
    if (operation == sizeof(sse_align_ops)/sizeof(sse_align_ops[0])) return 0;
    const struct sse_align_op *entry = &sse_align_ops[operation];
    if (i >= n) return 0;
    unsigned modrm = code[i++], mod = modrm >> 6, rm = modrm & 7;
    if (mod == 3) return 0;
    int base = -1, index = -1;
    unsigned scale = 1;
    if (rm == 4) {
        if (i >= n) return 0;
        unsigned sib = code[i++], sb = sib & 7, si = (sib >> 3) & 7;
        scale = 1u << (sib >> 6);
        index = (int)(si | ((rex & 2) << 2));
        if (index == 4) index = -1;                                                              /* no index */
        if (sb == 5 && mod == 0) return 0;                                                       /* disp32, no base */
        base = (int)(sb | ((rex & 1) << 3));
    } else {
        if (rm == 5 && mod == 0) return 0;                                                       /* RIP-relative */
        base = (int)(rm | ((rex & 1) << 3));
    }
    long long disp = 0;
    if (mod == 1) {
        if (i >= n) return 0;
        disp = (signed char)code[i++];
    } else if (mod == 2) {
        if (i + 4 > n) return 0;
        disp = (int)((unsigned)code[i] | (unsigned)code[i+1] << 8 | (unsigned)code[i+2] << 16 | (unsigned)code[i+3] << 24);
        i += 4;
    }
    if (base == 4 || base == index) return 0;                                                    /* RSP base, base == index */
    unsigned immediate = 0;
    if (entry->immediate) {
        if (i >= n) return 0;
        immediate = code[i++];
    }
    if (i > 15) return 0;
    out->xmm = ((modrm >> 3) & 7) | ((rex & 4) << 1);
    out->operation = operation; out->immediate = immediate;
    out->base = base; out->index = index; out->scale = scale; out->disp = disp; out->store = entry->kind == 2; out->length = i;
    return 1;
}

/* The effective address, from the 16 general registers in x86 order (rax, rcx, rdx, rbx, rsp, rbp, rsi, rdi, r8..r15). */
static inline unsigned long long sse_align_address(const struct sse_align_insn *d, const unsigned long long reg[16]) {
    unsigned long long a = reg[d->base] + (unsigned long long)d->disp;
    if (d->index >= 0) a += reg[d->index] * d->scale;
    return a;
}

#endif
