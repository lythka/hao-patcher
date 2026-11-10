"""Check the live block transform's supported ABI and transactional refusals.

These are IR controls, not GPU sampler acceptance. Run the original block-read
GPU fixtures through compat.dylib separately and compare their saved readbacks.
"""
import json
from pathlib import Path
import subprocess
import sys
from replay_runtime import paths

BLOCK = 'air.block_read_texture_2d_array.v4f32'
READ = 'air.sample_texture_2d_array.v4f32'
TEX = '%struct._texture_2d_array_t addrspace(1)*'
SAMPLER = '%struct._sampler_t addrspace(2)*'
TYPES = [TEX, SAMPLER, '<2 x i32>', 'i32', '<2 x i32>', 'i32',
         'i32', '<4 x float>*', 'i8*', '<2 x i32>']


def fixture(shape='<i32 2, i32 1>', sparse='null', offset='%offset', tail='', lod='0'):
    args = ['%tex', '%sampler', '%coord', '%layer', offset, lod,
            '%access', '%out', sparse, shape]
    operands = ', '.join(t + ' ' + v for t, v in zip(TYPES, args))
    return f'''%struct._texture_2d_array_t = type opaque
%struct._sampler_t = type opaque
declare void @{BLOCK}({', '.join(TYPES)})
define void @probe({TEX} %tex, {SAMPLER} %sampler, <2 x i32> %coord,
 i32 %layer, <2 x i32> %offset, i32 %lod, i32 %access, <4 x float>* %out,
 i8* %sparse, <2 x i32> %shape) {{
 {tail}call void @{BLOCK}({operands})
 ret void
}}
!air.kernel = !{{!100}}
!100 = !{{void ({', '.join(TYPES)})* @probe}}
'''


def check(driver, output):
    driver, output = Path(driver).resolve(), Path(output).resolve()
    output.mkdir()
    llvm, env = paths()
    env['COMPAT_BLOCK_READS'] = 'per-axis-mip0'
    rows = []

    def run(name, text, changed, environment=env):
        source, fixed = output / (name + '.ll'), output / (name + '-fixed.ll')
        source.write_text(text)
        p = subprocess.run([str(driver), str(llvm), str(source), str(fixed)],
                           env=environment, capture_output=True, text=True)
        (output / (name + '.stderr')).write_text(p.stderr)
        assert not p.returncode, (name, p.stderr)
        result = json.loads(p.stdout)
        assert result['changed'] == changed, (name, result)
        assert result['original_unchanged'] and result['valid'], (name, result)
        transformed = fixed.read_text()
        if changed:
            assert transformed.count('call <4 x float> @' + READ) == 2
            assert 'call void @' + BLOCK not in transformed
            assert transformed.count('<2 x i32> %offset') >= 1
        rows.append(dict(name=name, **result))

    for shape in ('<i32 2, i32 1>', '<i32 1, i32 2>'):
        run('pair-' + str(len(rows)), fixture(shape=shape), True)
    for label, kw in (
        ('dynamic-lod', dict(lod='%lod')),
        ('nonzero-lod', dict(lod='1')),
        ('sparse', dict(sparse='%sparse')),
        ('sparse-undef', dict(sparse='undef')),
        ('dynamic-shape', dict(shape='%shape')),
        ('undef-shape', dict(shape='undef')),
        ('wide-shape', dict(shape='<i32 4, i32 1>')),
        ('square', dict(shape='<i32 2, i32 2>')),
        ('empty', dict(shape='zeroinitializer')),
        ('tail', dict(tail='tail ')),
    ):
        run('refuse-' + label, fixture(**kw), False)
    text = fixture()
    run('refuse-no-kernel-stage', text.replace('!air.kernel = !{!100}', ''), False)
    run('refuse-fragment-stage', text.replace('!air.kernel', '!air.fragment'), False)
    run('refuse-abi', text.replace('i32 %access', 'i64 %access').replace(
        'i32, <4 x float>*', 'i64, <4 x float>*'), False)
    run('refuse-calling-convention', text.replace('call void @', 'call fastcc void @'), False)
    run('refuse-metadata', text.replace('i32 1>)', 'i32 1>), !compat.test !0') +
        '!0 = !{!"preserve"}\n', False)
    # A supported call followed by an unsupported one must leave both intact.
    call = next(line for line in text.splitlines() if 'call void @' in line)
    run('refuse-mixed-module', text.replace(call, call + '\n' +
        call.replace('i8* null', 'i8* %sparse')), False)
    run('refuse-inline-sampler', text.replace(SAMPLER + ' %sampler, <2 x i32> %coord, i32 %layer',
        SAMPLER + ' null, <2 x i32> %coord, i32 %layer'), False)
    query='llvm.igil.is.normalized.'
    run('refuse-existing-query-declaration', text +
        f'declare i32 @{query}({SAMPLER}, i32)\n', False)
    run('refuse-existing-query', text.replace(' ret void',
        f' %q = call i32 @{query}({SAMPLER} %sampler, i32 0)\n ret void') +
        f'declare i32 @{query}({SAMPLER}, i32)\n', False)
    run('disabled', text, False, {k: v for k, v in env.items() if k != 'COMPAT_BLOCK_READS'})
    (output / 'results.json').write_text(json.dumps(rows, indent=2) + '\n')
    return rows


if __name__ == '__main__':
    print(json.dumps(dict(ir_controls=len(check(*sys.argv[1:])))))
