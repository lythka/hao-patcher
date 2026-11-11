extern int marker(void) __attribute__((weak_import));
static int replacement(void) {return 42;}
__attribute__((used,section("__DATA,__interpose"))) static struct {const void *r,*o;} pair={replacement,marker};
