/* Register bootstrap aliases immediately before zone metadata relocation
 * removes its old mapping. The zone allocator can expand at this point;
 * pmap_init is too early to allocate a hashed PV entry. Bootstrap still runs
 * on one CPU. Preserve the original pmap_remove_options arguments and return.
 */
pv_alias_start:
    pushq %rdi
    pushq %rsi
    pushq %rdx
    pushq %rcx
    pushq %r8
    pushq %r9
    pushq %rbx
    pushq %rbp
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15
    subq $8, %rsp
    .byte 0x4c, 0x8d, 0x35
    .long kernel_pmap - (origin + . - pv_alias_start + 4)
    movabsq $0xffffff8000000000, %rbx
    .byte 0x4c, 0x8b, 0x25
    .long avail_start - (origin + . - pv_alias_start + 4)
    addq %rbx, %r12
    andq $-4096, %r12
    xorl %r15d, %r15d
Lpv_scan:
    cmpq %r12, %rbx
    jae Lpv_range_done
    movq %r14, %rdi
    movq %rbx, %rsi
    .byte 0xe8
    .long pmap_find_pa - (origin + . - pv_alias_start + 4)
    shrq $12, %rax
    testl %eax, %eax
    jz Lpv_small_step
    .byte 0x3b, 0x05
    .long last_managed_page - (origin + . - pv_alias_start + 4)
    ja Lpv_small_step
    .byte 0x48, 0x8b, 0x15
    .long phys_attributes - (origin + . - pv_alias_start + 4)
    testb $1, (%rdx,%rax)
    jz Lpv_small_step
    shlq $5, %rax
    .byte 0x48, 0x8b, 0x15
    .long pv_head_table - (origin + . - pv_alias_start + 4)
    addq %rax, %rdx
    callq Lpv_register
    movq %r14, %rdi
    movq %rbx, %rsi
    .byte 0xe8
    .long pmap_query_pagesize - (origin + . - pv_alias_start + 4)
    cmpl $0x200000, %eax
    jne Lpv_small_step
    addq $0x200000, %rbx
    jmp Lpv_scan
Lpv_small_step:
    addq $4096, %rbx
    jmp Lpv_scan
Lpv_range_done:
    testl %r15d, %r15d
    jnz Lpv_finished
    incl %r15d
    .byte 0x48, 0x8b, 0x1d
    .long virtual_avail - (origin + . - pv_alias_start + 4)
    addq $4095, %rbx
    andq $-4096, %rbx
    .byte 0x4c, 0x8b, 0x25
    .long vm_pages_end - (origin + . - pv_alias_start + 4)
    andq $-4096, %r12
    jmp Lpv_scan
Lpv_finished:
    addq $8, %rsp
    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %rbp
    popq %rbx
    popq %r9
    popq %r8
    popq %rcx
    popq %rdx
    popq %rsi
    popq %rdi
    .byte 0xe9
    .long pmap_remove_options - (origin + . - pv_alias_start + 4)

/* RDX=root, RBX=VA, R14=pmap, RAX=physical page*32. */
Lpv_register:
    cmpq $0, 24(%rdx)
    je Lpv_new_root
    movq %rdx, %rcx
Lpv_existing:
    cmpq %r14, 24(%rcx)
    jne Lpv_next_existing
    movq 16(%rcx), %r8
    andq $-4096, %r8
    cmpq %rbx, %r8
    je Lpv_done
Lpv_next_existing:
    movq (%rcx), %rcx
    cmpq %rdx, %rcx
    jne Lpv_existing
Lpv_add_alias:
    pushq %r12
    pushq %r13
    subq $8, %rsp
    movq %rdx, %r12
    movq %rax, %r13
    shrq $5, %r13
    .byte 0x48, 0x8b, 0x3d
    .long pv_zone - (origin + . - pv_alias_start + 4)
    movl $0x8004, %esi /* Z_ZERO | Z_NOFAIL */
    .byte 0xe8
    .long zalloc_flags - (origin + . - pv_alias_start + 4)
    movq %rbx, 16(%rax)
    movq %r14, 24(%rax)
    movl %r13d, 32(%rax)
    /* Insert after the existing rooted entry, as pv_hash_add does. */
    movq (%r12), %rcx
    movq %rcx, (%rax)
    movq %r12, 8(%rax)
    movq %rax, 8(%rcx)
    movq %rax, (%r12)
    movq %rbx, %rdx
    shrq $12, %rdx
    xorl %r14d, %edx
    .byte 0x23, 0x15
    .long pv_hash_mask - (origin + . - pv_alias_start + 4)
    .byte 0x48, 0x8b, 0x0d
    .long pv_hash_table - (origin + . - pv_alias_start + 4)
    leaq (%rcx,%rdx,8), %rdx
    movq (%rdx), %rcx
    movq %rcx, 40(%rax)
    movq %rax, (%rdx)
    addq $8, %rsp
    popq %r13
    popq %r12
Lpv_done:
    retq
Lpv_new_root:
    movq %rbx, 16(%rdx)
    movq %r14, 24(%rdx)
    movq %rdx, (%rdx)
    movq %rdx, 8(%rdx)
    retq
