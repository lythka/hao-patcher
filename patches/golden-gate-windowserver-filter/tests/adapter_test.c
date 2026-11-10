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
static unsigned calls;

static void callback(struct context *info) { assert(info == expected); ++info->changed; }

// The exact failing dispatch, recreated in source, without a Metal device or GUI.
__attribute__((noinline)) static bool query(void *info)
{
    bool (*fn)(void *) = (void *)((const void ***)info)[0][124];
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
    ++calls;
}

static void passthrough(void *r, void *u, const void *cb, void *info, unsigned flags)
{
    assert(r == (void *)1 && u == (void *)2 && cb == callbacks && flags == 0x51);
    assert(info == expected);
    if (info) assert(expected->table == original);
    ++calls;
}

static void nested(void *r, void *u, const void *cb, void *info, unsigned flags)
{
    const void **outer = expected->table;
    // Reentry with the same context sees the already adapted table and forwards.
    assert(!filter_render(render, (struct filter_identity){original, callbacks}, r, u, cb, info, flags));
    assert(expected->table == outer);
}

int main(int argc, char **argv)
{
    (void)argv;
    for (unsigned i = 0; i < 14; ++i) original[i] = (void *)callback;
    struct context c = {original, 0}; expected = &c; callbacks = (void *)3;
    if (argc > 1) {
        // Put the actual fourteen-entry table at the end of a readable page.
        // Unadapted slot 124 is out of bounds; SIGSEGV proves the fixture fails.
        size_t page = (size_t)getpagesize();
        char *p = mmap(NULL, page*2, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANON, -1, 0);
        assert(p != MAP_FAILED && !mprotect(p+page, page, PROT_NONE));
        c.table = (const void **)(p+page-sizeof original);
        memcpy(c.table, original, sizeof original);
        return query(&c);
    }
    assert(filter_render(render, (struct filter_identity){original, callbacks}, (void *)1, (void *)2, callbacks, &c, 0x51));
    assert(c.table == original && c.changed == 14 && calls == 1);
    assert(filter_render(nested, (struct filter_identity){original, callbacks}, (void *)1, (void *)2, callbacks, &c, 0x51));
    assert(c.table == original && c.changed == 28 && calls == 2);
    assert(!filter_render(passthrough, (struct filter_identity){0}, (void *)1, (void *)2, callbacks, &c, 0x51));
    assert(!filter_render(passthrough, (struct filter_identity){original, (void *)4}, (void *)1, (void *)2, callbacks, &c, 0x51));
    assert(!filter_render(passthrough, (struct filter_identity){(void *)4, callbacks}, (void *)1, (void *)2, callbacks, &c, 0x51));
    expected = NULL;
    assert(!filter_render(passthrough, (struct filter_identity){original, callbacks}, (void *)1, (void *)2, callbacks, NULL, 0x51));
    assert(calls == 6);
    puts("PASS: inversion query, 14 callbacks, context writes, restoration, reentry, identity mismatch, null context");
}
