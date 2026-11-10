/* Offline native x86 replay of the retained decoder, not a driver or live patch.
 * Arguments: extracted 26A428 MultitouchSupport, captured V3 packet, [--pipeline].
 * The Python patch recipe validates UUID/instructions before creating patched
 * input. The internal-diagnostics predicate returns false in both runs.
 * --pipeline exercises the dispatcher and header callbacks, counting entry to
 * contact/image processing instead of running those algorithms. It supplies
 * libc bzero and omits timestamp diagnostics. No Apple framework is loaded.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

static unsigned paths, images;
static void path_frame(void *d, void *f, uint64_t n, uint64_t t) { ++paths; }
static void image_frame(void *d, void *f, void *p, int n, uint64_t s, uint64_t t) { ++images; }
static void clear(void *p, size_t n) { memset(p, 0, n); }
static void bind(unsigned char *code, size_t at, size_t slot, void *fn) {
    /* Six-byte RIP-indirect jump fits the original import stub. */
    int32_t delta = (int32_t)(slot - (at + 6));
    code[at] = 0xff; code[at+1] = 0x25;
    memcpy(code+at+2, &delta, 4); memcpy(code+slot, &fn, sizeof(fn));
}

static unsigned char *file(const char *path, size_t *length) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(2); }
    fseek(f, 0, SEEK_END); *length = (size_t)ftell(f); rewind(f);
    unsigned char *b = malloc(*length);
    if (!b || fread(b, 1, *length, f) != *length) exit(2);
    fclose(f); return b;
}
int main(int argc, char **argv) {
    if (argc != 3 && argc != 4) return 2;
    bool pipeline = argc == 4 && !strcmp(argv[3], "--pipeline");
    size_t size, length;
    unsigned char *bytes = file(argv[1], &size), *packet = file(argv[2], &length);
    if (size < 0x42a43 || length > 4096) return 2;
    size_t mapped = size + 4096;
    unsigned char *code = mmap(NULL, mapped, PROT_READ|PROT_WRITE,
                              MAP_PRIVATE|MAP_ANON, -1, 0);
    if (code == MAP_FAILED) { perror("mmap"); return 2; }
    memcpy(code, bytes, size);
    memcpy(code + 0x42a40, "\x31\xc0\xc3", 3); /* xor eax,eax; ret */
    if (pipeline) {
        bind(code, 0x42872, size, clear);
        bind(code, 0xa4ad, size+8, path_frame);
        bind(code, 0xa503, size+16, image_frame);
        code[0xc768] = 0xc3; /* timestamp diagnostics, outside the routing test */
    }
    if (mprotect(code, mapped, PROT_READ|PROT_EXEC)) { perror("mprotect"); return 2; }
    unsigned char device[0x2000] = {0}, parsed[0x1000] = {0}, image[4096] = {0};
    uint32_t endian = 1, capacity = sizeof(image);
    void *image_pointer = image;
    memcpy(device + 0x64, &endian, sizeof(endian));
    memcpy(parsed + 0x28, &image_pointer, sizeof(image_pointer));
    memcpy(parsed + 0x30, &capacity, sizeof(capacity));
    bool (*parse)(void *, int, void *, void *) = (void *)(code + 0xf539);
    bool ok;
    if (pipeline) {
        void *frame_pointer = parsed, *alg_pointer = image;
        memcpy(device+0x188, &frame_pointer, sizeof(frame_pointer));
        memcpy(device+0x98, &alg_pointer, sizeof(alg_pointer));
        device[0x81c] = 2; /* request the legacy header in the callback buffer */
        void (*process)(void *, void *, int) = (void *)(code + 0xcd1e);
        process(device, packet, (int)length);
        printf("path_calls=%u image_calls=%u legacy_header=%02x contacts=%u\n",
               paths, images, device[0x91c], device[0x92c]);
        ok = paths != 0 || images != 0;
    } else {
        ok = parse(packet, (int)length, parsed, device);
    }
    printf("accepted=%d contacts=%u sequence=%u\n", ok, parsed[0x48], parsed[0x10]);
    for (unsigned i=0; ok && i<parsed[0x48] && i<32; ++i) {
        unsigned char *p = parsed + 0x74 + i*30;
        int16_t x, y; memcpy(&x,p+4,2); memcpy(&y,p+6,2);
        printf("contact=%u id=%u state=%u x=%d y=%d raw=",i,p[0],p[2],x,y);
        for (unsigned j=0;j<30;++j) printf("%02x",p[j]);
        puts("");
    }
    munmap(code,mapped); free(bytes); free(packet);
    return 0;
}
