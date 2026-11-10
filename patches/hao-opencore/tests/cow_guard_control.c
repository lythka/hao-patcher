/* Native VM negative control; run only in the test guest. */
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/vm_statistics.h>
#include <mach/memory_object_types.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    alarm(15);
    setvbuf(stdout, NULL, _IONBF, 0);
    const char *op = argc > 1 ? argv[1] : "entry";
    const int tagged = argc > 2 ? atoi(argv[2]) : 1;
    const mach_vm_size_t size = 16384;
    mach_vm_address_t source = 0, dest = 0;
    int flags = VM_FLAGS_ANYWHERE | (tagged ? VM_MAKE_TAG(VM_MEMORY_MALLOC_SMALL) : 0);
    kern_return_t kr = mach_vm_map(mach_task_self(), &source, size, 0, flags,
        MACH_PORT_NULL, 0, FALSE, VM_PROT_READ | VM_PROT_WRITE,
        VM_PROT_READ | VM_PROT_WRITE, VM_INHERIT_DEFAULT);
    printf("map tagged=%d result=%d source=0x%llx size=%llu\n", tagged, kr, source, size);
    if (kr) return 1;
    memset((void *)source, 0xa5, size);
    if (!strcmp(op, "entry")) {
        memory_object_size_t entry_size = size;
        mach_port_t entry = MACH_PORT_NULL;
        printf("calling mach_make_memory_entry_64 MAP_MEM_VM_COPY\n");
        kr = mach_make_memory_entry_64(mach_task_self(), &entry_size, source,
            VM_PROT_READ | MAP_MEM_VM_COPY, &entry, MACH_PORT_NULL);
        printf("entry result=%d size=%llu port=%u\n", kr, entry_size, entry);
        if (entry != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), entry);
    } else if (!strcmp(op, "copy")) {
        kr = mach_vm_allocate(mach_task_self(), &dest, size, VM_FLAGS_ANYWHERE);
        if (!kr) {
            printf("calling mach_vm_copy\n");
            kr = mach_vm_copy(mach_task_self(), source, size, dest);
            printf("copy result=%d equal=%d\n", kr, !kr && !memcmp((void *)source, (void *)dest, size));
            if (!kr && memcmp((void *)source, (void *)dest, size)) kr = KERN_FAILURE;
            mach_vm_deallocate(mach_task_self(), dest, size);
        }
    } else { fprintf(stderr, "usage: %s entry|copy [tagged=1]\n", argv[0]); kr = KERN_INVALID_ARGUMENT; }
    mach_vm_deallocate(mach_task_self(), source, size);
    return kr ? 1 : 0;
}
