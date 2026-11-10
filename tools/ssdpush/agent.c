/*
 * ssdpush-agent: receiving end of `ssdimg push`. Runs on the target Mac (as root for a raw disk),
 * reads a frame stream on stdin and writes it to a raw disk or a file.
 *
 *   ssdpush-agent <target> [--workers N] [--tcp]
 *
 * --tcp: the frame stream runs over a TCP connection instead of stdin/stdout, which then only
 * carry the setup (the host's ssh session): the agent reads a one-time token line from stdin, listens
 * on an ephemeral port, prints "LISTEN <port>", and accepts the one connection that sends that token
 * first (15 s). Everything after that is the protocol below, unchanged; every chunk is still hash-checked
 * and read back, so the stream needs no encryption of its own. The agent exits if the ssh session ends.
 *
 * Every data chunk arrives zstd-compressed exactly as stored in the ssdimg object store, with its
 * BLAKE2b-160 content hash. The agent decompresses it, checks the hash, writes it, and (if asked)
 * reads it back from the device and compares. Nothing is written for a chunk whose hash fails.
 *
 * Protocol (little-endian). Host -> agent:
 *   "SSDPUSH1" u64 image_size u32 chunk_size u32 flags       flags: 1 = verify read-back,
 *                                                              2 = TRIM zero ranges instead of writing zeros
 *                                                                  (Linux block devices, macOS raw disks via
 *                                                                  DKIOCUNMAP; the drive must read zeros after
 *                                                                  TRIM; falls back to writing)
 *   'D' u64 off u32 wlen u8 hash[20] u32 clen <clen bytes>    zstd chunk, write first wlen bytes
 *   'Z' u64 off u32 wlen                                       write wlen zero bytes
 *   'R' u64 off u32 len <len bytes>                            raw bytes (partition table)
 *   'Q' u32 n { u64 off u32 len u8 hash[20] } * n              which ranges differ from hash?
 *   'P' u64 off u32 len                                        read len bytes back (capture; may be pipelined)
 *   'F'                                                        drain, fsync, report
 *   'X'                                                        exit
 * Agent -> host: text lines "READY ...", "OK ...", "ERR ..."; 'Q' answers u32 m { u32 index } * m;
 * 'P' answers exactly len raw bytes.
 *
 * Builds for macOS (raw /dev/rdiskN) and Linux (/dev/sdX, static musl via `zig cc`).
 */
#ifdef __linux__
#define _GNU_SOURCE
#endif
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#ifdef __APPLE__
#include <sys/disk.h>
#include <IOKit/pwr_mgt/IOPMLib.h>
#else
#include <linux/fs.h>
#endif
#include <sys/resource.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>

#define ZSTD_STATIC_LINKING_ONLY
#include "zstd.h"
#include "blake2b.h"

#define COALESCE (8u << 20)   /* max bytes per device write */
#define QUEUE_MAX 128         /* jobs in flight (bounds RAM) */

static void die(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    fputs("ERR ", stdout); vfprintf(stdout, fmt, ap); fputc('\n', stdout); fflush(stdout);
    va_end(ap); _exit(1);
}

/* ---------------------------------------------------------------- stdin helpers */
static void rd(void *p, size_t n) {
    if (n && fread(p, 1, n, stdin) != n) die("unexpected end of input");
}
static uint64_t rd64(void) { uint8_t b[8]; rd(b, 8); uint64_t v = 0; for (int i = 7; i >= 0; i--) v = v << 8 | b[i]; return v; }
static uint32_t rd32(void) { uint8_t b[4]; rd(b, 4); return b[0] | b[1] << 8 | b[2] << 16 | (uint32_t)b[3] << 24; }

/* ---------------------------------------------------------------- jobs */
typedef struct job {
    uint64_t seq, off;
    uint32_t wlen, clen;
    char type;                /* D Z R */
    uint8_t hash[20];
    uint8_t *in;              /* compressed or raw input */
    uint8_t *out;             /* data to write (wlen bytes) */
    int done;
    struct job *next;
} job;

static int fd = -1, wfd = -1, vfd = -1, verify = 0, trim = 0, workers = 3;   /* wfd: writes, vfd: reads (both uncached on Linux) */
static uint64_t image_size, target_size; static uint32_t chunk_size;
static uint8_t *zeros;

static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv_work = PTHREAD_COND_INITIALIZER, cv_done = PTHREAD_COND_INITIALIZER,
                      cv_space = PTHREAD_COND_INITIALIZER, cv_drained = PTHREAD_COND_INITIALIZER;
static job *work_head, *work_tail;        /* jobs waiting for a worker */
static job *ring[QUEUE_MAX];               /* all in-flight jobs by seq % QUEUE_MAX */
static uint64_t next_seq, written_seq;     /* assigned / fully written (and verified) */
static uint64_t taken_seq;                 /* handed to the writer */
static uint64_t bytes_written, chunks_written, zero_chunks, raw_frames, bytes_trimmed;

/* Recycled chunk buffers. Fresh malloc() of chunk-sized buffers costs an mmap + ~100 page faults +
 * munmap per chunk with musl (1.67M faults per 4 GiB pushed); reusing them avoids all of that. */
typedef struct buf { struct buf *next; } buf;
static pthread_mutex_t pool_mu = PTHREAD_MUTEX_INITIALIZER;
static buf *pool_out, *pool_in;
static size_t in_cap;                     /* size of pooled input buffers */
static uint8_t *pool_get(buf **pool, size_t size) {
    pthread_mutex_lock(&pool_mu);
    buf *b = *pool; if (b) *pool = b->next;
    pthread_mutex_unlock(&pool_mu);
    if (b) return (uint8_t *)b;
    uint8_t *p; if (posix_memalign((void **)&p, 4096, size)) die("out of memory");
    return p;
}
static void pool_put(buf **pool, uint8_t *p) {
    buf *b = (buf *)p;
    pthread_mutex_lock(&pool_mu);
    b->next = *pool; *pool = b;
    pthread_mutex_unlock(&pool_mu);
}
static uint8_t *in_get(uint32_t clen) { return clen <= in_cap ? pool_get(&pool_in, in_cap) : malloc(clen ? clen : 1); }
static void in_put(uint8_t *p, uint32_t clen) { if (clen <= in_cap) pool_put(&pool_in, p); else free(p); }

static void *worker(void *arg) {
    (void)arg;
    ZSTD_DCtx *dctx = ZSTD_createDCtx();
    for (;;) {
        pthread_mutex_lock(&mu);
        while (!work_head) pthread_cond_wait(&cv_work, &mu);
        job *j = work_head; work_head = j->next; if (!work_head) work_tail = NULL;
        pthread_mutex_unlock(&mu);

        if (j->type == 'D') {
            unsigned long long fcs = ZSTD_getFrameContentSize(j->in, j->clen);
            if (fcs == ZSTD_CONTENTSIZE_ERROR || fcs == ZSTD_CONTENTSIZE_UNKNOWN || fcs > chunk_size)
                die("chunk at %llu: bad zstd frame (content size %llu, chunk size %u)", (unsigned long long)j->off, fcs, chunk_size);
            uint8_t *buf = pool_get(&pool_out, chunk_size);
            size_t r = ZSTD_decompressDCtx(dctx, buf, fcs, j->in, j->clen);
            if (ZSTD_isError(r) || r != fcs)
                die("chunk at %llu: zstd: %s", (unsigned long long)j->off, ZSTD_isError(r) ? ZSTD_getErrorName(r) : "short");
            uint8_t h[20]; blake2b(h, 20, buf, fcs);
            if (memcmp(h, j->hash, 20))
                die("chunk at %llu: content hash mismatch (corrupt object or transfer)", (unsigned long long)j->off);
            if (fcs < j->wlen) die("chunk at %llu: %llu bytes < write length %u", (unsigned long long)j->off, fcs, j->wlen);
            in_put(j->in, j->clen); j->in = NULL; j->out = buf;
        } else if (j->type == 'R') {
            j->out = j->in; j->in = NULL;
        } else {
            j->out = zeros;
        }
        pthread_mutex_lock(&mu);
        j->done = 1;
        pthread_cond_broadcast(&cv_done);
        pthread_mutex_unlock(&mu);
    }
    return NULL;
}

static void pwrite_all(const uint8_t *p, size_t n, uint64_t off) {
    while (n) {
        ssize_t w = pwrite(wfd, p, n, (off_t)off);
        if (w < 0) { if (errno == EINTR) continue; die("write at %llu: %s", (unsigned long long)off, strerror(errno)); }
        p += w; n -= (size_t)w; off += (uint64_t)w;
    }
}
static void pread_all(uint8_t *p, size_t n, uint64_t off) {
    while (n) {
        ssize_t r = pread(vfd, p, n, (off_t)off);
        if (r < 0) { if (errno == EINTR) continue; die("read at %llu: %s", (unsigned long long)off, strerror(errno)); }
        if (r == 0) die("read at %llu: end of target", (unsigned long long)off);
        p += r; n -= (size_t)r; off += (uint64_t)r;
    }
}

/* Stage buffers between the writer and the verifier: the writer fills and writes one while the
 * verifier reads back and compares the previous one, so writing and verifying overlap. */
#define NSTAGE 3
typedef struct { uint8_t *buf; uint64_t off; size_t len; int nb, full; } stage_t;
static stage_t stg[NSTAGE];
static pthread_cond_t cv_stage_full = PTHREAD_COND_INITIALIZER, cv_stage_free = PTHREAD_COND_INITIALIZER;

/* TRIM a zero range instead of writing it; 0 = not possible (then it is written, and trim is off) */
static int discard(uint64_t off, size_t len) {
#ifdef __linux__
    uint64_t r[2] = { off, len };
    if (ioctl(wfd, BLKDISCARD, r) == 0) { bytes_trimmed += len; return 1; }
    fprintf(stderr, "ssdpush-agent: TRIM at %llu failed (%s); writing zeros instead\n", (unsigned long long)off, strerror(errno));
#elif defined(__APPLE__)
    dk_extent_t e = { .offset = off, .length = len };
    dk_unmap_t u = { .extents = &e, .extentsCount = 1 };
    if (ioctl(wfd, DKIOCUNMAP, &u) == 0) { bytes_trimmed += len; return 1; }
    fprintf(stderr, "ssdpush-agent: TRIM at %llu failed (%s); writing zeros instead\n", (unsigned long long)off, strerror(errno));
#else
    (void)off; (void)len;
#endif
    trim = 0;
    return 0;
}

static void complete(int nb, size_t len) {   /* caller holds mu: nb jobs are on the device (and verified) */
    bytes_written += len;
    written_seq += nb;
    pthread_cond_broadcast(&cv_space);
    pthread_cond_broadcast(&cv_drained);
}

/* Writes jobs strictly in seq order, coalescing contiguous ones into a stage buffer. */
static void *writer(void *arg) {
    (void)arg;
    for (int w = 0;; w = (w + 1) % NSTAGE) {
        job *batch[64]; int nb = 0; size_t len = 0;
        stage_t *st = &stg[w];
        pthread_mutex_lock(&mu);
        while (st->full) pthread_cond_wait(&cv_stage_free, &mu);
        for (;;) {
            job *j = ring[taken_seq % QUEUE_MAX];
            if (j && j->seq == taken_seq && j->done) break;
            pthread_cond_wait(&cv_done, &mu);
        }
        /* take the ready, contiguous run starting at taken_seq */
        uint64_t s = taken_seq;
        while (nb < 64) {
            job *j = ring[s % QUEUE_MAX];
            if (!j || j->seq != s || !j->done) break;
            if (nb && (batch[nb - 1]->off + batch[nb - 1]->wlen != j->off || len + j->wlen > COALESCE)) break;
            if (nb && trim && (batch[0]->type == 'Z') != (j->type == 'Z')) break;   /* zero runs alone */
            batch[nb++] = j; len += j->wlen; s++;
        }
        taken_seq = s;
        pthread_mutex_unlock(&mu);

        uint64_t off = batch[0]->off;
        if (off + len > target_size) die("write past end of target (%llu > %llu)", (unsigned long long)(off + len), (unsigned long long)target_size);
        if (trim && batch[0]->type == 'Z' && discard(off, len)) {
            if (verify) memset(st->buf, 0, len);           /* the read-back must find zeros */
        } else {
            size_t pos = 0;
            for (int k = 0; k < nb; k++) { memcpy(st->buf + pos, batch[k]->out, batch[k]->wlen); pos += batch[k]->wlen; }
            pwrite_all(st->buf, len, off);
        }

        pthread_mutex_lock(&mu);
        for (int k = 0; k < nb; k++) {   /* data is in the stage buffer now; the jobs can go */
            job *j = batch[k];
            if (j->type == 'D') { chunks_written++; pool_put(&pool_out, j->out); }
            else if (j->type == 'R') { raw_frames++; free(j->out); }
            else zero_chunks++;
            ring[j->seq % QUEUE_MAX] = NULL;
            free(j);
        }
        if (verify) {
            st->off = off; st->len = len; st->nb = nb; st->full = 1;
            pthread_cond_signal(&cv_stage_full);
        } else {
            complete(nb, len);
        }
        pthread_mutex_unlock(&mu);
    }
    return NULL;
}

/* Reads each written stage back from the device (uncached) and compares, in write order. */
static void *verifier(void *arg) {
    (void)arg;
    uint8_t *back;
    if (posix_memalign((void **)&back, 4096, COALESCE)) die("out of memory");
    for (int v = 0;; v = (v + 1) % NSTAGE) {
        stage_t *st = &stg[v];
        pthread_mutex_lock(&mu);
        while (!st->full) pthread_cond_wait(&cv_stage_full, &mu);
        pthread_mutex_unlock(&mu);
        pread_all(back, st->len, st->off);
        if (memcmp(back, st->buf, st->len))
            die("verify failed: read-back differs in %llu..%llu", (unsigned long long)st->off, (unsigned long long)(st->off + st->len));
        pthread_mutex_lock(&mu);
        st->full = 0;
        complete(st->nb, st->len);
        pthread_cond_broadcast(&cv_stage_free);
        pthread_mutex_unlock(&mu);
    }
    return NULL;
}

static void submit(job *j) {
    pthread_mutex_lock(&mu);
    while (next_seq - written_seq >= QUEUE_MAX) pthread_cond_wait(&cv_space, &mu);
    j->seq = next_seq++;
    ring[j->seq % QUEUE_MAX] = j;
    j->next = NULL;
    if (work_tail) work_tail->next = j; else work_head = j;
    work_tail = j;
    pthread_cond_signal(&cv_work);
    pthread_mutex_unlock(&mu);
}

static void drain(void) {
    pthread_mutex_lock(&mu);
    while (written_seq != next_seq) pthread_cond_wait(&cv_drained, &mu);
    pthread_mutex_unlock(&mu);
}

/* ---------------------------------------------------------------- 'Q' (which ranges differ from a hash?)
 * Used by --delta and by the verify pass after a push: QTHREADS threads each take the next entry, read
 * it from the device (uncached) and hash it, so several reads are in flight and hashing uses every core. */
#define QTHREADS 4
typedef struct { uint64_t off; uint32_t len; uint8_t want[20]; int bad; } qent;
static qent *qv; static uint32_t qn, qnext;
static pthread_mutex_t qmu = PTHREAD_MUTEX_INITIALIZER;

static void *q_worker(void *arg) {
    (void)arg;
    uint8_t *buf, h[20];
    if (posix_memalign((void **)&buf, 4096, chunk_size)) die("out of memory");
    for (;;) {
        pthread_mutex_lock(&qmu);
        uint32_t i = qnext < qn ? qnext++ : qn;
        pthread_mutex_unlock(&qmu);
        if (i == qn) break;
        qent *e = &qv[i];
        if (e->off + e->len > target_size) { e->bad = 1; continue; }
        pread_all(buf, e->len, e->off);
        blake2b(h, 20, buf, e->len);
        e->bad = memcmp(h, e->want, 20) != 0;
    }
    free(buf);
    return NULL;
}

static uint32_t query(qent *q, uint32_t n) {   /* -> number of entries that differ (q[i].bad set) */
    qv = q; qn = n; qnext = 0;
    pthread_t t[QTHREADS];
    for (int k = 0; k < QTHREADS; k++) pthread_create(&t[k], NULL, q_worker, NULL);
    for (int k = 0; k < QTHREADS; k++) pthread_join(t[k], NULL);
    uint32_t m = 0;
    for (uint32_t i = 0; i < n; i++) m += q[i].bad;
    return m;
}

/* ---------------------------------------------------------------- 'P' (read back to the host)
 * Served by a reader thread (disk) and a sender thread (stdout), so reading the next range overlaps
 * sending the previous one while the host keeps several requests in flight. Any other output waits
 * for them first (p_flush). */
#define PQ 4
typedef struct { uint64_t off; uint32_t len; uint8_t *buf; int state; } preq;   /* 0 free, 1 asked, 2 read */
static preq pq[PQ];
static int pq_submit;
static pthread_mutex_t pmu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t pcv = PTHREAD_COND_INITIALIZER;

static void *p_reader(void *arg) {
    (void)arg;
    for (int r = 0;; r = (r + 1) % PQ) {
        pthread_mutex_lock(&pmu);
        while (pq[r].state != 1) pthread_cond_wait(&pcv, &pmu);
        pthread_mutex_unlock(&pmu);
        pread_all(pq[r].buf, pq[r].len, pq[r].off);
        pthread_mutex_lock(&pmu);
        pq[r].state = 2;
        pthread_cond_broadcast(&pcv);
        pthread_mutex_unlock(&pmu);
    }
    return NULL;
}

static void *p_sender(void *arg) {
    (void)arg;
    for (int w = 0;; w = (w + 1) % PQ) {
        pthread_mutex_lock(&pmu);
        while (pq[w].state != 2) pthread_cond_wait(&pcv, &pmu);
        pthread_mutex_unlock(&pmu);
        if (fwrite(pq[w].buf, 1, pq[w].len, stdout) != pq[w].len || fflush(stdout)) die("stdout: %s", strerror(errno));
        pthread_mutex_lock(&pmu);
        pq[w].state = 0;
        pthread_cond_broadcast(&pcv);
        pthread_mutex_unlock(&pmu);
    }
    return NULL;
}

static void p_submit(uint64_t off, uint32_t len) {
    static int started;
    if (!started) {
        for (int i = 0; i < PQ; i++)
            if (posix_memalign((void **)&pq[i].buf, 4096, COALESCE)) die("out of memory");
        pthread_t t;
        pthread_create(&t, NULL, p_reader, NULL);
        pthread_create(&t, NULL, p_sender, NULL);
        started = 1;
    }
    pthread_mutex_lock(&pmu);
    while (pq[pq_submit].state != 0) pthread_cond_wait(&pcv, &pmu);
    pq[pq_submit].off = off; pq[pq_submit].len = len; pq[pq_submit].state = 1;
    pq_submit = (pq_submit + 1) % PQ;
    pthread_cond_broadcast(&pcv);
    pthread_mutex_unlock(&pmu);
}

static void p_flush(void) {   /* every asked range has been sent */
    pthread_mutex_lock(&pmu);
    for (int i = 0; i < PQ; i++) while (pq[i].state != 0) pthread_cond_wait(&pcv, &pmu);
    pthread_mutex_unlock(&pmu);
}

static double now(void) { struct timeval t; gettimeofday(&t, NULL); return t.tv_sec + t.tv_usec / 1e6; }

/* ---------------------------------------------------------------- --tcp */
#ifndef TCP_KEEPIDLE
#define TCP_KEEPIDLE TCP_KEEPALIVE   /* macOS name for the idle time before keepalive probes */
#endif
static int ctl_fd = -1;   /* the ssh session's stdin, kept as a lifeline */

static void *ctl_watch(void *arg) {   /* host or ssh gone: stop (a half-open TCP stream would hang) */
    (void)arg;
    char b[64];
    while (read(ctl_fd, b, sizeof b) > 0) {}
    _exit(3);
}

static void tcp_setup(void) {
    char tok[65] = {0};
    for (int n = 0; n < 65; ) {                 /* token line, unbuffered: stdin is reattached below */
        ssize_t r = read(0, tok + n, 1);
        if (r <= 0) die("--tcp: no token on stdin");
        if (tok[n] == '\n') { tok[n] = 0; break; }
        if (++n == 65) die("--tcp: token too long");
    }
    if (strlen(tok) != 64) die("--tcp: bad token");
    int ls = socket(AF_INET6, SOCK_STREAM, 0), zero = 0, one = 1;
    if (ls < 0) die("socket: %s", strerror(errno));
    setsockopt(ls, IPPROTO_IPV6, IPV6_V6ONLY, &zero, sizeof zero);   /* IPv4 and IPv6 */
    struct sockaddr_in6 a = { .sin6_family = AF_INET6 };
    socklen_t al = sizeof a;
    if (bind(ls, (struct sockaddr *)&a, sizeof a) || listen(ls, 4) || getsockname(ls, (struct sockaddr *)&a, &al))
        die("listen: %s", strerror(errno));
    printf("LISTEN %u\n", ntohs(a.sin6_port)); fflush(stdout);
    double until = now() + 15;
    int s = -1;
    while (s < 0) {
        int left = (int)((until - now()) * 1000);
        struct pollfd pf = { .fd = ls, .events = POLLIN };
        if (left <= 0 || poll(&pf, 1, left) <= 0) die("--tcp: no connection with the token within 15 s");
        int c = accept(ls, NULL, NULL);
        if (c < 0) continue;
        struct timeval tv = { 3, 0 };
        setsockopt(c, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        char got[64]; size_t n = 0;
        while (n < 64) { ssize_t r = read(c, got + n, 64 - n); if (r <= 0) break; n += (size_t)r; }
        if (n == 64 && !memcmp(got, tok, 64)) s = c; else close(c);   /* wrong peer: keep waiting */
    }
    close(ls);
    struct timeval tv0 = { 0, 0 };
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv0, sizeof tv0);
    int buf = 4 << 20, idle = 10, intvl = 5, cnt = 3;
    setsockopt(s, SOL_SOCKET, SO_RCVBUF, &buf, sizeof buf);
    setsockopt(s, SOL_SOCKET, SO_SNDBUF, &buf, sizeof buf);
    setsockopt(s, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);       /* short replies go out at once */
    setsockopt(s, SOL_SOCKET, SO_KEEPALIVE, &one, sizeof one);       /* dead peer noticed in ~25 s */
    setsockopt(s, IPPROTO_TCP, TCP_KEEPIDLE, &idle, sizeof idle);
    setsockopt(s, IPPROTO_TCP, TCP_KEEPINTVL, &intvl, sizeof intvl);
    setsockopt(s, IPPROTO_TCP, TCP_KEEPCNT, &cnt, sizeof cnt);
    ctl_fd = dup(0);
    if (ctl_fd < 0 || dup2(s, 0) < 0 || dup2(s, 1) < 0) die("dup: %s", strerror(errno));
    close(s);
    pthread_t t;
    pthread_create(&t, NULL, ctl_watch, NULL);
}

/* ---------------------------------------------------------------- main */

int main(int argc, char **argv) {
    signal(SIGPIPE, SIG_IGN);
    if (argc < 2) { fprintf(stderr, "usage: %s <target> [--workers N] [--tcp]\n", argv[0]); return 2; }
    int tcp = 0;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--workers") && i + 1 < argc) workers = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--tcp")) tcp = 1;
    }
    if (workers < 1) workers = 1;
    if (tcp) tcp_setup();
    static char inbuf[1 << 20];
    setvbuf(stdin, inbuf, _IOFBF, sizeof inbuf);

    fd = open(argv[1], O_RDWR);
    if (fd < 0) die("open %s: %s", argv[1], strerror(errno));
#ifdef __APPLE__
    /* no system sleep while this agent writes the disk (recoveryOS on battery sleeps after 20 min, the display
       after 5); held until the process exits, released by the kernel then */
    IOPMAssertionID noidle = 0, nosleep = 0;
    IOPMAssertionCreateWithName(kIOPMAssertionTypePreventUserIdleSystemSleep, kIOPMAssertionLevelOn,
                                CFSTR("ssdpush-agent"), &noidle);
    IOPMAssertionCreateWithName(kIOPMAssertionTypePreventSystemSleep, kIOPMAssertionLevelOn,
                                CFSTR("ssdpush-agent"), &nosleep);
#endif
    struct stat st; fstat(fd, &st);
    uint32_t bsize = 512;
    int is_dev = S_ISCHR(st.st_mode) || S_ISBLK(st.st_mode);
#ifdef __APPLE__
    if (is_dev) {
        uint64_t count = 0;
        if (ioctl(fd, DKIOCGETBLOCKSIZE, &bsize) || ioctl(fd, DKIOCGETBLOCKCOUNT, &count)) die("ioctl: %s", strerror(errno));
        target_size = count * bsize;
    } else {
        target_size = (uint64_t)st.st_size;
        fcntl(fd, F_NOCACHE, 1);   /* read-back must come from storage, not the page cache */
    }
    vfd = wfd = fd;                /* raw char devices are uncached already */
#else
    if (is_dev) {
        int ss = 512;
        if (ioctl(fd, BLKGETSIZE64, &target_size) || ioctl(fd, BLKSSZGET, &ss)) die("ioctl: %s", strerror(errno));
        bsize = (uint32_t)ss;
    } else {
        target_size = (uint64_t)st.st_size;
    }
    vfd = open(argv[1], O_RDONLY | O_DIRECT);   /* read-back bypasses the page cache */
    if (vfd < 0) die("open %s (O_DIRECT): %s", argv[1], strerror(errno));
    /* writes bypass it too: no copy into the page cache, and no forced flush when the read-back of a
     * just-written range is uncached (stage buffers are 4096-aligned, lengths/offsets sector multiples) */
    wfd = is_dev ? open(argv[1], O_RDWR | O_DIRECT) : fd;
    if (wfd < 0) die("open %s (O_DIRECT, write): %s", argv[1], strerror(errno));
#endif

    char magic[8]; rd(magic, 8);
    if (memcmp(magic, "SSDPUSH1", 8)) die("bad handshake");
    image_size = rd64(); chunk_size = rd32(); uint32_t flags = rd32();
    verify = flags & 1;
    trim = (flags & 2) && is_dev;
    if (!chunk_size || chunk_size > (1u << 24)) die("bad chunk size %u", chunk_size);
    if (target_size < image_size) die("target is %llu bytes, image needs %llu", (unsigned long long)target_size, (unsigned long long)image_size);
    zeros = calloc(1, chunk_size > COALESCE ? chunk_size : COALESCE);
    in_cap = chunk_size + (64u << 10);   /* a zstd frame of one chunk is at most a little over chunk_size */

    for (int i = 0; i < NSTAGE; i++)
        if (posix_memalign((void **)&stg[i].buf, 4096, COALESCE)) die("out of memory");
    pthread_t t;
    for (int i = 0; i < workers; i++) pthread_create(&t, NULL, worker, NULL);
    pthread_create(&t, NULL, writer, NULL);
    pthread_create(&t, NULL, verifier, NULL);
    printf("READY size=%llu bsize=%u workers=%d verify=%d trim=%d\n", (unsigned long long)target_size, bsize, workers, verify, trim);
    fflush(stdout);

    double t0 = now();
    for (;;) {
        int c = fgetc(stdin);
        if (c == EOF) die("input closed without exit frame");
        if (c == 'D' || c == 'Z' || c == 'R') {
            p_flush();                          /* never reorder a write before an earlier read */
            job *j = calloc(1, sizeof *j);
            j->type = (char)c; j->off = rd64(); j->wlen = rd32();
            if (c == 'D') {
                rd(j->hash, 20); j->clen = rd32();
                if (j->clen > (1u << 26)) die("frame too large");
                j->in = in_get(j->clen); rd(j->in, j->clen);
                if (j->wlen > chunk_size) die("write length %u > chunk size", j->wlen);
            } else if (c == 'R') {
                if (j->wlen > COALESCE) die("raw frame too large");
                j->in = malloc(j->wlen ? j->wlen : 1); rd(j->in, j->wlen);
            } else if (j->wlen > chunk_size) die("zero length %u > chunk size", j->wlen);
            if (j->off % bsize || j->wlen % bsize) die("unaligned write at %llu len %u", (unsigned long long)j->off, j->wlen);
            submit(j);
        } else if (c == 'Q') {
            drain(); p_flush();
            uint32_t n = rd32();
            qent *q = malloc((size_t)n * sizeof *q + 1);
            for (uint32_t i = 0; i < n; i++) {
                q[i].off = rd64(); q[i].len = rd32(); rd(q[i].want, 20);
                if (q[i].len > chunk_size) die("query length too large");
            }
            uint32_t m = query(q, n);
            uint8_t b4[4] = { m, m >> 8, m >> 16, m >> 24 };
            fwrite(b4, 1, 4, stdout);
            for (uint32_t i = 0; i < n; i++)
                if (q[i].bad) { uint8_t x[4] = { i, i >> 8, i >> 16, i >> 24 }; fwrite(x, 1, 4, stdout); }
            fflush(stdout);
            free(q);
        } else if (c == 'P') {
            drain();
            uint64_t off = rd64(); uint32_t len = rd32();
            if (len > COALESCE || off + len > target_size) die("bad read request %llu+%u", (unsigned long long)off, len);
            p_submit(off, len);
        } else if (c == 'F') {
            drain(); p_flush();
            if (fsync(wfd) && errno != ENOTSUP && errno != EINVAL) die("fsync: %s", strerror(errno));
#ifdef __APPLE__
            if (is_dev) ioctl(fd, DKIOCSYNCHRONIZECACHE);
#endif
            double dt = now() - t0;
            struct rusage ru; getrusage(RUSAGE_SELF, &ru);
            printf("OK bytes=%llu chunks=%llu zero=%llu trimmed=%llu raw=%llu seconds=%.1f cpu_user=%.1f cpu_sys=%.1f minflt=%ld\n",
                   (unsigned long long)bytes_written, (unsigned long long)chunks_written, (unsigned long long)zero_chunks,
                   (unsigned long long)bytes_trimmed,
                   (unsigned long long)raw_frames, dt, ru.ru_utime.tv_sec + ru.ru_utime.tv_usec / 1e6,
                   ru.ru_stime.tv_sec + ru.ru_stime.tv_usec / 1e6, ru.ru_minflt);
            fflush(stdout);
        } else if (c == 'X') {
            drain(); p_flush();
            if (wfd != fd) close(wfd);
            close(fd);
            return 0;
        } else {
            die("unknown frame type 0x%02x", c);
        }
    }
}
