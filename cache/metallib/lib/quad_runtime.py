"""Add quad shuffles to the Intel split runtime without replacing existing helpers."""
import datetime
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import subprocess
import collective_runtime
import compiler_mask

UNIT = Path(__file__).resolve().parents[1]
SOURCES = UNIT / 'quad-shuffle'
BASE = UNIT / 'shader-runtime-helpers'
RELATIVE = Path('System/Library/Extensions/AppleIntelGraphicsShared.bundle/Contents/Resources/runtime.igil64.split')


def split_module():
    spec = importlib.util.spec_from_file_location('split_runtime', BASE / 'build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(source, llvm, output, revision, compiler=None):
    source, llvm, output = (Path(p).resolve() for p in (source, llvm, output))
    output.mkdir(parents=True)
    work = output / 'build'
    work.mkdir()
    commands = []

    def run(args):
        args = list(map(str, args))
        result = subprocess.run(args, capture_output=True, text=True)
        commands.append(dict(args=args, returncode=result.returncode,
                             stdout=result.stdout, stderr=result.stderr))
        (work / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        result.check_returncode()

    assembler = work / 'assemble'
    run(['xcrun', 'clang', '-arch', 'x86_64', '-O2', '-Wall', '-Wextra', '-Werror',
         BASE / 'assemble.c', '-o', assembler])
    run(['codesign', '-s', '-', assembler])
    additions = {}

    def assemble(symbol, text):
        # LLVM otherwise records the build directory as source_filename, making
        # identical helpers differ between build directories.
        path = work / (symbol + '.ll')
        path.write_text(f'source_filename = "{symbol}.ll"\n' + text)
        bitcode = path.with_suffix('.bc')
        run([assembler, llvm, path, bitcode])
        additions[symbol] = bitcode.read_bytes()

    for path in sorted(SOURCES.glob('*.ll')):
        symbol, = re.findall(r'^define .*? @(air_quad_\w+)\(', path.read_text(), re.M)
        assemble(symbol, path.read_text())
    if compiler:
        for symbol, text in collective_runtime.sources().items():
            assemble(symbol, text)
    split = split_module()
    original = split.decode(source.read_bytes())
    destination = output / RELATIVE
    destination.parent.mkdir(parents=True)
    destination.write_bytes(split.extend(original, additions))
    compiler_report = None
    outputs = [destination]
    if compiler:
        compiler = Path(compiler).resolve()
        repaired, compiler_report = compiler_mask.repair(compiler.read_bytes())
        binary = output / compiler_mask.RELATIVE
        binary.parent.mkdir(parents=True)
        binary.write_bytes(repaired)
        binary.chmod(0o755)
        run(['codesign', '-f', '-s', '-', binary])
        outputs.append(binary)
    git = lambda *args: subprocess.check_output(['git', '-C', str(UNIT), *args], text=True).strip()
    inputs = [source, llvm, BASE / 'assemble.c', BASE / 'build.py', Path(__file__),
              Path(collective_runtime.__file__),
              *sorted(SOURCES.glob('*.ll'))]
    if compiler:
        inputs += [compiler, Path(compiler_mask.__file__)]
    report = dict(schema=1, unit='cache/metallib', revision=revision,
                  commit=git('rev-parse', 'HEAD'), dirty=bool(git('status', '--porcelain', '--', str(UNIT))),
                  date=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  inputs=[dict(path=str(p), sha256=sha(p)) for p in inputs],
                  preserved_symbols=len(original), added_symbols=sorted(additions),
                  output_sha256=sha(destination), compiler_repair=compiler_report,
                  native_acceptance=False)
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(output)}\n' for p in outputs))
    return report
