#include <assert.h>
#include <stdio.h>
#include "../src/adapter.h"

static unsigned calls, clips, original_finalizes;
static unsigned char update[0x550];
static void added(void *p)
{
    calls++;
    if (original_finalizes && p) ((unsigned char *)p)[0x543] |= 4;
}
static void clip(void *p, void *region)
{
    assert(calls == 1 && clips == 0 && p == update && region == NULL);
    // Represent QuartzCore producing its invalidation data, then publishing
    // completion. The adapter must invoke that work, not set the flag itself.
    update[0x248] = 0xab;
    update[0x543] |= 4;
    clips++;
}
int main(void)
{
    for (unsigned flags = 0; flags < 256; flags++) {
        for (unsigned eligible = 0; eligible < 2; eligible++) {
            for (original_finalizes = 0; original_finalizes < 2; original_finalizes++) {
                memset(update, 0, sizeof update);
                update[0x543] = flags;
                calls = clips = 0;
                bool needed = eligible && !original_finalizes && (flags & 5) == 1;
                assert(filter_finish_backdrops(added, clip, eligible, update) == needed);
                assert(calls == 1 && clips == needed);
                assert(update[0x248] == (needed ? 0xab : 0));
                assert(update[0x543] == (flags | ((needed || original_finalizes) ? 4 : 0)));
            }
        }
    }
    original_finalizes = 0;
    calls = clips = 0;
    assert(!filter_finish_backdrops(added, clip, false, (void *)1));
    assert(calls == 1 && clips == 0); // Unmatched calls never inspect update layout.
    calls = clips = 0;
    assert(!filter_finish_backdrops(added, clip, true, NULL));
    assert(calls == 1 && clips == 0);
    puts("PASS: deferred backdrop preparation, ordering and caller isolation");
}
