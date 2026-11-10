"""Static register-only SSE thunks, from the decoder's explicit list; never executable user bytes."""
from pathlib import Path
import re


def operations():
    text = (Path(__file__).parent/'src/sse_ops.def').read_text()
    return [(name, *(int(v, 0) for v in values.split(',')))
            for name, values in re.findall(r'^SSE_OP\((\w+), (.*)\)$', text, re.M)]


def assembly():
    lines = ['.text', '.globl _IntelCommpage_sse_execute', '_IntelCommpage_sse_execute:',
             # SysV: RDI=thunk, RSI=SseWork. Thunks use only XMM1 and flags; no GPRs.
             'pushq %rbp', 'movq %rsp, %rbp', 'andq $-16, %rsp', 'subq $512, %rsp',
             '#ifdef KERNEL', 'movq %cr0, %r11', 'clts', '#endif',
             'fxsave64 (%rsp)', 'ldmxcsr 48(%rsi)',
             'movdqu 32(%rsi), %xmm0', 'movdqu (%rsi), %xmm1', 'movdqu 16(%rsi), %xmm2',
             'callq *%rdi', 'movdqu %xmm1, (%rsi)', 'stmxcsr 48(%rsi)', 'fxrstor64 (%rsp)',
             '#ifdef KERNEL', 'movq %r11, %cr0', '#endif', 'movq %rbp, %rsp', 'popq %rbp', 'retq']
    for name, prefix, map_, op, imm, kind in operations():
        lines += ['.p2align 4', f'L_sse_{name}:']
        for value in range(256 if imm else 1):
            # Copies use integer code; MOVNTDQA has no register-source encoding.
            code = ([] if kind else ([0x66] if prefix else [])+[0x0f]+([map_] if map_ else [])+
                    [op, 0xca]+([value] if imm else []))
            lines += ['.byte '+','.join(hex(b) for b in code+[0xc3]), '.p2align 4, 0x90']
    lines += ['.section __DATA,__const', '.p2align 3', '.globl _IntelCommpage_sse_table', '_IntelCommpage_sse_table:']
    lines += [f'.quad L_sse_{op[0]}' for op in operations()]
    return '\n'.join(lines)+'\n'
