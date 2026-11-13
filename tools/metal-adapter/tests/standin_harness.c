// Host test driver (native, no Apple binary): the stand-in's request rewriting, without any compiler loaded.
//   standin_harness req7 TEMPLATE REQ1 OUT      the type-7 downgrade request the stand-in sends for REQ1
//   standin_harness splice REQ MODULE OUT        REQ with its module replaced (what goes to 3802 after the downgrade)
//   standin_harness req13 REQ13 OUT              the type-13 request after option translation and module-cache move
//                                                (HAO_FE_MODULE_CACHE from the environment)
//   standin_harness reply27 REPLY OUT            a 3802 type-1 reply with its reflection in 27's layout (reflection27)
//   standin_harness template X OUT               the built-in template (installed build, HAO_TEMPLATE_INC), else exit 3
// Exit 0 and OUT written, or exit 3 when the stand-in would not rewrite (layout refused / no change).
#include "../src/standin.c"

static int put(const char *p, const void *d, size_t n) {
  FILE *f = fopen(p, "wb"); if (!f) return 2;
  fwrite(d, 1, n, f); fclose(f); return 0;
}

int main(int argc, char **argv) {
  if (argc < 4) { fprintf(stderr, "usage: see source\n"); return 2; }
  size_t n1, n2, on = 0;
  uint8_t *a = readfile(argv[2], &n1), *o = NULL;
  if (!a) return 2;
  if (!strcmp(argv[1], "req7") && argc == 5) {
    uint8_t *r = readfile(argv[3], &n2); if (!r) return 2;
    o = req7_for(a, n1, r, &on);
  } else if (!strcmp(argv[1], "splice") && argc == 5) {
    uint8_t *m = readfile(argv[3], &n2); if (!m) return 2;
    o = resplice(a, n1, NULL, 0, m, (uint32_t)n2, &on);
  } else if (!strcmp(argv[1], "reply27") && argc == 4) {
    o = reflection27(0, a, n1, &on);
  } else if (!strcmp(argv[1], "req13") && argc == 4) {
    int mv; o = translate13(0, a, n1, &on, &mv); (void)mv;
  } else if (!strcmp(argv[1], "template") && argc == 4) {
#ifdef HAO_TEMPLATE_INC
    o = (uint8_t *)kTemplate; on = sizeof kTemplate;
#endif
  } else return 2;
  if (!o) return 3;
  return put(argv[argc - 1], o, on);
}
