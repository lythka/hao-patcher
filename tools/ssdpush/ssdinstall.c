/*
 * ssdinstall: install an image bundle (`ssdimg bundle`, tools/ssdimg/streamfile.py) from a USB disk on a disk through
 * ssdpush-agent, without this Mac: the Recovery side of "Reinstall macOS" (patches/rec-reinstall). It plays the bundle's frame stream
 * into the agent exactly as `ssdimg push` does over the cable, so the block copy, hash check and read-back are the
 * agent's own; this program adds what push.py does on the host: the backup GPT relocated to the end of the target,
 * the end verify pass (every chunk re-read and hash-checked, chunk 0 / tail / GPT compared byte for byte), --delta.
 *
 *   ssdinstall list-disks                      eligible targets: physical internal fixed whole disks
 *   ssdinstall list-images [DIR...]            bundles (default: a bundle folder at the top level of any volume,
 *                                              /Volumes/x/x/IMAGE)
 *   ssdinstall install BUNDLE TARGET [--yes] [--delta] [--no-verify] [--file] [--agent PATH] [--startup]
 *       TARGET  /dev/diskN (must pass the list-disks checks; unmounted first) or, with --file, a regular file
 *               (tests / --simulate in the app). Without --yes it asks for the disk name on the terminal.
 *       --startup  afterwards make the installed disk's OpenCore the firmware default (startup-internal; a NetBoot
 *                  default is kept and the new disk starts once)
 *   A disk target: refused when it holds the bundle itself; afterwards the APFS container is grown to the disk
 *   (the image is smaller than most disks), as `ssdimg push` does.
 *
 * Machine-readable output on stdout (the app parses it), one per line:
 *   STEP <prepare|delta|write|gpt|flush|verify|grow|startup|done>   PROGRESS <phase> <done> <total>
 *   INFO <text>   ERROR <text>   DONE seconds=<s> ...
 * Exit status 0 only when everything was written and verified.
 *
 * Integrity: IMAGE's sha256 of index.bin / head.bin / tail.bin is checked first; every frame header read from the
 * stream must equal its index entry (offset, length, hash, size) before it is forwarded; the agent checks each
 * chunk's BLAKE2b-160 before writing it; the verify pass re-reads the disk. IMAGE itself is not signed.
 */
#include <CommonCrypto/CommonDigest.h>
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/ps/IOPowerSources.h>
#include <IOKit/pwr_mgt/IOPMLib.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <limits.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <unistd.h>

#define ENTRY 64
#define QBATCH 4096
#define MAXREAD (1u << 20)

typedef struct { char type; uint16_t seg; uint64_t spos, off; uint32_t wlen, clen; uint8_t hash[20]; } frame_t;

static char bundle[PATH_MAX];
static char kv_keys[64][64], kv_vals[64][256];
static int nkv;
static frame_t *fr;
static uint32_t nfr;
static uint64_t img_size, stream_total;
static uint32_t cs, sector;
static int64_t tail_index = -1;
static uint8_t *head, *tailbuf;
static size_t tail_len;
static FILE *to_agent, *from_agent;
static pid_t agent_pid;
static uint64_t target_size;
static uint32_t target_bsize;

static double now(void) { struct timeval t; gettimeofday(&t, NULL); return t.tv_sec + t.tv_usec / 1e6; }

static void fail(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    printf("ERROR "); vprintf(fmt, ap); printf("\n"); fflush(stdout);
    va_end(ap);
    if (agent_pid > 0) kill(agent_pid, SIGTERM);
    exit(1);
}

static const char *kv(const char *k) {
    for (int i = 0; i < nkv; i++) if (!strcmp(kv_keys[i], k)) return kv_vals[i];
    return NULL;
}

static int read_kv(const char *dir) {
    char p[PATH_MAX]; snprintf(p, sizeof p, "%s/IMAGE", dir);
    FILE *f = fopen(p, "r");
    if (!f) return -1;
    char line[512];
    nkv = 0;
    while (nkv < 64 && fgets(line, sizeof line, f)) {
        char *e = strchr(line, '=');
        if (!e) continue;
        *e = 0; line[strcspn(line, "\n")] = 0; e[1 + strcspn(e + 1, "\n")] = 0;
        snprintf(kv_keys[nkv], 64, "%s", line); snprintf(kv_vals[nkv], 256, "%s", e + 1); nkv++;
    }
    fclose(f);
    return kv("format") && !strcmp(kv("format"), "ssdstream1") ? 0 : -1;
}

static uint8_t *slurp(const char *name, size_t *len) {
    char p[PATH_MAX]; snprintf(p, sizeof p, "%s/%s", bundle, name);
    int fd = open(p, O_RDONLY);
    if (fd < 0) return NULL;
    struct stat st; fstat(fd, &st);
    uint8_t *b = malloc(st.st_size + 1);
    size_t got = 0;
    while (got < (size_t)st.st_size) { ssize_t r = read(fd, b + got, st.st_size - got); if (r <= 0) break; got += r; }
    close(fd);
    if (got != (size_t)st.st_size) { free(b); return NULL; }
    *len = got;
    return b;
}

static void sha256hex(const uint8_t *b, size_t n, char out[65]) {
    uint8_t d[32]; CC_SHA256(b, (CC_LONG)n, d);
    for (int i = 0; i < 32; i++) sprintf(out + 2 * i, "%02x", d[i]);
}

static uint8_t *checked(const char *name, size_t *len, int required) {
    char key[64]; snprintf(key, sizeof key, "sha256.%s", name);
    const char *want = kv(key);
    if (!want) { if (required) fail("IMAGE has no %s", key); return NULL; }
    uint8_t *b = slurp(name, len);
    if (!b) fail("cannot read %s/%s", bundle, name);
    char h[65]; sha256hex(b, *len, h);
    if (strcmp(h, want)) fail("%s: sha256 mismatch (bundle damaged)", name);
    return b;
}

static uint64_t le64(const uint8_t *p) { uint64_t v = 0; for (int i = 7; i >= 0; i--) v = v << 8 | p[i]; return v; }
static uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static void put64(uint8_t *p, uint64_t v) { for (int i = 0; i < 8; i++) p[i] = v >> (8 * i); }
static void put32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = v >> (8 * i); }

static void load_bundle(const char *dir) {
    if (!realpath(dir, bundle)) fail("%s: %s", dir, strerror(errno));
    if (read_kv(bundle)) fail("%s: not an image bundle (no IMAGE with format=ssdstream1)", bundle);
    img_size = strtoull(kv("size"), NULL, 10); cs = atoi(kv("chunk_size")); sector = atoi(kv("sector_size"));
    stream_total = strtoull(kv("stream_bytes"), NULL, 10);
    tail_index = kv("tail_index") ? strtoll(kv("tail_index"), NULL, 10) : -1;
    size_t n;
    uint8_t *idx = checked("index.bin", &n, 1);
    nfr = (uint32_t)(n / ENTRY);
    if (n % ENTRY || nfr != strtoul(kv("frames"), NULL, 10)) fail("index.bin does not match IMAGE frames=%s", kv("frames"));
    fr = calloc(nfr + 1, sizeof *fr);
    for (uint32_t i = 0; i < nfr; i++) {
        const uint8_t *e = idx + (size_t)i * ENTRY;
        fr[i].type = e[0]; fr[i].seg = e[2] | e[3] << 8; fr[i].spos = le64(e + 8); fr[i].off = le64(e + 16);
        fr[i].wlen = le32(e + 24); fr[i].clen = le32(e + 28); memcpy(fr[i].hash, e + 32, 20);
        if ((fr[i].type != 'D' && fr[i].type != 'Z') || fr[i].off % cs || fr[i].wlen > cs || fr[i].off + fr[i].wlen > img_size)
            fail("index entry %u is invalid", i);
    }
    free(idx);
    /* every segment present and long enough before anything is written (a missing one would stop mid-disk) */
    int nseg = kv("segments") ? atoi(kv("segments")) : 0;
    for (int s = 0; s < nseg; s++) {
        uint64_t need = 0;
        for (uint32_t i = 0; i < nfr; i++)
            if (fr[i].seg == s && fr[i].spos + (fr[i].type == 'D' ? 37 + fr[i].clen : 13) > need) need = fr[i].spos + (fr[i].type == 'D' ? 37 + fr[i].clen : 13);
        char p[PATH_MAX]; snprintf(p, sizeof p, "%s/stream.%03d", bundle, s);
        struct stat st;
        if (stat(p, &st)) fail("%s: %s (bundle incomplete)", p, strerror(errno));
        if ((uint64_t)st.st_size < need) fail("%s is %lld bytes, the index needs %llu (bundle incomplete)", p, (long long)st.st_size, need);
    }
    for (uint32_t i = 0; i < nfr; i++) if (fr[i].seg >= nseg) fail("index entry %u names segment %u of %d", i, fr[i].seg, nseg);
    head = checked("head.bin", &n, 1);
    if (n != cs) fail("head.bin is %zu bytes, chunk size %u", n, cs);
    tailbuf = checked("tail.bin", &tail_len, 0);
    if ((tail_index >= 0) != (tailbuf != NULL)) fail("tail.bin and IMAGE tail_index disagree");
}

/* ------------------------------------------------------------------ GPT (port of ssdimg/gpt.py relocated) */
static uint32_t crc32(const uint8_t *b, size_t n) {
    uint32_t c = 0xFFFFFFFF;
    for (size_t i = 0; i < n; i++) { c ^= b[i]; for (int k = 0; k < 8; k++) c = c >> 1 ^ (0xEDB88320 & -(c & 1)); }
    return ~c;
}

typedef struct { uint64_t off; uint32_t len; uint8_t *data; } rwrite_t;
static rwrite_t rw[5];

static void gpt_relocate(uint64_t total_sectors) {
    const uint8_t *h = head + sector;
    if (memcmp(h, "EFI PART", 8)) fail("head.bin: no GPT signature at LBA 1");
    uint32_t hsz = le32(h + 12), crc = le32(h + 16);
    if (hsz < 92 || hsz > sector) fail("GPT header size %u", hsz);
    uint8_t hb[512]; memcpy(hb, h, hsz); memset(hb + 16, 0, 4);
    if (crc32(hb, hsz) != crc) fail("primary GPT header CRC mismatch");
    uint64_t entries_lba = le64(h + 72); uint32_t num = le32(h + 80), esz = le32(h + 84), ecrc = le32(h + 88);
    size_t elen = (size_t)num * esz, esec = (elen + sector - 1) / sector;
    if ((entries_lba + esec) * sector > cs) fail("GPT entries beyond chunk 0");
    const uint8_t *ent = head + entries_lba * sector;
    if (crc32(ent, elen) != ecrc) fail("GPT entry array CRC mismatch");
    uint64_t end = 0;
    for (uint32_t i = 0; i < num; i++) {
        const uint8_t *e = ent + (size_t)i * esz; int used = 0;
        for (int k = 0; k < 16; k++) used |= e[k];
        if (used && le64(e + 40) + 1 > end) end = le64(e + 40) + 1;
    }
    uint64_t last = total_sectors - 1, b_entries = last - esec, last_usable = b_entries - 1;
    if (end - 1 > last_usable) fail("partitions end at LBA %llu, the target allows up to %llu", end - 1, last_usable);
    uint8_t *entries = calloc(1, esec * sector); memcpy(entries, ent, elen);
    for (int k = 0; k < 2; k++) {
        uint8_t *x = calloc(1, sector); memcpy(x, h, hsz);
        put64(x + 24, k ? last : 1); put64(x + 32, k ? 1 : last); put64(x + 48, last_usable);
        put64(x + 72, k ? b_entries : entries_lba);
        memset(x + 16, 0, 4); put32(x + 16, crc32(x, hsz));
        rw[k ? 4 : 1] = (rwrite_t){ (k ? last : 1) * sector, sector, x };
    }
    uint8_t *mbr = malloc(sector); memcpy(mbr, head, sector);
    if (mbr[446 + 4] == 0xEE) put32(mbr + 446 + 12, total_sectors - 1 > 0xFFFFFFFFull ? 0xFFFFFFFF : (uint32_t)(total_sectors - 1));
    rw[0] = (rwrite_t){ 0, sector, mbr };
    rw[2] = (rwrite_t){ entries_lba * sector, (uint32_t)(esec * sector), entries };
    rw[3] = (rwrite_t){ b_entries * sector, (uint32_t)(esec * sector), entries };
}

/* ------------------------------------------------------------------ agent */
static void agent_start(const char *agent, const char *target) {
    int in[2], out[2];
    if (pipe(in) || pipe(out)) fail("pipe: %s", strerror(errno));
    agent_pid = fork();
    if (agent_pid < 0) fail("fork: %s", strerror(errno));
    if (!agent_pid) {
        dup2(in[0], 0); dup2(out[1], 1); close(in[1]); close(out[0]);
        execl(agent, agent, target, (char *)NULL);
        printf("ERR exec %s: %s\n", agent, strerror(errno)); _exit(127);
    }
    close(in[0]); close(out[1]);
    to_agent = fdopen(in[1], "w"); from_agent = fdopen(out[0], "r");
    static char wbuf[8 << 20]; setvbuf(to_agent, wbuf, _IOFBF, sizeof wbuf);
}

static void agent_line(char *buf, size_t n, const char *want) {
    fflush(to_agent);
    if (!fgets(buf, (int)n, from_agent)) {
        int st = 0; waitpid(agent_pid, &st, 0); agent_pid = 0;
        fail("agent exited (status %d)", WIFEXITED(st) ? WEXITSTATUS(st) : -1);
    }
    buf[strcspn(buf, "\n")] = 0;
    if (strncmp(buf, want, strlen(want))) fail("agent: %s", buf);
}

static void asend(const void *b, size_t n) {
    if (fwrite(b, 1, n, to_agent) != n) {
        char l[512] = "";
        if (fgets(l, sizeof l, from_agent)) { l[strcspn(l, "\n")] = 0; fail("agent: %s", l); }
        fail("agent connection lost");
    }
}

static void exact(void *b, size_t n) {
    fflush(to_agent);
    if (fread(b, 1, n, from_agent) != n) fail("agent exited during a read");
}

static void read_at(uint64_t off, uint8_t *dst, size_t n) {   /* 4096-aligned 'P' reads */
    uint64_t a = target_bsize > 4096 ? target_bsize : 4096, s = off / a * a, e = (off + n + a - 1) / a * a;
    if (e > target_size) e = target_size;
    uint8_t *buf = malloc(e - s);
    for (uint64_t p = s; p < e; ) {
        uint32_t l = e - p > MAXREAD ? MAXREAD : (uint32_t)(e - p);
        uint8_t h[13] = { 'P' }; put64(h + 1, p); put32(h + 9, l); asend(h, 13);
        exact(buf + (p - s), l); p += l;
    }
    memcpy(dst, buf + (off - s), n); free(buf);
}

/* ------------------------------------------------------------------ frames */
static FILE *segf; static int seg_open = -1; static uint64_t seg_pos;

static void send_frame(uint32_t i) {
    frame_t *f = &fr[i];
    if (f->seg != seg_open) {
        if (segf) fclose(segf);
        char p[PATH_MAX]; snprintf(p, sizeof p, "%s/stream.%03u", bundle, f->seg);
        if (!(segf = fopen(p, "r"))) fail("%s: %s", p, strerror(errno));
        static char rbuf[4 << 20]; setvbuf(segf, rbuf, _IOFBF, sizeof rbuf);
        seg_open = f->seg; seg_pos = 0;
    }
    if (seg_pos != f->spos) { if (fseeko(segf, f->spos, SEEK_SET)) fail("seek stream.%03u", f->seg); seg_pos = f->spos; }
    size_t hl = f->type == 'D' ? 37 : 13;
    uint8_t h[37];
    if (fread(h, 1, hl, segf) != hl) fail("stream.%03u: short read at %llu", f->seg, f->spos);
    int ok = h[0] == f->type && le64(h + 1) == f->off && le32(h + 9) == f->wlen;
    if (f->type == 'D') ok = ok && !memcmp(h + 13, f->hash, 20) && le32(h + 33) == f->clen;
    if (!ok) fail("stream.%03u at %llu: frame header differs from the index (bundle damaged)", f->seg, f->spos);
    asend(h, hl);
    seg_pos += hl;
    if (f->type == 'D') {
        static uint8_t *blob; static size_t cap;
        if (f->clen > cap) { free(blob); cap = f->clen; blob = malloc(cap); }
        if (fread(blob, 1, f->clen, segf) != f->clen) fail("stream.%03u: short read", f->seg);
        asend(blob, f->clen);
        seg_pos += f->clen;
    }
}

static uint64_t frame_bytes(uint32_t i) { return fr[i].type == 'D' ? 37 + fr[i].clen : 13; }

static int overlaps_rw(uint64_t off, uint32_t len) {
    for (int k = 0; k < 5; k++) if (rw[k].off < off + len && off < rw[k].off + rw[k].len) return 1;
    return 0;
}

/* frames to check with 'Q': not chunk 0, not the partial tail, nothing the relocated GPT rewrote */
static int q_ok(uint32_t i) {
    return fr[i].off && (int64_t)(fr[i].off / cs) != tail_index && fr[i].wlen == cs && !overlaps_rw(fr[i].off, cs);
}

/* 'Q' over the listed frames, pipelined one batch ahead; returns the frames whose hash differs (count in *nbad) */
static uint32_t *query(uint32_t *list, uint32_t n, const char *phase, uint32_t *nbad) {
    uint32_t nb = (n + QBATCH - 1) / QBATCH, *bad = malloc((n + 1) * sizeof *bad), m = 0;
    uint8_t *body = malloc(5 + QBATCH * 32);
    for (uint32_t b = 0; b <= nb; b++) {
        if (b < nb) {                                     /* queue batch b, then answer batch b - 1 */
            uint32_t s = b * QBATCH, c = n - s < QBATCH ? n - s : QBATCH;
            body[0] = 'Q'; put32(body + 1, c);
            for (uint32_t k = 0; k < c; k++) {
                frame_t *f = &fr[list[s + k]];
                put64(body + 5 + 32 * k, f->off); put32(body + 13 + 32 * k, cs); memcpy(body + 17 + 32 * k, f->hash, 20);
            }
            asend(body, 5 + 32 * c);
        }
        if (b) {
            uint8_t b4[4]; exact(b4, 4);
            uint32_t mm = le32(b4), s = (b - 1) * QBATCH;
            for (uint32_t k = 0; k < mm; k++) { exact(b4, 4); bad[m++] = list[s + le32(b4)]; }
            printf("PROGRESS %s %llu %llu\n", phase, (unsigned long long)(b * (uint64_t)QBATCH > n ? n : b * (uint64_t)QBATCH) * cs,
                   (unsigned long long)n * cs);
            fflush(stdout);
        }
    }
    free(body);
    *nbad = m;
    return bad;
}

/* ------------------------------------------------------------------ disks (diskutil plists) */
static CFTypeRef plist_cmd(const char *cmd) {
    FILE *p = popen(cmd, "r");
    if (!p) return NULL;
    CFMutableDataRef d = CFDataCreateMutable(NULL, 0);
    uint8_t b[65536]; size_t r;
    while ((r = fread(b, 1, sizeof b, p)) > 0) CFDataAppendBytes(d, b, r);
    int st = pclose(p);
    CFTypeRef v = st ? NULL : CFPropertyListCreateWithData(NULL, d, kCFPropertyListImmutable, NULL, NULL);
    CFRelease(d);
    return v;
}
static int dbool(CFDictionaryRef d, const char *k) {
    CFStringRef s = CFStringCreateWithCString(NULL, k, kCFStringEncodingUTF8);
    CFTypeRef v = CFDictionaryGetValue(d, s); CFRelease(s);
    return v && CFGetTypeID(v) == CFBooleanGetTypeID() && CFBooleanGetValue(v);
}
static int dstr(CFDictionaryRef d, const char *k, char *out, size_t n) {
    CFStringRef s = CFStringCreateWithCString(NULL, k, kCFStringEncodingUTF8);
    CFTypeRef v = CFDictionaryGetValue(d, s); CFRelease(s);
    out[0] = 0;
    return v && CFGetTypeID(v) == CFStringGetTypeID() && CFStringGetCString(v, out, n, kCFStringEncodingUTF8);
}
static uint64_t dnum(CFDictionaryRef d, const char *k) {
    CFStringRef s = CFStringCreateWithCString(NULL, k, kCFStringEncodingUTF8);
    CFTypeRef v = CFDictionaryGetValue(d, s); CFRelease(s);
    long long x = 0;
    if (v && CFGetTypeID(v) == CFNumberGetTypeID()) CFNumberGetValue(v, kCFNumberLongLongType, &x);
    return (uint64_t)x;
}

static int valid_disk_name(const char *n) {
    if (strncmp(n, "disk", 4) || !n[4]) return 0;
    for (const char *p = n + 4; *p; p++) if (*p < '0' || *p > '9') return 0;
    return 1;
}

/* names of the APFS containers whose physical store is a partition of disk (synthesized diskN) */
static int containers_of(const char *disk, char out[][32], int max) {
    int n = 0;
    CFDictionaryRef l = plist_cmd("/usr/sbin/diskutil apfs list -plist");
    CFArrayRef cs_ = l ? CFDictionaryGetValue(l, CFSTR("Containers")) : NULL;
    for (CFIndex i = 0; cs_ && i < CFArrayGetCount(cs_) && n < max; i++) {
        CFDictionaryRef c = CFArrayGetValueAtIndex(cs_, i);
        CFArrayRef ps = CFDictionaryGetValue(c, CFSTR("PhysicalStores"));
        for (CFIndex k = 0; ps && k < CFArrayGetCount(ps); k++) {
            char id[64]; dstr(CFArrayGetValueAtIndex(ps, k), "DeviceIdentifier", id, sizeof id);
            size_t dl = strlen(disk);
            if (!strncmp(id, disk, dl) && id[dl] == 's') { dstr(c, "ContainerReference", out[n++], 32); break; }
        }
    }
    if (l) CFRelease(l);
    return n;
}

/* 0 = eligible; else why not. desc gets "size<TAB>model<TAB>volume names" */
static const char *disk_check(const char *disk, char *desc, size_t dn) {
    if (!valid_disk_name(disk)) return "not a whole disk name (diskN)";
    char cmd[128]; snprintf(cmd, sizeof cmd, "/usr/sbin/diskutil info -plist %s", disk);
    CFDictionaryRef i = plist_cmd(cmd);
    if (!i) return "diskutil info failed";
    char model[128], vp[32], bus[64];
    dstr(i, "MediaName", model, sizeof model); dstr(i, "VirtualOrPhysical", vp, sizeof vp); dstr(i, "BusProtocol", bus, sizeof bus);
    uint64_t size = dnum(i, "TotalSize") ? dnum(i, "TotalSize") : dnum(i, "Size");
    const char *why = NULL;
    if (!dbool(i, "WholeDisk")) why = "not a whole disk";
    else if (!dbool(i, "Internal")) why = "not internal";
    else if (!strcmp(vp, "Virtual")) why = "virtual (disk image / RAM disk)";
    else if (dbool(i, "Removable") || dbool(i, "RemovableMedia") || dbool(i, "Ejectable")) why = "removable";
    else if (!strcmp(bus, "USB")) why = "USB";
    CFRelease(i);
    /* volume names, for the user: what is on it now */
    char names[512] = "", ctr[8][32];
    int nc = containers_of(disk, ctr, 8);
    CFDictionaryRef l = plist_cmd("/usr/sbin/diskutil apfs list -plist");
    CFArrayRef cl = l ? CFDictionaryGetValue(l, CFSTR("Containers")) : NULL;
    for (CFIndex k = 0; cl && k < CFArrayGetCount(cl); k++) {
        CFDictionaryRef c = CFArrayGetValueAtIndex(cl, k); char ref[32]; dstr(c, "ContainerReference", ref, sizeof ref);
        int mine = 0; for (int j = 0; j < nc; j++) mine |= !strcmp(ref, ctr[j]);
        CFArrayRef vols = mine ? CFDictionaryGetValue(c, CFSTR("Volumes")) : NULL;
        for (CFIndex v = 0; vols && v < CFArrayGetCount(vols); v++) {
            CFDictionaryRef vd = CFArrayGetValueAtIndex(vols, v); char nm[128];
            CFArrayRef roles = CFDictionaryGetValue(vd, CFSTR("Roles"));
            int show = !roles || !CFArrayGetCount(roles) || CFArrayContainsValue(roles, CFRangeMake(0, CFArrayGetCount(roles)), CFSTR("System"));
            if (show && dstr(vd, "Name", nm, sizeof nm) && strlen(names) + strlen(nm) + 3 < sizeof names) {
                if (names[0]) strcat(names, ", ");
                strcat(names, nm);
            }
        }
    }
    if (l) CFRelease(l);
    struct statfs *m; int nm = getmntinfo(&m, MNT_NOWAIT);
    for (int k = 0; k < nm; k++) {
        /* never the disk the running system booted from (recoveryOS runs from a RAM disk, so in recovery this
         * only ever excludes something unexpected; on a normal Mac it excludes its own disk) */
        if (!strcmp(m[k].f_mntonname, "/") && !strncmp(m[k].f_mntfromname, "/dev/", 5)) {
            const char *f = m[k].f_mntfromname + 5;
            for (int j = -1; j < nc; j++) {
                const char *nmj = j < 0 ? disk : ctr[j]; size_t l = strlen(nmj);
                if (!strncmp(f, nmj, l) && (f[l] == 's' || !f[l])) why = "holds the running system";
            }
        }
    }
    snprintf(desc, dn, "%llu\t%s\t%s", (unsigned long long)size, model, names[0] ? names : "-");
    return why;
}

static int cmd_list_disks(void) {
    CFDictionaryRef l = plist_cmd("/usr/sbin/diskutil list -plist internal physical");
    CFArrayRef w = l ? CFDictionaryGetValue(l, CFSTR("WholeDisks")) : NULL;
    int n = 0;
    for (CFIndex i = 0; w && i < CFArrayGetCount(w); i++) {
        char d[32], desc[768]; CFStringGetCString(CFArrayGetValueAtIndex(w, i), d, sizeof d, kCFStringEncodingUTF8);
        const char *why = disk_check(d, desc, sizeof desc);
        if (why) fprintf(stderr, "skip %s: %s\n", d, why);
        else { printf("/dev/%s\t%s\n", d, desc); n++; }
    }
    if (l) CFRelease(l);
    return n ? 0 : 1;
}

static void print_image(const char *dir) {
    char real[PATH_MAX];
    if (!realpath(dir, real) || read_kv(real)) return;
    printf("%s", real);
    const char *keys[] = { "name", "model", "macos_version", "macos_build", "size", "data_bytes", "stream_bytes", "created", "commit" };
    for (size_t k = 0; k < sizeof keys / sizeof *keys; k++) printf("\t%s=%s", keys[k], kv(keys[k]) ? kv(keys[k]) : "");
    printf("\n");
}

static int cmd_list_images(int argc, char **argv) {
    if (argc) { for (int i = 0; i < argc; i++) print_image(argv[i]); return 0; }
    glob_t g;
    if (glob("/Volumes/*/*/IMAGE", 0, NULL, &g) == 0) {
        for (size_t i = 0; i < g.gl_pathc; i++) { char *s = strdup(g.gl_pathv[i]); *strrchr(s, '/') = 0; print_image(s); free(s); }
        globfree(&g);
    }
    return 0;
}

/* unmount everything on disk (its partitions and the APFS containers they hold); fail if anything stays mounted */
static void unmount_disk(const char *disk) {
    char cmd[128]; snprintf(cmd, sizeof cmd, "/usr/sbin/diskutil unmountDisk %s >/dev/null 2>&1", disk);
    char ctr[8][32]; int nc = containers_of(disk, ctr, 8);
    for (int k = 0; k < nc; k++) {               /* the synthesized APFS disks first: unmountDisk of the store may not */
        char c2[128]; snprintf(c2, sizeof c2, "/usr/sbin/diskutil unmountDisk %s >/dev/null 2>&1", ctr[k]);
        if (valid_disk_name(ctr[k])) system(c2);
    }
    system(cmd);
    struct statfs *m; int nm = getmntinfo(&m, MNT_NOWAIT);
    for (int k = 0; k < nm; k++) {
        const char *f = m[k].f_mntfromname;
        if (strncmp(f, "/dev/", 5)) continue;
        f += 5;
        const char *names[9]; int nn = 0; names[nn++] = disk; for (int j = 0; j < nc; j++) names[nn++] = ctr[j];
        for (int j = 0; j < nn; j++) {
            size_t l = strlen(names[j]);
            if (!strncmp(f, names[j], l) && (f[l] == 's' || !f[l])) fail("%s is still mounted at %s", m[k].f_mntfromname, m[k].f_mntonname);
        }
    }
}

/* 1 when PATH lives on disk: on one of its partitions or on a volume of an APFS container it holds */
static int path_on_disk(const char *path, const char *disk) {
    struct statfs sf;
    if (statfs(path, &sf) || strncmp(sf.f_mntfromname, "/dev/", 5)) return 0;
    const char *f = sf.f_mntfromname + 5;
    char ctr[8][32]; int nc = containers_of(disk, ctr, 8);
    for (int j = -1; j < nc; j++) {
        const char *n = j < 0 ? disk : ctr[j]; size_t l = strlen(n);
        if (!strncmp(f, n, l) && (f[l] == 's' || !f[l])) return 1;
    }
    return 0;
}

/* the image's APFS container grown to the whole disk (the image is smaller than most disks; as ssdimg push does).
 * The new partition table shows once the agent has closed the disk: wait up to 30 s for it. Failing here leaves a
 * verified install that boots at the image's size, so it is reported, not fatal. */
static void grow(const char *disk) {
    char cmd[1024], l[256] = "";
    snprintf(cmd, sizeof cmd,
             "i=0; while [ $i -lt 30 ]; do p=$(/usr/sbin/diskutil list %s | /usr/bin/awk '/Apple_APFS/{print $NF; exit}');"
             " [ -n \"$p\" ] && break; sleep 1; i=$((i + 1)); done;"
             " [ -n \"$p\" ] && /usr/sbin/diskutil apfs resizeContainer \"$p\" 0 >/dev/null 2>&1 &&"
             " echo \"$p $(/usr/sbin/diskutil info \"$p\" | /usr/bin/awk -F': *' '/Disk Size/{print $2}')\"", disk);
    FILE *p = popen(cmd, "r");
    if (p && fgets(l, sizeof l, p)) l[strcspn(l, "\n")] = 0;
    if (p) pclose(p);
    if (l[0]) printf("INFO grow: APFS container %s\n", l);
    else printf("INFO grow FAILED: the disk keeps the image's size; diskutil apfs resizeContainer <APFS partition> 0\n");
    fflush(stdout);
}

static const char *find_agent(const char *argv0, const char *opt) {
    static char p[PATH_MAX];
    if (opt) return opt;
    char self[PATH_MAX];
    if (realpath(argv0, self)) {
        *strrchr(self, '/') = 0;
        const char *rel[] = { "ssdpush-agent", "bin/ssdpush-agent", "ssdpush-agent-macos-x86_64", "ssdpush-agent-macos" };
        for (size_t i = 0; i < sizeof rel / sizeof *rel; i++) {
            snprintf(p, sizeof p, "%s/%s", self, rel[i]);
#if defined(__x86_64__)
            if (strstr(rel[i], "macos") && !strstr(rel[i], "x86_64")) continue;
#else
            if (strstr(rel[i], "x86_64")) continue;
#endif
            if (access(p, X_OK) == 0) return p;
        }
    }
    if (access("/usr/local/ssdtools/bin/ssdpush-agent", X_OK) == 0) return "/usr/local/ssdtools/bin/ssdpush-agent";
    fail("no ssdpush-agent found (next to ssdinstall or in /usr/local/ssdtools/bin); use --agent");
    return NULL;
}

static int cmd_install(int argc, char **argv, const char *argv0) {
    const char *dir = NULL, *target = NULL, *agent = NULL;
    int yes = 0, delta = 0, noverify = 0, file = 0, startup = 0;
    for (int i = 0; i < argc; i++) {
        if (!strcmp(argv[i], "--yes")) yes = 1;
        else if (!strcmp(argv[i], "--delta")) delta = 1;
        else if (!strcmp(argv[i], "--no-verify")) noverify = 1;
        else if (!strcmp(argv[i], "--file")) file = 1;
        else if (!strcmp(argv[i], "--startup")) startup = 1;
        else if (!strcmp(argv[i], "--agent") && i + 1 < argc) agent = argv[++i];
        else if (!dir) dir = argv[i];
        else if (!target) target = argv[i];
        else fail("unexpected argument %s", argv[i]);
    }
    if (!dir || !target) fail("usage: ssdinstall install BUNDLE TARGET [--yes] [--delta] [--no-verify] [--file] [--agent PATH] [--startup]");
    double t0 = now();
    printf("STEP prepare\n"); fflush(stdout);
    load_bundle(dir);
    printf("INFO image %s (macOS %s %s, %s) %llu bytes, %u frames, %llu bytes to read\n", kv("name"), kv("macos_version") ?: "?",
           kv("macos_build") ?: "?", kv("model") ?: "?", (unsigned long long)img_size, nfr, (unsigned long long)stream_total);
    agent = find_agent(argv0, agent);
    char dev[PATH_MAX] = "", dname[32] = "";
    if (file) {
        struct stat st;
        if (stat(target, &st) || !S_ISREG(st.st_mode)) fail("--file: %s is not a regular file", target);
        snprintf(dev, sizeof dev, "%s", target);
        if (strlen(target) >= sizeof dev) fail("--file: path too long");
    } else {
        const char *d = !strncmp(target, "/dev/r", 6) ? target + 6 : !strncmp(target, "/dev/", 5) ? target + 5 : target;
        char desc[768]; const char *why = disk_check(d, desc, sizeof desc);
        if (why) fail("/dev/%s: %s; refusing", d, why);
        if (path_on_disk(bundle, d)) fail("the image is on /dev/%s, the disk it would erase; copy it to a USB disk", d);
        snprintf(dname, sizeof dname, "%.31s", d);
        printf("INFO target /dev/%s\t%s\n", d, desc);
        if (!yes) {
            fprintf(stderr, "ALL DATA ON /dev/%s WILL BE DESTROYED. Type %s to continue: ", d, d);
            char ans[64] = ""; if (!fgets(ans, sizeof ans, stdin)) ans[0] = 0;
            ans[strcspn(ans, "\n")] = 0;
            if (strcmp(ans, d)) fail("aborted");
        }
        unmount_disk(d);
        snprintf(dev, sizeof dev, "/dev/r%s", d);
    }
    CFStringRef ps = IOPSGetProvidingPowerSourceType(NULL);
    printf("INFO power %s\n", ps && CFEqual(ps, CFSTR(kIOPMACPowerKey)) ? "ac" : "battery");
    IOPMAssertionID as1 = 0, as2 = 0;
    IOPMAssertionCreateWithName(kIOPMAssertionTypePreventUserIdleSystemSleep, kIOPMAssertionLevelOn, CFSTR("ssdinstall"), &as1);
    IOPMAssertionCreateWithName(kIOPMAssertionTypePreventSystemSleep, kIOPMAssertionLevelOn, CFSTR("ssdinstall"), &as2);
    signal(SIGPIPE, SIG_IGN);
    agent_start(agent, dev);
    uint8_t hdr[24]; memcpy(hdr, "SSDPUSH1", 8); put64(hdr + 8, img_size); put32(hdr + 16, cs); put32(hdr + 20, file ? 0 : 2);
    asend(hdr, 24);
    char line[512]; agent_line(line, sizeof line, "READY");
    char *p = strstr(line, "size="); target_size = p ? strtoull(p + 5, NULL, 10) : 0;
    p = strstr(line, "bsize="); target_bsize = p ? atoi(p + 6) : 0;
    printf("INFO agent %s\n", line);
    if (target_bsize != sector) fail("target sector size %u != image sector size %u", target_bsize, sector);
    gpt_relocate(target_size / sector);

    uint32_t *todo = malloc((nfr + 1) * sizeof *todo), ntodo = 0;
    if (delta) {
        printf("STEP delta\n"); fflush(stdout);
        uint32_t *q = malloc((nfr + 1) * sizeof *q), nq = 0, nbad;
        for (uint32_t i = 0; i < nfr; i++) if (q_ok(i)) q[nq++] = i;
        uint32_t *bad = query(q, nq, "delta", &nbad);
        char *send_it = calloc(nfr, 1);
        for (uint32_t k = 0; k < nbad; k++) send_it[bad[k]] = 1;
        for (uint32_t i = 0; i < nfr; i++) if (!q_ok(i)) send_it[i] = 1;
        for (uint32_t i = 0; i < nfr; i++) if (send_it[i]) todo[ntodo++] = i;
        printf("INFO delta: %u of %u frames differ\n", ntodo, nfr);
        free(q); free(bad); free(send_it);
    } else {
        for (uint32_t i = 0; i < nfr; i++) todo[ntodo++] = i;
    }
    uint64_t total = 0, done = 0;
    for (uint32_t k = 0; k < ntodo; k++) total += frame_bytes(todo[k]);
    printf("STEP write\n"); fflush(stdout);
    double tw = now(), last = 0;
    for (uint32_t k = 0; k < ntodo; k++) {
        send_frame(todo[k]);
        done += frame_bytes(todo[k]);
        if (now() - last > 0.5 || k + 1 == ntodo) {
            last = now();
            printf("PROGRESS write %llu %llu\n", (unsigned long long)done, (unsigned long long)total); fflush(stdout);
        }
    }
    printf("STEP gpt\n"); fflush(stdout);
    for (int k = 0; k < 5; k++) {
        uint8_t h[13] = { 'R' }; put64(h + 1, rw[k].off); put32(h + 9, rw[k].len);
        asend(h, 13); asend(rw[k].data, rw[k].len);
    }
    printf("STEP flush\n"); fflush(stdout);
    asend("F", 1);
    agent_line(line, sizeof line, "OK");
    printf("INFO written %llu bytes of stream in %.0fs; agent %s\n", (unsigned long long)total, now() - tw, line + 3);

    if (!noverify) {
        printf("STEP verify\n"); fflush(stdout);
        uint32_t *q = malloc((nfr + 1) * sizeof *q), nq = 0, nbad, partial = 0;
        for (uint32_t k = 0; k < ntodo; k++) {
            uint32_t i = todo[k];
            if (q_ok(i)) q[nq++] = i;
            else if (fr[i].off && (int64_t)(fr[i].off / cs) != tail_index) partial++;
        }
        uint32_t *bad = query(q, nq, "verify", &nbad);
        if (nbad) fail("VERIFY FAILED: %u chunks differ from the image (first at offset %llu)", nbad, (unsigned long long)fr[bad[0]].off);
        /* chunk 0 = head.bin with the relocated GPT pieces that fall inside it */
        uint8_t *want = malloc(cs), *got = malloc(cs);
        memcpy(want, head, cs);
        for (int k = 0; k < 5; k++) if (rw[k].off < cs) memcpy(want + rw[k].off, rw[k].data, rw[k].off + rw[k].len <= cs ? rw[k].len : cs - rw[k].off);
        read_at(0, got, cs);
        if (memcmp(want, got, cs)) fail("VERIFY FAILED: chunk 0 (MBR + GPT) differs");
        if (tailbuf) {
            uint64_t toff = (uint64_t)tail_index * cs;
            uint8_t *tw2 = malloc(tail_len), *tg = malloc(tail_len);
            memcpy(tw2, tailbuf, tail_len);
            for (int k = 0; k < 5; k++)
                if (rw[k].off < toff + tail_len && toff < rw[k].off + rw[k].len) {
                    uint64_t s = rw[k].off > toff ? rw[k].off : toff, e = rw[k].off + rw[k].len < toff + tail_len ? rw[k].off + rw[k].len : toff + tail_len;
                    memcpy(tw2 + (s - toff), rw[k].data + (s - rw[k].off), e - s);
                }
            read_at(toff, tg, tail_len);
            if (memcmp(tw2, tg, tail_len)) fail("VERIFY FAILED: the image's last (partial) chunk differs");
            free(tw2); free(tg);
        }
        for (int k = 0; k < 5; k++) {
            if (rw[k].off < cs) continue;
            uint8_t *b = malloc(rw[k].len); read_at(rw[k].off, b, rw[k].len);
            if (memcmp(b, rw[k].data, rw[k].len)) fail("VERIFY FAILED: GPT piece at %llu differs", (unsigned long long)rw[k].off);
            free(b);
        }
        printf("INFO verify: %u chunks re-read and hash-checked, chunk 0 + %s5 GPT pieces compared%s\n", nq,
               tailbuf ? "tail + " : "", partial ? " (some chunks overlapping the relocated GPT: GPT bytes only)" : "");
        free(q); free(bad); free(want); free(got);
    }
    asend("X", 1); fflush(to_agent); fclose(to_agent);
    int st = 0; waitpid(agent_pid, &st, 0); agent_pid = 0;
    if (!WIFEXITED(st) || WEXITSTATUS(st)) fail("agent exited with status %d", WIFEXITED(st) ? WEXITSTATUS(st) : -1);
    if (!file) { printf("STEP grow\n"); fflush(stdout); grow(dname); }
    if (startup && !file) {
        printf("STEP startup\n"); fflush(stdout);
        char self[PATH_MAX], cmd[PATH_MAX + 64] = "";
        if (realpath(argv0, self)) {
            *strrchr(self, '/') = 0;
            const char *rel[] = { "startup-internal", "bin/startup-internal" };
            for (int i = 0; i < 2 && !cmd[0]; i++) {
                char s[PATH_MAX]; snprintf(s, sizeof s, "%s/%s", self, rel[i]);
                if (access(s, R_OK) == 0) snprintf(cmd, sizeof cmd, "/bin/sh '%s' %s", s, dname);
            }
        }
        if (!cmd[0]) fail("startup-internal not found next to ssdinstall");
        FILE *sp = popen(cmd, "r"); char l[512];
        while (sp && fgets(l, sizeof l, sp)) printf("INFO startup: %s", l);
        if (!sp || pclose(sp)) fail("startup-internal failed: the disk is installed and verified, but the firmware default was not set");
    }
    IOPMAssertionRelease(as1); IOPMAssertionRelease(as2);
    printf("STEP done\nDONE seconds=%.0f frames=%u\n", now() - t0, ntodo); fflush(stdout);
    return 0;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (argc >= 2 && !strcmp(argv[1], "list-disks")) return cmd_list_disks();
    if (argc >= 2 && !strcmp(argv[1], "list-images")) return cmd_list_images(argc - 2, argv + 2);
    if (argc >= 2 && !strcmp(argv[1], "install")) return cmd_install(argc - 2, argv + 2, argv[0]);
    fprintf(stderr, "usage: ssdinstall list-disks | list-images [DIR...] | install BUNDLE TARGET [--yes] [--delta] [--no-verify] [--file] [--agent PATH] [--startup]\n");
    return 2;
}
