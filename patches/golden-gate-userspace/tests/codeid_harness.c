/* Runs the code identity carrier as src/codeid_carrier.py links it, against this Mac's own x86_64 shared cache
 * (under Rosetta), without changing the cache: the linker places the carrier in memory this process maps near the
 * cache instead of in the cache's free tail, and the hooks are called directly instead of through the entry jumps.
 * Everything else is the real thing: the relocated prologues jump back into Apple's Security bodies, calls go
 * through Security's GOT and its exports.
 *
 *   codeid_harness LIST_DIR CASES     prints "UNSLID_TEXT UNSLID_DATA" and reads one line from stdin:
 *                                     "TEXT_FILE DATA_FILE HOOK_CODE HOOK_STATIC USE_DIR" (unslid hex addresses),
 *                                     then runs each line of CASES: "static PATH REQUIREMENT" or "running PATH
 *                                     REQUIREMENT" (spawns PATH as x86_64, names it by audit token as CoreWiFi and
 *                                     authd do), printing "original=N carried=N" for each. */
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <mach-o/dyld.h>
#include <mach/mach.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
extern const void *_dyld_get_shared_cache_range(size_t *length);
OSStatus SecCodeCreateWithAuditToken(const audit_token_t *audit, SecCSFlags flags, SecCodeRef *target);
typedef OSStatus (*check_fn)(SecStaticCodeRef, SecCSFlags, SecRequirementRef, CFErrorRef *);

static void *load(const char *path, size_t *size)
{
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    static char buf[1 << 16];
    *size = fread(buf, 1, sizeof buf, f);
    fclose(f);
    return buf;
}

static int run_case(char *line, check_fn code_hook, check_fn static_hook)
{
    char kind[16], path[1024], req[1024];
    if (sscanf(line, "%15s %1023s %1023[^\n]", kind, path, req) != 3) return 2;
    CFStringRef text = CFStringCreateWithCString(NULL, req, kCFStringEncodingUTF8);
    SecRequirementRef requirement = NULL;
    if (SecRequirementCreateWithString(text, 0, &requirement)) return 2;
    OSStatus original = 1, carried = 1;
    if (!strcmp(kind, "static")) {
        CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL, (const UInt8 *)path, (CFIndex)strlen(path), false);
        SecStaticCodeRef code = NULL;
        if (SecStaticCodeCreateWithPath(url, 0, &code)) return 2;
        original = SecStaticCodeCheckValidityWithErrors(code, 0, requirement, NULL);
        CFErrorRef error = NULL;
        carried = static_hook(code, 0, requirement, &error);
        if (!carried && error) carried = 3;      /* success must clear errors */
        if (error) CFRelease(error);
        CFRelease(code); CFRelease(url);
    } else {
        int in[2], out[2];
        if (pipe(in) || pipe(out)) return 2;
        posix_spawn_file_actions_t actions;
        posix_spawn_file_actions_init(&actions);
        posix_spawn_file_actions_adddup2(&actions, in[0], 0);
        posix_spawn_file_actions_adddup2(&actions, out[1], 1);
        posix_spawn_file_actions_addclose(&actions, in[1]);
        posix_spawn_file_actions_addclose(&actions, out[0]);
        posix_spawnattr_t attrs;
        posix_spawnattr_init(&attrs);
        cpu_type_t preferred = CPU_TYPE_X86_64;
        size_t used = 0;
        posix_spawnattr_setbinpref_np(&attrs, 1, &preferred, &used);
        pid_t pid;
        char *args[] = {path, NULL};
        if (posix_spawn(&pid, path, &actions, &attrs, args, environ)) return 2;
        close(in[0]); close(out[1]);
        char ready;
        mach_port_t name = MACH_PORT_NULL;
        audit_token_t token;
        mach_msg_type_number_t count = TASK_AUDIT_TOKEN_COUNT;
        SecCodeRef code = NULL;
        if (read(out[0], &ready, 1) == 1 && !task_name_for_pid(mach_task_self(), pid, &name)
            && !task_info(name, TASK_AUDIT_TOKEN, (task_info_t)&token, &count)
            && !SecCodeCreateWithAuditToken(&token, 0, &code)) {
            original = SecCodeCheckValidityWithErrors(code, 0, requirement, NULL);
            carried = code_hook((SecStaticCodeRef)code, 0, requirement, NULL);
            CFRelease(code);
        }
        close(in[1]);
        int status;
        if (waitpid(pid, &status, 0) != pid) kill(pid, SIGKILL);
    }
    printf("original=%d carried=%d\n", (int)original, (int)carried);
    fflush(stdout);
    CFRelease(requirement); CFRelease(text);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    size_t length;
    const uint8_t *cache = _dyld_get_shared_cache_range(&length);
    if (!cache) return 2;
    uint64_t unslid = *(const uint64_t *)(cache + *(const uint32_t *)(cache + 16));
    uint64_t slide = (uint64_t)cache - unslid;
    /* one region near the cache: text in the first half, data in the second */
    uint8_t *region = mmap((void *)(cache - 0x10000000), 0x20000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (region == MAP_FAILED) return 2;
    long long distance = (long long)(region - cache);
    if (distance < -0x40000000LL || distance > 0x40000000LL) { fprintf(stderr, "region too far\n"); return 2; }
    printf("%llx %llx\n", (unsigned long long)((uint64_t)region - slide), (unsigned long long)((uint64_t)region + 0x10000 - slide));
    fflush(stdout);
    char text_file[1024], data_file[1024];
    unsigned long long hook_code, hook_static, use_dir;
    if (scanf("%1023s %1023s %llx %llx %llx", text_file, data_file, &hook_code, &hook_static, &use_dir) != 5) return 2;
    size_t size;
    void *bytes = load(text_file, &size);
    if (!bytes || size > 0x10000) return 2;
    memcpy(region, bytes, size);
    bytes = load(data_file, &size);
    if (!bytes || size > 0x10000) return 2;
    memcpy(region + 0x10000, bytes, size);
    if (mprotect(region, 0x10000, PROT_READ | PROT_EXEC)) return 2;
    ((void (*)(const char *))(use_dir + slide))(argv[1]);
    FILE *cases = fopen(argv[2], "r");
    if (!cases) return 2;
    char line[4096];
    while (fgets(line, sizeof line, cases))
        if (run_case(line, (check_fn)(hook_code + slide), (check_fn)(hook_static + slide))) return 2;
    return 0;
}
