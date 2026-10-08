"""Strict trace diff (design principle 4): codes, bars and sides exactly; R / pnl to float precision.

compare(case, trace, adapter) -> list of mismatch records {path, expected, actual}. An empty list = the adapter equals the golden.
A known divergence stores the exact mismatch list it produces (`observed`); same_mismatches() checks a run reproduces it.

Float tolerance of one expected trade field (P2-2: no case-wide tolerance):
    EPS (float noise)
  + the trade's own `tol` - only when it names this adapter, with a stated reason (schema: <= 0.001)
  + the adapter's stated output resolution for that trade (`_res`, e.g. the engine journal's 4 dp rounding).
Every number on the float path must be finite and non-boolean (P1 on b01d439): expected R / pnl, actual R / pnl, every
declared tolerance and `_res`; `_res` must also be >= 0 and at most RES_MAX. Anything else raises schema.NumberError (a hard
failure: never a mismatch that a known divergence could absorb, never a silent pass).
Final state (P2-1): every expect.final key the adapter declares (adapters.CAPS) must be in the trace and equal; a key the
adapter does not declare is not compared for it (schema.FINAL_KEYS rejects typos at load time).
"""
from .schema import TOL_MAX, NumberError, canonical, finite

FLOAT_FIELDS = ('R', 'pnl')
EPS = 1e-9
DRIFT = 1e-6        # observed values are recorded rounded to 6 dp: a re-run must reproduce them to that precision
# Largest output resolution an adapter may state per trade (`_res`). The engine journal's is pnl 1e-4 and
# R (1e-4 + |R| x 0.005) / risk_usd, i.e. ~5e-4 R per R on the pack's 10 USDT risk; anything larger is a tolerance in disguise.
RES_MAX = {'R': 0.005, 'pnl': 1e-4}


def _tol(e, a, adapter, f, k):
    t = e.get('tol') or {}
    own = 0.0
    if f in t:
        own = finite(t[f], f'trades[{k}].tol.{f}')
        if not 0.0 <= own <= TOL_MAX:
            raise NumberError(f'trades[{k}].tol.{f}={t[f]!r}: outside [0, {TOL_MAX}]')
        if adapter not in t.get('adapters', ()):
            own = 0.0
    res = a.get('_res') or {}
    if not isinstance(res, dict):
        raise NumberError(f'trades[{k}]._res={res!r}: a dict of {FLOAT_FIELDS}')
    r = 0.0
    if f in res:
        r = _actual(res[f], f'trades[{k}]._res.{f}')
        if not 0.0 <= r <= RES_MAX[f]:
            raise NumberError(f'trades[{k}]._res.{f}={res[f]!r}: an output resolution is in [0, {RES_MAX[f]}]')
    return EPS + own + r


def _actual(av, where):
    """An actual R / pnl: a finite int / float (never a string, bool or NaN)."""
    if isinstance(av, str):
        raise NumberError(f'{where}: actual {av!r} is a string, not a number')
    return finite(av, where)


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
                    evf = finite(ev, f'expect trades[{k}].{f}')
                    tol = _tol(e, a, adapter, f, k)
                    if av is None:
                        out.append(dict(path=f'trades[{k}].{f}', expected=ev, actual=None))
                    elif not abs(evf - _actual(av, f'trades[{k}].{f}')) <= tol:
                        out.append(dict(path=f'trades[{k}].{f}', expected=ev, actual=round(av, 6)))
                elif ev != av or isinstance(ev, bool) != isinstance(av, bool):
                    out.append(dict(path=f'trades[{k}].{f}', expected=ev, actual=av))
    extra = set(trace.final) - caps['final']
    if extra:
        raise AssertionError(f'{adapter}: trace.final reports undeclared keys {sorted(extra)} (declare them in adapters.CAPS)')
    for f, ev in (case['expect'].get('final') or {}).items():
        av = trace.final.get(f, KeyError)
        if f in caps['final'] and (av != ev or isinstance(av, bool) != isinstance(ev, bool)):
            out.append(dict(path=f'final.{f}', expected=ev, actual=trace.final.get(f)))
    return out


def _same(o, a, field=None):
    if isinstance(o, dict) and isinstance(a, dict):
        return set(o) == set(a) and all(_same(o[k], a[k], k) for k in o)
    if isinstance(o, list) and isinstance(a, list):
        return len(o) == len(a) and all(_same(x, y, field) for x, y in zip(o, a))
    if field in FLOAT_FIELDS and o is not None and a is not None:
        return abs(finite(o, 'observed') - finite(a, 'actual')) <= DRIFT
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
