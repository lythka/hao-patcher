// Load into the WindowServer launcher after golden-gate-userspace.
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <mach-o/loader.h>
#include <os/log.h>
#include <pthread.h>
#include <stdint.h>
#include <stdatomic.h>
#include "adapter.h"

extern void CARenderOGLRenderDisplayWithOptions(void *, void *, const void *, void *, unsigned);
extern void CARenderUpdateAddedAllContexts(void *);
extern void CARenderUpdateClipBackdropInvalidations(void *, void *);
static struct filter_identity identity;
static const void *software_update_return;
static pthread_once_t once = PTHREAD_ONCE_INIT;

static bool uuid_is(const struct mach_header *header, const uint8_t want[16])
{
    if (!header || header->magic != MH_MAGIC_64 || header->cputype != CPU_TYPE_X86_64) return false;
    const struct mach_header_64 *mh = (const void *)header;
    const uint8_t *p = (const void *)(mh + 1), *end = p + mh->sizeofcmds;
    for (uint32_t i = 0; i < mh->ncmds && p + sizeof(struct load_command) <= end; ++i) {
        const struct load_command *lc = (const void *)p;
        if (lc->cmdsize < sizeof *lc || lc->cmdsize > (size_t)(end - p)) return false;
        if (lc->cmd == LC_UUID && lc->cmdsize >= sizeof(struct uuid_command))
            return !memcmp(((const struct uuid_command *)lc)->uuid, want, 16);
        p += lc->cmdsize;
    }
    return false;
}

static void identify(void)
{
    static const uint8_t quartz[16] = {0x0a,0xd3,0x3c,0xc9,0x75,0xa1,0x35,0x9d,
                                     0xbc,0x41,0x4e,0xe2,0x5b,0xe4,0xf5,0x99};
    static const uint8_t sky[16] = {0x02,0xa9,0xa2,0x02,0x2f,0x39,0x32,0x04,
                                  0x86,0xde,0xa0,0x9e,0xa6,0xae,0x8d,0xee};
    const struct mach_header *sky_header = NULL;
    bool quartz_ok = false;
    if (!getsectbyname("__SLSERVER", "__slserver")) return;
    for (uint32_t i = 0; i < _dyld_image_count(); ++i) {
        const struct mach_header *h = _dyld_get_image_header(i);
        if (uuid_is(h, quartz)) quartz_ok = true;
        if (uuid_is(h, sky)) sky_header = h;
    }
    if (!quartz_ok || !sky_header) return;
    // Symbol addresses relative to SkyLight's __TEXT (0x7ff809e53000).
    // These offsets are meaningful only for the exact x86_64 UUID above.
    identity.api_table = (const uint8_t *)sky_header + 0x398e4a40;
    identity.display_callbacks = (const uint8_t *)sky_header + 0x398d85b0;
    // Return from CARenderUpdateAddedAllContexts in layer_tile_traverse,
    // before the software rendering workers are dispatched.
    software_update_return = (const uint8_t *)sky_header + 0x361271;
    os_log(OS_LOG_DEFAULT, "windowserver-filter: 26A428 display-aware inversion adapter enabled");
}

static void render_display(void *renderer, void *update, const void *callbacks,
                           void *info, unsigned options)
{
    pthread_once(&once, identify);
    bool adapted = filter_render(CARenderOGLRenderDisplayWithOptions, identity,
                                 renderer, update, callbacks, info, options);
    static atomic_bool reported;
    if (adapted && !atomic_exchange(&reported, true))
        os_log(OS_LOG_DEFAULT, "windowserver-filter: first legacy render completed with inversion adapter");
}

static void added_all_contexts(void *update)
{
    const void *caller = __builtin_return_address(0);
    pthread_once(&once, identify);
    bool adapted = filter_finish_backdrops(CARenderUpdateAddedAllContexts,
        CARenderUpdateClipBackdropInvalidations,
        software_update_return && caller == software_update_return, update);
    static atomic_bool reported;
    if (adapted && !atomic_exchange(&reported, true))
        os_log(OS_LOG_DEFAULT, "windowserver-filter: completed deferred software backdrop preparation");
}

__attribute__((used, section("__DATA,__interpose"))) static const struct {
    const void *replacement, *original;
} interposers[] = {
    {(const void *)render_display, (const void *)CARenderOGLRenderDisplayWithOptions},
    {(const void *)added_all_contexts, (const void *)CARenderUpdateAddedAllContexts},
};
