// Load into the WindowServer launcher after golden-gate-userspace.
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <mach-o/loader.h>
#include <os/log.h>
#include <pthread.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include "adapter.h"

extern void CARenderOGLRenderDisplayWithOptions(void *, void *, const void *, void *, unsigned);
extern void CARenderUpdateAddedAllContexts(void *);
extern void CARenderUpdateClipBackdropInvalidations(void *, void *);

struct skylight_profile {
    const char *build;
    uint8_t uuid[16];
    uintptr_t api_table, api_count, display_callbacks, software_update_return;
};
struct quartzcore_profile {
    const char *build;
    uint8_t uuid[16];
    unsigned inverse_slot;
    size_t update_flags;
};
#define UUID(...) {__VA_ARGS__}
#define SKYLIGHT_PROFILE(b, u, a, n, d, r) {b, u, a, n, d, r},
#define QUARTZCORE_PROFILE(b, u, s, f)
static const struct skylight_profile skylight_profiles[] = {
#include "profiles.h"
};
#undef SKYLIGHT_PROFILE
#undef QUARTZCORE_PROFILE
#define SKYLIGHT_PROFILE(b, u, a, n, d, r)
#define QUARTZCORE_PROFILE(b, u, s, f) {b, u, s, f},
static const struct quartzcore_profile quartzcore_profiles[] = {
#include "profiles.h"
};
#undef SKYLIGHT_PROFILE
#undef QUARTZCORE_PROFILE
#undef UUID

#define SKYLIGHT_PATH "/SkyLight.framework/Versions/A/SkyLight"
#define QUARTZCORE_PATH "/QuartzCore.framework/Versions/A/QuartzCore"

static struct filter_identity identity;
static const void *software_update_return;
static size_t update_flags;
static pthread_once_t once = PTHREAD_ONCE_INIT;

static bool image_uuid(const struct mach_header *header, uint8_t out[16])
{
    if (!header || header->magic != MH_MAGIC_64 || header->cputype != CPU_TYPE_X86_64) return false;
    const struct mach_header_64 *mh = (const void *)header;
    const uint8_t *p = (const void *)(mh + 1), *end = p + mh->sizeofcmds;
    for (uint32_t i = 0; i < mh->ncmds && p + sizeof(struct load_command) <= end; ++i) {
        const struct load_command *lc = (const void *)p;
        if (lc->cmdsize < sizeof *lc || lc->cmdsize > (size_t)(end - p)) return false;
        if (lc->cmd == LC_UUID && lc->cmdsize >= sizeof(struct uuid_command)) {
            memcpy(out, ((const struct uuid_command *)lc)->uuid, 16);
            return true;
        }
        p += lc->cmdsize;
    }
    return false;
}

// The loaded image whose path ends in suffix, and its UUID; NULL if absent or not x86_64.
static const struct mach_header *loaded(const char *suffix, uint8_t uuid[16])
{
    size_t sl = strlen(suffix);
    for (uint32_t i = 0, n = _dyld_image_count(); i < n; ++i) {
        const char *name = _dyld_get_image_name(i);
        size_t nl = name ? strlen(name) : 0;
        if (nl < sl || strcmp(name + nl - sl, suffix)) continue;
        const struct mach_header *h = _dyld_get_image_header(i);
        return image_uuid(h, uuid) ? h : NULL;
    }
    return NULL;
}

static const struct skylight_profile *skylight_for(const uint8_t uuid[16])
{
    for (size_t i = 0; i < sizeof skylight_profiles / sizeof *skylight_profiles; ++i)
        if (!memcmp(skylight_profiles[i].uuid, uuid, 16)) return &skylight_profiles[i];
    return NULL;
}

static const struct quartzcore_profile *quartzcore_for(const uint8_t uuid[16])
{
    for (size_t i = 0; i < sizeof quartzcore_profiles / sizeof *quartzcore_profiles; ++i)
        if (!memcmp(quartzcore_profiles[i].uuid, uuid, 16)) return &quartzcore_profiles[i];
    return NULL;
}

static void uuid_text(const uint8_t u[16], char out[37])
{
    snprintf(out, 37, "%02X%02X%02X%02X-%02X%02X-%02X%02X-%02X%02X-%02X%02X%02X%02X%02X%02X",
             u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7], u[8], u[9], u[10], u[11], u[12], u[13], u[14], u[15]);
}

static void identify(void)
{
    if (!getsectbyname("__SLSERVER", "__slserver")) return;   // not WindowServer: pass through
    uint8_t sky_uuid[16], quartz_uuid[16];
    char text[37];
    const struct mach_header *sky = loaded(SKYLIGHT_PATH, sky_uuid);
    if (!sky) {
        os_log(OS_LOG_DEFAULT, "windowserver-filter: MISSING: x86_64 SkyLight image; adapters off");
        return;
    }
    if (!loaded(QUARTZCORE_PATH, quartz_uuid)) {
        os_log(OS_LOG_DEFAULT, "windowserver-filter: MISSING: x86_64 QuartzCore image; adapters off");
        return;
    }
    const struct skylight_profile *s = skylight_for(sky_uuid);
    if (!s) {
        uuid_text(sky_uuid, text);
        os_log(OS_LOG_DEFAULT, "windowserver-filter: unsupported: no profile for SkyLight UUID %{public}s; adapters off", text);
        return;
    }
    const struct quartzcore_profile *q = quartzcore_for(quartz_uuid);
    if (!q) {
        uuid_text(quartz_uuid, text);
        os_log(OS_LOG_DEFAULT, "windowserver-filter: unsupported: no profile for QuartzCore UUID %{public}s; adapters off", text);
        return;
    }
    if (q->inverse_slot >= FILTER_TABLE_MAX || s->api_count > q->inverse_slot) {
        os_log(OS_LOG_DEFAULT, "windowserver-filter: unsupported: inverse slot %u for %lu API entries; adapters off",
               q->inverse_slot, (unsigned long)s->api_count);
        return;
    }
    // Offsets from SkyLight's mach header, meaningful only for the matched UUID.
    identity.api_table = (const uint8_t *)sky + s->api_table;
    identity.display_callbacks = (const uint8_t *)sky + s->display_callbacks;
    identity.api_count = (unsigned)s->api_count;
    identity.inverse_slot = q->inverse_slot;
    // Return from CARenderUpdateAddedAllContexts in layer_tile_traverse,
    // before the software rendering workers are dispatched.
    software_update_return = (const uint8_t *)sky + s->software_update_return;
    update_flags = q->update_flags;
    os_log(OS_LOG_DEFAULT, "windowserver-filter: adapters enabled for SkyLight %{public}s, QuartzCore %{public}s",
           s->build, q->build);
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
        software_update_return && caller == software_update_return, update_flags, update);
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
