"""Assemble rewritten AIR programs/plain LLVM helpers and check emitted patterns.

AIR programs use metal -c. Helpers without AIR version metadata use metal-as:
retained original/fixed controls establish that metal -c crashes on these plain
LLVM libraries. Neither route is Intel backend or GPU acceptance.
"""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import compatibility


def command(text, source, output):
    if not re.search(r'^!air\.version\s*=', text, re.M):
        return ['xcrun', 'metal-as', str(source), '-o', str(output)]
    triple = re.search(r'target triple = "air64_v\d+-apple-(macosx|ios)([^"]+)"', text)
    if not triple:
        raise ValueError('unsupported AIR target')
    options = ['-mmacos-version-min=' + '.'.join(triple[2].split('.')[:2])] if triple[1] == 'macosx' else ['-target', 'air64-apple-ios' + triple[2]]
    return ['xcrun', 'metal', '-c', *options, str(source), '-o', str(output)]


def main():
    census, output = Path(sys.argv[1]), Path(sys.argv[2])
    output.mkdir()
    rows = [r for r in json.loads((census / 'census.json').read_text())['modules'] if r.get('status') == 'rewritten']

    def one(row):
        source = census / 'modules' / row['sha256'] / 'fixed.ll'
        text = source.read_text()
        directory = output / row['sha256']; directory.mkdir()
        result = dict(sha256=row['sha256'], ir_sha256=hashlib.sha256(text.encode()).hexdigest(), assembled=False)
        try:
            args = command(text, source, directory / 'fixed.air')
            result['command'] = args
            process = subprocess.run(args, capture_output=True, text=True)
            if process.returncode:
                raise ValueError(process.stderr[:3000])
            result['assembled'] = True
            process = subprocess.run(['xcrun', 'metal-objdump', '--disassemble', str(directory / 'fixed.air')], capture_output=True, text=True)
            if process.returncode or '; ModuleID' not in process.stdout:
                raise ValueError('missing emitted disassembly: ' + process.stderr[:1000])
            emitted = process.stdout[process.stdout.index('; ModuleID'):]
            (directory / 'emitted.ll').write_text(emitted)
            compatibility.refuse_bad_state(emitted)
            result['emitted_patterns_absent'] = True
        except (ValueError, AssertionError) as error:
            result['error'] = str(error)
        (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, rows))
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    failures = sum('error' in r for r in results)
    print(json.dumps(dict(modules=len(results), assembled=sum(r['assembled'] for r in results),
                          clean=sum(r.get('emitted_patterns_absent', False) for r in results), errors=failures)))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
