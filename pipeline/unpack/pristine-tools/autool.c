/* autool: small root-side helpers for the arm64-unpack run (guest recoveryOS has no python/perl).
 *   autool links ROOT LINKS.TXT        hard links from payloadv2 links.txt ("=src" then "+dst" lines), like unpack/links.py
 *   autool check ROOT LIST             READ-ONLY: compare ROOT with an hmeta list (mklist.py format); prints one line per
 *                                      difference "KIND path want got" and a summary. KIND: missing type owner mode
 *                                      flags firmlinkflag xattr-missing xattr-value xattr-extra mtime
 *   autool walk ROOT                   READ-ONLY dump: path typ uid gid mode flags mtime.ns nlink size xattrs(name=hex|name=#len:fnv)
 * Build: xcrun clang -O2 -arch arm64 -arch x86_64 -o autool autool.c ; codesign -s - -f autool */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fts.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#ifndef SF_FIRMLINK
#define SF_FIRMLINK 0x00800000
#endif
static int hexval(int c) { return c <= '9' ? c - '0' : (c | 32) - 'a' + 10; }
static void mkparents(char *p) { for (char *s = p + 1; *s; s++) if (*s == '/') { *s = 0; mkdir(p, 0755); *s = '/'; } }

static int links(const char *root, const char *lf) {
  FILE *f = fopen(lf, "r"); if (!f) { perror(lf); return 2; }
  char *line = NULL; size_t cap = 0; ssize_t n; char src[4096] = "", s[4096], d[4096]; long made = 0, exist = 0, miss = 0, err = 0;
  while ((n = getline(&line, &cap, f)) > 0) {
    if (line[n - 1] == '\n') line[--n] = 0; if (!n) continue;
    if (line[0] == '=') { snprintf(src, sizeof src, "%s", line + 1); continue; }
    snprintf(s, sizeof s, "%s/%s", root, src); snprintf(d, sizeof d, "%s/%s", root, line + 1);
    struct stat st;
    if (lstat(s, &st)) { miss++; fprintf(stderr, "MISSING SRC %s\n", src); continue; }
    if (!lstat(d, &st)) { exist++; continue; }
    mkparents(d);
    if (linkat(AT_FDCWD, s, AT_FDCWD, d, 0)) { err++; if (err < 50) fprintf(stderr, "LINK-FAIL %s: %s\n", d, strerror(errno)); } else made++;
  }
  printf("links made=%ld already_exist=%ld missing_src=%ld failed=%ld\n", made, exist, miss, err); return err || miss;
}

static unsigned long fnv(const unsigned char *b, ssize_t n) { unsigned long h = 1469598103934665603UL; for (ssize_t i = 0; i < n; i++) { h ^= b[i]; h *= 1099511628211UL; } return h; }
static long cnt[16]; static const char *KN[] = {"missing","type","owner","mode","flags","firmlinkflag","xattr-missing","xattr-value","xattr-extra","mtime","provenance","hardlink-rows","lines"};
static void diff(int k, const char *p, const char *fmt, ...) __attribute__((format(printf,3,4)));
#include <stdarg.h>
static void diff(int k, const char *p, const char *fmt, ...) { cnt[k]++; printf("%s\t%s\t", KN[k], p); va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap); putchar('\n'); }

static int check(const char *root, const char *list) {
  FILE *f = fopen(list, "r"); if (!f) { perror(list); return 2; }
  char *line = NULL; size_t cap = 0; ssize_t len; char p[4096]; static unsigned char val[1 << 16], val2[1 << 16]; static char names[1 << 16];
  while ((len = getline(&line, &cap, f)) > 0) {
    if (line[len - 1] == '\n') line[--len] = 0;
    char *fld[9]; int nf = 0;
    for (char *s = line, *q; nf < 9; s = q + 1) { fld[nf++] = s; q = strchr(s, '\t'); if (!q) break; *q = 0; }
    if (nf < 9) continue; cnt[12]++;
    if (!strcmp(fld[0], ".")) snprintf(p, sizeof p, "%s", root); else snprintf(p, sizeof p, "%s/%s", root, fld[0]);
    struct stat st; if (lstat(p, &st)) { diff(0, fld[0], "-"); continue; }
    char t = fld[1][0]; if (t == 'H') { cnt[11]++; continue; }
    int ok = (t == 'F' && S_ISREG(st.st_mode)) || (t == 'D' && S_ISDIR(st.st_mode)) || (t == 'L' && S_ISLNK(st.st_mode));
    if (!ok) { diff(1, fld[0], "%c %o", t, st.st_mode); continue; }
    unsigned u = strtoul(fld[2], 0, 10), g = strtoul(fld[3], 0, 10), m = strtoul(fld[4], 0, 8); unsigned long fl = strtoul(fld[5], 0, 16);
    if (st.st_uid != u || st.st_gid != g) diff(2, fld[0], "%u:%u %u:%u", u, g, st.st_uid, st.st_gid);
    if ((st.st_mode & 07777) != m) diff(3, fld[0], "%o %o", m, st.st_mode & 07777);
    unsigned long w = fl & ~(unsigned long)(SF_FIRMLINK | UF_COMPRESSED), h = st.st_flags & ~(unsigned long)(SF_FIRMLINK | UF_COMPRESSED);
    if (w != h) diff(4, fld[0], "%lx %x", fl, st.st_flags);
    if ((fl & SF_FIRMLINK) != (st.st_flags & SF_FIRMLINK)) diff(5, fld[0], "%lx %x", fl & SF_FIRMLINK, st.st_flags & SF_FIRMLINK);
    if (strcmp(fld[6], "-")) { long s = strtol(fld[6], 0, 10), ns = strtol(fld[7], 0, 10);
      if (st.st_mtimespec.tv_sec != s || st.st_mtimespec.tv_nsec != ns) diff(9, fld[0], "%ld.%09ld %ld.%09ld", s, ns, (long)st.st_mtimespec.tv_sec, st.st_mtimespec.tv_nsec); }
    /* xattrs: listed must be present and equal; anything else present is extra */
    char want[8192] = ","; if (strcmp(fld[8], "-")) {
      char *xs = strdup(fld[8]);
      for (char *tok = strtok(xs, ","); tok; tok = strtok(NULL, ",")) {
        char *eq = strchr(tok, '='); if (!eq) continue; *eq = 0; char *hx = eq + 1; size_t n = strlen(hx) / 2;
        strlcat(want, tok, sizeof want); strlcat(want, ",", sizeof want);
        if (!strcmp(tok, "com.apple.fs.firmlink")) continue;   /* hidden from getxattr: read with fl get */
        for (size_t i = 0; i < n; i++) val[i] = (unsigned char)(hexval(hx[2 * i]) << 4 | hexval(hx[2 * i + 1]));
        ssize_t r = getxattr(p, tok, val2, sizeof val2, 0, XATTR_NOFOLLOW);
        if (r < 0) diff(6, fld[0], "%s -", tok); else if ((size_t)r != n || memcmp(val, val2, n)) diff(7, fld[0], "%s len%zu len%zd", tok, n, r);
      }
      free(xs);
    }
    ssize_t nl = listxattr(p, names, sizeof names, XATTR_NOFOLLOW);
    for (ssize_t i = 0; nl > 0 && i < nl; i += strlen(names + i) + 1) {
      char k[1100]; snprintf(k, sizeof k, ",%s,", names + i);
      if (!strcmp(names + i, "com.apple.provenance")) { diff(10, fld[0], "present"); continue; }
      if (!strstr(want, k)) diff(8, fld[0], "%s", names + i);
    }
  }
  fprintf(stderr, "check %s %s:", root, list); for (int k = 0; k < 13; k++) fprintf(stderr, " %s=%ld", KN[k], cnt[k]); fprintf(stderr, "\n");
  long tot = 0; for (int k = 0; k < 11; k++) tot += cnt[k]; return tot ? 1 : 0;
}

static int walk(const char *root) {
  char *paths[] = { (char *)root, NULL }; FTS *ft = fts_open(paths, FTS_PHYSICAL | FTS_NOCHDIR | FTS_XDEV, NULL); if (!ft) { perror("fts"); return 2; }
  FTSENT *e; size_t rl = strlen(root); static char names[1 << 16]; static unsigned char v[1 << 16]; long n = 0, unread = 0;
  while ((e = fts_read(ft))) {
    if (e->fts_info == FTS_DP) continue;
    if (e->fts_info == FTS_DNR || e->fts_info == FTS_ERR || e->fts_info == FTS_NS) { unread++; fprintf(stderr, "UNREADABLE %s %s\n", e->fts_path, strerror(e->fts_errno)); if (e->fts_info == FTS_NS) continue; }
    struct stat *st = e->fts_statp; const char *rel = e->fts_path[rl] ? e->fts_path + rl + 1 : ".";
    char t = S_ISDIR(st->st_mode) ? 'D' : S_ISREG(st->st_mode) ? 'F' : S_ISLNK(st->st_mode) ? 'L' : '?';
    printf("%s\t%c\t%u\t%u\t%o\t%x\t%ld.%09ld\t%u\t%lld\t", rel, t, st->st_uid, st->st_gid, st->st_mode & 07777, st->st_flags,
           (long)st->st_mtimespec.tv_sec, st->st_mtimespec.tv_nsec, st->st_nlink, (long long)(t == 'F' ? st->st_size : 0));
    ssize_t nl = listxattr(e->fts_path, names, sizeof names, XATTR_NOFOLLOW); int first = 1;
    for (ssize_t i = 0; nl > 0 && i < nl; i += strlen(names + i) + 1) {
      ssize_t r = getxattr(e->fts_path, names + i, v, sizeof v, 0, XATTR_NOFOLLOW);
      printf("%s%s=", first ? "" : ",", names + i); first = 0;
      if (r >= 0 && r <= 64) for (ssize_t j = 0; j < r; j++) printf("%02x", v[j]); else printf("#%zd:%016lx", r, r > 0 ? fnv(v, r) : 0);
    }
    printf("%s\n", first ? "-" : ""); n++;
  }
  fts_close(ft); fprintf(stderr, "walk %s: %ld entries, %ld unreadable\n", root, n, unread); return 0;
}
int main(int c, char **v) {
  setvbuf(stdout, NULL, _IOFBF, 1 << 20);
  if (c == 4 && !strcmp(v[1], "links")) return links(v[2], v[3]);
  if (c == 4 && !strcmp(v[1], "check")) return check(v[2], v[3]);
  if (c == 3 && !strcmp(v[1], "walk")) return walk(v[2]);
  fprintf(stderr, "usage: autool links ROOT LINKS | check ROOT LIST | walk ROOT\n"); return 2;
}
