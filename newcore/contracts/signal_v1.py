"""Semantic validators for signal_intent_v1, signal_result_v1, candidate_score_v1 and candidate_evaluation_v1.

Each check_* function is STANDALONE: it re-checks every field, type and closed field set itself (PR #50 r2 - nothing it
accepts or encode_result emits depends on the JSON Schema having run), and raises InvalidRecord (path + bounded message;
payload text and unknown field names are never echoed - base.show) on the first broken rule. Promotion / use goes
through check_signal_intent / check_candidate_score, whose trusted clock `now_ms` is mandatory; check_*_shape are the
clock-free variants for audit / replay / evaluation records and never authorize use.

- strict tokens: parse_strict refuses duplicate keys, NaN / Infinity and every binary float token, so 1700000000000.0 or
  1e3 can never pass as an integer timestamp (JSON Schema's "integer" accepts 1.0);
- time: generated / as-of strictly before expiry, a bounded TTL, order expiry inside the signal's life, universe as-of
  not after the candidate, proof-of-prior anchored before the committed evaluated window, candidates valid when it
  opens; TTLs are maximum ceilings (1 h signal / 24 h candidate), not defaults - sources choose shorter expiries;
- hashes: never all-zero, and every hash the evaluation can recompute (universe, weights, benchmark set, commitment) is
  recomputed and compared;
- cells: unique candidate cells (strategy, version, symbol, side), non-overlapping exclusions, never scored and excluded;
- units: per-unit numeric bounds, level method / unit compatibility, one unit across a trail;
- reason codes: membership in CONTRACT_REASONS or the NEWCORE gate registry (newcore.domain.reasons), scoped per
  result status / candidate / stand-down / exclusion.

Rulings (PR #50, Codex triage): no fixed READY score floor - READY is computed server-side from the named versioned
risk/activity profile; STAND_DOWN may carry a high opportunity score but must give a veto reason; evidence immaturity
lowers `automation_ceiling` and never blocks research / observe / dry-run / testnet / manual modes.

Bounds here are contract plausibility limits (a typo or hostile value never reaches the engine), NOT risk limits: the
selected profile and the risk gateway still decide what is allowed.
"""
from __future__ import annotations

import collections
import functools
import hashlib
import inspect
import json
import re
from decimal import Decimal

from ..domain.base import MAX_TS_MS, MIN_TS_MS, req
from ..domain.base import show as _domain_show
from ..domain.errors import InvalidRecord
from ..domain.incident import check_detail
from ..domain.reasons import GATE_NAMESPACES, ReasonCode

MAX_PAYLOAD_BYTES = 65_536
MAX_SIGNAL_TTL_MS = 3_600_000              # CEILING, not a default: a signal lives at most 1 h from generation
MAX_CANDIDATE_TTL_MS = 86_400_000          # CEILING, not a default: a candidate lives at most 24 h from as-of
MAX_WINDOW_MS = 366 * 86_400_000         # a committed forward window spans at most 366 days
CLOCK_SKEW_MS = 5_000                      # tolerated sender clock lead against the trusted now_ms
ZERO_SHA256 = '0' * 64
SHA256_RE = re.compile(r'[0-9a-f]{64}')

UNIT_MAX = {'price': Decimal(10) ** 12, 'percent': Decimal(100), 'ticks': Decimal(1_000_000), 'atr': Decimal(100)}
RISK_PERCENT_MAX = Decimal(100)
REDUCE_PERCENT_MAX = Decimal(100)
MAX_RISK_USD_MAX = Decimal(10) ** 9
SCORE_DIMENSIONS = ('opportunity', 'entry_quality', 'hold_quality', 'regime_fit', 'evidence_readiness',
                    'operational_readiness')

# Automation ceiling per evidence class: the highest AUTOMATIC stage a candidate of that class may feed. Lower stages
# (and manual mainnet, a separate explicit mode) are never blocked by evidence immaturity.
STAGES = ('observe', 'dry_run', 'testnet', 'auto_mainnet')
CEILING_BY_EVIDENCE = {'backtest': 'testnet', 'paper': 'testnet', 'testnet': 'auto_mainnet',
                       'live_forward': 'auto_mainnet'}

# Contract reason registry (append-only within v1): code -> (scopes, meaning). Scopes: 'result:<status>', 'candidate',
# 'stand_down' (a veto that explains STAND_DOWN), 'exclusion'. NEWCORE gate codes (newcore.domain.reasons, gate
# namespaces, not deprecated) are additionally valid for result:rejected, result:held, candidate, stand_down, exclusion.
CONTRACT_REASONS = {
    'ingress.accepted': (('result:accepted',), 'every gate passed; an order intent was created'),
    'ingress.dry_run_validated': (('result:validated',), 'dry run: every gate passed; nothing was sent'),
    'ingress.duplicate_replay': (('result:duplicate',), 'byte-equivalent retry; the original result is returned'),
    'ingress.schema_invalid': (('result:rejected',), 'the payload failed the JSON Schema'),
    'ingress.semantic_invalid': (('result:rejected',), 'the payload failed a cross-field / time / unit rule'),
    'ingress.expired': (('result:rejected',), 'the signal or its order expired before intake'),
    'ingress.not_yet_valid': (('result:rejected',), 'generated in the future beyond the tolerated clock skew'),
    'ingress.idempotency_conflict': (('result:rejected',), 'the signal_id was reused with a different payload'),
    'ingress.unknown_account': (('result:rejected',), 'the account alias is not allowlisted'),
    'ingress.unsupported_capability': (('result:rejected', 'exclusion'), 'the venue cannot do what is asked'),
    'ingress.unknown_target': (('result:rejected',), 'target_ref names no owned position / lot / intent'),
    'ingress.ownership_unresolved': (('result:held',), 'ownership is unknown; held for reconciliation'),
    'ingress.reconciliation_pending': (('result:held',), 'reconciliation is running; held until it settles'),
    'candidate.profile_ready': (('candidate',), 'the selected profile marks this candidate eligible now'),
    'candidate.near_threshold': (('candidate',), 'close to the profile thresholds but not yet eligible'),
    'candidate.no_entry_trigger': (('candidate',), 'valid idea, no entry trigger yet'),
    'candidate.extended': (('candidate',), 'the move is stretched; do not chase'),
    'candidate.structure_break': (('candidate',), 'structure is failing for the proposed side'),
    'stand_down.regime_veto': (('candidate', 'stand_down'), 'the regime vetoes entries for this cell'),
    'stand_down.operational_veto': (('candidate', 'stand_down'), 'an operational fact vetoes entries'),
    'stand_down.event_window': (('candidate', 'stand_down'), 'a high-impact event window is active'),
    'stand_down.cooldown': (('candidate', 'stand_down'), 'the profile cooldown is active'),
    'stand_down.profile_limit': (('candidate', 'stand_down'), 'a profile concurrency / frequency limit is reached'),
    'exclusion.no_market_data': (('exclusion',), 'no usable market data for the member'),
    'exclusion.stale_data': (('exclusion',), 'market data older than the freshness limit'),
    'exclusion.delisted': (('exclusion',), 'the member is delisted at as-of (kept, never dropped)'),
    'exclusion.halted': (('exclusion',), 'the member is halted at as-of'),
    'exclusion.insufficient_history': (('exclusion',), 'not enough history for the strategy'),
    'exclusion.liquidity_floor': (('exclusion',), 'below the liquidity / capacity floor'),
    'exclusion.strategy_not_applicable': (('exclusion',), 'the strategy does not apply to the member'),
}
GATE_SCOPES = frozenset({'result:rejected', 'result:held', 'candidate', 'stand_down', 'exclusion'})
REASON_SCOPES = frozenset(s for scopes, _ in CONTRACT_REASONS.values() for s in scopes)


# Display-text boundary (on top of the domain detail rule: printable ASCII, <= 160, no 20+ key-character run). Refused,
# never scrubbed. Heuristic and bounded, not a guarantee: detail must still be built from fixed templates filled with
# non-secret values. Refused: credential words; query-string / assignment shapes ('=', '?', '&'); and any 8+ character
# alphanumeric run mixing a digit with a lowercase letter (password / token shaped - symbols are upper case, numbers and
# units are separated by a space).
CREDENTIAL_WORDS = re.compile(r'authori[sz]ation|\bauth\b|bearer|api[\s_-]?key|secret|passw(or)?d|\bpwd\b|signature|'
                              r'\bsig\b|token|private[\s_-]?key|cookie|session[\s_-]?id|credential|\bjwt\b', re.I)
ASSIGNMENT_CHARS = re.compile(r'[=?&]')
MIXED_TOKEN = re.compile(r'[A-Za-z0-9]{8,}')

# Closed field sets (the JSON Schema property sets; a drift test pins them). The semantic layer refuses an unknown field
# at every level by itself, so nothing it accepts - and nothing encode_result emits - depends on the schema having run.
INTENT_KEYS = frozenset({'contract_version', 'signal_id', 'trade_family_id', 'source', 'account', 'action', 'symbol',
                         'side', 'strategy_id', 'strategy_version', 'generated_at_ms', 'expires_at_ms', 'dry_run',
                         'order', 'risk', 'stop', 'target', 'trail', 'reduce_percent', 'target_ref'})
ORDER_KEYS = frozenset({'type', 'price', 'expires_at_ms'})
RISK_KEYS = frozenset({'risk_percent', 'max_risk_usd'})
LEVEL_KEYS = frozenset({'method', 'distance', 'lookback_bars'})
VALUE_KEYS = frozenset({'unit', 'value'})
TRAIL_KEYS = frozenset({'trigger', 'distance', 'update_step'})
RESULT_KEYS = frozenset({'contract_version', 'result_id', 'signal_id', 'status', 'reason_code', 'retryable',
                         'recorded_at_ms', 'decision_id', 'original_result_id', 'original_status', 'detail'})
CANDIDATE_KEYS = frozenset({'contract_version', 'candidate_id', 'evidence_class', 'as_of_ms', 'expires_at_ms',
                            'universe_id', 'universe_snapshot_sha256', 'universe_as_of_ms', 'scoring_version',
                            'weights_sha256', 'profile_id', 'profile_version', 'automation_ceiling', 'strategy_id',
                            'strategy_version', 'symbol', 'side', 'entry_state', 'scores', 'reason_codes',
                            'source_manifest_sha256', 'structural_stop'})
STOP_KEYS = VALUE_KEYS
EVALUATION_KEYS = frozenset({'contract_version', 'evaluation_id', 'evidence_class', 'as_of_ms', 'universe',
                             'universe_snapshot_sha256', 'scoring', 'weights_sha256', 'profile_id', 'profile_version',
                             'candidate_ids', 'exclusions', 'exclusion_counts', 'benchmarks', 'benchmark_set_sha256',
                             'sealed', 'prior_commitment'})
UNIVERSE_KEYS = frozenset({'universe_id', 'as_of_ms', 'members'})
MEMBER_KEYS = frozenset({'symbol', 'status'})
SCORING_KEYS = frozenset({'scoring_version', 'weights'})
EXCLUSION_KEYS = frozenset({'symbol', 'strategy_id', 'side', 'reason_code'})
BENCHMARK_KEYS = frozenset({'benchmark_id', 'kind'})
PRIOR_KEYS = frozenset({'commitment_sha256', 'anchor_kind', 'anchor_ref', 'anchored_at_ms', 'window_start_ms',
                        'window_end_ms', 'benchmark_set_sha256'})
# What the proof-of-prior digest covers from prior_commitment: everything except the anchor facts, which only exist
# after the digest (the digest itself, where / when / how it was anchored).
COMMITTED_PRIOR_FIELDS = frozenset({'window_start_ms', 'window_end_ms', 'benchmark_set_sha256'})

# Per action: (required, forbidden, allowed target_ref prefixes)
ACTIONS = {
    'enter': ({'side', 'order', 'risk', 'stop'}, {'target_ref', 'reduce_percent'}, ()),
    'reduce': ({'target_ref', 'reduce_percent'}, {'side', 'order', 'risk', 'stop', 'target', 'trail'}, ('pos', 'lot')),
    'close': ({'target_ref'}, {'side', 'order', 'risk', 'stop', 'target', 'trail', 'reduce_percent', 'trade_family_id'},
              ('pos', 'lot')),
    'modify': ({'target_ref'}, {'side', 'order', 'risk', 'reduce_percent'}, ('pos', 'lot', 'int')),
}
SOURCES = frozenset({'internal_strategy', 'tradingview', 'manual'})
SIDES = frozenset({'LONG', 'SHORT'})
METHODS = frozenset({'fixed', 'atr', 'recent_high_low'})
ORDER_TYPES = frozenset({'market', 'limit_post_only', 'stop_market'})
STATUSES = frozenset({'validated', 'accepted', 'rejected', 'duplicate', 'held'})
EVIDENCE_CLASSES = frozenset(CEILING_BY_EVIDENCE)
ENTRY_STATES = frozenset({'READY', 'SETUP', 'WAIT', 'EXTENDED', 'BREAKING', 'STAND_DOWN'})
MEMBER_STATUSES = frozenset({'listed', 'halted', 'delisted'})
BENCHMARK_KINDS = frozenset({'flat_cash', 'buy_and_hold', 'random_entry_same_exits', 'equal_weight_universe'})
ANCHOR_KINDS = frozenset({'rfc3161_timestamp', 'public_append_only_log', 'signed_public_git_tag'})

DECIMAL_RE = re.compile(r'0\.(?=[0-9]*[1-9])[0-9]{1,18}|[1-9][0-9]{0,17}(\.[0-9]{1,18})?')
WEIGHT_RE = re.compile(r'0(\.[0-9]{1,6})?|1(\.0{1,6})?')
SYMBOL_RE = re.compile(r'[A-Z0-9]{2,30}')
LABEL_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}')
VERSION_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,31}')
ACCOUNT_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,31}')
REASON_RE = re.compile(r'[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*')
ANCHOR_REF_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/#-]{0,127}')
MAX_EXCLUSIONS = 20_000


def show(v):
    """A rejected value in an error message (PR #50 r2 self-probe): scalars via the domain rule (text is never echoed,
    only type / length / hash prefix); a container is summarized by type and size - its repr would echo the secrets
    inside it."""
    if v is None or type(v) in (bool, int, str, bytes):
        return _domain_show(v)
    if type(v) in (dict, list, tuple):
        return f'<{type(v).__name__} len={len(v)}>'
    return f'<{type(v).__name__}>'


# ----------------------------------------------------------------------------------------------------------- helpers
def _strict(fn):
    """Fails closed as InvalidRecord (never KeyError / TypeError) on a malformed payload. A wrong CALL - for example a
    missing trusted clock - is still a TypeError: the signature is bound before the guarded body runs."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def run(obj, *a, **kw):
        sig.bind(obj, *a, **kw)
        req(type(obj) is dict, fn.__name__, 'payload is not a JSON object')
        try:
            return fn(obj, *a, **kw)
        except (KeyError, TypeError, AttributeError, IndexError, ArithmeticError) as ex:
            raise InvalidRecord(fn.__name__, f'payload is not schema-valid ({type(ex).__name__})') from None
    return run


def _reject_float(text):
    raise InvalidRecord('payload', f'binary float token {show(text)}: decimals are strings, integers have no point')


def _reject_constant(text):
    raise InvalidRecord('payload', 'NaN / Infinity is not JSON')


def _no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        req(k not in out, 'payload', f'duplicate key {show(k)}')
        out[k] = v
    return out


def parse_strict(raw):
    """Decode one contract payload: <= 64 KiB, UTF-8, an object, no duplicate keys, no float / NaN / Infinity token."""
    if isinstance(raw, str):
        raw = raw.encode('utf-8', 'surrogatepass')
    req(isinstance(raw, (bytes, bytearray)), 'payload', 'payload is not bytes / text')
    req(len(raw) <= MAX_PAYLOAD_BYTES, 'payload', f'larger than {MAX_PAYLOAD_BYTES} bytes')
    try:
        text = bytes(raw).decode('utf-8')
    except UnicodeDecodeError:
        raise InvalidRecord('payload', 'not UTF-8') from None
    try:
        obj = json.loads(text, parse_float=_reject_float, parse_constant=_reject_constant,
                         object_pairs_hook=_no_duplicates)
    except InvalidRecord:
        raise
    except (ValueError, RecursionError) as ex:
        raise InvalidRecord('payload', f'not JSON ({type(ex).__name__})') from None
    req(type(obj) is dict, 'payload', 'top level is not an object')
    return obj


def canonical_bytes(obj):
    """Canonical contract bytes: sorted keys, no whitespace, ASCII, no NaN."""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def sha256_of(obj):
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def _closed(obj, keys, path):
    """A JSON object with no field outside `keys`. Unknown names are counted, never echoed (a name can be a secret)."""
    req(type(obj) is dict, path, 'not a JSON object')
    unknown = [k for k in obj if k not in keys]
    req(not unknown, path, f'{len(unknown)} unknown field(s)')
    return obj


def _text(v, rx, path, what):
    req(type(v) is str and rx.fullmatch(v) is not None, path, f'{show(v)} is not {what}')
    return v


def _one_of(v, allowed, path):
    req(type(v) is str and v in allowed, path, f'{show(v)} is not one of {len(allowed)} allowed values')
    return v


def _id(v, path, *prefixes):
    req(type(v) is str and re.fullmatch(r'([a-z]{2,4})_[0-9a-f]{32}', v) is not None and v.split('_')[0] in prefixes,
        path, f'{show(v)} is not a {"/".join(prefixes)}_<32 hex> id')
    return v


def _bool(v, path):
    req(type(v) is bool, path, 'boolean required')
    return v


def _version1(v, path):
    req(type(v) is int and v == 1, path, 'contract_version must be the integer 1')


def _ms(v, path):
    req(type(v) is int, path, f'integer UTC milliseconds required, not {type(v).__name__}')
    req(MIN_TS_MS <= v <= MAX_TS_MS, path, f'{show(v)} outside [{MIN_TS_MS}, {MAX_TS_MS}]')
    return v


def _hash(v, path):
    req(type(v) is str and SHA256_RE.fullmatch(v) is not None, path, 'not a lowercase sha256 hex digest')
    req(v != ZERO_SHA256, path, 'an all-zero digest proves nothing')
    return v


def _dec(v, path):
    req(type(v) is str and DECIMAL_RE.fullmatch(v) is not None, path,
        f'{show(v)} is not canonical positive decimal text (<= 18 + 18 ASCII digits, no exponent)')
    return Decimal(v)


def _bounded(v, path, hi):
    d = _dec(v, path)
    req(d <= hi, path, f'above the contract bound {hi}')
    return d


def _value(obj, path):
    _closed(obj, VALUE_KEYS, path)
    unit = _one_of(obj['unit'], UNIT_MAX, path + '.unit')
    d = _bounded(obj['value'], path + '.value', UNIT_MAX[unit])
    if unit == 'ticks':
        req(d == d.to_integral_value(), path + '.value', 'ticks are whole numbers')
    return unit


def _fresh(start, expires, now_ms, path):
    """Trusted-clock freshness: started no later than now + skew, and not yet expired."""
    _ms(now_ms, 'now_ms')
    req(start <= now_ms + CLOCK_SKEW_MS, path, 'dated in the future beyond the tolerated clock skew')
    req(now_ms < expires, path, 'expired')


def reason_scopes(code):
    """The scopes a reason code is valid in; empty when it is in no registry (or deprecated)."""
    if type(code) is not str:
        return frozenset()
    if code in CONTRACT_REASONS:
        return frozenset(CONTRACT_REASONS[code][0])
    try:
        rc = ReasonCode(code)
    except ValueError:
        return frozenset()
    if rc.deprecated or code.split('.', 1)[0] not in GATE_NAMESPACES:
        return frozenset()
    return GATE_SCOPES


def _reason(code, path, scope):
    req(scope in reason_scopes(code), path, f'{show(code)} is not a registered {scope} reason code')
    return code


# ----------------------------------------------------------------------------------------------------- signal intent
def _level(obj, path):
    _closed(obj, LEVEL_KEYS, path)
    method, lookback = _one_of(obj['method'], METHODS, path + '.method'), obj.get('lookback_bars')
    unit = _value(obj['distance'], path + '.distance')
    if lookback is not None:
        req(type(lookback) is int and 1 <= lookback <= 1000, path + '.lookback_bars', 'integer bars in [1, 1000]')
    if method == 'fixed':
        req(unit != 'atr', path, 'a fixed level is in price, percent or ticks (method atr carries ATR units)')
        req(lookback is None, path + '.lookback_bars', 'a fixed level has no lookback')
    elif method == 'atr':
        req(unit == 'atr', path, 'method atr needs distance unit atr')
    else:
        req(lookback is not None, path + '.lookback_bars', 'recent_high_low needs lookback_bars')


@_strict
def check_signal_shape(obj):
    """Every structural, cross-field, time-order, bound and unit rule of signal_intent_v1 WITHOUT the trusted clock.
    For audit / replay of a stored signal only: it never authorizes promotion or use - that is check_signal_intent."""
    p = 'signal'
    _closed(obj, INTENT_KEYS, p)
    _version1(obj['contract_version'], p + '.contract_version')
    _id(obj['signal_id'], p + '.signal_id', 'sig')
    if obj.get('trade_family_id') is not None:
        _id(obj['trade_family_id'], p + '.trade_family_id', 'fam')
    _one_of(obj['source'], SOURCES, p + '.source')
    _text(obj['account'], ACCOUNT_RE, p + '.account', 'an account alias')
    action = _one_of(obj['action'], ACTIONS, p + '.action')
    _text(obj['symbol'], SYMBOL_RE, p + '.symbol', 'a symbol')
    _text(obj['strategy_id'], LABEL_RE, p + '.strategy_id', 'a strategy id')
    _text(obj['strategy_version'], VERSION_RE, p + '.strategy_version', 'a version')
    _bool(obj['dry_run'], p + '.dry_run')
    gen = _ms(obj['generated_at_ms'], p + '.generated_at_ms')
    exp = _ms(obj['expires_at_ms'], p + '.expires_at_ms')
    req(gen < exp, p + '.expires_at_ms', 'must be after generated_at_ms')
    req(exp - gen <= MAX_SIGNAL_TTL_MS, p + '.expires_at_ms', f'TTL above the {MAX_SIGNAL_TTL_MS} ms ceiling')
    required, forbidden, targets = ACTIONS[action]
    req(required <= set(obj), p, f'{action} needs {", ".join(sorted(required))}')
    req(not (forbidden & set(obj)), p, f'{action} never carries {", ".join(sorted(forbidden & set(obj)))}')
    if 'target_ref' in obj:
        _id(obj['target_ref'], p + '.target_ref', *targets)
    if action == 'modify':
        req(any(k in obj for k in ('stop', 'target', 'trail')), p, 'modify changes a stop, target or trail')
    if 'side' in obj:
        _one_of(obj['side'], SIDES, p + '.side')
    if 'order' in obj:
        order = _closed(obj['order'], ORDER_KEYS, p + '.order')
        otype = _one_of(order['type'], ORDER_TYPES, p + '.order.type')
        if otype == 'market':
            req('price' not in order and 'expires_at_ms' not in order, p + '.order', 'a market order has no price or expiry')
        else:
            req('price' in order, p + '.order.price', f'{otype} needs a price')
            _value(order['price'], p + '.order.price')
        if 'expires_at_ms' in order:
            oexp = _ms(order['expires_at_ms'], p + '.order.expires_at_ms')
            req(gen < oexp <= exp, p + '.order.expires_at_ms', 'must be after generation and not after the signal')
    if 'risk' in obj:
        risk = _closed(obj['risk'], RISK_KEYS, p + '.risk')
        req(bool(set(risk)), p + '.risk', 'needs risk_percent or max_risk_usd')
        if 'risk_percent' in risk:
            _bounded(risk['risk_percent'], p + '.risk.risk_percent', RISK_PERCENT_MAX)
        if 'max_risk_usd' in risk:
            _bounded(risk['max_risk_usd'], p + '.risk.max_risk_usd', MAX_RISK_USD_MAX)
    if 'reduce_percent' in obj:
        _bounded(obj['reduce_percent'], p + '.reduce_percent', REDUCE_PERCENT_MAX)
    for name in ('stop', 'target'):
        if name in obj:
            _level(obj[name], f'{p}.{name}')
    if 'trail' in obj:
        trail = _closed(obj['trail'], TRAIL_KEYS, p + '.trail')
        req({'trigger', 'distance'} <= set(trail), p + '.trail', 'needs trigger and distance')
        units = {_value(trail[k], f'{p}.trail.{k}') for k in ('trigger', 'distance', 'update_step') if k in trail}
        req(len(units) == 1, p + '.trail', 'trigger, distance and update_step use one unit')
    return obj


@_strict
def check_signal_intent(obj, *, now_ms):
    """The promotion / use check: check_signal_shape plus MANDATORY freshness against the caller's trusted clock
    (signal and its order not expired, not generated beyond the clock skew)."""
    check_signal_shape(obj)
    _fresh(obj['generated_at_ms'], obj['expires_at_ms'], now_ms, 'signal.expires_at_ms')
    order = obj.get('order') or {}
    if 'expires_at_ms' in order:
        req(now_ms < order['expires_at_ms'], 'signal.order.expires_at_ms', 'expired')
    return obj


# ----------------------------------------------------------------------------------------------------- signal result
def check_result_detail(v, path='result.detail'):
    """The display-text boundary: the NEWCORE domain detail rule (printable ASCII, <= 160, no key-shaped token) plus the
    refusals above. Refused, never scrubbed; the error never echoes the text (base.show)."""
    check_detail(v, path)
    if v is not None:
        req(CREDENTIAL_WORDS.search(v) is None, path, 'display text never names a credential')
        req(ASSIGNMENT_CHARS.search(v) is None, path, "display text has no '=', '?' or '&' (query / assignment shape)")
        req(not any(re.search('[0-9]', t) and re.search('[a-z]', t) for t in MIXED_TOKEN.findall(v)), path,
            'display text has no password / token shaped run (8+ characters mixing digits and lower case)')


@_strict
def check_signal_result(obj):
    """Every rule of signal_result_v1, standalone (it does not rely on the schema having run)."""
    p = 'result'
    _closed(obj, RESULT_KEYS, p)
    _version1(obj['contract_version'], p + '.contract_version')
    rid = _id(obj['result_id'], p + '.result_id', 'res')
    _id(obj['signal_id'], p + '.signal_id', 'sig')
    status = _one_of(obj['status'], STATUSES, p + '.status')
    _text(obj['reason_code'], REASON_RE, p + '.reason_code', 'a reason code')
    _reason(obj['reason_code'], p + '.reason_code', f'result:{status}')
    retryable = _bool(obj['retryable'], p + '.retryable')
    _ms(obj['recorded_at_ms'], p + '.recorded_at_ms')
    if obj.get('decision_id') is not None:
        _id(obj['decision_id'], p + '.decision_id', 'dec')
    if status in ('accepted', 'validated', 'held', 'duplicate'):
        req(retryable is False, p + '.retryable', f'a {status} result is never retried by the sender')
    if status in ('accepted', 'validated'):
        req(obj.get('decision_id') is not None, p + '.decision_id', f'a {status} result names its decision')
    if status == 'duplicate':
        oid = _id(obj['original_result_id'], p + '.original_result_id', 'res')
        req(oid != rid, p + '.original_result_id', 'a duplicate never cites itself')
        _one_of(obj['original_status'], STATUSES - {'duplicate'}, p + '.original_status')
    else:
        req('original_result_id' not in obj and 'original_status' not in obj, p + '.original_result_id',
            'only a duplicate cites an original result')
    check_result_detail(obj.get('detail'))
    return obj


def encode_result(obj):
    """The ONLY way a signal result leaves the process: the full standalone check (unknown fields refused, detail
    boundary included), then canonical bytes of a fresh object built from the allowlisted fields only."""
    check_signal_result(obj)
    return canonical_bytes({k: obj[k] for k in sorted(RESULT_KEYS) if k in obj})


# --------------------------------------------------------------------------------------------------- candidate score
@_strict
def check_candidate_shape(obj):
    """Every rule of candidate_score_v1 WITHOUT the trusted clock (audit / evaluation records). Promotion or use of a
    candidate goes through check_candidate_score."""
    p = 'candidate'
    _closed(obj, CANDIDATE_KEYS, p)
    _version1(obj['contract_version'], p + '.contract_version')
    _id(obj['candidate_id'], p + '.candidate_id', 'cand')
    evidence = _one_of(obj['evidence_class'], EVIDENCE_CLASSES, p + '.evidence_class')
    ceiling = _one_of(obj['automation_ceiling'], STAGES, p + '.automation_ceiling')
    for name in ('universe_id', 'profile_id', 'strategy_id'):
        _text(obj[name], LABEL_RE, f'{p}.{name}', 'a label')
    for name in ('scoring_version', 'profile_version', 'strategy_version'):
        _text(obj[name], VERSION_RE, f'{p}.{name}', 'a version')
    _text(obj['symbol'], SYMBOL_RE, p + '.symbol', 'a symbol')
    _one_of(obj['side'], SIDES, p + '.side')
    state = _one_of(obj['entry_state'], ENTRY_STATES, p + '.entry_state')
    as_of = _ms(obj['as_of_ms'], p + '.as_of_ms')
    exp = _ms(obj['expires_at_ms'], p + '.expires_at_ms')
    uni = _ms(obj['universe_as_of_ms'], p + '.universe_as_of_ms')
    req(as_of < exp, p + '.expires_at_ms', 'must be after as_of_ms')
    req(exp - as_of <= MAX_CANDIDATE_TTL_MS, p + '.expires_at_ms', f'TTL above the {MAX_CANDIDATE_TTL_MS} ms ceiling')
    req(uni <= as_of, p + '.universe_as_of_ms', 'the universe snapshot cannot postdate the candidate')
    for name in ('universe_snapshot_sha256', 'weights_sha256', 'source_manifest_sha256'):
        _hash(obj[name], f'{p}.{name}')
    req(STAGES.index(ceiling) <= STAGES.index(CEILING_BY_EVIDENCE[evidence]), p + '.automation_ceiling',
        f'{evidence} evidence caps automation at {CEILING_BY_EVIDENCE[evidence]}')
    scores = _closed(obj['scores'], frozenset(SCORE_DIMENSIONS), p + '.scores')
    req(len(scores) == len(SCORE_DIMENSIONS), p + '.scores', 'exactly the six dimensions')
    for k in SCORE_DIMENSIONS:
        req(type(scores[k]) is int and 0 <= scores[k] <= 100, f'{p}.scores.{k}', 'integer in [0, 100]')
    codes = obj['reason_codes']
    req(type(codes) is list and len(codes) <= 32, p + '.reason_codes', 'a list of at most 32 reason codes')
    for i, code in enumerate(codes):
        _reason(code, f'{p}.reason_codes[{i}]', 'candidate')
    req(len(set(codes)) == len(codes), p + '.reason_codes', 'duplicate reason code')
    if state == 'STAND_DOWN':
        req(any('stand_down' in reason_scopes(c) for c in codes), p + '.reason_codes',
            'STAND_DOWN names the veto that caused it (a stand_down.* or gate reason)')
    if state == 'READY':
        req(not any('stand_down' in reason_scopes(c) for c in codes), p + '.reason_codes',
            'READY cannot carry a veto (stand_down.* or gate) reason')
    stop = obj.get('structural_stop')
    if stop is not None:
        _value(stop, p + '.structural_stop')
    return obj


@_strict
def check_candidate_score(obj, *, now_ms):
    """The promotion / use check: check_candidate_shape plus MANDATORY trusted-clock freshness."""
    check_candidate_shape(obj)
    _fresh(obj['as_of_ms'], obj['expires_at_ms'], now_ms, 'candidate.expires_at_ms')
    return obj


# ---------------------------------------------------------------------------------------------- candidate evaluation
def commitment_digest(evaluation, candidates):
    """The proof-of-prior digest: the whole evaluation - including `sealed` and the committed prior_commitment fields
    (evaluated window bounds, benchmark-set hash) - plus every candidate record. Only the anchor facts that come into
    existence after the digest (the digest itself, anchor kind / ref / time) are left out."""
    body = {k: v for k, v in evaluation.items() if k != 'prior_commitment'}
    prior = evaluation.get('prior_commitment')
    body['prior_commitment'] = None if prior is None else {k: prior[k] for k in sorted(COMMITTED_PRIOR_FIELDS)}
    return sha256_of({'evaluation': body, 'candidates': sorted(candidates, key=lambda c: c['candidate_id'])})


def _overlaps(a, b):
    """Two exclusion / candidate cells on one symbol overlap when strategy and side are each equal or unspecified."""
    return all(a.get(k) is None or b.get(k) is None or a.get(k) == b.get(k) for k in ('strategy_id', 'side'))


@_strict
def check_candidate_evaluation(obj, candidates):
    """Binds one evaluation run: every level closed and typed, universe / weights / benchmark hashes recomputed, every
    member covered by a candidate or a reason-coded exclusion, cells unique (no duplicate candidate cell, no overlapping
    exclusion, no cell both scored and excluded), counts exact, every candidate consistent with the run, proof-of-prior
    ordering and digest (window included), candidates still valid when the committed window opens."""
    p = 'evaluation'
    _closed(obj, EVALUATION_KEYS, p)
    _version1(obj['contract_version'], p + '.contract_version')
    _id(obj['evaluation_id'], p + '.evaluation_id', 'eval')
    evidence = _one_of(obj['evidence_class'], EVIDENCE_CLASSES, p + '.evidence_class')
    as_of = _ms(obj['as_of_ms'], p + '.as_of_ms')
    _text(obj['profile_id'], LABEL_RE, p + '.profile_id', 'a label')
    _text(obj['profile_version'], VERSION_RE, p + '.profile_version', 'a version')
    sealed = _bool(obj['sealed'], p + '.sealed')
    # universe
    universe = _closed(obj['universe'], UNIVERSE_KEYS, p + '.universe')
    _text(universe['universe_id'], LABEL_RE, p + '.universe.universe_id', 'a label')
    uni_as_of = _ms(universe['as_of_ms'], p + '.universe.as_of_ms')
    req(uni_as_of <= as_of, p + '.universe.as_of_ms', 'the universe snapshot cannot postdate the evaluation')
    members = universe['members']
    req(type(members) is list and 1 <= len(members) <= 2000, p + '.universe.members', '1..2000 members')
    for i, m in enumerate(members):
        _closed(m, MEMBER_KEYS, f'{p}.universe.members[{i}]')
        _text(m['symbol'], SYMBOL_RE, f'{p}.universe.members[{i}].symbol', 'a symbol')
        _one_of(m['status'], MEMBER_STATUSES, f'{p}.universe.members[{i}].status')
    symbols = [m['symbol'] for m in members]
    req(symbols == sorted(set(symbols)), p + '.universe.members', 'members are unique and sorted by symbol')
    status = {m['symbol']: m['status'] for m in members}
    req(_hash(obj['universe_snapshot_sha256'], p + '.universe_snapshot_sha256') == sha256_of(universe),
        p + '.universe_snapshot_sha256', 'does not match the universe snapshot')
    # scoring
    scoring = _closed(obj['scoring'], SCORING_KEYS, p + '.scoring')
    _text(scoring['scoring_version'], VERSION_RE, p + '.scoring.scoring_version', 'a version')
    weights = _closed(scoring['weights'], frozenset(SCORE_DIMENSIONS), p + '.scoring.weights')
    req(len(weights) == len(SCORE_DIMENSIONS), p + '.scoring.weights', 'exactly the six dimensions')
    for k in SCORE_DIMENSIONS:
        _text(weights[k], WEIGHT_RE, f'{p}.scoring.weights.{k}', 'a weight in [0, 1]')
    req(sum(Decimal(weights[k]) for k in SCORE_DIMENSIONS) == 1, p + '.scoring.weights', 'weights sum to exactly 1')
    req(_hash(obj['weights_sha256'], p + '.weights_sha256') == sha256_of(scoring), p + '.weights_sha256',
        'does not match the scoring version + weights')
    # benchmarks
    bench = obj['benchmarks']
    req(type(bench) is list and 1 <= len(bench) <= 16, p + '.benchmarks', '1..16 benchmarks')
    for i, b in enumerate(bench):
        _closed(b, BENCHMARK_KEYS, f'{p}.benchmarks[{i}]')
        _text(b['benchmark_id'], LABEL_RE, f'{p}.benchmarks[{i}].benchmark_id', 'a label')
        _one_of(b['kind'], BENCHMARK_KINDS, f'{p}.benchmarks[{i}].kind')
    req(len({b['benchmark_id'] for b in bench}) == len(bench), p + '.benchmarks', 'duplicate benchmark_id')
    req(_hash(obj['benchmark_set_sha256'], p + '.benchmark_set_sha256') == sha256_of(bench),
        p + '.benchmark_set_sha256', 'does not match the benchmark set')
    # exclusions: closed, registered, on members, unique / non-overlapping cells, counted exactly
    exclusions = obj['exclusions']
    req(type(exclusions) is list and len(exclusions) <= MAX_EXCLUSIONS, p + '.exclusions', 'a bounded list')
    by_symbol = collections.defaultdict(list)
    for i, ex in enumerate(exclusions):
        ep = f'{p}.exclusions[{i}]'
        _closed(ex, EXCLUSION_KEYS, ep)
        req(ex['symbol'] in status if type(ex['symbol']) is str else False, ep + '.symbol', 'not a universe member')
        if ex.get('strategy_id') is not None:
            _text(ex['strategy_id'], LABEL_RE, ep + '.strategy_id', 'a label')
        if ex.get('side') is not None:
            _one_of(ex['side'], SIDES, ep + '.side')
        _reason(ex['reason_code'], ep + '.reason_code', 'exclusion')
        for other in by_symbol[ex['symbol']]:
            req(not _overlaps(ex, other), ep, 'overlaps another exclusion of the same cell (one reason per cell)')
        by_symbol[ex['symbol']].append(ex)
    counts = obj['exclusion_counts']
    req(type(counts) is dict and all(type(v) is int for v in counts.values()), p + '.exclusion_counts',
        'reason code -> integer count')
    tally = collections.Counter(ex['reason_code'] for ex in exclusions)
    req(dict(tally) == counts, p + '.exclusion_counts', 'does not equal the reason-coded exclusions')
    # candidates: exactly the listed ids, each valid, consistent with this run, unique cells, never also excluded
    ids = obj['candidate_ids']
    req(type(ids) is list and all(type(i) is str for i in ids) and len(set(ids)) == len(ids), p + '.candidate_ids',
        'a list of unique candidate ids')
    req(type(candidates) in (list, tuple), p + '.candidates', 'the candidate records are required as a list')
    for c in candidates:
        check_candidate_shape(c)
    seen = [c['candidate_id'] for c in candidates]
    req(len(seen) == len(set(seen)) and set(seen) == set(ids), p + '.candidate_ids',
        'must list exactly the candidate records of this run')
    bind = {'evidence_class': evidence, 'universe_id': universe['universe_id'],
            'universe_snapshot_sha256': obj['universe_snapshot_sha256'], 'universe_as_of_ms': uni_as_of,
            'scoring_version': scoring['scoring_version'], 'weights_sha256': obj['weights_sha256'],
            'profile_id': obj['profile_id'], 'profile_version': obj['profile_version'], 'as_of_ms': as_of}
    covered, cells = set(by_symbol), set()
    for c in candidates:
        cp = f'{p}.candidates[{c["candidate_id"]}]'
        for k, v in bind.items():
            req(c[k] == v, f'{cp}.{k}', 'does not match the evaluation run')
        req(status.get(c['symbol']) == 'listed', cp + '.symbol', 'only a listed universe member can be a candidate')
        cell = (c['strategy_id'], c['strategy_version'], c['symbol'], c['side'])
        req(cell not in cells, cp, 'duplicate candidate cell (strategy, version, symbol, side) under another id')
        cells.add(cell)
        req(not any(_overlaps(ex, c) for ex in by_symbol.get(c['symbol'], ())), cp,
            'the cell is both scored and excluded')
        covered.add(c['symbol'])
    missing = sorted(set(status) - covered)
    req(not missing, p + '.universe.members', f'{len(missing)} member(s) neither scored nor excluded')
    for sym, st in status.items():
        if st != 'listed':
            req(any(ex['reason_code'] == f'exclusion.{st}' and ex.get('strategy_id') is None and ex.get('side') is None
                    for ex in by_symbol.get(sym, ())), f'{p}.universe.members[{sym}]',
                f'a {st} member is kept and excluded symbol-wide as exclusion.{st}')
    # proof-of-prior
    prior = obj['prior_commitment']
    if evidence == 'backtest':
        req(not sealed and prior is None, p + '.prior_commitment', 'proof-of-prior is never evidence for a backtest')
    if sealed:
        req(prior is not None, p + '.prior_commitment', 'a sealed evaluation needs its prior commitment')
    if prior is not None:
        pp = p + '.prior_commitment'
        _closed(prior, PRIOR_KEYS, pp)
        _one_of(prior['anchor_kind'], ANCHOR_KINDS, pp + '.anchor_kind')
        _text(prior['anchor_ref'], ANCHOR_REF_RE, pp + '.anchor_ref', 'an anchor reference')
        anchored = _ms(prior['anchored_at_ms'], pp + '.anchored_at_ms')
        start = _ms(prior['window_start_ms'], pp + '.window_start_ms')
        end = _ms(prior['window_end_ms'], pp + '.window_end_ms')
        req(as_of <= anchored, pp + '.anchored_at_ms', 'anchored before the candidates it commits were scored')
        req(anchored < start, pp + '.anchored_at_ms', 'must be anchored before the evaluated forward window opens')
        req(start < end, pp + '.window_end_ms', 'the window must be non-empty')
        req(end - start <= MAX_WINDOW_MS, pp + '.window_end_ms', f'window longer than {MAX_WINDOW_MS} ms')
        for c in candidates:
            req(c['expires_at_ms'] > start, f'{p}.candidates[{c["candidate_id"]}].expires_at_ms',
                'the candidate expires before the committed window opens')
        req(_hash(prior['benchmark_set_sha256'], pp + '.benchmark_set_sha256') == obj['benchmark_set_sha256'],
            pp + '.benchmark_set_sha256', 'the benchmarks were not the pre-registered set')
        req(_hash(prior['commitment_sha256'], pp + '.commitment_sha256') == commitment_digest(obj, candidates),
            pp + '.commitment_sha256', 'does not commit to this evaluation, its window and its candidates')
    return obj
