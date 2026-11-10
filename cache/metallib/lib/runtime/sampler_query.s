.globl _compat_query_getregister
.p2align 4
_compat_query_getregister:
    movq (%rsp), %rax
    cmpq _compat_address_return(%rip), %rax
    jne 1f
    movq %rbx, %r8
    movl -0x2c(%rbp), %r9d
    jmp _compat_register_impl
1:
    jmpq *_compat_real_getregister(%rip)
