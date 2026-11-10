// 26A428 SkyLight passes a render context, not CA::WindowServer::Display.
// Keep its fourteen QuartzCore APIs and provide Display::actual_inverse_color
// at slot 124 for colorInvertDisplayAware. Apple's x86_64 default returns false.
#include <stdbool.h>
#include <stddef.h>
#include <string.h>

typedef void (*render_fn)(void *, void *, const void *, void *, unsigned);

// The software tile traversal promises no later backdrop clipping pass. Complete
// the deferred pass here, before it dispatches concurrent rendering workers.
// The caller enables this only for the identified SkyLight/QuartzCore pair.
static inline bool filter_finish_backdrops(void (*added)(void *),
                                          void (*clip)(void *, void *),
                                          bool software_caller, void *update)
{
    added(update);
    if (!software_caller || !update) return false;
    unsigned char flags = ((const unsigned char *)update)[0x543];
    // will_clip_backdrops (1), backdrop_invalidations_finalized (4).
    if ((flags & 5) != 1) return false;
    // NULL is QuartzCore's infinite clip region: keep all invalidations and
    // let its own implementation build the array and finalize the update.
    clip(update, NULL);
    return true;
}
struct filter_identity {
    const void *api_table;
    const void *display_callbacks;
};

static bool filter_not_inverted(const void *context)
{
    (void)context;
    return false;
}

static bool filter_render(render_fn render, struct filter_identity identity,
                          void *renderer, void *update, const void *callbacks,
                          void *info, unsigned options)
{
    const void **original = NULL;
    if (info) memcpy(&original, info, sizeof original);
    bool adapt = identity.api_table && identity.display_callbacks &&
                 callbacks == identity.display_callbacks &&
                 original == identity.api_table;
    if (!adapt) {
        render(renderer, update, callbacks, info, options);
        return false;
    }
    const void *extended[125] = {0};
    memcpy(extended, original, 14 * sizeof(void *));
    extended[124] = (const void *)filter_not_inverted;
    const void **table = extended;
    memcpy(info, &table, sizeof table);
    // info itself must retain its identity: the callbacks mutate its fields.
    // It and VTableDisplay already live on the synchronous caller's stack.
    render(renderer, update, callbacks, info, options);
    memcpy(info, &original, sizeof original);
    return true;
}
