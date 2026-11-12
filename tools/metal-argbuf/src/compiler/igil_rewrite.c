// igil_rewrite.c: IGIL text rewrite giving the HD 4000 (Gen7) native argument-buffer resources.
//
// The IGIL target (libIGIL-Metal) lowers argument-buffer members, but leaves two things iCBE cannot compile or the
// driver cannot serve:
//   textures  LD_UAV_TYPED rD, rC, u[rI.x]      (handle rI, no declared range)    -> LOAD rD, rC, t[TBASE+rI], 2D
//   buffers   a pointer on this driver is (UAV register << 32) | byte offset, and the backend selects the surface
//             from the high word on every access; the high word is the binding-table index, which is (number of
//             constant buffers) + (number of textures) + register number. A pointer loaded from an argument buffer works once its surface is
//             declared; the surfaces are declared so that their binding-table index is fixed, BUF_BTI+k, so the
//             encoder writes (BUF_BTI+h) << 32 | offset without knowing the pipeline.
// Argument-buffer textures are declared as t(TBASE+k) at texture slot TEX_SLOT+k and buffers as u(UBASE+k) at buffer
// slot BUF_SLOT+k, with kernel-argument numbers after the shader's own. Non-uniform texture handles keep the IGIL
// target's own waterfall loop.
#include "igil_rewrite.h"
#include <ctype.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { char *p; size_t n, cap; } buf;
static void put(buf *b, const char *s, size_t n) {
    if (b->n + n + 1 > b->cap) { b->cap = (b->n + n + 1) * 2 + 256; b->p = realloc(b->p, b->cap); }
    memcpy(b->p + b->n, s, n); b->n += n; b->p[b->n] = 0;
}
static void putf(buf *b, const char *fmt, ...) {
    char t[1024]; va_list a; va_start(a, fmt); int n = vsnprintf(t, sizeof t, fmt, a); va_end(a);
    put(b, t, (size_t)n);
}

// ---- lines ------------------------------------------------------------------------------------------------------
typedef struct { const char *s; size_t n; } line;
static line *split(const char *in, int *count) {
    int cap = 256, n = 0; line *v = malloc(cap * sizeof *v);
    for (const char *p = in; *p;) {
        const char *e = strchr(p, '\n'); size_t len = e ? (size_t)(e - p) : strlen(p);
        if (n == cap) v = realloc(v, (cap *= 2) * sizeof *v);
        v[n++] = (line){ p, len };
        p += len + (e ? 1 : 0);
    }
    *count = n; return v;
}

// An instruction: opcode and up to 6 operands, from "\tOPC[.mod] a, b, c;"
typedef struct { char op[48]; char a[10][64]; int na; } insn;
static int parse(line l, insn *x) {
    const char *p = l.s, *e = l.s + l.n;
    while (p < e && isspace((unsigned char)*p)) p++;
    if (p >= e || !isupper((unsigned char)*p)) return 0;
    const char *semi = memchr(p, ';', (size_t)(e - p)); if (!semi) return 0;
    int i = 0; while (p < semi && !isspace((unsigned char)*p) && i < 47) x->op[i++] = *p++; x->op[i] = 0;
    x->na = 0;
    while (p < semi && x->na < 10) {
        while (p < semi && (isspace((unsigned char)*p) || *p == ',')) p++;
        if (p >= semi) break;
        int j = 0, depth = 0;   // commas inside (...) or [...] belong to the operand
        while (p < semi && (depth || *p != ',') && j < 63) {
            if (*p == '(' || *p == '[') depth++; else if ((*p == ')' || *p == ']') && depth) depth--;
            x->a[x->na][j++] = *p++;
        }
        while (j && isspace((unsigned char)x->a[x->na][j - 1])) j--;
        x->a[x->na][j] = 0; x->na++;
    }
    return 1;
}
static int reg(const char *s) { return s[0] == 'r' && isdigit((unsigned char)s[1]) ? atoi(s + 1) : -1; }
static int ureg(const char *s) { return s[0] == 'u' && isdigit((unsigned char)s[1]) ? atoi(s + 1) : -1; }
static int creg(const char *s) { return s[0] == 'C' && isdigit((unsigned char)s[1]) ? atoi(s + 1) : -1; }
static int startswith(const char *s, const char *p) { return !strncmp(s, p, strlen(p)); }

// pointer accesses: which operand is the surface, which the address
typedef struct { const char *op; int surf, addr; } ptrop;
static const ptrop PTROPS[] = {
    { "LD_RAW_PTR", 2, 1 }, { "BLD_RAW_PTR", 2, 1 }, { "SLD_RAW_PTR", 2, 1 },
    { "STORE_RAW_PTR", 0, 1 }, { "BSTORE_RAW_PTR", 0, 1 }, { "SSTORE_RAW_PTR", 0, 1 },
};
static const ptrop *ptr_access(const insn *x) {
    for (size_t i = 0; i < sizeof PTROPS / sizeof *PTROPS; i++) {
        size_t n = strlen(PTROPS[i].op);
        if (!strncmp(x->op, PTROPS[i].op, n) && (x->op[n] == 0 || x->op[n] == '.')) return &PTROPS[i];
    }
    return NULL;
}
static int is_store(const insn *x) { return strstr(x->op, "STORE") != NULL; }
static int is_load(const insn *x) {
    return startswith(x->op, "LD") || startswith(x->op, "BLD") || startswith(x->op, "SLD") || startswith(x->op, "LOAD")
        || startswith(x->op, "GATHER") || startswith(x->op, "SAMPLE") || strstr(x->op, "ATOMIC");
}

// "s0[r28.x]" with r28 holding argument-buffer data (a sampler handle)
static int abt_index(const char *op, const char *abt, int temps);
static int sampler_index(const char *op, const char *abt, int temps) {
    if (op[0] != 's' || !isdigit((unsigned char)op[1])) return 0;
    const char *b = strchr(op, '['); if (!b || b[1] != 'r') return 0;
    int r = atoi(b + 2); return r >= 0 && r <= temps && abt[r];
}
// "t[r9.x]" -> 9 when r9 holds argument-buffer data
static int abt_index(const char *op, const char *abt, int temps) {
    if (op[1] != '[' || op[2] != 'r') return 0;
    int r = atoi(op + 3); return r >= 0 && r <= temps && abt[r];
}

static char *rewrite_resources(const char *in, const igil_config *cfg, igil_stats *st) {
    int nl; line *L = split(in, &nl);
    int temps = -1, tmax = -1, umax = -1, smax = -1, karg = -1, dcl_at = -1, temps_at = -1, ncb = 0, ntexdcl = 0;
    for (int i = 0; i < nl; i++) {
        char t[256]; size_t n = L[i].n < 255 ? L[i].n : 255; memcpy(t, L[i].s, n); t[n] = 0;
        const char *d;
        if ((d = strstr(t, "DCL_TEMPS r0..r"))) { temps = atoi(d + 15); temps_at = i; }
        if ((d = strstr(t, "DCL_SAMPLER s")) && atoi(d + 13) > smax) smax = atoi(d + 13);
        if ((d = strstr(t, "DCL_RESOURCE t")) && atoi(d + 14) > tmax) tmax = atoi(d + 14);
        if ((d = strstr(t, "DCL_UAV")) && (d = strstr(d, " u")) && isdigit((unsigned char)d[2]) && atoi(d + 2) > umax) umax = atoi(d + 2);
        if ((d = strstr(t, "KERNEL_ARGUMENT, ")) && atoi(d + 17) > karg) karg = atoi(d + 17);
        if (strstr(t, "DCL_CONSTANTBUFFER C")) ncb++;
        if (strstr(t, "DCL_RESOURCE t")) ntexdcl++;
        if (dcl_at < 0 && (strstr(t, "\tDCL_INPUT") == t || strstr(t, "\tDCL_TEMPS") == t)) dcl_at = i;
    }
    if (temps < 0 || dcl_at < 0) { free(L); return NULL; }

    // taint: registers holding values loaded from memory (flow-insensitive fixed point), and abt: values that came
    // from a constant buffer or a buffer surface (argument-buffer contents), followed through private-memory spills
    char *taint = calloc((size_t)temps + 1, 1), *abt = calloc((size_t)temps + 1, 1);
    int spill = 0;   // an argument-buffer value was stored to private memory
    insn x;
    for (int changed = 1; changed;) {
        changed = 0;
        for (int i = temps_at + 1; i < nl; i++) {
            if (!parse(L[i], &x) || x.na < 2) continue;
            if (is_store(&x)) {
                if (!spill && x.a[0][0] == 'm') for (int k = 1; k < x.na; k++) { int s2 = reg(x.a[k]); if (s2 >= 0 && s2 <= temps && abt[s2]) { spill = 1; changed = 1; } }
                continue;
            }
            int d = reg(x.a[0]); if (d < 0 || d > temps) continue;
            int t = is_load(&x), a = 0;
            if (t) {   // which memory: the surface operand (last operand of loads)
                const char *sf = x.a[x.na - 1];
                a = sf[0] == 'C' || (sf[0] == 'u' && (isdigit((unsigned char)sf[1]) || sf[1] == '[')) || (sf[0] == 'm' && spill);
                if (startswith(x.op, "SAMPLE") || startswith(x.op, "LOAD") || startswith(x.op, "GATHER")) a = 0;
            }
            for (int k = 1; k < x.na; k++) {
                int s2 = reg(x.a[k]); if (s2 < 0 || s2 > temps) continue;
                if (taint[s2]) t = 1;
                if (abt[s2] && !is_load(&x)) a = 1;
            }
            if (t && !taint[d]) { taint[d] = 1; changed = 1; }
            if (a && !abt[d]) { abt[d] = 1; changed = 1; }
        }
    }
    // what to rewrite
    int ntex = 0, nbuf = 0, nsamp = 0;
    for (int i = temps_at + 1; i < nl; i++) {
        if (!parse(L[i], &x)) continue;
        for (int k = 0; k < x.na; k++) if (sampler_index(x.a[k], abt, temps)) { nsamp++; break; }
        if (!strcmp(x.op, "LD_UAV_TYPED") && x.na == 3 && startswith(x.a[2], "u[")) ntex++;
        for (int k = 0; k < x.na; k++) if (startswith(x.a[k], "t[") && abt_index(x.a[k], abt, temps)) { ntex++; break; }
        const ptrop *p = ptr_access(&x);
        if (p && x.na > p->surf && (ureg(x.a[p->surf]) >= 0 || creg(x.a[p->surf]) >= 0)) { int a = reg(x.a[p->addr]); if (a >= 0 && a <= temps && taint[a]) nbuf++; }
    }
    if (!ntex && !nbuf && !nsamp) { free(taint); free(abt); free(L); return NULL; }

    int tbase = tmax + 1, ubase = umax + 1, sbase = smax + 1;
    int T = temps + 1;   // scratch registers: T (texture index), T+1 (sampler index)
    // binding table: constant buffers, then textures, then UAVs by register number (measured on the HD 4000), so
    // uN has index ncb + ntex + N and u(BUF_BTI - ncb - ntex + k) has the fixed index BUF_BTI + k
    // The texture range is declared whenever buffers are rewritten too: it keeps uN registers low (iCBE rejects UAV
    // registers in the 150s) and the binding-table arithmetic the same in every pipeline.
    int dtex = ntex || nbuf;
    int ufix = cfg->buf_bti - ncb - ntexdcl - (dtex ? cfg->tex_count : 0);
    if (nbuf && umax >= ufix) { free(taint); free(abt); free(L); return NULL; }   // would collide with the fixed range
    buf o = {0};
    for (int i = 0; i < nl; i++) {
        if (i == dcl_at) {
            int ka = karg + 1;
            if (dtex) for (int k = 0; k < cfg->tex_count; k++)
                putf(&o, "\tDCL_RESOURCE t%d, 2D, FLOAT =\n\t{\n\t  KERNEL_ARGUMENT, %d,\n\t  LOCATION_INDEX, %d, 0,\n"
                         "\t  EMULATION_ARGUMENT, FALSE,\n\t  SAMPLER_NUM, [4294967295]\n\t};\n\n", tbase + k, ka++, cfg->tex_slot + k);
            if (nbuf) for (int k = 0; k < cfg->buf_count; k++)
                putf(&o, "\tDCL_UAVRAW u%d, TRUE =\n\t{\n\t  KERNEL_ARGUMENT, %d,\n\t  LOCATION_INDEX, %d, 0,\n"
                         "\t  EMULATION_ARGUMENT, FALSE,\n\t  STRIDE, 4, 4\n\t};\n\n", ufix + k, ka++, cfg->buf_slot + k);
            if (nsamp) for (int k = 0; k < cfg->samp_count; k++)
                putf(&o, "\tDCL_SAMPLER s%d =\n\t{\n\t  KERNEL_ARGUMENT, %d,\n\t  LOCATION_INDEX, %d, 0,\n"
                         "\t  EMULATION_ARGUMENT, FALSE\n\t};\n\n", sbase + k, ka++, cfg->samp_slot + k);
        }
        if (i == temps_at) { putf(&o, "\tDCL_TEMPS r0..r%d;\n", T + 1); continue; }
        if (i > temps_at && parse(L[i], &x)) {
            if (!strcmp(x.op, "LD_UAV_TYPED") && x.na == 3 && startswith(x.a[2], "u[")) {
                char idx[48]; strcpy(idx, x.a[2] + 2); idx[strcspn(idx, "]")] = 0;
                putf(&o, "\tIADD r%d.x, %s, %d;\n\tLOAD %s, %s, t[r%d.x], 2D;\n", T, idx, tbase, x.a[0], x.a[1], T);
                st->textures++; continue;
            }
            int ti = -1, si = -1;
            for (int k = 0; k < x.na; k++) {
                if (startswith(x.a[k], "t[") && abt_index(x.a[k], abt, temps)) ti = k;
                if (sampler_index(x.a[k], abt, temps)) si = k;
            }
            if (ti >= 0 || si >= 0) {   // sample/load/gather on argument-buffer handles: offset into the declared ranges
                char idx[48];
                if (ti >= 0) { strcpy(idx, x.a[ti] + 2); idx[strcspn(idx, "]")] = 0; putf(&o, "\tIADD r%d.x, %s, %d;\n", T, idx, tbase); st->textures++; }
                if (si >= 0) { strcpy(idx, strchr(x.a[si], '[') + 1); idx[strcspn(idx, "]")] = 0; putf(&o, "\tIADD r%d.x, %s, %d;\n", T + 1, idx, sbase); st->samplers++; }
                putf(&o, "\t%s ", x.op);
                for (int k = 0; k < x.na; k++) {
                    if (k) put(&o, ", ", 2);
                    if (k == ti) putf(&o, "t[r%d.x]", T);
                    else if (k == si) putf(&o, "s0[r%d.x]", T + 1);
                    else put(&o, x.a[k], strlen(x.a[k]));
                }
                put(&o, ";\n", 2); continue;
            }
            {
                const ptrop *p = ptr_access(&x);
                int isu = p && x.na > p->surf && ureg(x.a[p->surf]) >= 0, isc = p && x.na > p->surf && creg(x.a[p->surf]) >= 0;
                int a = isu || isc ? reg(x.a[p->addr]) : -1;
                if (a >= 0 && a <= temps && taint[a]) {
                    st->buffers++;
                    if (isc) {   // `constant` pointer from an argument buffer: a C surface ignores the pointer's high
                                 // word (reads the outer constant buffer); a UAV surface selects by the high word
                        putf(&o, "\t%s ", x.op);
                        for (int k = 0; k < x.na; k++) {
                            if (k) put(&o, ", ", 2);
                            if (k == p->surf) putf(&o, "u%d", ufix); else put(&o, x.a[k], strlen(x.a[k]));
                        }
                        put(&o, ";\n", 2); continue;
                    }
                }
            }
        }
        put(&o, L[i].s, L[i].n); put(&o, "\n", 1);
    }
    // keep the input's trailing-newline shape
    if (o.n && in[strlen(in) - 1] != '\n') o.p[--o.n] = 0;
    st->tex_base = tbase; st->buf_base = ufix; st->samp_base = sbase; (void)ubase;
    free(taint); free(abt); free(L);
    return o.p;
}

// ---- graphics: pointer dereferences ----------------------------------------------------------------------------
// Vertex/pixel programs (the IGIL target's 32-bit graphics lowering) read through a pointer loaded from an argument
// buffer as `MOV rD, C-1[rI.x]` (constant pointer: rI = byte address >> 4, "C-1" = no buffer) or
// `LD_RAW rD, rA.x, u0` (device pointer, u0 undeclared). Only the pointer's low word (byte offset) survives; its high
// word, BUF_BTI + h, is still in .y of the LD_RAW that loaded the pointer (air_pass.c makes that a 64-bit load). Each
// access becomes LD_RAW from a declared raw-UAV range selected by h: u(ubase+h) at buffer slot BUF_SLOT+h (emit_read).
typedef struct { int ptr; int addr; } gdef;   // per register: LD_RAW register holding the pointer, byte-address register
static int graphics(const char *in) { return strstr(in, "PIXELSHADER ") || strstr(in, "VERTEXSHADER "); }

// One read through a pointer: pixel shaders index the UAV range with the handle; vertex shaders (Gen7 SIMD4x2) ignore a
// dynamic surface index -- measured: they always read the first UAV -- so they select the surface with a compare chain
// (the handle is uniform per draw).
static void emit_read(buf *o, int vertex, int T, int ptr, const char *dst, const char *addr, int ubase, const igil_config *cfg) {
    if (!vertex) { putf(o, "\tIADD r%d.x, r%d.y, %d;\n\tLD_RAW %s, %s, u[r%d.x];\n", T, ptr, ubase - cfg->buf_bti, dst, addr, T); return; }
    putf(o, "\tIADD r%d.x, r%d.y, %d;\n", T, ptr, -cfg->buf_bti);
    for (int k = 0; k < cfg->buf_count; k++) putf(o, "\tIF_ICMP.eq r%d.x, %d;\n\tLD_RAW %s, %s, u%d;\n\tENDIF;\n", T, k, dst, addr, ubase + k);
}

static char *rewrite_graphics_buffers(const char *in, const igil_config *cfg, igil_stats *st) {
    int vertex = strstr(in, "VERTEXSHADER ") != NULL;
    int nl; line *L = split(in, &nl);
    int temps = -1, temps_at = -1, umax = -1, karg = -1, dcl_at = -1;
    for (int i = 0; i < nl; i++) {
        char t[256]; size_t n = L[i].n < 255 ? L[i].n : 255; memcpy(t, L[i].s, n); t[n] = 0;
        const char *d;
        if ((d = strstr(t, "DCL_TEMPS r0..r"))) { temps = atoi(d + 15); temps_at = i; }
        if ((d = strstr(t, "DCL_UAV")) && (d = strstr(d, " u")) && isdigit((unsigned char)d[2]) && atoi(d + 2) > umax) umax = atoi(d + 2);
        if ((d = strstr(t, "KERNEL_ARGUMENT, ")) && atoi(d + 17) > karg) karg = atoi(d + 17);
        if (dcl_at < 0 && (strstr(t, "\tDCL_INPUT") == t || strstr(t, "\tDCL_TEMPS") == t)) dcl_at = i;
    }
    if (temps < 0 || dcl_at < 0) { free(L); return NULL; }
    gdef *g = malloc(((size_t)temps + 1) * sizeof *g);
    for (int r = 0; r <= temps; r++) g[r] = (gdef){ -1, -1 };
    insn x;
    // pointer provenance, in program order (graphics programs here are straight-line apart from waterfall loops)
    for (int i = temps_at + 1; i < nl; i++) {
        if (!parse(L[i], &x) || x.na < 2) continue;
        int d = reg(x.a[0]); if (d < 0 || d > temps) continue;
        if (!strcmp(x.op, "LD_RAW") && x.na == 3 && (x.a[2][0] == 'C' || x.a[2][0] == 'u')) { g[d] = (gdef){ d, -1 }; continue; }
        gdef v = { -1, -1 };
        if (!strcmp(x.op, "SHR") && x.na == 3 && !strcmp(x.a[2], "4")) {   // byte address -> 16-byte register index
            int s2 = reg(x.a[1]); if (s2 >= 0 && s2 <= temps && g[s2].ptr >= 0) v = (gdef){ g[s2].ptr, s2 };
        } else if (!strcmp(x.op, "MOV") || !strcmp(x.op, "IADD") || !strcmp(x.op, "SHL")) {
            for (int k = 1; k < x.na && v.ptr < 0; k++) { int s2 = reg(x.a[k]); if (s2 >= 0 && s2 <= temps && g[s2].ptr >= 0) v = (gdef){ g[s2].ptr, -1 }; }
        }
        g[d] = v;
    }
    int need = 0;
    for (int i = temps_at + 1; i < nl; i++) {
        if (!parse(L[i], &x)) continue;
        for (int k = 0; k < x.na; k++) if (startswith(x.a[k], "C-1[")) need = 1;
        if (!strcmp(x.op, "LD_RAW") && x.na == 3 && !strcmp(x.a[2], "u0") && umax < 0) need = 1;
    }
    if (!need) { free(g); free(L); return NULL; }
    int ubase = umax + 1, T = temps + 1;   // scratch T (index), T+1 (address), T+2 (loaded register)
    buf o = {0}; int fails = 0;
    for (int i = 0; i < nl; i++) {
        if (i == dcl_at) {
            int ka = karg + 1;
            for (int k = 0; k < cfg->buf_count; k++)
                putf(&o, "\tDCL_UAVRAW u%d, TRUE =\n\t{\n\t  KERNEL_ARGUMENT, %d,\n\t  LOCATION_INDEX, %d, 0,\n"
                         "\t  EMULATION_ARGUMENT, FALSE,\n\t  STRIDE, 4, 4\n\t};\n\n", ubase + k, ka++, cfg->buf_slot + k);
        }
        if (i == temps_at) { putf(&o, "\tDCL_TEMPS r0..r%d;\n", T + 2); continue; }
        if (i > temps_at && parse(L[i], &x)) {
            if (!strcmp(x.op, "LD_RAW") && x.na == 3 && !strcmp(x.a[2], "u0") && umax < 0) {   // device pointer
                int a = reg(x.a[1]);
                if (a >= 0 && a <= temps && g[a].ptr >= 0) {
                    emit_read(&o, vertex, T, g[a].ptr, x.a[0], x.a[1], ubase, cfg);
                    st->buffers++; continue;
                }
                fails++;
            }
            int ck = -1;
            for (int k = 0; k < x.na; k++) if (startswith(x.a[k], "C-1[")) ck = k;
            if (ck >= 0) {   // constant pointer: load the 16-byte register the operand names, keep its swizzle
                int ri = reg(x.a[ck] + 4); const char *close = strchr(x.a[ck], ']');
                if (ri >= 0 && ri <= temps && close && g[ri].ptr >= 0 && g[ri].addr >= 0) {
                    char d2[16], a2[16]; snprintf(d2, sizeof d2, "r%d", T + 2); snprintf(a2, sizeof a2, "r%d.x", T + 1);
                    putf(&o, "\tAND r%d.x, r%d.x, -16;\n", T + 1, g[ri].addr);
                    emit_read(&o, vertex, T, g[ri].ptr, d2, a2, ubase, cfg);
                    putf(&o, "\t%s ", x.op);
                    for (int k = 0; k < x.na; k++) {
                        if (k) put(&o, ", ", 2);
                        if (k == ck) putf(&o, "r%d%s", T + 2, close + 1); else put(&o, x.a[k], strlen(x.a[k]));
                    }
                    put(&o, ";\n", 2); st->buffers++; continue;
                }
                fails++;
            }
        }
        put(&o, L[i].s, L[i].n); put(&o, "\n", 1);
    }
    if (o.n && in[strlen(in) - 1] != '\n') o.p[--o.n] = 0;
    if (fails) st->unresolved += fails;
    st->buf_base = ubase;
    free(g); free(L);
    return o.p;
}

char *igil_rewrite(const char *in, const igil_config *cfg, igil_stats *st) {
    memset(st, 0, sizeof *st);
    char *r = rewrite_resources(in, cfg, st);
    if (!graphics(in)) return r;
    igil_stats gs; memset(&gs, 0, sizeof gs);
    char *r2 = rewrite_graphics_buffers(r ? r : in, cfg, &gs);
    if (!r2) return r;
    free(r);
    st->buffers += gs.buffers; st->buf_base = gs.buf_base; st->unresolved += gs.unresolved;
    return r2;
}
