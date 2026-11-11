/* Test guest: a native slice beside a preserved Apple slice. Reports readiness, then waits for EOF. */
#include <unistd.h>
#ifndef VARIANT
#define VARIANT "a"
#endif
int main(void) {
    char c;
    if (write(1, VARIANT, 1) != 1) return 1;
    while (read(0, &c, 1) > 0) {}
    return 0;
}
