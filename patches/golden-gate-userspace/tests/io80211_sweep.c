// sweep: call the 7 IO80211 functions over their whole 32-bit input range and print the output as runs.
//   sweep raw FILE TEXT_VMADDR TEXT_SIZE ADDR1 .. ADDR7   map FILE's __TEXT (fileoff 0) and call code at those vmaddrs
//   sweep dlopen PATH                                     dlopen PATH and dlsym the 7 names
//   sweep check PATH EXPECTED                             dlopen PATH; check both ends and the middle of every run
// Output per function: "== name" then "lo hi value" lines (value: string or integer), one per run of equal output.
// The 32-bit sweeps take about 15 minutes under Rosetta; check mode takes well under a second.
#include <dlfcn.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

typedef const char *(*strfn)(int);
typedef int (*intfn)(int);
typedef int (*int2fn)(int, int);

static const char *names[7] = {
    "apple80211_apmodetoStr", "apple80211_authtype_uppertoStr", "apple80211_roam_profile_typetoStr",
    "apple80211_roam_status_bss_flagstoStr", "convertPhyTypeToString", "convertToApple80211Error",
    "convertAssocStatusToAssocResult"};
static uintptr_t lo_text, hi_text;   // raw mode: returned strings must point into the mapped __TEXT

static void pstr(const char *s) {
    if (!s) { printf("NULL\n"); return; }
    if (lo_text && ((uintptr_t)s < lo_text || (uintptr_t)s >= hi_text)) { printf("OUTSIDE-TEXT\n"); return; }
    putchar('"');
    for (; *s; s++) {
        if (*s == '"' || *s == '\\') printf("\\%c", *s);
        else if (*s >= 32 && *s < 127) putchar(*s);
        else printf("\\x%02x", (unsigned char)*s);
    }
    printf("\"\n");
}

static void sweep_str(const char *name, strfn f) {
    printf("== %s\n", name);
    const char *cur = f(0); uint32_t start = 0;
    for (uint64_t u = 1; u <= 0x100000000ULL; u++) {
        const char *p = u < 0x100000000ULL ? f((int)(uint32_t)u) : NULL;
        if (u == 0x100000000ULL || (p != cur && (p == NULL || cur == NULL || strcmp(p, cur)))) {
            printf("%08x %08x ", start, (uint32_t)(u - 1)); pstr(cur);
            cur = p; start = (uint32_t)u;
        }
    }
}

static void sweep_int(const char *name, int (*call)(void *, int, int), void *f, int fixed, int which) {
    // which 0: vary arg0 with arg1 = fixed; 1: vary arg1 with arg0 = fixed
    printf("== %s %s %d\n", name, which ? "arg1, arg0 =" : "arg0, arg1 =", fixed);
    // A run is a constant value or "identity" (output = the varied input), so pass-through ranges stay one line.
    int v0 = which ? call(f, fixed, 0) : call(f, 0, fixed);
    int id = v0 == 0, cur = v0; uint32_t start = 0;
    for (uint64_t u = 1; u <= 0x100000000ULL; u++) {
        int v = 0, vid = 0;
        if (u < 0x100000000ULL) {
            v = which ? call(f, fixed, (int)(uint32_t)u) : call(f, (int)(uint32_t)u, fixed);
            vid = v == (int)(uint32_t)u;
        }
        if (u == 0x100000000ULL || vid != id || (!id && v != cur)) {
            if (id && start == (uint32_t)(u - 1)) printf("%08x %08x %d\n", start, start, (int)start);
            else if (id) printf("%08x %08x identity\n", start, (uint32_t)(u - 1));
            else printf("%08x %08x %d\n", start, (uint32_t)(u - 1), cur);
            id = vid; cur = v; start = (uint32_t)u;
        }
    }
}
static int call1(void *f, int a, int b) { (void)b; return ((intfn)f)(a); }
static int call2(void *f, int a, int b) { return ((int2fn)f)(a, b); }

// check mode: EXPECTED is a sweep's output; returns the number of mismatching points (-1: unreadable).
static int check(void **fn, const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) { perror(path); return -1; }
    char line[512], name[128] = "", which[32] = "";
    int fixed = 0, bad = 0, points = 0;
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\n")] = 0;
        if (!strncmp(line, "== ", 3)) {
            which[0] = 0; fixed = 0;
            sscanf(line + 3, "%127s %31[^,], %*s = %d", name, which, &fixed);
            continue;
        }
        unsigned lo, hi; char val[400];
        if (sscanf(line, "%x %x %399[^\n]", &lo, &hi, val) != 3) { fprintf(stderr, "bad line: %s\n", line); return -1; }
        int k = 0;
        while (k < 7 && strcmp(names[k], name)) k++;
        if (k == 7) { fprintf(stderr, "unknown function %s\n", name); return -1; }
        uint32_t at[3] = {lo, hi, lo + (hi - lo) / 2};
        for (int j = 0; j < 3; j++) {
            int u = (int)at[j], ok;
            points++;
            if (k < 5) {
                const char *got = ((strfn)fn[k])(u);
                size_t n = strlen(val);
                ok = got && n >= 2 && strlen(got) == n - 2 && !strncmp(got, val + 1, n - 2);
            } else {
                int got = k == 5 ? ((intfn)fn[k])(u)
                        : !strcmp(which, "arg1") ? ((int2fn)fn[k])(fixed, u) : ((int2fn)fn[k])(u, fixed);
                ok = !strcmp(val, "identity") ? got == u : got == atoi(val);
            }
            if (!ok) { bad++; printf("MISMATCH %s %s %d at %08x: expected %s\n", name, which, fixed, at[j], val); }
        }
    }
    fclose(f);
    printf("checked %d points, %d mismatches\n", points, bad);
    return bad;
}

int main(int argc, char **argv) {
    void *fn[7];
    if (argc == 12 && !strcmp(argv[1], "raw")) {
        uint64_t vm = strtoull(argv[3], 0, 16), size = strtoull(argv[4], 0, 16);
        int fd = open(argv[2], O_RDONLY);
        if (fd < 0) { perror(argv[2]); return 1; }
        uint8_t *m = mmap(0, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        if (pread(fd, m, size, 0) != (ssize_t)size) { perror("pread"); return 1; }
        if (mprotect(m, size, PROT_READ | PROT_EXEC)) { perror("mprotect"); return 1; }
        lo_text = (uintptr_t)m; hi_text = (uintptr_t)m + size;
        for (int i = 0; i < 7; i++) fn[i] = m + (strtoull(argv[5 + i], 0, 16) - vm);
    } else if ((argc == 3 && !strcmp(argv[1], "dlopen")) || (argc == 4 && !strcmp(argv[1], "check"))) {
        void *h = dlopen(argv[2], RTLD_NOW | RTLD_LOCAL);
        if (!h) { fprintf(stderr, "%s\n", dlerror()); return 1; }
        for (int i = 0; i < 7; i++)
            if (!(fn[i] = dlsym(h, names[i]))) { fprintf(stderr, "%s\n", dlerror()); return 1; }
    } else {
        fprintf(stderr, "usage: sweep raw FILE TEXT_VMADDR TEXT_SIZE ADDR*7 | sweep dlopen PATH | sweep check PATH EXPECTED\n");
        return 2;
    }
    if (argc == 4) return check(fn, argv[3]) == 0 ? 0 : 1;
    for (int i = 0; i < 5; i++) sweep_str(names[i], (strfn)fn[i]);
    sweep_int(names[5], call1, fn[5], 0, 0);
    int fixed[] = {0, 1, 2, 3, -1, 0x7fffffff, (int)0x80000000};
    for (unsigned i = 0; i < sizeof fixed / sizeof *fixed; i++) sweep_int(names[6], call2, fn[6], fixed[i], 0);
    int fixed0[] = {0, 1, 5, 0x3e8, 0xf9, 0xffff, -1};
    for (unsigned i = 0; i < sizeof fixed0 / sizeof *fixed0; i++) sweep_int(names[6], call2, fn[6], fixed0[i], 1);
    return 0;
}
