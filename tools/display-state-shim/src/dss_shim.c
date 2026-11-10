// dss_shim: 27.0 (26A428) x86_64 display-system-state compatibility shim for the WindowServer process.
//
// 27.0's x86_64 CoreDisplay encodes the display system state without the one-byte "sync-session UUID present"
// flag that every 27.0 SkyLight client decodes at offset 0x20 (the arm64e server, CAWSManager, writes it). Every
// client then fails "Error deserializing display system state" and sees no displays. This library wraps CoreDisplay's
// _CGXGetDisplaySystemState, the function SkyLight's MIG server calls, and inserts that byte (0: no UUID; the x86_64
// server has no sync session). README.md describes the layout.
//
// It acts only when the loaded SkyLight and CoreDisplay are exactly the 26A428 x86_64 builds and the process is the
// window server; otherwise it passes every call through unchanged. It never changes a buffer it does not recognise.
#include <mach/mach.h>
#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <mach-o/loader.h>
#include <os/log.h>
#include <stdatomic.h>
#include <stdint.h>
#include <string.h>

#include "dss_convert.h"

extern int _CGXGetDisplaySystemState(mach_port_t port, uint64_t seed, uint8_t **data, uint32_t *len);

static const uint8_t SKYLIGHT_26A428[16] = {0x02, 0xA9, 0xA2, 0x02, 0x2F, 0x39, 0x32, 0x04,
                                            0x86, 0xDE, 0xA0, 0x9E, 0xA6, 0xAE, 0x8D, 0xEE};
static const uint8_t COREDISPLAY_26A428[16] = {0x61, 0xC8, 0x2C, 0x50, 0xC9, 0x1D, 0x30, 0x28,
                                               0x93, 0x3E, 0xFC, 0x20, 0xD2, 0xAB, 0x40, 0xFA};

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

static int loaded_is(const char *suffix, const uint8_t want[16])
{
    size_t sl = strlen(suffix);
    for (uint32_t i = 0, n = _dyld_image_count(); i < n; i++) {
        const char *name = _dyld_get_image_name(i);
        size_t nl = name ? strlen(name) : 0;
        if (nl < sl || strcmp(name + nl - sl, suffix)) continue;
        uint8_t u[16];
        return image_uuid(_dyld_get_image_header(i), u) && !memcmp(u, want, 16);
    }
    return 0;
}

// 1: act; 0: pass through. Decided once, on the first call.
static int gate(void)
{
    static atomic_int state;   // 0 undecided, 1 act, 2 inert
    int s = atomic_load(&state);
    if (s) return s == 1;
    const char *why = NULL;
    if (!loaded_is("/SkyLight.framework/Versions/A/SkyLight", SKYLIGHT_26A428)) why = "SkyLight is not 26A428 x86_64";
    else if (!loaded_is("/CoreDisplay.framework/Versions/A/CoreDisplay", COREDISPLAY_26A428)) why = "CoreDisplay is not 26A428 x86_64";
    else if (!getsectbyname("__SLSERVER", "__slserver")) why = "not the window server";
    atomic_store(&state, why ? 2 : 1);
    if (why) os_log(shim_log(), "dss-shim inert: %{public}s", why);
    return !why;
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
