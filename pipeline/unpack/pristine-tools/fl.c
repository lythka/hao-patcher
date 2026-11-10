/* fl: get/set/clear APFS firmlinks via fsctl APFSIOC_FIRMLINK_CTL (0xC4084A3C),
 * same call/struct as Apple's UpdateBrainLibrary (ParallelPatchFirmlinkFiles / firmlinkEnd).
 * struct (0x408 bytes): char target[1024]; uint32_t op; uint32_t flags;
 *   get:   op=1, flags=0      (brain: "Failed to read firm link information")
 *   clear: op=0, flags=0, empty target (brain: "is a firmlink. Clearing firmlink.")
 *   set:   op=0, flags=2, target = path relative to Data root (brain: firmlinkEnd)
 * fsctl options = 1 (FSOPT_NOFOLLOW), as in the brain.
 * usage: fl get PATH... | fl set PATH TARGET | fl clear PATH | fl setlist ROOT FILE (lines "/src<TAB>target")
 */
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>
int fsctl(const char *, unsigned long, void *, unsigned int);
#define APFSIOC_FIRMLINK_CTL 0xC4084A3CUL
struct fctl { char target[1024]; uint32_t op; uint32_t flags; };
static int doit(const char *p, uint32_t op, uint32_t fl, const char *tgt) {
  struct fctl c; memset(&c, 0, sizeof c); c.op = op; c.flags = fl;
  if (tgt) strlcpy(c.target, tgt, sizeof c.target);
  int r = fsctl(p, APFSIOC_FIRMLINK_CTL, &c, 1);
  int e = errno;
  if (op == 1) printf("get %s rc=%d errno=%d(%s) target='%s' op=%u flags=%u\n", p, r, r?e:0, r?strerror(e):"", c.target, c.op, c.flags);
  else printf("%s %s -> '%s' rc=%d errno=%d(%s)\n", tgt&&*tgt?"set":"clear", p, tgt?tgt:"", r, r?e:0, r?strerror(e):"");
  return r;
}
int main(int argc, char **argv) {
  if (argc < 3) { fprintf(stderr, "usage\n"); return 2; }
  int bad = 0;
  if (!strcmp(argv[1], "get")) { for (int i = 2; i < argc; i++) bad |= doit(argv[i], 1, 0, NULL) != 0; }
  else if (!strcmp(argv[1], "set") && argc == 4) bad = doit(argv[2], 0, 2, argv[3]) != 0;
  else if (!strcmp(argv[1], "setf") && argc == 6) bad = doit(argv[2], strtoul(argv[4],0,0), strtoul(argv[5],0,0), argv[3]) != 0;
  else if (!strcmp(argv[1], "clear")) bad = doit(argv[2], 0, 0, "") != 0;
  else if (!strcmp(argv[1], "setlist") && argc == 4) {
    FILE *f = fopen(argv[3], "r"); char line[2200];
    if (!f) { perror("open"); return 2; }
    while (fgets(line, sizeof line, f)) {
      line[strcspn(line, "\n")] = 0; char *t = strchr(line, '\t'); if (!t) continue; *t++ = 0;
      char p[2100]; snprintf(p, sizeof p, "%s%s", argv[2], line);
      bad |= doit(p, 0, 2, t) != 0;
    }
  } else { fprintf(stderr, "usage\n"); return 2; }
  return bad;
}
