"""Load and validate zb-golden/1 cases (stdlib only); canonical JSON and sha256 of a case's `expect` block."""
import glob, hashlib, json, os

from . import CASES_DIR

SCHEMA = 'zb-golden/1'
ADAPTERS = ('legacy_engine', 'legacy_backtest', 'newcore_sim', 'newcore_live_sim')
APPLIES = ('required', 'not_applicable', 'known_divergence', 'pending_adapter')
SIDES = ('long', 'short', 'both', 'n/a')
TFS = {'4h': 14400, '1h': 3600}
SIGNAL_KINDS = {'enter_long': 'le', 'enter_short': 'se', 'exit_long': 'lx', 'exit_short': 'sx'}
EXIT_CODES = ('STOP_HIT', 'TIME_EXIT', 'SIGNAL_EXIT', 'TP_FULL', 'TP_BASKET', 'TP_PARTIAL', 'TP_LADDER', 'LIQUIDATED', 'FLATTEN')
SLOT_KEYS = ('id', 'sides', 'risk', 'share', 'max_pos', 'symbols', 'entry', 'stop', 'trail', 'target', 'tp1', 'ladder', 'runner',
             'dca', 'pyramid', 'time_exit')
TRADE_KEYS = ('sym', 'side', 'i_in', 'i_out', 'exit', 'R', 'pnl')
REQUIRED_TOP = ('schema', 'id', 'title', 'behaviours', 'side', 'tf', 'clock', 'account', 'market', 'path_policy', 'slot',
                'signals', 'faults', 'expect', 'applies_to', 'known_divergences', 'provenance')


class CaseError(ValueError):
    """A fixture that does not follow zb-golden/1. Always a hard failure, never a skip."""


def canonical(obj):
    """Canonical JSON: sorted keys, no whitespace, UTF-8. Numbers keep their JSON spelling (cases use decimal strings)."""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def sha256(obj):
    return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()


def expect_sha(case):
    return sha256(case['expect'])


def _req(cond, case_id, msg):
    if not cond:
        raise CaseError(f'{case_id}: {msg}')


def validate(case, path=None):
    cid = case.get('id', path or '?')
    for k in REQUIRED_TOP:
        _req(k in case, cid, f'missing top-level key {k!r}')
    unknown = set(case) - set(REQUIRED_TOP) - {'costs', 'instruments', 'adapter_options', 'notes'}
    _req(not unknown, cid, f'unknown top-level keys {sorted(unknown)}')
    _req(case['schema'] == SCHEMA, cid, f"schema must be {SCHEMA!r}")
    if path:
        _req(os.path.basename(path) == f"{case['id']}.json", cid, 'file name must be <id>.json')
    _req(case['side'] in SIDES, cid, f"side must be one of {SIDES}")
    _req(case['tf'] in TFS, cid, f"tf must be one of {sorted(TFS)}")
    _req(isinstance(case['behaviours'], list) and case['behaviours'], cid, 'behaviours: non-empty list')
    _req(case['path_policy'] == 'zb-path/1', cid, "path_policy must be 'zb-path/1'")
    _req('start' in case['clock'], cid, 'clock.start required')
    _req(int(case['clock'].get('entry_cycle_delay_s', 15)) >= 15, cid, 'clock.entry_cycle_delay_s must be >= 15 (the replay cycle runs at close + 15 s)')
    _req('equity' in case['account'], cid, 'account.equity required')
    # market
    mk = case['market']
    _req(isinstance(mk, dict) and mk, cid, 'market: at least one symbol')
    n = None
    for s, spec in mk.items():
        b = spec.get('base') or {}
        _req(b.get('kind') == 'flat', cid, f'market.{s}.base.kind: only "flat" is implemented in v1')
        _req(n is None or int(b['n']) == n, cid, 'every symbol must have the same bar count')
        n = int(b['n'])
        for k, row in (spec.get('bars') or {}).items():
            _req(0 <= int(k) < n and len(row) == 4, cid, f'market.{s}.bars[{k}]: [o, h, l, c] inside the series')
            o, h, l, c = map(float, row)
            _req(h >= max(o, c) and l <= min(o, c), cid, f'market.{s}.bars[{k}]: h/l must contain o and c')
        _req((spec.get('atr') or {'kind': 'computed'}).get('kind') == 'computed', cid,
             f'market.{s}.atr: only "computed" in v1 (the legacy engine computes ATR from candles; flat bases give an exact ATR)')
    # slot
    sl = case['slot']
    _req(not (set(sl) - set(SLOT_KEYS)), cid, f'slot: unknown keys {sorted(set(sl) - set(SLOT_KEYS))}')
    _req(sl.get('sides') in ('long', 'short', 'both'), cid, 'slot.sides')
    for s in sl.get('symbols') or list(mk):
        _req(s in mk, cid, f'slot.symbols: {s} has no market')
    # signals
    for g in case['signals']:
        _req(g.get('kind') in SIGNAL_KINDS and g.get('sym') in mk and 0 <= int(g['bar']) < n, cid, f'bad signal {g}')
    _req(case['faults'] == [], cid, 'faults: not implemented in v1 (outage/restart/ambiguity twins come with the fault-capable fake)')
    # expect
    ex = case['expect']
    _req(isinstance(ex.get('trades'), list), cid, 'expect.trades: list')
    for t in ex['trades']:
        _req(set(t) <= set(TRADE_KEYS) and {'sym', 'side', 'i_in', 'i_out', 'exit', 'R'} <= set(t), cid, f'expect trade keys {sorted(t)}')
        _req(t['exit'] in EXIT_CODES and t['side'] in ('LONG', 'SHORT'), cid, f'expect trade codes {t}')
    tol = ex.get('tolerance') or {}
    _req(not (set(tol) - {'R', 'pnl', 'why'}) and (not (set(tol) - {'why'}) or tol.get('why')), cid,
         'expect.tolerance: only R / pnl, and a "why" is mandatory when any tolerance is declared')
    # applies_to / known divergences
    ap = case['applies_to']
    _req(set(ap) == set(ADAPTERS), cid, f'applies_to must name every adapter {ADAPTERS} (a missing adapter is an error, never a skip)')
    for a, v in ap.items():
        st = v if isinstance(v, str) else v.get('status')
        _req(st in APPLIES, cid, f'applies_to.{a}: {v}')
        _req(st == 'required' or (isinstance(v, dict) and v.get('reason')), cid, f'applies_to.{a}: a reason is mandatory for {st}')
    kd = case['known_divergences']
    for d in kd:
        for k in ('adapter', 'ticket', 'finding', 'reason', 'observed'):
            _req(k in d, cid, f'known_divergences: {k!r} missing in {d}')
        _req(status(case, d['adapter']) == 'known_divergence', cid, f"known divergence for {d['adapter']} but applies_to is not known_divergence")
        _req(isinstance(d['observed'], list) and d['observed'], cid, 'known_divergences.observed: the exact mismatch list')
    for a in ADAPTERS:
        if status(case, a) == 'known_divergence':
            _req(sum(d['adapter'] == a for d in kd) == 1, cid, f'applies_to.{a} = known_divergence needs exactly one known_divergences entry')
    return case


def status(case, adapter):
    v = case['applies_to'][adapter]
    return v if isinstance(v, str) else v['status']


def load(path):
    with open(path, encoding='utf-8') as f:
        case = json.load(f)
    return validate(case, path)


def case_paths():
    return sorted(glob.glob(os.path.join(CASES_DIR, '*.json')))


def load_all():
    cases = [load(p) for p in case_paths()]
    ids = [c['id'] for c in cases]
    if len(ids) != len(set(ids)):
        raise CaseError(f'duplicate case ids: {sorted(i for i in ids if ids.count(i) > 1)}')
    return cases
