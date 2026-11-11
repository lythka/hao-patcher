// dss_shim: Golden Gate x86_64 display-system-state compatibility shim for the WindowServer process.
//
// Golden Gate's x86_64 CoreDisplay encodes the display system state without the one-byte "sync-session UUID present"
// flag that every Golden Gate SkyLight client decodes at offset 0x20 (the arm64e server, CAWSManager, writes it). Every
// client then fails "Error deserializing display system state" and sees no displays. This library wraps CoreDisplay's
// _CGXGetDisplaySystemState, the function SkyLight's MIG server calls, and inserts that byte (0: no UUID; the x86_64
// server has no sync session). README.md describes the layout.
//
// It acts only when the loaded SkyLight and CoreDisplay are x86_64 builds listed in dss_builds.h and the process is
// the window server; otherwise it logs why once and passes every call through unchanged. It never changes a buffer
// it does not recognise.
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <mach-o/loader.h>
#include <os/log.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "dss_convert.h"

extern int _CGXGetDisplaySystemState(mach_port_t port, uint64_t seed, uint8_t **data, uint32_t *len);

struct dss_build { const char *build; uint8_t uuid[16]; };
#define UUID(...) {__VA_ARGS__}
#define SKYLIGHT_BUILD(b, u) {b, u},
#define COREDISPLAY_BUILD(b, u)
static const struct dss_build skylight_builds[] = {
#include "dss_builds.h"
};
#undef SKYLIGHT_BUILD
#undef COREDISPLAY_BUILD
#define SKYLIGHT_BUILD(b, u)
#define COREDISPLAY_BUILD(b, u) {b, u},
static const struct dss_build coredisplay_builds[] = {
#include "dss_builds.h"
};
#undef SKYLIGHT_BUILD
#undef COREDISPLAY_BUILD
#undef UUID

static os_log_t shim_log(void)
{
    static os_log_t l;
    if (!l) l = os_log_create("com.hao.dss-shim", "shim");
    return l;
}

static int image_uuid(const struct mach_header *mh, uint8_t out[16])
{
    if (!mh || mh->magic != MH_MAGIC_64) return 0;
    const struct mach_header_64 *h = (const void *)mh;
    const uint8_t *p = (const uint8_t *)(h + 1), *end = p + h->sizeofcmds;
    for (uint32_t i = 0; i < h->ncmds && p + sizeof(struct load_command) <= end; i++) {
        const struct load_command *lc = (const void *)p;
        if (lc->cmdsize < sizeof *lc || p + lc->cmdsize > end) return 0;
        if (lc->cmd == LC_UUID && lc->cmdsize >= sizeof(struct uuid_command)) {
            memcpy(out, ((const struct uuid_command *)lc)->uuid, 16);
            return 1;
        }
        p += lc->cmdsize;
    }
    return 0;
}

// The listed build of the loaded image whose path ends in SUFFIX. NULL with an empty UUID_TEXT: no such x86_64 image;
// NULL with UUID_TEXT set: its UUID has no entry.
static const char *loaded_build(const char *suffix, const struct dss_build *builds, size_t count, char uuid_text[37])
{
    size_t sl = strlen(suffix);
    uuid_text[0] = 0;
    for (uint32_t i = 0, n = _dyld_image_count(); i < n; i++) {
        const char *name = _dyld_get_image_name(i);
        size_t nl = name ? strlen(name) : 0;
        if (nl < sl || strcmp(name + nl - sl, suffix)) continue;
        uint8_t u[16];
        if (!image_uuid(_dyld_get_image_header(i), u)) return NULL;
        for (size_t b = 0; b < count; b++)
            if (!memcmp(builds[b].uuid, u, 16)) return builds[b].build;
        snprintf(uuid_text, 37, "%02X%02X%02X%02X-%02X%02X-%02X%02X-%02X%02X-%02X%02X%02X%02X%02X%02X", u[0], u[1], u[2],
                 u[3], u[4], u[5], u[6], u[7], u[8], u[9], u[10], u[11], u[12], u[13], u[14], u[15]);
        return NULL;
    }
    return NULL;
}

// 1: act; 0: pass through. Decided once, on the first call.
static int gate(void)
{
    static atomic_int state;   // 0 undecided, 1 act, 2 inert
    int s = atomic_load(&state);
    if (s) return s == 1;
    char sky_uuid[37], cd_uuid[37];
    const char *sky = loaded_build("/SkyLight.framework/Versions/A/SkyLight", skylight_builds,
                                   sizeof skylight_builds / sizeof *skylight_builds, sky_uuid);
    const char *cd = loaded_build("/CoreDisplay.framework/Versions/A/CoreDisplay", coredisplay_builds,
                                  sizeof coredisplay_builds / sizeof *coredisplay_builds, cd_uuid);
    int act = 0;
    if (!getsectbyname("__SLSERVER", "__slserver"))
        os_log(shim_log(), "dss-shim inert: not the window server");
    else if (!sky && !*sky_uuid)
        os_log(shim_log(), "dss-shim inert: MISSING: x86_64 SkyLight image");
    else if (!sky)
        os_log(shim_log(), "dss-shim inert: unsupported: no entry for SkyLight UUID %{public}s", sky_uuid);
    else if (!cd && !*cd_uuid)
        os_log(shim_log(), "dss-shim inert: MISSING: x86_64 CoreDisplay image");
    else if (!cd)
        os_log(shim_log(), "dss-shim inert: unsupported: no entry for CoreDisplay UUID %{public}s", cd_uuid);
    else {
        act = 1;
        os_log(shim_log(), "dss-shim: active for SkyLight %{public}s, CoreDisplay %{public}s", sky, cd);
    }
    atomic_store(&state, act ? 1 : 2);
    return act;
}

static int dss_shim_get(mach_port_t port, uint64_t seed, uint8_t **data, uint32_t *len)
{
    int r = _CGXGetDisplaySystemState(port, seed, data, len);
    if (!data || !len || !*data || !gate()) return r;
    uint8_t *in = *data;
    uint32_t n = *len;
    size_t need = 0;
    enum dss_result c = dss_check_x86(in, n, &need);
    if (c != DSS_X86) {
        static atomic_int told;
        if (!atomic_exchange(&told, 1))
            os_log(shim_log(), "dss-shim: reply of %u bytes left unchanged (%{public}s)", n, dss_result_name(c));
        return r;
    }
    vm_address_t out = 0;
    if (vm_allocate(mach_task_self(), &out, need, VM_FLAGS_ANYWHERE) != KERN_SUCCESS) return r;
    dss_convert_x86((uint8_t *)out, in, n);
    vm_deallocate(mach_task_self(), (vm_address_t)in, n);
    *data = (uint8_t *)out;
    *len = (uint32_t)need;
    static atomic_int first;
    if (!atomic_exchange(&first, 1))
        os_log(shim_log(), "dss-shim: first display state rewritten: %u -> %u bytes, %u displays", n, (unsigned)need,
               dss_display_count((const uint8_t *)out, (uint32_t)need));
    return r;
}

__attribute__((used, section("__DATA,__interpose"))) static const struct {
    const void *replacement, *original;
} dss_interposers[] = {
    {(const void *)dss_shim_get, (const void *)_CGXGetDisplaySystemState},
};
