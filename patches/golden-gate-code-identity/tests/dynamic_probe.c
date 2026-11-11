/* Runs GUEST (native slice), then asks Security, as CoreWiFi's CWFIsCodesignedByApple does, whether the running
 * guest named by its audit token satisfies REQUIREMENT. Prints the OSStatus. */
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <mach/mach.h>
#include <mach/machine.h>
#include <spawn.h>
#include <stdio.h>
#include <string.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
/* Security SPI (SecCodePriv.h) that CoreWiFi calls. */
OSStatus SecCodeCreateWithAuditToken(const audit_token_t *audit, SecCSFlags flags, SecCodeRef *target);

int main(int argc, char **argv) {
    if (argc != 3) return 2;
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
    char *args[] = {argv[1], NULL};
    if (posix_spawn(&pid, argv[1], &actions, &attrs, args, environ)) return 2;
    close(in[0]); close(out[1]);
    char ready;
    OSStatus status = errSecCSInternalError;
    mach_port_t name = MACH_PORT_NULL;
    audit_token_t token;
    mach_msg_type_number_t count = TASK_AUDIT_TOKEN_COUNT;
    if (read(out[0], &ready, 1) == 1 && !task_name_for_pid(mach_task_self(), pid, &name)
        && !task_info(name, TASK_AUDIT_TOKEN, (task_info_t)&token, &count)) {
        CFDataRef data = CFDataCreate(NULL, (const UInt8 *)&token, sizeof token);
        CFStringRef text = CFStringCreateWithCString(NULL, argv[2], kCFStringEncodingUTF8);
        SecRequirementRef requirement = NULL;
        SecCodeRef code = NULL;
        CFErrorRef error = NULL;
        status = SecRequirementCreateWithString(text, 0, &requirement);
        if (!status) status = SecCodeCreateWithAuditToken((const audit_token_t *)CFDataGetBytePtr(data), 0, &code);
        if (!status) status = SecCodeCheckValidityWithErrors(code, 0, requirement, &error);
        if (!status && error) status = errSecCSInternalError;
        if (error) CFRelease(error);
        if (code) CFRelease(code);
        if (requirement) CFRelease(requirement);
        CFRelease(text); CFRelease(data);
    }
    close(in[1]);
    int exit_status;
    if (waitpid(pid, &exit_status, 0) != pid) kill(pid, SIGKILL);
    printf("%d\n", (int)status);
    return status ? 1 : 0;
}
