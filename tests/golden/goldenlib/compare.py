"""Strict trace diff (design principle 4): codes, bars and sides exactly; R / pnl to float precision.

compare(case, trace, adapter) -> list of mismatch records {path, expected, actual}. An empty list = the adapter equals the golden.
A known divergence stores the exact mismatch list it produces (`observed`); same_mismatches() checks a run reproduces it.

Float tolerance of one expected trade field (P2-2: no case-wide tolerance):
    EPS (float noise)
  + the trade's own `tol` - only when it names this adapter, with a stated reason (schema: <= 0.001)
  + the adapter's stated output resolution for that trade (`_res`, e.g. the engine journal's 4 dp rounding).
Final state (P2-1): every expect.final key the adapter declares (adapters.CAPS) must be in the trace and equal; a key the
adapter does not declare is not compared for it (schema.FINAL_KEYS rejects typos at load time).
"""
from .schema import canonical

FLOAT_FIELDS = ('R', 'pnl')
EPS = 1e-9
DRIFT = 1e-6        # observed values are recorded rounded to 6 dp: a re-run must reproduce them to that precision


def _tol(e, a, adapter, f):
    t = e.get('tol') or {}
    own = float(t[f]) if f in t and adapter in t.get('adapters', ()) else 0.0
    return EPS + own + float((a.get('_res') or {}).get(f, 0.0))


def _fmt(t):
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in t.items() if not k.startswith('_')}


def compare(case, trace, adapter):
    from .adapters import CAPS
    caps = CAPS[adapter]
    exp = case['expect']['trades']
    act = trace.trades
    out = []
    if len(exp) != len(act):
        out.append(dict(path='trades', expected=exp, actual=[_fmt(t) for t in act]))
    else:
        for k, (e, a) in enumerate(zip(exp, act)):
            for f, ev in e.items():
                if f == 'tol':
                    continue
                av = a.get(f)
                if f in FLOAT_FIELDS:
                    if av is None or abs(float(ev) - av) > _tol(e, a, adapter, f):
                        out.append(dict(path=f'trades[{k}].{f}', expected=ev, actual=None if av is None else round(av, 6)))
                elif ev != av:
                    out.append(dict(path=f'trades[{k}].{f}', expected=ev, actual=av))
    extra = set(trace.final) - caps['final']
    if extra:
        raise AssertionError(f'{adapter}: trace.final reports undeclared keys {sorted(extra)} (declare them in adapters.CAPS)')
    for f, ev in (case['expect'].get('final') or {}).items():
        if f in caps['final'] and trace.final.get(f, KeyError) != ev:
            out.append(dict(path=f'final.{f}', expected=ev, actual=trace.final.get(f)))
    return out


def _same(o, a, field=None):
    if isinstance(o, dict) and isinstance(a, dict):
        return set(o) == set(a) and all(_same(o[k], a[k], k) for k in o)
    if isinstance(o, list) and isinstance(a, list):
        return len(o) == len(a) and all(_same(x, y, field) for x, y in zip(o, a))
    if field in FLOAT_FIELDS and o is not None and a is not None:
        return abs(float(o) - float(a)) <= DRIFT
    return o == a


def same_mismatches(case, observed, mismatches):
    """True when a run reproduces the recorded known divergence exactly (paths, expected and actual values; R / pnl to the
    6 dp they are recorded with). Any other difference is drift inside the divergence and must fail, not xfail."""
    if len(observed) != len(mismatches):
        return False
    key = lambda m: m['path']
    return all(o['path'] == m['path'] and _same(o.get('actual'), m['actual'], o['path'].rsplit('.', 1)[-1])
               and canonical(o.get('expected')) == canonical(m['expected'])
               for o, m in zip(sorted(observed, key=key), sorted(mismatches, key=key)))


def report(case, adapter, mismatches, trace):
    from .adapters import CAPS
    lines = [f"{case['id']} [{adapter}]: {len(mismatches)} mismatch(es) against the golden expectation"]
    for m in mismatches:
        lines.append(f"  {m['path']}: expected {canonical(m['expected'])}  actual {canonical(m['actual'])}")
    lines.append('  actual trades: ' + canonical([_fmt(t) for t in trace.trades]))
    skipped = sorted(set(case['expect'].get('final') or {}) - CAPS[adapter]['final'])
    if skipped:
        lines.append(f'  expect.final {skipped}: not reported by {adapter} (adapters.CAPS)')
    return '\n'.join(lines)
