// Process-local Live Text settings, set before the functional helpers' constructors (they depend on this image).
// No launchd job, user preference or per-user cache is created: Espresso keeps its ordinary cache unless a
// diagnostic run supplies HAO_LIVE_TEXT_E5_CACHE itself. Shaders compile in the image's MTLCompilerService.
#include <stdlib.h>
#include <pthread.h>
#ifndef LIVE_TEXT_ROOT
#error LIVE_TEXT_ROOT must name the installed runtime folder
#endif
static void configure_once(void) {
    setenv("HAO_LIVE_TEXT_ROOT", LIVE_TEXT_ROOT, 1);
    setenv("VISION_MPSNDARRAY_LIBRARY", LIVE_TEXT_ROOT "/MPSNDArray.metallib", 1);
}
void hao_live_text_configure(void) {
    static pthread_once_t once = PTHREAD_ONCE_INIT;
    pthread_once(&once, configure_once);
}
__attribute__((constructor)) static void configure(void) { hao_live_text_configure(); }
