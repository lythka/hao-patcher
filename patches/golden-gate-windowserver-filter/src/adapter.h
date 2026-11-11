// Golden Gate x86_64 SkyLight passes a render context, not CA::WindowServer::Display.
// Keep its QuartzCore API entries and provide Display::actual_inverse_color at the
// build's vtable slot for colorInvertDisplayAware. Apple's x86_64 default returns false.
// The build-specific values come from src/profiles.h.
#include <stdbool.h>
#include <stddef.h>
#include <string.h>

typedef void (*render_fn)(void *, void *, const void *, void *, unsigned);

// The largest inverse slot the stack table supports; larger profiles never adapt.
#define FILTER_TABLE_MAX 256u

// The software tile traversal promises no later backdrop clipping pass. Complete
// the deferred pass here, before it dispatches concurrent rendering workers.
// The caller enables this only for the identified SkyLight/QuartzCore pair;
// flags_offset is that QuartzCore's Update byte holding the two bits below.
static inline bool filter_finish_backdrops(void (*added)(void *),
                                          void (*clip)(void *, void *),
                                          bool software_caller, size_t flags_offset, void *update)
{
    added(update);
    if (!software_caller || !update || !flags_offset) return false;
    unsigned char flags = ((const unsigned char *)update)[flags_offset];
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
    unsigned api_count;      // entries WSInitialize fills in api_table
    unsigned inverse_slot;   // CA::WindowServer::Display::actual_inverse_color
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
                 identity.api_count <= identity.inverse_slot &&
                 identity.inverse_slot < FILTER_TABLE_MAX &&
                 callbacks == identity.display_callbacks &&
                 original == identity.api_table;
    if (!adapt) {
        render(renderer, update, callbacks, info, options);
        return false;
    }
    const void *extended[FILTER_TABLE_MAX] = {0};
    memcpy(extended, original, identity.api_count * sizeof(void *));
    extended[identity.inverse_slot] = (const void *)filter_not_inverted;
    const void **table = extended;
    memcpy(info, &table, sizeof table);
    // info itself must retain its identity: the callbacks mutate its fields.
    // It and VTableDisplay already live on the synchronous caller's stack.
    render(renderer, update, callbacks, info, options);
    memcpy(info, &original, sizeof original);
    return true;
}
