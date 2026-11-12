# argbuf_tramp.s: one pre-hook for every draw/dispatch selector, whatever its signature.
# Saves the integer and vector argument registers, calls agb_pre(self, _cmd) (which binds the argument-buffer
# resources and returns the original IMP for _cmd), restores the registers and tail-jumps to that IMP with the
# caller's stack untouched (by-value MTLSize/MTLRegion arguments stay where they are).
    .text
    .globl _agb_tramp
    .p2align 4
_agb_tramp:
    pushq   %rbp
    movq    %rsp, %rbp
    pushq   %rdi
    pushq   %rsi
    pushq   %rdx
    pushq   %rcx
    pushq   %r8
    pushq   %r9
    pushq   %rax
    subq    $136, %rsp
    movdqu  %xmm0, 0(%rsp)
    movdqu  %xmm1, 16(%rsp)
    movdqu  %xmm2, 32(%rsp)
    movdqu  %xmm3, 48(%rsp)
    movdqu  %xmm4, 64(%rsp)
    movdqu  %xmm5, 80(%rsp)
    movdqu  %xmm6, 96(%rsp)
    movdqu  %xmm7, 112(%rsp)
    callq   _agb_pre
    movq    %rax, %r11
    movdqu  0(%rsp), %xmm0
    movdqu  16(%rsp), %xmm1
    movdqu  32(%rsp), %xmm2
    movdqu  48(%rsp), %xmm3
    movdqu  64(%rsp), %xmm4
    movdqu  80(%rsp), %xmm5
    movdqu  96(%rsp), %xmm6
    movdqu  112(%rsp), %xmm7
    addq    $136, %rsp
    popq    %rax
    popq    %r9
    popq    %r8
    popq    %rcx
    popq    %rdx
    popq    %rsi
    popq    %rdi
    popq    %rbp
    jmpq    *%r11
