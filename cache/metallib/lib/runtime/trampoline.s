// Pinned 3802 entry: six register arguments and six stack argument slots.
// Copy stack arguments, preserve register arguments, substitute the verified
// module clone, then preserve return registers while disposing that clone.
.globl _compat_tramp
.p2align 4
_compat_tramp:
    .cfi_startproc
    pushq %rbp
    .cfi_def_cfa_offset 16
    .cfi_offset %rbp, -16
    movq %rsp, %rbp
    .cfi_def_cfa_register %rbp
    subq $320, %rsp
    movq %rdi, 48(%rsp)
    movq %rsi, 56(%rsp)
    movq %rdx, 64(%rsp)
    movq %rcx, 72(%rsp)
    movq %r8, 80(%rsp)
    movq %r9, 88(%rsp)
    movq %rax, 96(%rsp)
    movdqu %xmm0, 112(%rsp)
    movdqu %xmm1, 128(%rsp)
    movdqu %xmm2, 144(%rsp)
    movdqu %xmm3, 160(%rsp)
    movdqu %xmm4, 176(%rsp)
    movdqu %xmm5, 192(%rsp)
    movdqu %xmm6, 208(%rsp)
    movdqu %xmm7, 224(%rsp)
    movq %r8, %rdi
    callq _compat_prepare
    movq %rax, 240(%rsp)
    movq 16(%rbp), %rax
    movq %rax, 0(%rsp)
    movq 24(%rbp), %rax
    movq %rax, 8(%rsp)
    movq 32(%rbp), %rax
    movq %rax, 16(%rsp)
    movq 40(%rbp), %rax
    movq %rax, 24(%rsp)
    movq 48(%rbp), %rax
    movq %rax, 32(%rsp)
    movq 56(%rbp), %rax
    movq %rax, 40(%rsp)
    movdqu 112(%rsp), %xmm0
    movdqu 128(%rsp), %xmm1
    movdqu 144(%rsp), %xmm2
    movdqu 160(%rsp), %xmm3
    movdqu 176(%rsp), %xmm4
    movdqu 192(%rsp), %xmm5
    movdqu 208(%rsp), %xmm6
    movdqu 224(%rsp), %xmm7
    movq 48(%rsp), %rdi
    movq 56(%rsp), %rsi
    movq 64(%rsp), %rdx
    movq 72(%rsp), %rcx
    movq 240(%rsp), %r8
    movq 88(%rsp), %r9
    movq 96(%rsp), %rax
    callq *_compat_real_entry(%rip)
    movq %rax, 248(%rsp)
    movq %rdx, 256(%rsp)
    movdqu %xmm0, 272(%rsp)
    movdqu %xmm1, 288(%rsp)
    movq 80(%rsp), %rdi
    movq 240(%rsp), %rsi
    callq _compat_finish
    movq 248(%rsp), %rax
    movq 256(%rsp), %rdx
    movdqu 272(%rsp), %xmm0
    movdqu 288(%rsp), %xmm1
    leave
    .cfi_def_cfa %rsp, 8
    retq
    .cfi_endproc
