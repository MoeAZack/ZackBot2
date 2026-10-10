"""TNET-01 scenario specification: format zb-newcore-tnet-scenario/1 (JSON), validated strictly (unknown keys, bad
references and impossible values are refused, never fixed up).

{
  "format": "zb-newcore-tnet-scenario/1",
  "name": "entry_stop_close_long",                    # [a-z0-9_]{3,48}
  "description": "free text",
  "symbols": ["SOLUSDT"],                             # every step's symbol must be listed here
  "account": {"min_balance": "100", "adopt_foreign": []},
  "steps": [                                          # run in order; ids unique; refs point BACKWARDS only
    {"id": "entry",   "op": "market", "symbol": "SOLUSDT", "side": "LONG", "reduce": false, "qty": "min"},
    {"id": "protect", "op": "stop",   "symbol": "SOLUSDT", "side": "LONG", "qty": "filled:entry",
     "trigger_offset_pct": "-5", "from": "entry", "route": "algo"},
    {"id": "verify",  "op": "query",  "ref": "protect"},
    {"id": "unstop",  "op": "cancel", "ref": "protect"},
    {"id": "close",   "op": "market", "symbol": "SOLUSDT", "side": "LONG", "reduce": true, "qty": "filled:entry"},
    {"id": "settle",  "op": "wait",   "seconds": 2}
  ],
  "faults": [{"at": "entry", "kind": "lost_response", "times": 1}],     # injected at the HTTP seam for that step
  "expect": {
    "outcomes": {"entry": ["final", "unknown"], "protect": ["known"]},  # allowed port outcome kinds per step
    "end_state": {"flat": true, "open_newcore_orders": 0},
    "max_duration_s": 120
  }
}
qty: "min" (minimum feasible), "filled:<step id>" (that step's executed quantity) or a positive decimal string.
"""
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

FORMAT = 'zb-newcore-tnet-scenario/1'
OPS = {
    'market': ({'id', 'op', 'symbol', 'side', 'reduce', 'qty'}, set()),
    'stop': ({'id', 'op', 'symbol', 'side', 'qty', 'trigger_offset_pct', 'from', 'route'}, set()),
    'query': ({'id', 'op', 'ref'}, set()),
    'cancel': ({'id', 'op', 'ref'}, set()),
    'wait': ({'id', 'op', 'seconds'}, set()),
}
FAULT_KINDS = ('lost_response', 'timeout', 'reset', 'http_5xx', 'dns_timeout', 'duplicate_resend')
OUTCOME_KINDS = ('known', 'final', 'acknowledged', 'rejected', 'unknown', 'not_found')
NAME_RE = re.compile(r'[a-z0-9_]{3,48}')
ID_RE = re.compile(r'[a-z][a-z0-9_]{0,31}')
SYMBOL_RE = re.compile(r'[A-Z0-9]{2,30}')
DEC_RE = re.compile(r'-?[0-9]+(\.[0-9]+)?')


class SpecError(ValueError):
    def __init__(self, path, msg):
        super().__init__(f'{path}: {msg}')
        self.path = path


def _req(cond, path, msg):
    if not cond:
        raise SpecError(path, msg)


def _keys(obj, path, required, optional=frozenset()):
    _req(isinstance(obj, dict), path, 'must be an object')
    missing, extra = set(required) - set(obj), set(obj) - set(required) - set(optional)
    _req(not missing, path, f'missing {sorted(missing)}')
    _req(not extra, path, f'unknown {sorted(extra)}')


def _dec(v, path, *, positive=False, lo=None, hi=None):
    _req(isinstance(v, str) and DEC_RE.fullmatch(v) is not None, path, 'a decimal STRING (never a JSON float)')
    d = Decimal(v)
    _req(not positive or d > 0, path, 'must be > 0')
    _req(lo is None or d >= lo, path, f'must be >= {lo}')
    _req(hi is None or d <= hi, path, f'must be <= {hi}')
    return d


def validate_spec(doc):
    """Return the spec document if valid; raise SpecError(path, reason) otherwise."""
    _keys(doc, '$', {'format', 'name', 'symbols', 'steps', 'expect'}, {'description', 'account', 'faults', 'targets'})
    if 'targets' in doc:
        t = doc['targets']
        _req(isinstance(t, list) and t and all(isinstance(x, str) and x in ('fake', 'testnet') for x in t)
             and len(set(t)) == len(t), '$.targets', 'a non-empty list of fake / testnet')
    _req(doc['format'] == FORMAT, '$.format', f'must be {FORMAT}')
    _req(isinstance(doc['name'], str) and NAME_RE.fullmatch(doc['name']), '$.name', '[a-z0-9_]{3,48}')
    if 'description' in doc:
        _req(isinstance(doc['description'], str) and len(doc['description']) <= 2000, '$.description', 'text')
    syms = doc['symbols']
    _req(isinstance(syms, list) and syms and len(set(syms)) == len(syms) and
         all(isinstance(s, str) and SYMBOL_RE.fullmatch(s) for s in syms), '$.symbols', 'unique symbols, non-empty')
    if 'account' in doc:
        a = doc['account']
        _keys(a, '$.account', set(), {'min_balance', 'adopt_foreign'})
        if 'min_balance' in a:
            _dec(a['min_balance'], '$.account.min_balance', lo=Decimal(0))
        if 'adopt_foreign' in a:
            _req(isinstance(a['adopt_foreign'], list) and all(isinstance(x, str) and 0 < len(x) <= 64
                                                             for x in a['adopt_foreign']),
                 '$.account.adopt_foreign', 'a list of client ids / SYMBOL:SIDE')
    steps = doc['steps']
    _req(isinstance(steps, list) and 0 < len(steps) <= 50, '$.steps', '1..50 steps')
    seen, kinds = [], {}
    for i, st in enumerate(steps):
        p = f'$.steps[{i}]'
        _req(isinstance(st, dict) and st.get('op') in OPS, p + '.op', f'one of {sorted(OPS)}')
        required, optional = OPS[st['op']]
        _keys(st, p, required, optional)
        _req(isinstance(st['id'], str) and ID_RE.fullmatch(st['id']) is not None, p + '.id', '[a-z][a-z0-9_]{0,31}')
        _req(st['id'] not in seen, p + '.id', 'duplicate step id')
        op = st['op']
        if op in ('market', 'stop'):
            _req(st['symbol'] in syms, p + '.symbol', 'not in $.symbols')
            _req(st['side'] in ('LONG', 'SHORT'), p + '.side', 'LONG / SHORT')
            q = st['qty']
            if isinstance(q, str) and q.startswith('filled:'):
                ref = q[len('filled:'):]
                _req(ref in seen and kinds[ref] == 'market', p + '.qty', 'filled:<an EARLIER market step>')
            elif q != 'min':
                _dec(q, p + '.qty', positive=True)
        if op == 'market':
            _req(type(st['reduce']) is bool, p + '.reduce', 'a boolean')
        if op == 'stop':
            _dec(st['trigger_offset_pct'], p + '.trigger_offset_pct', lo=Decimal('-50'), hi=Decimal('50'))
            _req(st['from'] in seen and kinds[st['from']] == 'market', p + '.from', 'an EARLIER market step')
            _req(st['route'] in ('classic', 'algo'), p + '.route', 'classic / algo')
        if op in ('query', 'cancel'):
            _req(st['ref'] in seen and kinds[st['ref']] in ('market', 'stop'), p + '.ref',
                 'an EARLIER market / stop step')
        if op == 'wait':
            _req(isinstance(st['seconds'], int) and not isinstance(st['seconds'], bool) and 0 < st['seconds'] <= 600,
                 p + '.seconds', 'an int in 1..600')
        seen.append(st['id'])
        kinds[st['id']] = op
    for i, f in enumerate(doc.get('faults', [])):
        p = f'$.faults[{i}]'
        _keys(f, p, {'at', 'kind'}, {'times'})
        _req(f['at'] in seen and kinds[f['at']] != 'wait', p + '.at', 'a step id (not a wait)')
        _req(f['kind'] in FAULT_KINDS, p + '.kind', f'one of {FAULT_KINDS}')
        if 'times' in f:
            _req(isinstance(f['times'], int) and not isinstance(f['times'], bool) and 1 <= f['times'] <= 5,
                 p + '.times', 'an int in 1..5')
    e = doc['expect']
    _keys(e, '$.expect', {'end_state'}, {'outcomes', 'max_duration_s'})
    for sid, allowed in e.get('outcomes', {}).items():
        p = f'$.expect.outcomes.{sid}'
        _req(sid in seen and kinds[sid] != 'wait', p, 'a step id (not a wait)')
        _req(isinstance(allowed, list) and allowed and all(k in OUTCOME_KINDS for k in allowed), p,
             f'a non-empty list of {OUTCOME_KINDS}')
    _keys(e['end_state'], '$.expect.end_state', {'flat', 'open_newcore_orders'})
    _req(type(e['end_state']['flat']) is bool, '$.expect.end_state.flat', 'a boolean')
    n = e['end_state']['open_newcore_orders']
    _req(isinstance(n, int) and not isinstance(n, bool) and n >= 0, '$.expect.end_state.open_newcore_orders', '>= 0')
    if 'max_duration_s' in e:
        m = e['max_duration_s']
        _req(isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 3600, '$.expect.max_duration_s', '1..3600')
    return doc


def load_spec(path):
    """Every malformed file is a SpecError (bad UTF-8, too deep, wrong JSON types), never an untyped crash."""
    try:
        return _load_spec(path)
    except (UnicodeDecodeError, RecursionError, TypeError, AttributeError, ValueError) as ex:
        if isinstance(ex, SpecError):
            raise
        raise SpecError('$', f'malformed spec ({type(ex).__name__})') from None


def _load_spec(path):
    with open(path, encoding='utf-8') as fh:
        try:
            doc = json.load(fh, parse_float=lambda s: (_ for _ in ()).throw(SpecError('$', 'JSON floats are refused '
                                                                                            '(use decimal strings)')))
        except json.JSONDecodeError as ex:
            raise SpecError('$', f'not JSON ({ex.msg})') from None
    return validate_spec(doc)


def spec_digest(doc):
    """sha256 of the canonical spec (sorted keys): the report records which spec ran."""
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
