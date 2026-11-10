#!/usr/bin/env python3
"""Hash and inspect every input; record every AIR module and refusing state.

Usage: census.py INPUTS.jsonl OUTDIR [WORKERS]
INPUTS rows contain path and sha256. Non-MTLB resources remain explicit
uninspected refusals; they never count as scanned AIR or compatible libraries.
No binaries or Apple disassembly are written into the source repository.
"""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import compatibility
import mtlb
import resource_formats


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    source, output = Path(sys.argv[1]), Path(sys.argv[2])
    dependencies = ['census.py', 'compatibility.py', 'texture_coords.py', 'read_sampler.py', 'null_compare.py', 'half2_load.py', 'private_half2.py', 'mkfix_half_vec_int.py', 'mtlb.py', 'memory_effects.py', 'resource_formats.py', 'inline_private.py']
    source_hashes = {name: sha((Path(__file__).parent / name).read_bytes()) for name in dependencies}
    previous = [Path(arg) / 'modules' for arg in sys.argv[4:]]
    output.mkdir(parents=True)
    cache = output / 'modules'
    cache.mkdir()
    inputs = [json.loads(line) for line in source.read_text().splitlines()]
    module_jobs, libraries = {}, []
    for row in inputs:
        p = Path(row['path']); data = p.read_bytes()
        if sha(data) != row['sha256']:
            raise ValueError('input hash drift: ' + str(p))
        item = dict(path=str(p), sha256=sha(data), size=len(data), modules=[])
        try:
            decoded = resource_formats.decode(data)
            groups = {}
            for module in decoded['modules']:
                blob = module['blob']
                digest = sha(blob)
                groups.setdefault(digest, []).extend(module['names'])
                module_jobs.setdefault(digest, blob)
            item.update(kind=decoded['kind'], native_slices=decoded['native_slices'])
            item['modules'] = [dict(sha256=digest, exports=names) for digest, names in groups.items()]
            item['exports'] = decoded['exports']
            item['indexed_symbols'] = decoded.get('symbols', 0)
            if decoded['native_slices']:
                item['refusal'] = 'opaque native slices remain uninspected: ' + str(len(decoded['native_slices']))
        except ValueError as e:
            item['refusal'] = str(e)
        libraries.append(item)
    print(f'{len(libraries)} libraries; {len(module_jobs)} unique modules', flush=True)

    def inspect(job):
        digest, blob = job
        directory = cache / digest
        directory.mkdir()
        air = directory / 'original.air'; air.write_bytes(blob)
        row = dict(sha256=digest)
        prior = next((root / digest for root in previous if (root / digest / 'report.json').exists()), None)
        if prior and (prior / 'report.json').exists():
            saved = json.loads((prior / 'report.json').read_text())
            text = (prior / 'original.ll').read_text()
            if sha(text.encode()) != saved['ir_sha256'] or sha((prior / 'original.air').read_bytes()) != digest:
                raise ValueError('cached IR/input hash drift: ' + digest)
        else:
            r = subprocess.run(['xcrun', 'metal-objdump', '--disassemble', str(air)], capture_output=True, text=True)
            if r.returncode or '; ModuleID' not in r.stdout:
                row['refusal'] = r.stderr or r.stdout[:300]
                return row
            text = r.stdout[r.stdout.index('; ModuleID'):]
        (directory / 'original.ll').write_text(text)
        row['ir_sha256'] = sha(text.encode())
        try:
            row['patterns'] = compatibility.scan(text)
            try:
                fixed, report = compatibility.rewrite(text)
                row.update(status='rewritten' if fixed != text else 'unchanged', report=report)
                if fixed != text:
                    (directory / 'fixed.ll').write_text(fixed)
            except (ValueError, AssertionError, KeyError, TypeError) as e:
                row.update(status='refused', refusal=str(e))
        except Exception as e:
            row.update(status='refused', refusal=f'{type(e).__name__}: {e}')
        (directory / 'report.json').write_text(json.dumps(row, indent=2) + '\n')
        return row
    with concurrent.futures.ThreadPoolExecutor(max_workers=int(sys.argv[3]) if len(sys.argv)>3 else 6) as pool:
        modules = list(pool.map(inspect, module_jobs.items()))
    if source_hashes != {name: sha((Path(__file__).parent / name).read_bytes()) for name in dependencies}:
        raise ValueError('source changed during census')
    report = dict(source_list_sha256=sha(source.read_bytes()), source_hashes=source_hashes,
                  libraries=libraries, modules=modules)
    (output / 'census.json').write_text(json.dumps(report, indent=2) + '\n')
    from collections import Counter
    summary = dict(libraries=len(libraries), library_refusals=sum('refusal' in r for r in libraries),
                   exports=sum(r.get('exports',0) for r in libraries),
                   indexed_symbols=sum(r.get('indexed_symbols',0) for r in libraries),
                   opaque_native_slices=sum(len(r.get('native_slices',[])) for r in libraries),
                   modules=len(modules),
                   states=dict(Counter(r.get('status','refused') for r in modules)))
    (output / 'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary),flush=True)


if __name__ == '__main__':
    main()
