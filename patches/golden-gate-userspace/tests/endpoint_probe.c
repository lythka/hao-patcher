/* QEMU-only test: prove selector adaptation with real ES events and API calls.
 * --adapt edits this test process's private cache pages; it installs nothing.
 * selector_edits.inc is generated from src/endpoint_security.py by the runner.
 */
#include <EndpointSecurity/EndpointSecurity.h>
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <mach/mach.h>
#include <mach-o/loader.h>
#include <spawn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
static struct { unsigned offset, before, after; } edits[]={
#include "selector_edits.inc"
};
static void adapt(void) {
    Dl_info info={0};
    if(!dladdr((void *)es_new_client,&info)) exit(10);
    unsigned char *base=info.dli_fbase;
    const struct mach_header_64 *h=(void *)base;
    const unsigned char expected[]={0xf6,0x17,0x55,0xb5,0x75,0x85,0x32,0xfd,0xbe,0x8f,0x36,0x6c,0x1e,0xae,0x01,0x2c};
    const struct load_command *lc=(void *)(base+sizeof(*h)); int found=0;
    for(unsigned i=0;i<h->ncmds;i++,lc=(void *)((char *)lc+lc->cmdsize))
        if(lc->cmd==LC_UUID) found=!memcmp(((const struct uuid_command *)lc)->uuid,expected,16);
    if(!found) {fprintf(stderr,"unsupported ES library UUID\n");exit(11);}
    for(unsigned i=0;i<sizeof(edits)/sizeof(*edits);i++) {
        unsigned char *p=base+edits[i].offset;unsigned n;memcpy(&n,p+1,4);
        if(p[0]!=0xbe||n!=edits[i].before) exit(12);
    }
    for(unsigned i=0;i<sizeof(edits)/sizeof(*edits);i++) {
        unsigned char *p=base+edits[i].offset;
        vm_address_t page=(vm_address_t)p&~(vm_page_size-1);
        kern_return_t kr=vm_protect(mach_task_self(),page,vm_page_size,0,VM_PROT_READ|VM_PROT_WRITE|VM_PROT_COPY);
        if(kr) {fprintf(stderr,"vm_protect %d\n",kr);exit(13);}
        memcpy(p+1,&edits[i].after,4);
        if(vm_protect(mach_task_self(),page,vm_page_size,0,VM_PROT_READ|VM_PROT_EXECUTE))exit(14);
    }
    puts("adapted=31");
}
int main(int argc,char **argv) {
    alarm(45);setbuf(stdout,NULL);
    if(argc==2&&!strcmp(argv[1],"--adapt"))adapt();
    dispatch_semaphore_t event=dispatch_semaphore_create(0);
    es_client_t *client=NULL;
    es_new_client_result_t r=es_new_client(&client,^(es_client_t *c,const es_message_t *m){
        (void)c;
        if(m->event_type==ES_EVENT_TYPE_NOTIFY_EXEC) {
            printf("event=EXEC version=%u pid=%u\n",m->version,m->process->audit_token.val[5]);
            dispatch_semaphore_signal(event);
        }
    });
    printf("new_client=%d\n",r);if(r!=ES_NEW_CLIENT_RESULT_SUCCESS)return 1;
    es_event_type_t wanted=ES_EVENT_TYPE_NOTIFY_EXEC;
    es_return_t subscribed=es_subscribe(client,&wanted,1);
    printf("subscribe=%d\n",subscribed);if(subscribed!=ES_RETURN_SUCCESS)return 2;
    es_event_type_t *list=NULL;size_t count=0;
    es_return_t subscriptions=es_subscriptions(client,&count,&list);
    printf("subscriptions=%d count=%zu\n",subscriptions,count);free(list);
    if(subscriptions!=ES_RETURN_SUCCESS||count!=1)return 3;
    audit_token_t self={0};mach_msg_type_number_t token_count=TASK_AUDIT_TOKEN_COUNT;
    if(task_info(mach_task_self(),TASK_AUDIT_TOKEN,(task_info_t)&self,&token_count))return 6;
    es_return_t muted=es_mute_process_events(client,&self,&wanted,1);
    es_muted_processes_t *processes=NULL;
    es_return_t queried=es_muted_processes_events(client,&processes);
    size_t process_count=processes?processes->count:0;
    if(processes)es_release_muted_processes(processes);
    es_return_t unmuted=es_unmute_process_events(client,&self,&wanted,1);
    printf("process_mute=%d query=%d count=%zu unmute=%d\n",muted,queried,process_count,unmuted);
    if(muted||queried||!process_count||unmuted)return 7;
    const char *path="/private/var/tmp/boot-fixes/not-executed";
    muted=es_mute_path_events(client,path,ES_MUTE_PATH_TYPE_LITERAL,&wanted,1);
    es_muted_paths_t *paths=NULL;
    queried=es_muted_paths_events(client,&paths);
    size_t path_count=paths?paths->count:0;
    if(paths)es_release_muted_paths(paths);
    unmuted=es_unmute_path_events(client,path,ES_MUTE_PATH_TYPE_LITERAL,&wanted,1);
    printf("path_mute=%d query=%d count=%zu unmute=%d\n",muted,queried,path_count,unmuted);
    if(muted||queried||!path_count||unmuted)return 8;
    pid_t pid;char *args[]={"/usr/bin/true",NULL};
    if(posix_spawn(&pid,args[0],NULL,NULL,args,environ))return 4;
    int status;waitpid(pid,&status,0);
    if(dispatch_semaphore_wait(event,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC)))return 5;
    es_clear_cache_result_t cleared=es_clear_cache(client);
    es_return_t unsubscribed=es_unsubscribe_all(client);
    es_return_t deleted=es_delete_client(client);
    printf("clear=%d unsubscribe=%d delete=%d child=%d\n",cleared,unsubscribed,deleted,status);
    return cleared!=ES_CLEAR_CACHE_RESULT_SUCCESS||unsubscribed!=ES_RETURN_SUCCESS||deleted!=ES_RETURN_SUCCESS||status;
}
