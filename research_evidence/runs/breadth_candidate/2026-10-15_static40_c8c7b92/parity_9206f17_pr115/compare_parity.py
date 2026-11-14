"""Dev-window parity: the 13 Static-40 slices re-run at the #115 head (master 9206f17 + #115 files) vs the #114 packets
(c8c7b92). Exact comparison of every packet field, ignoring only `commit`, `runner_sha256` and `packet_sha256`.
Trades are compared field by field in order (symbol, side, t_in, t_out, prices, pnl, R and every other trade field).

usage: python compare_parity.py BASE_DIR NEW_DIR OUT_JSON
"""
import hashlib
import json
import os
import sys

IGNORED = ('commit', 'runner_sha256', 'packet_sha256')


def diff(a, b, path=''):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if path == '' and k in IGNORED:
                continue
            if k not in a or k not in b:
                out.append(f'{path}/{k}: only in {"new" if k in b else "base"}')
            else:
                out += diff(a[k], b[k], f'{path}/{k}')
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f'{path}: length {len(a)} != {len(b)}']
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f'{path}[{i}]')
        return out
    return [] if (a == b and type(a) is type(b)) else [f'{path}: {a!r} != {b!r}']


def sha(fn):
    with open(fn, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def main(base, new, out):
    names = sorted(f for f in os.listdir(base) if f.endswith('_defaults.json'))
    assert sorted(f for f in os.listdir(new) if f.endswith('_defaults.json')) == names and len(names) == 13
    rows = {}
    for n in names:
        a, b = (json.load(open(os.path.join(d, n), encoding='utf-8')) for d in (base, new))
        d = diff(a, b)
        rows[n] = {'base_sha256': sha(os.path.join(base, n)), 'new_sha256': sha(os.path.join(new, n)),
                   'base_commit': a['commit'], 'new_commit': b['commit'], 'trades': [len(a['trades']), len(b['trades'])],
                   'ignored_differ': [k for k in IGNORED if a.get(k) != b.get(k)], 'differences': d[:50],
                   'n_differences': len(d)}
    ok = all(r['n_differences'] == 0 for r in rows.values())
    doc = {'format': 'zb-breadth-parity/1', 'ignored_fields': list(IGNORED), 'packets': rows,
           'verdict': ('PARITY: all 13 slices identical trade-by-trade and in every stat (ignoring commit / '
                       'runner_sha256 / packet_sha256)') if ok else 'DIFFERENCE: see packets[*].differences'}
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(doc, sort_keys=True, indent=1) + '\n')
    print(doc['verdict'])
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:4]))
