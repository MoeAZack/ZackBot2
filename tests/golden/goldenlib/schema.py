"""Load and validate zb-golden/1 cases (stdlib only); canonical JSON and the case's protected CONTRACT hash.

contract_sha(case) = sha256(canonical(case minus DESCRIPTIVE)): the expectation (`expect`, incl. per-trade tolerances), which
adapters it binds (`applies_to`), the recorded legacy behaviour (`known_divergences`) AND every causal input (market, signals,
slot, costs, clock, account, faults, adapter_options, ...) AND the `behaviours` ID list (registry IDs that drive the per-commit
tier gate; Codex golden r3 residual ruling point 1). Only the descriptive text (title, provenance, notes) is outside it. Any
new top-level key is protected automatically (validate() rejects keys it does not know). Every known divergence carries a
stable `divergence_id` (point 2): tier coverage keys on adapter + divergence_id; ticket / finding must equal the ID's entry in
the append-only registry DIVERGENCES.json (Codex golden r5 ruling #2; still inside the hash)."""
import decimal, glob, hashlib, json, math, numbers, os, re

from . import CASES_DIR, registry

SCHEMA = 'zb-golden/1'
ADAPTERS = ('legacy_engine', 'legacy_backtest', 'newcore_sim', 'newcore_live_sim')
APPLIES = ('required', 'not_applicable', 'known_divergence', 'pending_adapter')
SIDES = ('long', 'short', 'both', 'n/a')
TFS = {'4h': 14400, '1h': 3600}
SIGNAL_KINDS = {'enter_long': 'le', 'enter_short': 'se', 'exit_long': 'lx', 'exit_short': 'sx'}
# Exit-code REGISTRY (machine codes; any free text next to them is display only). Append-only within a schema version: a code
# is never reused for another meaning and never deleted - it is deprecated (DEPRECATED_EXIT_CODES: code -> what replaces it)
# and stays valid. test_vocabulary.py pins the order, so an insertion, removal or rename fails.
EXIT_CODE_MEANING = {
    'STOP_HIT': 'the protective stop order RESTING ON THE EXCHANGE filled (exchange stop-market; at the stop, or at the open '
                'when the candle gapped through it)',
    'TIME_EXIT': 'bot market close: the holding-time limit was reached at a candle-close decision',
    'SIGNAL_EXIT': 'bot market close: the strategy exit signal at a candle-close decision',
    'TP_FULL': 'bot market close of the whole position at the take-profit level',
    'TP_BASKET': 'bot market close of the whole DCA basket at the basket target',
    'TP_PARTIAL': 'the first partial take-profit (tp1) closed the rest of the position',
    'TP_LADDER': 'a take-profit ladder level closed the rest of the position',
    'LIQUIDATED': 'the exchange liquidated the position',
    'FLATTEN': 'bot market close of every position on an owner / safety flatten command',
    # appended by AUD-08 (vocabulary alignment with the legacy engine journal)
    'STOP_CROSSED': 'BOT market close because the protective level it had just computed (trail / breakeven / ratchet) was '
                    'already crossed by the price, so no stop order could rest there - a taker close at the mark, never an '
                    'exchange stop fill',
    'RESYNC': 'the bot found the position gone (or smaller) on the exchange without a recorded cause and booked what left at '
              'the market price',
    'STOP_FAILED': 'the protective stop could not be placed right after the entry, so the bot closed the position again at market',
    'BASKET_TP_PART': 'the runner part of a DCA basket target (dca_frac) closed the rest of the position',
}
EXIT_CODES = tuple(EXIT_CODE_MEANING)
DEPRECATED_EXIT_CODES = {}                          # code -> replacement code; a deprecated code stays in EXIT_CODES
# Behaviour REGISTRY (Codex golden r3 residual ruling, point 1): `behaviours` is a compact ID vocabulary that drives a structural
# per-commit gate (tiers.classes: core keeps one case per behaviour), so it is a versioned registry with ONE meaning per ID,
# not free text. validate() rejects an unknown or duplicated ID. Append-only within BEHAVIOURS_VERSION: an ID is never reused
# for another meaning, renamed, reordered or deleted - it is deprecated (DEPRECATED_BEHAVIOURS: id -> replacement) and stays
# valid. The registry is DATA (tests/golden/BEHAVIOURS.json, Codex golden r5 ruling #1): test_ledger requires it to extend the
# base tree's copy (registry.extends_behaviours), so a meaning edited together with a local pin fails against the base.
BEHAVIOURS_VERSION, BEHAVIOUR_MEANING, DEPRECATED_BEHAVIOURS = registry.load_behaviours()
BEHAVIOURS = tuple(BEHAVIOUR_MEANING)
# AUD-08 fault vocabulary (smallest slices; the fault-capable fake comes with NC-03 / NC-08). Times are integer UTC ms.
FAULT_KINDS = {
    'exchange_outage': ('from_ms', 'to_ms'),        # the bot can neither read nor send; orders resting ON the exchange still work
    'restart': ('at_ms', 'down_ms'),                # the bot process stops at at_ms and starts from its persisted state down_ms later
    'lost_response': ('order', 'nth', 'truth'),     # the nth such order reaches the exchange (truth) but its answer is lost
}
FAULT_ORDERS = ('entry',)
FAULT_TRUTH = ('filled', 'not_filled')
INSTRUMENT_KEYS = ('step', 'min_qty', 'min_notional', 'tick')      # exchange filters (feasibility.size_check vocabulary)
SLOT_KEYS = ('id', 'sides', 'risk', 'share', 'max_pos', 'symbols', 'entry', 'stop', 'trail', 'target', 'tp1', 'ladder', 'runner',
             'dca', 'pyramid', 'time_exit')
TRADE_KEYS = ('sym', 'side', 'i_in', 'i_out', 'exit', 'R', 'pnl', 'tol')
TOL_KEYS = ('R', 'pnl', 'adapters', 'why')          # per-trade tolerance: a stated reason and the adapters it applies to
FINAL_KEYS = ('lots',)                              # expect.final keys an adapter may declare (adapters.CAPS); others = typo
COST_KEYS = ('model', 'taker_fee', 'slip', 'funding_per_bar')
DESCRIPTIVE = ('title', 'provenance', 'notes')     # the only keys outside the contract hash (behaviours is IN it since AUD-08 vocab)
CORR_ID = re.compile(r'CORR-\d{4}')
# Stable identity of a recorded legacy defect (Codex golden r3 residual ruling point 2), e.g. AUD07-C11, AUD07-TP-GAP-OPEN.
# Unique per adapter within a case; the same ID on several cases (or on both adapters) names the same recorded defect.
# Every ID is an ACTIVE entry of the append-only registry tests/golden/DIVERGENCES.json (Codex golden r5 ruling #2) and a case
# carries that entry's exact ticket / finding on an adapter inside its scope; test_ledger anchors the registry in the base.
DIVERGENCE_ID = registry.DIVERGENCE_ID
DIVERGENCE_REGISTRY = {e['id']: e for e in registry.load_divergences(ADAPTERS)}
KD_KEYS = ('adapter', 'divergence_id', 'ticket', 'finding', 'reason', 'observed', 'correction')
TOL_MAX = 0.001                                      # largest per-trade tolerance (path / rounding artefacts only)
REQUIRED_TOP = ('schema', 'id', 'title', 'behaviours', 'side', 'tf', 'clock', 'account', 'market', 'path_policy', 'slot',
                'signals', 'faults', 'expect', 'applies_to', 'known_divergences', 'provenance')


class CaseError(ValueError):
    """A fixture that does not follow zb-golden/1. Always a hard failure, never a skip."""


class NumberError(ValueError):
    """A non-finite, boolean or malformed number where a finite decimal is required (P1 on b01d439: NaN made
    abs(expected - actual) > tol false, so a NaN expectation, actual or resolution passed every comparison)."""


DECIMAL = re.compile(r'-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?')


def finite(x, what):
    """float(x) for a finite, non-boolean number: an int / float (numpy floats included), or a JSON decimal string. NaN,
    +-Infinity, bool, None, '1_0', ' 1', 'nan', 'inf' and overflow to inf ('1e999') all raise NumberError."""
    if isinstance(x, bool) or not (isinstance(x, numbers.Real) or isinstance(x, str)):
        raise NumberError(f'{what}: {x!r} is not a finite number')
    if isinstance(x, str) and not DECIMAL.fullmatch(x):
        raise NumberError(f'{what}: {x!r} is not a decimal number')
    v = float(x)
    if not math.isfinite(v):
        raise NumberError(f'{what}: {x!r} is not finite')
    return v


def _no_constant(name):
    raise ValueError(f'JSON constant {name} is not allowed (NaN / Infinity are never a golden value)')


def _finite_float(s):
    v = float(s)
    if not math.isfinite(v):
        raise ValueError(f'JSON number {s} overflows to {v}')
    return v


def strict_loads(text):
    """json.loads that REJECTS NaN / Infinity / -Infinity and numbers that overflow to inf (never canonicalises them)."""
    return json.loads(text, parse_constant=_no_constant, parse_float=_finite_float)


def canonical(obj):
    """Canonical JSON: sorted keys, no whitespace, UTF-8. Numbers keep their JSON spelling (cases use decimal strings)."""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def sha256(obj):
    return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()


def contract_sha(case):
    """The protected contract of a case: everything except DESCRIPTIVE (see the module docstring)."""
    return sha256({k: v for k, v in case.items() if k not in DESCRIPTIVE})


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
    _validate_behaviours(case, cid)
    _req(case['path_policy'] == 'zb-path/1', cid, "path_policy must be 'zb-path/1'")
    _req(isinstance(case['clock'], dict) and 'start' in case['clock'], cid, 'clock.start required')
    _req(_is_ms(case['clock']['start']) and case['clock']['start'] >= 0, cid,
         f"clock.start={case['clock']['start']!r}: integer UTC milliseconds (an ISO string, float, bool or negative value is "
         'rejected; AUD-08 golden clock-ms)')
    _req(int(case['clock'].get('entry_cycle_delay_s', 15)) >= 15, cid, 'clock.entry_cycle_delay_s must be >= 15 (the replay cycle runs at close + 15 s)')
    _req('equity' in case['account'], cid, 'account.equity required')
    co = case.get('costs') or {}
    _req(not (set(co) - set(COST_KEYS)), cid, f'costs: unknown keys {sorted(set(co) - set(COST_KEYS))} (a misspelt cost is never ignored)')
    _req(co.get('model', 'legacy') == 'legacy', cid, "costs.model: only 'legacy' in v1")
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
    _validate_faults(case, cid, n)
    _validate_instruments(case, cid)
    _validate_dca(case, cid)
    # expect
    ex = case['expect']
    _req(isinstance(ex.get('trades'), list), cid, 'expect.trades: list')
    _req(not (set(ex) - {'trades', 'final'}), cid,
         f"expect: unknown keys {sorted(set(ex) - {'trades', 'final'})} (a case-wide tolerance is not allowed: give a per-trade "
         "`tol` with the adapters it applies to and a reason)")
    for t in ex['trades']:
        _req(set(t) <= set(TRADE_KEYS) and {'sym', 'side', 'i_in', 'i_out', 'exit', 'R'} <= set(t), cid, f'expect trade keys {sorted(t)}')
        _req(t['exit'] in EXIT_CODES and t['side'] in ('LONG', 'SHORT'), cid, f'expect trade codes {t}')
        for k in ('R', 'pnl'):
            if k in t:
                _req(_is_finite(t[k]), cid, f'expect trade {k}={t[k]!r}: a finite decimal (never NaN / Infinity / bool)')
        tol = t.get('tol')
        if tol is not None:
            _req(isinstance(tol, dict) and not (set(tol) - set(TOL_KEYS)) and (set(tol) & {'R', 'pnl'}) and tol.get('why'), cid,
                 f'expect trade tol {tol}: R and/or pnl, plus a mandatory "why"')
            _req(isinstance(tol.get('adapters'), list) and tol['adapters'] and set(tol['adapters']) <= set(ADAPTERS), cid,
                 f'expect trade tol {tol}: "adapters" = the adapters the tolerance applies to (never implicit)')
            _req(all(_is_finite(tol[k]) and 0 <= float(tol[k]) <= TOL_MAX for k in ('R', 'pnl') if k in tol), cid,
                 f'expect trade tol {tol}: a finite number in [0, {TOL_MAX}] (a path / rounding artefact, never a behaviour difference)')
    fin = ex.get('final') or {}
    _req(isinstance(fin, dict) and not (set(fin) - set(FINAL_KEYS)), cid,
         f'expect.final: unknown keys {sorted(set(fin) - set(FINAL_KEYS))} (known: {FINAL_KEYS})')
    # applies_to / known divergences
    ap = case['applies_to']
    _req(set(ap) == set(ADAPTERS), cid, f'applies_to must name every adapter {ADAPTERS} (a missing adapter is an error, never a skip)')
    for a, v in ap.items():
        st = v if isinstance(v, str) else v.get('status')
        _req(st in APPLIES, cid, f'applies_to.{a}: {v}')
        _req(st == 'required' or (isinstance(v, dict) and v.get('reason')), cid, f'applies_to.{a}: a reason is mandatory for {st}')
    kd = case['known_divergences']
    for d in kd:
        for k in KD_KEYS:
            _req(k in d, cid, f'known_divergences: {k!r} missing in {d}')
        _req(isinstance(d['divergence_id'], str) and DIVERGENCE_ID.fullmatch(d['divergence_id']), cid,
             f"known_divergences: divergence_id {d['divergence_id']!r} must match {DIVERGENCE_ID.pattern} (e.g. AUD07-C11)")
        _validate_divergence_ref(d, cid)
        _req(isinstance(d['correction'], str) and CORR_ID.fullmatch(d['correction']), cid,
             f"known_divergences: 'correction' must name the ledger entry that recorded it (CORR-nnnn), got {d['correction']!r}")
        _req(status(case, d['adapter']) == 'known_divergence', cid, f"known divergence for {d['adapter']} but applies_to is not known_divergence")
        _req(isinstance(d['observed'], list) and d['observed'], cid, 'known_divergences.observed: the exact mismatch list')
        for o in d['observed']:
            f = str(o.get('path', '')).rsplit('.', 1)[-1] if isinstance(o, dict) else None
            _req(isinstance(o, dict) and f is not None, cid, f'known_divergences.observed: {o!r} is not a mismatch record')
            if f in ('R', 'pnl'):
                _req(all(o.get(k) is None or _is_finite(o[k]) for k in ('expected', 'actual')), cid,
                     f'known_divergences.observed {o}: R / pnl values must be finite numbers')
    pairs = [(d['adapter'], d['divergence_id']) for d in kd]
    dup = sorted({p for p in pairs if pairs.count(p) > 1})
    _req(not dup, cid, f'known_divergences: divergence_id listed more than once on one adapter {dup}')
    for a in ADAPTERS:
        if status(case, a) == 'known_divergence':
            _req(sum(d['adapter'] == a for d in kd) == 1, cid, f'applies_to.{a} = known_divergence needs exactly one known_divergences entry')
    return case


def clock_ms(case):
    """clock.start: integer UTC milliseconds (AUD-08 golden clock-ms; an ISO string is never parsed). Anything else is a
    CaseError, so no consumer (market.build, the fault window check) can reinterpret it."""
    v = case['clock']['start']
    if not (_is_ms(v) and v >= 0):
        raise CaseError(f"{case.get('id', '?')}: clock.start={v!r} is not integer UTC milliseconds")
    return v


def _is_ms(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _validate_behaviours(case, cid):
    """`behaviours`: a non-empty list of registry IDs (BEHAVIOUR_MEANING), each at most once."""
    b = case['behaviours']
    _req(isinstance(b, list) and b and all(isinstance(x, str) for x in b), cid, 'behaviours: a non-empty list of behaviour IDs')
    unknown = sorted(set(b) - set(BEHAVIOURS))
    _req(not unknown, cid, f'behaviours: unknown IDs {unknown} (registry {BEHAVIOURS_VERSION}: tests/golden/BEHAVIOURS.json; a '
                           'new behaviour is appended there with its one meaning)')
    dup = sorted({x for x in b if b.count(x) > 1})
    _req(not dup, cid, f'behaviours: listed more than once {dup}')


def _validate_divergence_ref(d, cid, reg=None):
    """A known divergence names an ACTIVE DIVERGENCES.json entry, on an adapter in its scope, with its exact ticket / finding
    (one meaning per ID: the registry, not the case, owns what the ID means)."""
    reg = DIVERGENCE_REGISTRY if reg is None else reg
    i = d['divergence_id']
    e = reg.get(i)
    _req(e is not None, cid, f'known_divergences: divergence_id {i!r} is not registered in tests/golden/DIVERGENCES.json (append '
                             'it there with its adapters, ticket and finding)')
    _req(e['status'] == 'active', cid, f'known_divergences: divergence_id {i!r} is retired - a retired ID is a tombstone and is '
                                       'never used again (append a new ID)')
    _req(d['adapter'] in e['adapters'], cid, f"known_divergences: divergence_id {i!r} is not registered for {d['adapter']} "
                                             f"(scope {e['adapters']})")
    _req((d['ticket'], d['finding']) == (e['ticket'], e['finding']), cid,
         f"known_divergences: divergence_id {i!r} carries ticket/finding {(d['ticket'], d['finding'])} but the registry binds it "
         f"to {(e['ticket'], e['finding'])} - an ID keeps its one meaning (append a new ID for another defect)")


def _validate_faults(case, cid, n):
    """AUD-08 fault vocabulary (FAULT_KINDS). Every time is an integer UTC millisecond inside the case's market; every fault
    records its `seed` explicitly (None = fully deterministic, nothing random)."""
    fl = case['faults']
    _req(isinstance(fl, list), cid, 'faults: a list')
    t0 = clock_ms(case)
    t1 = t0 + n * TFS[case['tf']] * 1000
    for k, f in enumerate(fl):
        _req(isinstance(f, dict) and f.get('kind') in FAULT_KINDS, cid, f'faults[{k}]: kind must be one of {sorted(FAULT_KINDS)}')
        want = set(FAULT_KINDS[f['kind']]) | {'kind', 'seed'}
        _req(set(f) == want, cid, f"faults[{k}] ({f['kind']}): exactly the keys {sorted(want)}, got {sorted(f)}")
        _req(f['seed'] is None or _is_ms(f['seed']), cid, f'faults[{k}].seed: an integer, or null for a deterministic fault')
        for key in FAULT_KINDS[f['kind']]:
            if key.endswith('_ms'):
                _req(_is_ms(f[key]) and f[key] >= 0, cid, f'faults[{k}].{key}: integer UTC milliseconds (never a string / float)')
        if f['kind'] == 'exchange_outage':
            _req(t0 <= f['from_ms'] < f['to_ms'] <= t1, cid, f'faults[{k}]: t0 <= from_ms < to_ms <= end of the market')
        elif f['kind'] == 'restart':
            _req(t0 <= f['at_ms'] and f['at_ms'] + f['down_ms'] <= t1, cid, f'faults[{k}]: the restart lies inside the market')
        elif f['kind'] == 'lost_response':
            _req(f['order'] in FAULT_ORDERS and f['truth'] in FAULT_TRUTH and _is_ms(f['nth']) and f['nth'] >= 1, cid,
                 f'faults[{k}]: order in {FAULT_ORDERS}, truth in {FAULT_TRUTH}, nth >= 1')


def _validate_instruments(case, cid):
    ins = case.get('instruments')
    if ins is None:
        return
    _req(isinstance(ins, dict) and ins, cid, 'instruments: {symbol: {step, min_qty, min_notional, tick}}')
    for s, r in ins.items():
        _req(s in case['market'], cid, f'instruments.{s}: no market for it')
        _req(isinstance(r, dict) and set(r) == set(INSTRUMENT_KEYS), cid, f'instruments.{s}: exactly {INSTRUMENT_KEYS}')
        for k in INSTRUMENT_KEYS:
            _req(_is_finite(r[k]) and float(r[k]) >= 0 and (float(r[k]) > 0 or k in ('min_qty', 'min_notional')), cid,
                 f'instruments.{s}.{k}={r[k]!r}: a finite decimal (step / tick > 0)')


DCA_KEYS = ('n', 'step_atr', 'scale', 'tp_atr', 'stop_atr')
DCA_MAX_N = 10                                       # legacy dca_dip ladders use 1..3 levels; NEWCORE caps adds at one
DCA_MAX_MULT = 100                                   # an ATR multiple / scale above this is a typo, never a strategy
DECIMAL_TEXT = re.compile(r'(0|[1-9][0-9]*)(\.[0-9]+)?')


def _validate_dca(case, cid):
    """slot.dca (Cowork review of #40): exactly DCA_KEYS; n an int (never bool / float / str) in 1..DCA_MAX_N; step_atr,
    scale, tp_atr and stop_atr plain positive decimal STRINGS (no float, bool, exponent, sign, NaN or inf) <= DCA_MAX_MULT."""
    d = case['slot'].get('dca')
    if d is None:
        return
    _req(isinstance(d, dict) and set(d) == set(DCA_KEYS), cid, f'slot.dca: exactly the keys {DCA_KEYS}')
    n = d['n']
    _req(type(n) is int and 1 <= n <= DCA_MAX_N, cid,
         f'slot.dca.n={n!r}: an int in 1..{DCA_MAX_N} (never a bool, float or string)')
    for k in DCA_KEYS[1:]:
        v = d[k]
        _req(type(v) is str and DECIMAL_TEXT.fullmatch(v) is not None, cid,
             f'slot.dca.{k}={v!r}: a plain decimal string (never a float, bool, exponent, sign, NaN or inf)')
        _req(0 < decimal.Decimal(v) <= DCA_MAX_MULT, cid, f'slot.dca.{k}={v!r}: must be > 0 and <= {DCA_MAX_MULT}')


def _is_finite(x):
    try:
        finite(x, 'x')
        return True
    except NumberError:
        return False


def status(case, adapter):
    v = case['applies_to'][adapter]
    return v if isinstance(v, str) else v['status']


def load(path):
    """utf-8-sig: a UTF-8 BOM (Windows editors) is accepted - the hash is over the parsed content, so it cannot hide a change.
    NaN / Infinity are rejected at parse time (strict_loads). Anything unreadable is a CaseError naming the file."""
    try:
        with open(path, encoding='utf-8-sig') as f:
            case = strict_loads(f.read())
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise CaseError(f'{os.path.basename(path)}: not a readable UTF-8 JSON case ({type(e).__name__}: {e})') from None
    if not isinstance(case, dict):
        raise CaseError(f'{os.path.basename(path)}: a case is a JSON object')
    return validate(case, path)


def case_paths():
    return sorted(glob.glob(os.path.join(CASES_DIR, '*.json')))


def load_all():
    cases = [load(p) for p in case_paths()]
    ids = [c['id'] for c in cases]
    if len(ids) != len(set(ids)):
        raise CaseError(f'duplicate case ids: {sorted(i for i in ids if ids.count(i) > 1)}')
    return cases
