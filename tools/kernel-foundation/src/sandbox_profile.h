/*
 * Rewrite a compiled sandbox profile from the target's operation numbering to the donor Sandbox kext's.
 * Shared by IntelCommpage (commpage.cpp) and the host test harness (tests/sandbox_profile_harness.c).
 * The includer provides size_t, memmove and memset.
 *
 * Profile layout, as the donor's profile_init reads it (all integers little-endian):
 *   u16 flags (bit 15: collection), u16 node count, u8 operation count, u8 counts b5..b8, u8 pad,
 *   u16 base-profile string, u16 regex count, u16 message-filter policy count; then u16 tables of
 *   regex, b5, b6, b7, b8 and policy entries; then one u16 node index per operation; nodes start at
 *   the next 8-byte boundary (8 bytes each); data follows the nodes. Node and data references are
 *   relative to those two bases, so only the operation table and the bases move.
 * Nodes: byte 0 = 0 filter (byte 1 filter id & 0x7f, u16 argument, u16 match, u16 unmatch) or 1 terminal
 * (bytes 1-3 action flags; bit 23: one inline 4-byte modifier at +4, else count at +4 and a data
 * offset/8 at +6 of 4-byte modifiers). Modifier 0x15 (message-filter) carries an absolute operation
 * number in byte 1 (a message-filter operation, numbered after the ordinary operations).
 * The kernel-mig-routine filter (mig_filter, 0x4c on both sides) appears in two forms. With bit 7 of byte 1 set
 * (0xcc: the syscall-mig mask form the donor's profile_populate_syscall_masks reads) the argument is a data reference
 * (offset/8) to a bitmap object: u8 type 1, u8 0, u16 length, then bytes; bit i (byte i/8, bit i%8) is
 * kernel-mig-routine index i in libsandbox's numbering. Without it (0x4c: one routine named in a rule, e.g. WebKit's
 * thread_info) the argument is that routine's index itself. The donor kext reads the bitmap by its own numbering (__mig_routine_aliases_) and maps
 * it to message ids itself, so each set bit is moved to the donor's index of the same routine (mig_map; 0xffff:
 * the donor has no such routine, the bit is dropped) and the length trimmed to the last set byte.
 */
#ifndef SANDBOX_PROFILE_H
#define SANDBOX_PROFILE_H

struct sandbox_op_layout {
    unsigned target_ops, donor_ops, target_names, filter_max;
    unsigned drop_count;
    const unsigned short *drop; /* ascending target indices without a donor entry, all < target_ops */
    unsigned mig_filter, mig_count; /* 0: no MIG remap */
    const unsigned short *mig_map;  /* target kernel-mig-routine index -> donor index, 0xffff none */
};

enum { SANDBOX_MIG_BYTES = 64, SANDBOX_MIG_NONE = 0xffff };

enum { SANDBOX_PASS = 0, SANDBOX_ADAPTED = 1, SANDBOX_REFUSED = 2 };
enum {
    SANDBOX_REASON_TRUNCATED = 1, SANDBOX_REASON_NODE_TYPE = 2, SANDBOX_REASON_FILTER = 4,
    SANDBOX_REASON_MESSAGE_FILTER = 8, SANDBOX_REASON_MODIFIERS = 16, SANDBOX_REASON_ENTRY = 32,
    SANDBOX_REASON_MIG = 64
};

static inline unsigned sandbox_u16(const unsigned char *p) { return (unsigned)p[0] | (unsigned)p[1] << 8; }

/* Donor number of a target operation or message-filter operation; ~0u if the donor has none. */
static inline unsigned sandbox_donor_op(const struct sandbox_op_layout *L, unsigned op) {
    unsigned below = 0;
    if (op >= L->target_names) return ~0u;
    for (unsigned i = 0; i < L->drop_count; i++) {
        if (L->drop[i] == op) return ~0u;
        if (L->drop[i] < op) below++;
    }
    return op - below;
}

/* The MIG bitmap object a syscall-mig mask filter node refers to: its offset, or 0 if it is not one. */
static inline size_t sandbox_mig_object(const unsigned char *p, size_t size, size_t data, const unsigned char *node,
                                        const struct sandbox_op_layout *L, unsigned *reason) {
    *reason = 0;
    if (!L->mig_filter || node[0] != 0 || node[1] != (L->mig_filter | 0x80)) return 0;
    size_t at = data + 8 * (size_t)sandbox_u16(node + 2);
    if (at > size || size - at < 4 || p[at] != 1) { *reason = SANDBOX_REASON_MIG; return 0; }
    unsigned length = sandbox_u16(p + at + 2);
    if (length > SANDBOX_MIG_BYTES || length > size - at - 4) { *reason = SANDBOX_REASON_MIG; return 0; }
    for (unsigned i = 0; i < 8 * length; i++)
        if ((p[at + 4 + i / 8] >> (i % 8) & 1) && (i >= L->mig_count || L->mig_map[i] >= 8 * SANDBOX_MIG_BYTES)) {
            if (i >= L->mig_count || L->mig_map[i] != SANDBOX_MIG_NONE) { *reason = SANDBOX_REASON_MIG; return 0; }
        }
    return at;
}

/* Move each set bit of the bitmap at `at` to the donor's index of the same routine; trim the length. */
static inline void sandbox_mig_remap(unsigned char *p, size_t at, const struct sandbox_op_layout *L) {
    unsigned char bits[SANDBOX_MIG_BYTES];
    unsigned length = sandbox_u16(p + at + 2), used = 0;
    memset(bits, 0, sizeof bits);
    for (unsigned i = 0; i < 8 * length; i++) {
        if (!(p[at + 4 + i / 8] >> (i % 8) & 1)) continue;
        unsigned d = L->mig_map[i];
        if (d == SANDBOX_MIG_NONE) continue;
        bits[d / 8] |= (unsigned char)(1u << (d % 8));
        if (d / 8 + 1 > used) used = d / 8 + 1;
    }
    if (!used && length) used = 1;
    memset(p + at + 4, 0, length);
    for (unsigned i = 0; i < used; i++) p[at + 4 + i] = bits[i];
    p[at + 2] = (unsigned char)used;
    p[at + 3] = (unsigned char)(used >> 8);
}

/* Check every node (and, with write, renumber message-filter modifiers and remap MIG bitmaps). Returns a reason or 0. */
static inline unsigned sandbox_modifiers(unsigned char *p, size_t size, size_t nodes, unsigned count,
                                         size_t data, const struct sandbox_op_layout *L, int write) {
    for (unsigned n = 0; n < count; n++) {
        unsigned char *node = p + nodes + 8 * (size_t)n;
        if (node[0] == 0) {
            if ((node[1] & 0x7f) == 0 || (node[1] & 0x7f) > L->filter_max) return SANDBOX_REASON_FILTER;
            if (L->mig_filter && node[1] == L->mig_filter) {
                /* One routine inline: its index moves to the donor's; a routine the donor lacks matches nothing. */
                unsigned routine = sandbox_u16(node + 2);
                if (routine >= L->mig_count) return SANDBOX_REASON_MIG;
                if (write) {
                    unsigned d = L->mig_map[routine];
                    node[2] = (unsigned char)d;
                    node[3] = (unsigned char)(d >> 8);
                }
                continue;
            }
            unsigned why;
            size_t at = sandbox_mig_object(p, size, data, node, L, &why);
            if (why) return why;
            if (at && write) {
                /* Nodes can share one bitmap object: remap it at its first reference only. */
                int seen = 0;
                for (unsigned m = 0; m < n && !seen; m++) {
                    const unsigned char *earlier = p + nodes + 8 * (size_t)m;
                    seen = earlier[0] == 0 && earlier[1] == (L->mig_filter | 0x80) &&
                           sandbox_u16(earlier + 2) == sandbox_u16(node + 2);
                }
                if (!seen) sandbox_mig_remap(p, at, L);
            }
            continue;
        }
        if (node[0] != 1) return SANDBOX_REASON_NODE_TYPE;
        unsigned char *list = node + 4;
        unsigned entries = 1;
        if (!(node[3] & 0x80)) {
            entries = node[4];
            if (!entries) continue;
            size_t at = data + 8 * (size_t)sandbox_u16(node + 6);
            if (at > size || 4 * (size_t)entries > size - at) return SANDBOX_REASON_MODIFIERS;
            list = p + at;
        }
        for (unsigned m = 0; m < entries; m++) {
            unsigned char *modifier = list + 4 * m;
            if (modifier[0] != 0x15) continue;
            if (modifier[1] < L->target_ops) return SANDBOX_REASON_MESSAGE_FILTER;
            unsigned op = sandbox_donor_op(L, modifier[1]);
            if (op == ~0u) return SANDBOX_REASON_MESSAGE_FILTER;
            if (write) modifier[1] = (unsigned char)op;
        }
    }
    return 0;
}

/*
 * A non-collection profile with the target's operation count is rewritten in place to the donor's layout:
 * the dropped operations' entries are removed, the nodes and data move down with the operation table's end,
 * message-filter operation numbers are renumbered and the count byte is set. *new_size receives the length.
 * Anything else passes unchanged (SANDBOX_PASS). A target-count profile this cannot rewrite exactly is left
 * unchanged and refused (SANDBOX_REFUSED, *reason set); nothing is written before every check passed.
 */
static inline int sandbox_profile_adapt(unsigned char *p, size_t size, const struct sandbox_op_layout *L,
                                        size_t *new_size, unsigned *reason) {
    *new_size = size;
    *reason = 0;
    if (size < 16 || (p[1] & 0x80) || p[4] != L->target_ops) return SANDBOX_PASS;
    size_t ops = 0x10 + 2 * ((size_t)sandbox_u16(p + 0xc) + p[5] + p[6] + p[7] + p[8] + sandbox_u16(p + 0xe));
    size_t ops_end = ops + 2 * (size_t)L->target_ops;
    size_t nodes = (ops_end + 7) & ~(size_t)7;
    unsigned count = sandbox_u16(p + 2);
    size_t data = nodes + 8 * (size_t)count;
    if (data > size) { *reason = SANDBOX_REASON_TRUNCATED; return SANDBOX_REFUSED; }
    for (unsigned i = 0; i < L->target_ops; i++) {
        if (sandbox_donor_op(L, i) != ~0u && sandbox_u16(p + ops + 2 * i) >= count) {
            *reason = SANDBOX_REASON_ENTRY; return SANDBOX_REFUSED;
        }
    }
    *reason = sandbox_modifiers(p, size, nodes, count, data, L, 0);
    if (*reason) return SANDBOX_REFUSED;
    sandbox_modifiers(p, size, nodes, count, data, L, 1);
    size_t w = ops;
    for (unsigned i = 0; i < L->target_ops; i++) {
        if (sandbox_donor_op(L, i) == ~0u) continue;
        p[w] = p[ops + 2 * i];
        p[w + 1] = p[ops + 2 * i + 1];
        w += 2;
    }
    size_t donor_nodes = (w + 7) & ~(size_t)7;
    memset(p + w, 0, donor_nodes - w);
    memmove(p + donor_nodes, p + nodes, size - nodes);
    p[4] = (unsigned char)L->donor_ops;
    *new_size = size - (nodes - donor_nodes);
    return SANDBOX_ADAPTED;
}

#endif
