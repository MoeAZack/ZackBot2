"""Runner-driven TNET-01 scenario specification: format zb-newcore-tnet-runner/1 (JSON), validated strictly (unknown
keys, impossible values and unbounded runs are refused, never fixed up). The port-level counterpart is
newcore.venue.tnet_spec (zb-newcore-tnet-scenario/1); this one drives the Runner, so its steps are SIGNALS and CYCLES,
never raw orders.

{
  "format": "zb-newcore-tnet-runner/1",
  "id": "T01-long",                                   # T<2 digits>, up to two -<suffix>
  "name": "long_cycle",                               # [a-z0-9_]{3,48}
  "description": "free text",
  "targets": ["fake", "testnet"],                     # where the scenario is meaningful
  "symbol": "SOLUSDT",
  "side": "LONG",
  "sizing": {"risk_pct": "0.002", "max_leverage": "3"},
  "stop": {"pct": "1"},                               # stop distance: pct of the signal close, or {"atr": "1"} x ATR14
  "fake": {"price": "100", "equity": "5000", "classic_stops": "accept"},       # FakeTarget only (optional)
  "steps": [
    {"op": "enter"},                                  # ENTER on the next closed candle, then one cycle
    {"op": "tick", "n": 2},                           # n cycles, no new signal
    {"op": "close"},                                  # CLOSE on the next closed candle, then one cycle
    {"op": "fault", "on": "entry", "kind": "lost_response"},    # the next entry/close/stop/cancel effect
    {"op": "restart"},                                # a new Runner on the reopened journal (same venue)
    {"op": "resume"},                                 # operator RESUME (leave HOLD); must succeed
    {"op": "await_exit", "max_ticks": 30, "fake_gap_pct": "-3"},   # cycle until the lot closes; else INCONCLUSIVE
    {"op": "check", "what": "protected_reconciled"}   # an intermediate truth check (flat | protected_reconciled)
  ],
  "expect": {"entries": 1, "trades": ["SIGNAL_EXIT"], "fills_match": true, "final": "flat"},
  "expect_testnet": {"stop_route": "any"},            # merged over expect on the testnet target (optional)
  "bound": {"max_ticks": 20, "max_orders": 4, "max_notional_usdt": "2000", "max_wall_s": 900,
            "settle_ms": 1500}                     # settle_ms optional, 0..10000 (testnet wait after a close)
}
Fault kinds: lost_response (the venue acts, the answer is lost), timeout (nothing sent, UNKNOWN), refuse (nothing sent,
REJECTED with `code` - on testnet a SYNTHETIC refusal at the HTTP seam, labelled so in the report).
"""
import hashlib
import json
import os
import re
from decimal import Decimal

from newcore.venue.tnet_spec import SpecError, _dec, _keys, _req

FORMAT = 'zb-newcore-tnet-runner/1'
MAX_SETTLE_MS = 10_000             # bound.settle_ms / --settle-ms: the testnet wait after a candle close
TARGETS = ('fake', 'testnet')
ID_RE = re.compile(r'T[0-9]{2}(-[a-z0-9]{1,12}){0,2}')
NAME_RE = re.compile(r'[a-z0-9_]{3,48}')
SYMBOL_RE = re.compile(r'[A-Z0-9]{2,30}')
FAULT_ON = ('entry', 'close', 'stop', 'cancel')
FAULT_KINDS = ('lost_response', 'timeout', 'refuse')
CHECKS = ('flat', 'protected_reconciled')
STEPS = {
    'enter': (set(), {'side'}),
    'close': (set(), {'side'}),
    'tick': ({'n'}, set()),
    'fault': ({'on', 'kind'}, {'code'}),
    'restart': (set(), set()),
    'resume': (set(), set()),
    'await_exit': ({'max_ticks'}, {'fake_gap_pct'}),
    'check': ({'what'}, set()),
    'move': ({'pct'}, set()),                      # FakeVenue only: the market gaps by pct (see targets.py)
}
EXPECT_KEYS = {'entries', 'skips', 'skip_reason', 'trades', 'entry_phases', 'entry_state', 'hold_seen', 'mode_end',
               'max_orders', 'fills_match', 'stop_route', 'final', 'incidents', 'adds', 'reduces',
               'open_orders_end'}
PLAN_DECIMALS = ('add_r', 'add_scale', 'tp1_r', 'tp1_frac', 'tp2_r', 'cap_mult')
PLAN_KEYS = set(PLAN_DECIMALS) | {'be_after_tp1', 'time_exit_candles'}
SPEC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'specs')


def _int(v, path, lo, hi):
    _req(isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi, path, f'an int in {lo}..{hi}')
    return v


def _expect(e, path):
    _keys(e, path, set(), EXPECT_KEYS)
    for k in ('entries', 'skips', 'max_orders', 'incidents', 'adds', 'reduces', 'open_orders_end'):
        if k in e:
            _int(e[k], f'{path}.{k}', 0, 100)
    for k in ('hold_seen', 'fills_match'):
        if k in e:
            _req(type(e[k]) is bool, f'{path}.{k}', 'a boolean')
    if 'skip_reason' in e:
        _req(isinstance(e['skip_reason'], str) and 0 < len(e['skip_reason']) <= 64, f'{path}.skip_reason', 'text')
    if 'trades' in e:
        _req(isinstance(e['trades'], list) and len(e['trades']) <= 10 and
             all(x is None or (isinstance(x, str) and 0 < len(x) <= 32) for x in e['trades']), f'{path}.trades',
             'a list of exit codes (or null)')
    if 'entry_phases' in e:
        _req(isinstance(e['entry_phases'], list) and 0 < len(e['entry_phases']) <= 10 and
             all(x in ('unknown', 'known', 'final') for x in e['entry_phases']), f'{path}.entry_phases',
             'a list of unknown / known / final')
    if 'entry_state' in e:
        _req(e['entry_state'] in ('filled', 'rejected', 'cancelled', 'not_sent'), f'{path}.entry_state',
             'filled / rejected / cancelled / not_sent')
    if 'mode_end' in e:
        _req(e['mode_end'] in ('active', 'hold'), f'{path}.mode_end', 'active / hold')
    if 'stop_route' in e:
        _req(e['stop_route'] in ('classic', 'algo', 'any'), f'{path}.stop_route', 'classic / algo / any')
    if 'final' in e:
        _req(e['final'] in CHECKS, f'{path}.final', ' / '.join(CHECKS))


def validate_rspec(doc):
    """Return the spec document if valid; raise SpecError(path, reason) otherwise."""
    _keys(doc, '$', {'format', 'id', 'name', 'targets', 'symbol', 'side', 'sizing', 'stop', 'steps', 'expect', 'bound'},
          {'description', 'fake', 'expect_testnet', 'management'})
    _req(doc['format'] == FORMAT, '$.format', f'must be {FORMAT}')
    _req(isinstance(doc['id'], str) and ID_RE.fullmatch(doc['id']) is not None, '$.id', 'T<2 digits>[-suffix[-suffix]]')
    _req(isinstance(doc['name'], str) and NAME_RE.fullmatch(doc['name']) is not None, '$.name', '[a-z0-9_]{3,48}')
    if 'description' in doc:
        _req(isinstance(doc['description'], str) and len(doc['description']) <= 2000, '$.description', 'text')
    t = doc['targets']
    _req(isinstance(t, list) and t and len(set(t)) == len(t) and all(x in TARGETS for x in t), '$.targets',
         f'a non-empty list of {TARGETS}')
    _req(isinstance(doc['symbol'], str) and SYMBOL_RE.fullmatch(doc['symbol']) is not None, '$.symbol', 'a symbol')
    _req(doc['side'] in ('LONG', 'SHORT'), '$.side', 'LONG / SHORT')
    _keys(doc['sizing'], '$.sizing', {'risk_pct', 'max_leverage'})
    _dec(doc['sizing']['risk_pct'], '$.sizing.risk_pct', positive=True, hi=Decimal('0.05'))
    _dec(doc['sizing']['max_leverage'], '$.sizing.max_leverage', positive=True, hi=Decimal('10'))
    s = doc['stop']
    _req(isinstance(s, dict) and len(s) == 1 and set(s) <= {'pct', 'atr'}, '$.stop', 'exactly one of pct / atr')
    for k, v in s.items():
        _dec(v, f'$.stop.{k}', positive=True, hi=Decimal('20'))
    if 'fake' in doc:
        f = doc['fake']
        _keys(f, '$.fake', set(), {'price', 'equity', 'classic_stops'})
        if 'price' in f:
            _dec(f['price'], '$.fake.price', positive=True)
        if 'equity' in f:
            _dec(f['equity'], '$.fake.equity', positive=True)
        if 'classic_stops' in f:
            _req(f['classic_stops'] in ('accept', 'refuse'), '$.fake.classic_stops', 'accept / refuse')
    if 'management' in doc:
        m = doc['management']
        _keys(m, '$.management', {'enabled', 'plan'})
        _req(type(m['enabled']) is bool, '$.management.enabled', 'a boolean')
        _keys(m['plan'], '$.management.plan', set(), PLAN_KEYS)
        for k in PLAN_DECIMALS:
            if m['plan'].get(k) is not None:
                _dec(m['plan'][k], f'$.management.plan.{k}', positive=True, hi=Decimal('20'))
        if 'be_after_tp1' in m['plan']:
            _req(type(m['plan']['be_after_tp1']) is bool, '$.management.plan.be_after_tp1', 'a boolean')
        t_ex = m['plan'].get('time_exit_candles')
        if t_ex is not None:
            _int(t_ex, '$.management.plan.time_exit_candles', 1, 500)
    b = doc['bound']
    _keys(b, '$.bound', {'max_ticks', 'max_orders', 'max_notional_usdt', 'max_wall_s'}, {'settle_ms'})
    _int(b['max_ticks'], '$.bound.max_ticks', 1, 240)
    _int(b['max_orders'], '$.bound.max_orders', 0, 20)
    _dec(b['max_notional_usdt'], '$.bound.max_notional_usdt', positive=True, hi=Decimal('100000'))
    _int(b['max_wall_s'], '$.bound.max_wall_s', 1, 3600)
    if 'settle_ms' in b:
        _int(b['settle_ms'], '$.bound.settle_ms', 0, MAX_SETTLE_MS)
    steps = doc['steps']
    _req(isinstance(steps, list) and 0 < len(steps) <= 50, '$.steps', '1..50 steps')
    ticks = 0
    for i, st in enumerate(steps):
        p = f'$.steps[{i}]'
        _req(isinstance(st, dict) and st.get('op') in STEPS, p + '.op', f'one of {sorted(STEPS)}')
        required, optional = STEPS[st['op']]
        _keys(st, p, required | {'op'}, optional)
        op = st['op']
        if op in ('enter', 'close'):
            ticks += 1
            if 'side' in st:
                _req(st['side'] in ('LONG', 'SHORT'), p + '.side', 'LONG / SHORT')
        elif op == 'tick':
            ticks += _int(st['n'], p + '.n', 1, 120)
        elif op == 'await_exit':
            ticks += _int(st['max_ticks'], p + '.max_ticks', 1, 120)
            if 'fake_gap_pct' in st:
                _dec(st['fake_gap_pct'], p + '.fake_gap_pct', lo=Decimal('-30'), hi=Decimal('30'))
        elif op == 'fault':
            _req(st['on'] in FAULT_ON, p + '.on', f'one of {FAULT_ON}')
            _req(st['kind'] in FAULT_KINDS, p + '.kind', f'one of {FAULT_KINDS}')
            _req(('code' in st) == (st['kind'] == 'refuse'), p + '.code', 'required with (and only with) refuse')
            if 'code' in st:
                _int(st['code'], p + '.code', -9999, -1)
        elif op == 'check':
            _req(st['what'] in CHECKS, p + '.what', ' / '.join(CHECKS))
        elif op == 'move':
            _dec(st['pct'], p + '.pct', lo=Decimal('-30'), hi=Decimal('30'))
            _req(t == ['fake'], p + '.op', 'move is FakeVenue only: the spec must target only fake')
    _req(ticks <= b['max_ticks'], '$.bound.max_ticks', f'the steps need up to {ticks} cycles')
    _expect(doc['expect'], '$.expect')
    if 'expect_testnet' in doc:
        _req('testnet' in t, '$.expect_testnet', 'only for a spec that targets testnet')
        _expect(doc['expect_testnet'], '$.expect_testnet')
    return doc


def expectations(doc, target):
    e = dict(doc['expect'])
    if target == 'testnet':
        e.update(doc.get('expect_testnet', {}))
    return e


def _no_floats(s):
    raise SpecError('$', 'JSON floats are refused (use decimal strings)')


def parse_rspec(text):
    try:
        doc = json.loads(text, parse_float=_no_floats)
    except json.JSONDecodeError as ex:
        raise SpecError('$', f'not JSON ({ex.msg})') from None
    return validate_rspec(doc)


def load_rspec(path):
    with open(path, encoding='utf-8') as fh:
        return parse_rspec(fh.read())


def bundled():
    """The bundled T01-T04 / T09-T12 specs, in file-name order (id order)."""
    return [load_rspec(os.path.join(SPEC_DIR, n)) for n in sorted(os.listdir(SPEC_DIR)) if n.endswith('.json')]


def rspec_digest(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
