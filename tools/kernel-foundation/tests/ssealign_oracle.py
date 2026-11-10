"""Build a guest differential test for every supported SSE opcode and every immediate.

Run with Python 3.14 --out NEW_DIRECTORY, then run ssealign_oracle in the QEMU guest.
Uses independently assembled mnemonics for aligned and misaligned memory operations.
"""
import argparse
import runpy
import subprocess
from pathlib import Path
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', type=Path, required=True)
out=parser.parse_args().out
out.mkdir(parents=True, exist_ok=True)
ops=runpy.run_path(str(Path(__file__).resolve().parents[1]/'sse_codegen.py'))['operations']()
lines=['.text']; names=[]
for name,p,m,o,imm,kind in ops:
 mnemonic=name.replace('mova_load','mova').replace('mova_store','mova').replace('movnt_store','movnt').replace('_load','').replace('_store','')
 for value in range(256 if imm else 1):
  label=f'probe_{len(names)}';names.append(f'{mnemonic}:{value}')
  arg='%xmm1, (%rdi)' if kind==2 else ('${}, '.format(value) if imm else '')+'(%rdi), %xmm1'
  lines += ['.p2align 4',f'_{label}:','ldmxcsr 48(%rsi)','movdqu (%rsi), %xmm1','movdqu 32(%rsi), %xmm0',mnemonic+' '+arg,'sfence','movdqu %xmm1, (%rsi)','stmxcsr 48(%rsi)','retq']
lines+=['.section __DATA,__const','.p2align 3','.globl _probes','_probes:']+[f'.quad _probe_{i}' for i in range(len(names))]
(out/'oracle.S').write_text('\n'.join(lines)+'\n')
c=r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <immintrin.h>
extern void (*probes[])(void*,void*);
static unsigned char aligned[64] __attribute__((aligned(64))), unaligned[64] __attribute__((aligned(64)));
static unsigned char a[64] __attribute__((aligned(64))), b[64] __attribute__((aligned(64)));
static const char *names[]={NAMES};
int main(void) {
 unsigned masks[]={0x1f80,0x3fc1,0xdfc2};
 for(unsigned m=0;m<3;m++) for(unsigned i=0;i<COUNT;i++) {
  for(unsigned j=0;j<64;j++){aligned[j]=(unsigned char)(j*37+i*13);a[j]=(unsigned char)(j*71+i*23);}
  memcpy(unaligned+8,aligned,16);memcpy(a+48,&masks[m],4);memcpy(b,a,64);
  probes[i](aligned,a);probes[i](unaligned+8,b);_mm_setcsr(0x1f80);
  if(memcmp(a,b,52)||memcmp(aligned,unaligned+8,16)) {printf("FAIL %s csr=%x\n",names[i],masks[m]);return 1;}
 }
 printf("PASS %u opcode/immediate forms x 3 MXCSR modes, aligned vs misaligned\n",COUNT);return 0;
}
'''.replace('COUNT',str(len(names))).replace('NAMES',','.join('"'+s+'"' for s in names))
(out/'oracle.c').write_text(c)
subprocess.run(['xcrun','clang','-arch','x86_64','-O2',str(out/'oracle.c'),str(out/'oracle.S'),'-o',str(out/'ssealign_oracle')],check=True)
subprocess.run(['codesign','-s','-',str(out/'ssealign_oracle')],check=True)
print(len(names),'opcode/immediate probes compiled')
