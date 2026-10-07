"""Strict trace diff (design principle 4): codes, bars and sides exactly; R / pnl only within the case's declared tolerance.

compare(case, trace) -> list of mismatch records {path, expected, actual}. An empty list = the adapter equals the golden.
A known divergence stores the exact mismatch list it produces (`observed`); same_mismatches() checks a run reproduces it.
"""
from .schema import canonical

FLOAT_FIELDS = ('R', 'pnl')


def _tol(case):
    t = case['expect'].get('tolerance') or {}
    return {k: float(t[k]) if k in t else 1e-9 for k in FLOAT_FIELDS}


def _fmt(t):
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in t.items()}


def compare(case, trace):
    tol = _tol(case)
    exp = case['expect']['trades']
    act = trace.trades
    out = []
    if len(exp) != len(act):
        out.append(dict(path='trades', expected=exp, actual=[_fmt(t) for t in act]))
    else:
        for k, (e, a) in enumerate(zip(exp, act)):
            for f, ev in e.items():
                av = a.get(f)
                if f in FLOAT_FIELDS:
                    if av is None or abs(float(ev) - av) > tol[f]:
                        out.append(dict(path=f'trades[{k}].{f}', expected=ev, actual=None if av is None else round(av, 6)))
                elif ev != av:
                    out.append(dict(path=f'trades[{k}].{f}', expected=ev, actual=av))
    for f, ev in (case['expect'].get('final') or {}).items():
        if f in trace.final and trace.final[f] != ev:
            out.append(dict(path=f'final.{f}', expected=ev, actual=trace.final[f]))
    return out


def _same(o, a, tol, field=None):
    if isinstance(o, dict) and isinstance(a, dict):
        return set(o) == set(a) and all(_same(o[k], a[k], tol, k) for k in o)
    if isinstance(o, list) and isinstance(a, list):
        return len(o) == len(a) and all(_same(x, y, tol, field) for x, y in zip(o, a))
    if field in FLOAT_FIELDS and o is not None and a is not None:
        return abs(float(o) - float(a)) <= tol[field]
    return o == a


def same_mismatches(case, observed, mismatches):
    """True when a run reproduces the recorded known divergence exactly (paths, expected and actual values; R / pnl within
    the case tolerance). Any other difference is drift inside the divergence and must fail, not xfail."""
    tol = _tol(case)
    if len(observed) != len(mismatches):
        return False
    key = lambda m: m['path']
    return all(o['path'] == m['path'] and _same(o.get('actual'), m['actual'], tol, o['path'].rsplit('.', 1)[-1])
               and canonical(o.get('expected')) == canonical(m['expected'])
               for o, m in zip(sorted(observed, key=key), sorted(mismatches, key=key)))


def report(case, adapter, mismatches, trace):
    lines = [f"{case['id']} [{adapter}]: {len(mismatches)} mismatch(es) against the golden expectation"]
    for m in mismatches:
        lines.append(f"  {m['path']}: expected {canonical(m['expected'])}  actual {canonical(m['actual'])}")
    lines.append('  actual trades: ' + canonical([_fmt(t) for t in trace.trades]))
    return '\n'.join(lines)
