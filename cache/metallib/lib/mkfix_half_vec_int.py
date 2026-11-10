#!/usr/bin/env python3
"""Fix for the HD 4000 (Metal 3802) MTLCompilerService crash at libLLVM+0x620899
(null+0x28 in DAGTypeLegalizer::PromoteIntRes_SimpleIntBinOp, reached from
SelectionDAG::LegalizeTypes): the old Intel backend never records a promoted value for
`bitcast <N x half> %x to <N x i16>` (N >= 3), so any vector integer op that reads it
(and/or/xor/add/sub/mul here; icmp and udiv crash the same way at other offsets) dereferences
a null SDNode.  RenderBox's transfer-function code uses this for copysign:
    %i = bitcast <3 x half> %v to <3 x i16> ; and %i, 0x8000 ; or with table value ; bitcast back

Rewrite: every <N x i16> integer op (and the icmp) that reads such a bitcast, directly or through
other rewritten ops, is done lane by lane on i16 scalars (extractelement half -> bitcast i16 ->
op -> ...), and a `bitcast <N x i16> -> <N x half>` of a rewritten value is rebuilt from
scalar `bitcast i16 -> half` lanes.  Same bits, same results (integer ops are lane-wise by
definition).  The original value names are kept (the last insertelement of each rebuilt vector
takes the old name), so numbered IR stays valid and any other user still sees an <N x i16>.
Lane-wise inputs alone are not enough: the backend folds an insertelement chain of scalar
bitcasts back into the vector bitcast (probe fix_v4_lanewise_in still crashes).
usage: mkfix_half_vec_int.py <in.ll> <out.ll>   (prints a JSON report; exit 0 also when nothing
matched, the output is then a byte copy of the input)"""
import json, re, sys

OPS = 'add|sub|mul|and|or|xor|udiv|sdiv|urem|srem'
ROOT = re.compile(r'^  (%[-\w.$]+) = bitcast <(\d+) x half> (%[-\w.$]+|undef|zeroinitializer) to <\2 x i16>(.*)$')
BIN = re.compile(r'^  (%[-\w.$]+) = (' + OPS + r')((?: (?:nuw|nsw|exact))*) <(\d+) x i16> (.+)$')
CMP = re.compile(r'^  (%[-\w.$]+) = icmp (\w+) <(\d+) x i16> (.+)$')
EXIT = re.compile(r'^  (%[-\w.$]+) = bitcast <(\d+) x i16> (%[-\w.$]+) to <\2 x half>(.*)$')
VAL = re.compile(r'\s*(%[-\w.$]+|zeroinitializer|undef|poison|<[^>]*>)\s*(?:,|$)')


def split_ops(text):
    """'A, B' (vector operands, maybe constant vectors) -> [A, B]; trailing metadata stays out."""
    ops = []; rest = text
    for _ in range(2):
        m = VAL.match(rest)
        if not m: return None
        ops.append(m.group(1)); rest = rest[m.end():]
    return ops if not rest.strip() or rest.strip().startswith('!') else None


def const_lanes(v, n):
    if v in ('zeroinitializer',): return ['0'] * n
    if v in ('undef', 'poison'): return ['undef'] * n
    if v.startswith('<'):
        lanes = [x.strip() for x in v[1:-1].split(',')]
        assert len(lanes) == n and all(x.startswith('i16 ') for x in lanes), v
        return [x[4:] for x in lanes]
    return None


def safe(name):
    return 'mlfx.' + name[1:].replace('"', '')


def rewrite_function(body):
    lines = body; n_root = n_op = n_exit = 0
    roots = {}
    for l in lines:
        m = ROOT.match(l)
        if m and int(m.group(2)) >= 3: roots[m.group(1)] = (int(m.group(2)), m.group(3))
    if not roots: return lines, dict(roots=0, ops=0, exits=0)
    # fixed point: which ops read a root or another rewritten op
    island = {}; changed = True
    while changed:
        changed = False
        for l in lines:
            m = BIN.match(l) or CMP.match(l)
            if not m or m.group(1) in island: continue
            ops = split_ops(m.group(5) if m.re is BIN else m.group(4))
            if ops and any(o in roots or o in island for o in ops):
                island[m.group(1)] = True; changed = True
    if not island: return lines, dict(roots=0, ops=0, exits=0)
    used = set()  # roots read by a rewritten op get scalar lanes; other roots are left alone
    for l in lines:
        m = BIN.match(l) or CMP.match(l)
        if m and m.group(1) in island:
            used |= set(split_ops(m.group(5) if m.re is BIN else m.group(4))) & set(roots)
    out = []
    lanes = {}  # value name -> [scalar i16 lane names]

    def lanes_of(v, n, pre):
        if v in lanes: return lanes[v]
        c = const_lanes(v, n)
        if c is not None: return c
        names = []
        for k in range(n):  # other <N x i16> SSA value: extract its lanes here
            nm = f'%{safe(v)}.x{len(pre)}.{k}'
            pre.append(f'  {nm} = extractelement <{n} x i16> {v}, i32 {k}'); names.append(nm)
        return names

    for l in lines:
        m = ROOT.match(l)
        if m and m.group(1) in used:
            out.append(l)
            n, src = roots[m.group(1)]; r = m.group(1); names = []
            for k in range(n):
                out.append(f'  %{safe(r)}.h{k} = extractelement <{n} x half> {src}, i32 {k}')
                out.append(f'  %{safe(r)}.l{k} = bitcast half %{safe(r)}.h{k} to i16'); names.append(f'%{safe(r)}.l{k}')
            lanes[r] = names; n_root += 1
            continue
        m = BIN.match(l)
        if m and m.group(1) in island:
            y, op, flags, n = m.group(1), m.group(2), m.group(3), int(m.group(4))
            a, b = split_ops(m.group(5)); pre = []
            la, lb = lanes_of(a, n, pre), lanes_of(b, n, pre); out += pre; names = []
            for k in range(n):
                out.append(f'  %{safe(y)}.l{k} = {op}{flags} i16 {la[k]}, {lb[k]}'); names.append(f'%{safe(y)}.l{k}')
            prev = 'undef'
            for k in range(n):
                nm = y if k == n - 1 else f'%{safe(y)}.v{k}'
                out.append(f'  {nm} = insertelement <{n} x i16> {prev}, i16 {names[k]}, i32 {k}'); prev = nm
            lanes[y] = names; n_op += 1
            continue
        m = CMP.match(l)
        if m and m.group(1) in island:
            y, pred, n = m.group(1), m.group(2), int(m.group(3))
            a, b = split_ops(m.group(4)); pre = []
            la, lb = lanes_of(a, n, pre), lanes_of(b, n, pre); out += pre; names = []
            for k in range(n):
                out.append(f'  %{safe(y)}.c{k} = icmp {pred} i16 {la[k]}, {lb[k]}'); names.append(f'%{safe(y)}.c{k}')
            prev = 'undef'
            for k in range(n):
                nm = y if k == n - 1 else f'%{safe(y)}.v{k}'
                out.append(f'  {nm} = insertelement <{n} x i1> {prev}, i1 {names[k]}, i32 {k}'); prev = nm
            n_op += 1
            continue
        m = EXIT.match(l)
        if m and m.group(3) in lanes:
            z, n, v = m.group(1), int(m.group(2)), m.group(3); prev = 'undef'
            for k in range(n):
                out.append(f'  %{safe(z)}.h{k} = bitcast i16 {lanes[v][k]} to half')
            for k in range(n):
                nm = z if k == n - 1 else f'%{safe(z)}.v{k}'
                out.append(f'  {nm} = insertelement <{n} x half> {prev}, half %{safe(z)}.h{k}, i32 {k}'); prev = nm
            n_exit += 1
            continue
        out.append(l)
    return out, dict(roots=n_root, ops=n_op, exits=n_exit)


def rewrite(text):
    lines = text.split('\n'); out = []; i = 0; rep = dict(functions=[], roots=0, ops=0, exits=0)
    while i < len(lines):
        l = lines[i]
        if l.startswith('define '):
            j = i + 1
            while lines[j] != '}': j += 1
            body, r = rewrite_function(lines[i + 1:j])
            if r['ops']:
                rep['functions'].append(re.match(r'define[^@]*@("[^"]+"|[^(]+)\(', l).group(1))
                for k in ('roots', 'ops', 'exits'): rep[k] += r[k]
            out += [l] + body + ['}']; i = j + 1
        else:
            out.append(l); i += 1
    res = '\n'.join(out)
    refuse_bad_state(res)
    return res, rep


def refuse_bad_state(text):
    # Refuse the bad state: no vector integer op/icmp may still read a <N x half> bitcast (N >= 3)
    for fn in re.findall(r'^define[^\n]*\n(?:.*\n)*?^}', text, re.M):
        roots = {m.group(1) for line in fn.splitlines() for m in [ROOT.match(line)] if m and int(m.group(2)) >= 3}
        for m in re.finditer(r'^  %[-\w.$]+ = (?:(?:' + OPS + r')(?: (?:nuw|nsw|exact))*|icmp \w+) <(\d+) x i16> (.+)$', fn, re.M):
            operands = re.findall(r'%[-\w.$]+', m.group(2))
            assert not any(o in roots for o in operands), m.group(0)


if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    text = open(src).read(); res, rep = rewrite(text)
    open(dst, 'w').write(res)
    rep['changed'] = res != text
    print(json.dumps(rep))
