"""Execute the generated LLVM bodies with a checked active-lane model."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import collective_runtime


class CollectiveSemantics(unittest.TestCase):
    def test_actual_ir_with_sparse_masks_prefixes_and_quads(self):
        with tempfile.TemporaryDirectory(prefix='collective-semantics-') as directory:
            work = Path(directory)
            objects, declarations, checks = [], [], []
            for name, source in collective_runtime.sources().items():
                source = source.replace('llvm.igil.active.channel.mask', 'test_mask')
                source = source.replace('llvm.igil.get.simd.id', 'test_lane')
                source = source.replace('llvm.igil.simd.shuffle', 'test_shuffle')
                source = source.replace('llvm.igil.minf', 'fminf').replace('llvm.igil.maxf', 'fmaxf')
                source = source.replace(' readnone', '')
                path = work / (name + '.ll')
                path.write_text(source)
                obj = path.with_suffix('.o')
                subprocess.run(['xcrun', 'clang', '-O2', '-Wno-override-module', '-c',
                                str(path), '-o', str(obj)], check=True, capture_output=True)
                objects.append(str(obj))
                if name == 'air_simd_is_first':
                    declarations.append(f'extern bool {name}(void);')
                    checks.append(f'assert({name}() == ((active & ((1ULL << lane)-1)) == 0)); ++checks;')
                    continue
                if name == 'air_simd_ballot_i64':
                    declarations.append(f'extern uint64_t {name}(bool);')
                    checks.append('''{
  components=1; component=0;
  uint64_t expected=0;
  for(unsigned s=0;s<width;s++) {
    bits[s][0]=((s*3+iteration)%9)&1;
    if((active & (1ULL<<s)) && bits[s][0]) expected |= 1ULL<<s;
  }
  assert(air_simd_ballot_i64(bits[lane][0])==expected); ++checks;
}''')
                    continue
                match = re.search(r'define (.*?) @', source)
                ty = match[1]
                count = int(ty[1]) if ty.startswith('<') else 1
                scalar = ty.split(' x ')[1][:-1] if count > 1 else ty
                cty = {'float': 'float', 'half': '_Float16', 'i32': 'uint32_t',
                       'i16': 'uint16_t', 'i8': 'uint8_t', 'i1': 'bool'}[scalar]
                alias = name + '_type'
                declarations.append(f'typedef {cty} {alias}' + (f' __attribute__((ext_vector_type({count})))' if count > 1 else '') + ';')
                declarations.append(f'extern {alias} {name}({alias});')
                op = next(o for o in ('sum', 'min', 'max', 'or', 'any', 'all') if '_'+o in name)
                init = '1' if op == 'all' else f'values[lane][c]' if op in ('min', 'max') else '0'
                condition = '(active & (1ULL << s))'
                if '_quad_' in name:
                    condition += ' && s/4 == lane/4'
                if '_prefix_exclusive_' in name:
                    condition += ' && s < lane'
                if '_prefix_inclusive_' in name:
                    condition += ' && s <= lane'
                expr = {'sum': 'expected + values[s][c]', 'or': '(uint32_t)expected | (uint32_t)values[s][c]',
                        'any': 'expected || values[s][c]', 'all': 'expected && values[s][c]',
                        'min': 'fminf(expected, values[s][c])', 'max': 'fmaxf(expected, values[s][c])'}[op]
                arg = 'input[c]' if count > 1 else 'input'
                output = 'output[c]' if count > 1 else 'output'
                checks.append(f'''{{
  components={count}; component=0;
  {alias} input={{0}};
  for(unsigned s=0;s<width;s++) for(unsigned c=0;c<components;c++) {{
    {cty} value=({cty})((s*3+c*7+iteration)%9);
    values[s][c]=(double)value; bits[s][c]=0;
    memcpy(&bits[s][c], &value, sizeof(value));
    if(s==lane) {arg}=value;
  }}
  {alias} output={name}(input);
  for(unsigned c=0;c<components;c++) {{
    {cty} expected=({cty})({init});
    for(unsigned s=0;s<width;s++) if({condition}) expected=({cty})({expr});
    if({output}!=expected) {{ fprintf(stderr,"{name} width=%u lane=%u mask=%llx channel=%u got=%g expected=%g\\n",width,lane,active,c,(double){output},(double)expected); abort(); }}
    ++checks;
  }}
}}''')
            source = '''#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
static unsigned lane,width,components,component,iteration;
static unsigned long long active,checks;
static uint32_t bits[64][4];
static double values[64][4];
uint32_t test_mask(void) {return active;}
uint32_t test_lane(void) {return lane;}
uint32_t test_shuffle(uint32_t value,uint32_t source) {
 assert(source<width && (active & (1ULL << source)));
 unsigned c=component++ % components;
 assert(value==bits[lane][c]);
 return bits[source][c];
}
''' + '\n'.join(declarations) + '''
int main(void) {
 uint64_t seed=0x819a4c16;
 for(width=8;width<=32;width*=2) for(iteration=1;iteration<1024;iteration++) {
  seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
  active=(iteration<256 ? iteration : seed) & ((1ULL << width)-1);
  if(!active) continue;
  for(lane=0;lane<width;lane++) if(active & (1ULL << lane)) {
''' + '\n'.join(checks) + '''
  }
 }
 printf("%llu collective comparisons passed\\n",checks);
}
'''
            path = work / 'test.c'
            path.write_text(source)
            binary = work / 'test'
            subprocess.run(['xcrun', 'clang', '-O2', str(path), *objects, '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run(['codesign', '-s', '-', str(binary)], check=True, capture_output=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('collective comparisons passed', result.stdout)
            print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
