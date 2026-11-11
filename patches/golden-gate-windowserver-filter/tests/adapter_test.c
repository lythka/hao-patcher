#include "../src/adapter.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

struct context { const void **table; unsigned changed; };
static struct context *expected;
static const void *callbacks;
static const void *original[14];
static unsigned calls, slot;

static void callback(struct context *info) { assert(info == expected); ++info->changed; }

// The exact failing dispatch, recreated in source, without a Metal device or GUI:
// prepare_one_filter calls actual_inverse_color at the build's vtable slot.
__attribute__((noinline)) static bool query(void *info)
{
    bool (*fn)(void *) = (void *)((const void ***)info)[0][slot];
    return fn(info);
}

static void render(void *r, void *u, const void *cb, void *info, unsigned flags)
{
    assert(r == (void *)1 && u == (void *)2 && cb == callbacks && flags == 0x51);
    assert(info == expected);
    assert(!query(info));
    struct context *c = info;
    for (unsigned i = 0; i < 14; ++i) {
        assert(c->table[i] == original[i]);
        ((void (*)(struct context *))c->table[i])(c);
    }
    for (unsigned i = 14; i < slot; ++i) assert(!c->table[i]);
    ++calls;
}

static void passthrough(void *r, void *u, const void *cb, void *info, unsigned flags)
{
    assert(r == (void *)1 && u == (void *)2 && cb == callbacks && flags == 0x51);
    assert(info == expected);
    if (info) assert(expected->table == original);
    ++calls;
}

static struct filter_identity id(const void *table, const void *cb, unsigned count, unsigned at)
{
    return (struct filter_identity){table, cb, count, at};
}

static void nested(void *r, void *u, const void *cb, void *info, unsigned flags)
{
    const void **outer = expected->table;
    // Reentry with the same context sees the already adapted table and forwards.
    assert(!filter_render(render, id(original, callbacks, 14, slot), r, u, cb, info, flags));
    assert(expected->table == outer);
}

int main(int argc, char **argv)
{
    for (unsigned i = 0; i < 14; ++i) original[i] = (void *)callback;
    struct context c = {original, 0}; expected = &c; callbacks = (void *)3;
    if (argc > 1) {
        // Put the actual fourteen-entry table at the end of a readable page.
        // Unadapted slot (argv[1]) is out of bounds; SIGSEGV proves the fixture fails.
        slot = (unsigned)atoi(argv[1]);
        size_t page = (size_t)getpagesize();
        char *p = mmap(NULL, page*2, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANON, -1, 0);
        assert(p != MAP_FAILED && !mprotect(p+page, page, PROT_NONE));
        c.table = (const void **)(p+page-sizeof original);
        memcpy(c.table, original, sizeof original);
        return query(&c);
    }
    // 26A428 QuartzCore uses slot 124, 26B131 slot 126.
    static const unsigned slots[] = {124, 126};
    for (unsigned s = 0; s < 2; ++s) {
        slot = slots[s];
        c.changed = calls = 0;
        assert(filter_render(render, id(original, callbacks, 14, slot), (void *)1, (void *)2, callbacks, &c, 0x51));
        assert(c.table == original && c.changed == 14 && calls == 1);
        assert(filter_render(nested, id(original, callbacks, 14, slot), (void *)1, (void *)2, callbacks, &c, 0x51));
        assert(c.table == original && c.changed == 28 && calls == 2);
        assert(!filter_render(passthrough, (struct filter_identity){0}, (void *)1, (void *)2, callbacks, &c, 0x51));
        assert(!filter_render(passthrough, id(original, (void *)4, 14, slot), (void *)1, (void *)2, callbacks, &c, 0x51));
        assert(!filter_render(passthrough, id((void *)4, callbacks, 14, slot), (void *)1, (void *)2, callbacks, &c, 0x51));
        // A profile whose slot the stack table cannot hold, or below the API entries, never adapts.
        assert(!filter_render(passthrough, id(original, callbacks, 14, FILTER_TABLE_MAX), (void *)1, (void *)2, callbacks, &c, 0x51));
        assert(!filter_render(passthrough, id(original, callbacks, 14, 13), (void *)1, (void *)2, callbacks, &c, 0x51));
        expected = NULL;
        assert(!filter_render(passthrough, id(original, callbacks, 14, slot), (void *)1, (void *)2, callbacks, NULL, 0x51));
        expected = &c;
        assert(calls == 8);
    }
    puts("PASS: inversion query at slots 124 and 126, 14 callbacks, context writes, restoration, reentry, "
         "identity and profile mismatch, null context");
}
