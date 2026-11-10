/* hmeta: apply Apple's metadata list to a tree whose CONTENT is already in place (hybrid route, guest root).
 * Never creates or deletes a file. Per entry: lchown, lchmod, remove com.apple.provenance, set listed xattrs
 * (com.apple.fs.firmlink skipped: only the APFS fsctl may set it), set mtime (ns), then chflags LAST
 * (FLG minus SF_FIRMLINK; UF_COMPRESSED is kept as found on disk, never added or removed).
 *
 * usage: hmeta apply ROOT LIST     LIST lines: path \t typ \t uid \t gid \t mode(octal) \t flags(hex) \t sec \t nsec \t xattrs
 *                                  path relative to ROOT ("." = ROOT), typ F/D/L, sec "-" = no mtime,
 *                                  xattrs "-" or name=hexvalue[,name=hexvalue...]
 *        hmeta own ROOT UID GID    walk ROOT: lchown every entry, remove com.apple.provenance (Preboot/Recovery)
 * Prints counters and the first 50 failures per kind; exit 0 when no syscall failed.
 * Build: xcrun clang -O2 -arch x86_64 -arch arm64 -o hmeta hmeta.c   (ad-hoc sign: codesign -s - -f hmeta)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <fts.h>
#include <time.h>
#include <sys/stat.h>
#include <sys/xattr.h>
#include <sys/attr.h>

#ifndef SF_FIRMLINK
#define SF_FIRMLINK 0x00800000
#endif
#define PROV "com.apple.provenance"

static long n_lines, n_missing, n_type, n_chown, n_chmod, n_xset, n_prov, n_mtime, n_flags, n_hl, n_cmpmis, n_fail;
static long n_extra_x, n_prov_stuck;
static int shown[8];

static void fail(int k, const char *what, const char *p) {
  n_fail++;
  if (shown[k]++ < 50) fprintf(stderr, "FAIL %s %s: %s\n", what, p, strerror(errno));
}

static int hexval(int c) { return c <= '9' ? c - '0' : (c | 32) - 'a' + 10; }

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }

static void do_xattrs(const char *p, char *xs) {
  static char val[1 << 16];
  if (!strcmp(xs, "-")) return;
  for (char *tok = strtok(xs, ","); tok; tok = strtok(NULL, ",")) {
    char *eq = strchr(tok, '='); if (!eq) continue; *eq = 0; char *h = eq + 1;
    size_t n = strlen(h) / 2; if (n > sizeof val) { errno = E2BIG; fail(3, "xattr-size", p); continue; }
    for (size_t i = 0; i < n; i++) val[i] = (char)(hexval(h[2 * i]) << 4 | hexval(h[2 * i + 1]));
    if (!strcmp(tok, "com.apple.fs.firmlink")) continue;
    if (setxattr(p, tok, val, n, 0, XATTR_NOFOLLOW) != 0) fail(3, "setxattr", p); else n_xset++;
  }
}

static int apply(const char *root, const char *list) {
  FILE *f = fopen(list, "r"); if (!f) { perror(list); return 2; }
  size_t cap = 0; char *line = NULL; ssize_t len; char p[4096];
  double t0 = now(), tl = t0;
  while ((len = getline(&line, &cap, f)) > 0) {
    if (line[len - 1] == '\n') line[--len] = 0;
    char *fld[9]; int nf = 0;
    for (char *s = line, *q; nf < 9; s = q + 1) { fld[nf++] = s; q = strchr(s, '\t'); if (!q) break; *q = 0; }
    if (nf < 9) { fprintf(stderr, "bad line %ld\n", n_lines + 1); continue; }
    n_lines++;
    if (!strcmp(fld[0], ".")) snprintf(p, sizeof p, "%s", root); else snprintf(p, sizeof p, "%s/%s", root, fld[0]);
    struct stat st;
    if (lstat(p, &st) != 0) { n_missing++; if (shown[0]++ < 50) fprintf(stderr, "MISSING %s\n", p); continue; }
    char typ = fld[1][0];
    if (typ == 'H') { n_hl++; continue; }  /* hard-link entry: metadata comes with its primary */
    int ok = (typ == 'F' && S_ISREG(st.st_mode)) || (typ == 'D' && S_ISDIR(st.st_mode)) || (typ == 'L' && S_ISLNK(st.st_mode));
    if (!ok) { n_type++; if (shown[1]++ < 50) fprintf(stderr, "TYPE %s want %c mode %o\n", p, typ, st.st_mode); continue; }
    uid_t u = (uid_t)strtoul(fld[2], 0, 10); gid_t g = (gid_t)strtoul(fld[3], 0, 10);
    mode_t m = (mode_t)strtoul(fld[4], 0, 8); unsigned long fl = strtoul(fld[5], 0, 16);
    if (st.st_uid != u || st.st_gid != g) { if (lchown(p, u, g) != 0) fail(2, "lchown", p); else n_chown++; }
    /* chown may clear set-id bits: always re-apply a mode with set-id/sticky bits */
    if ((st.st_mode & 07777) != m || (m & 07000)) {
      if (fchmodat(AT_FDCWD, p, m, AT_SYMLINK_NOFOLLOW) != 0) fail(2, "lchmod", p); else n_chmod++;
    }
    if (removexattr(p, PROV, XATTR_NOFOLLOW) == 0) { n_prov++; if (getxattr(p, PROV, NULL, 0, 0, XATTR_NOFOLLOW) >= 0) n_prov_stuck++; }
    else if (errno != ENOATTR) fail(3, "rm-provenance", p);
    do_xattrs(p, fld[8]);
    if (strcmp(fld[6], "-")) {
      struct attrlist al; memset(&al, 0, sizeof al); al.bitmapcount = ATTR_BIT_MAP_COUNT; al.commonattr = ATTR_CMN_MODTIME;
      struct timespec ts = { (time_t)strtoll(fld[6], 0, 10), strtol(fld[7], 0, 10) };
      if (setattrlist(p, &al, &ts, sizeof ts, FSOPT_NOFOLLOW) != 0) fail(4, "mtime", p); else n_mtime++;
    }
    unsigned long want = (fl & ~(unsigned long)SF_FIRMLINK & ~(unsigned long)UF_COMPRESSED) | (st.st_flags & UF_COMPRESSED);
    if ((fl & UF_COMPRESSED) != (st.st_flags & UF_COMPRESSED)) n_cmpmis++;
    if (want != st.st_flags) { if (lchflags(p, (unsigned int)want) != 0) fail(5, "lchflags", p); else n_flags++; }
    if (n_lines % 50000 == 0) { double t = now(); fprintf(stderr, "progress %ld lines %.1f s (%.0f/s last chunk)\n", n_lines, t - t0, 50000 / (t - tl)); tl = t; }
  }
  double dt = now() - t0;
  printf("apply %s %s: lines %ld in %.1f s (%.0f entries/s)\n", root, list, n_lines, dt, n_lines / (dt > 0 ? dt : 1));
  printf("  missing %ld type-mismatch %ld hardlink-skip %ld | chown %ld chmod %ld provenance-removed %ld (still-present %ld) xattr-set %ld mtime %ld chflags %ld | compressed-bit-differs %ld | failures %ld\n",
         n_missing, n_type, n_hl, n_chown, n_chmod, n_prov, n_prov_stuck, n_xset, n_mtime, n_flags, n_cmpmis, n_fail);
  fclose(f); return n_fail || n_missing || n_type ? 1 : 0;
}

static int own(const char *root, uid_t u, gid_t g) {
  char *paths[] = { (char *)root, NULL }; double t0 = now();
  FTS *ft = fts_open(paths, FTS_PHYSICAL | FTS_NOCHDIR | FTS_XDEV, NULL); if (!ft) { perror("fts"); return 2; }
  FTSENT *e;
  while ((e = fts_read(ft))) {
    if (e->fts_info == FTS_DP) continue;
    n_lines++;
    if (lchown(e->fts_path, u, g) != 0) fail(2, "lchown", e->fts_path); else n_chown++;
    if (removexattr(e->fts_path, PROV, XATTR_NOFOLLOW) == 0) { n_prov++; if (getxattr(e->fts_path, PROV, NULL, 0, 0, XATTR_NOFOLLOW) >= 0) n_prov_stuck++; } else if (errno != ENOATTR) fail(3, "rm-provenance", e->fts_path);
    char names[4096]; ssize_t n = listxattr(e->fts_path, names, sizeof names, XATTR_NOFOLLOW);
    for (ssize_t i = 0; n > 0 && i < n; i += strlen(names + i) + 1) { n_extra_x++; if (shown[6]++ < 50) printf("  xattr %s %s\n", e->fts_path, names + i); }
  }
  fts_close(ft);
  printf("own %s %u:%u: entries %ld chown %ld provenance-removed %ld (still-present %ld) other-xattrs %ld failures %ld in %.1f s\n",
         root, u, g, n_lines, n_chown, n_prov, n_prov_stuck, n_extra_x, n_fail, now() - t0);
  return n_fail ? 1 : 0;
}

int main(int argc, char **argv) {
  setvbuf(stdout, NULL, _IOLBF, 0);
  if (argc == 4 && !strcmp(argv[1], "apply")) return apply(argv[2], argv[3]);
  if (argc == 5 && !strcmp(argv[1], "own")) return own(argv[2], (uid_t)atoi(argv[3]), (gid_t)atoi(argv[4]));
  fprintf(stderr, "usage: hmeta apply ROOT LIST | hmeta own ROOT UID GID\n"); return 2;
}
