"""Semantic validators for signal_intent_v1, signal_result_v1, candidate_score_v1 and candidate_evaluation_v1.

Each check_* function assumes the payload already passed its JSON Schema, re-checks the shape it relies on, and raises
InvalidRecord (path + bounded message; payload text is never echoed - base.show) on the first broken rule:

- strict tokens: parse_strict refuses duplicate keys, NaN / Infinity and every binary float token, so 1700000000000.0 or
  1e3 can never pass as an integer timestamp (JSON Schema's "integer" accepts 1.0);
- time: generated / as-of strictly before expiry, a bounded TTL, order expiry inside the signal's life, universe as-of
  not after the candidate, proof-of-prior anchored before the evaluated window;
- hashes: never all-zero, and every hash the evaluation can recompute (universe, weights, benchmark set, commitment) is
  recomputed and compared;
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
import json
import re
from decimal import Decimal

from ..domain.base import MAX_TS_MS, MIN_TS_MS, req, show
from ..domain.errors import InvalidRecord
from ..domain.incident import check_detail
from ..domain.reasons import GATE_NAMESPACES, ReasonCode

MAX_PAYLOAD_BYTES = 65_536
MAX_SIGNAL_TTL_MS = 3_600_000              # a signal lives at most 1 h from generation
MAX_CANDIDATE_TTL_MS = 86_400_000          # a candidate lives at most 24 h from as-of
CLOCK_SKEW_MS = 5_000                      # tolerated sender clock lead when the caller supplies now_ms
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

# Display text that names a credential is refused outright (the domain KEY_SHAPED backstop catches long tokens; this
# catches short ones such as "Authorization: Bearer abc").
CREDENTIAL_WORDS = re.compile(r'authori[sz]ation|bearer|api[\s_-]?key|secret|passw(or)?d|signature|token|'
                              r'private[\s_-]?key|cookie|session[\s_-]?id', re.I)


# ----------------------------------------------------------------------------------------------------------- helpers
def _strict(fn):
    """A payload that skipped its JSON Schema fails closed as InvalidRecord, never as KeyError / TypeError."""
    @functools.wraps(fn)
    def run(obj, *a, **kw):
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


def _ms(v, path):
    req(type(v) is int, path, f'integer UTC milliseconds required, not {type(v).__name__}')
    req(MIN_TS_MS <= v <= MAX_TS_MS, path, f'{show(v)} outside [{MIN_TS_MS}, {MAX_TS_MS}]')
    return v


def _hash(v, path):
    req(type(v) is str and SHA256_RE.fullmatch(v) is not None, path, 'not a lowercase sha256 hex digest')
    req(v != ZERO_SHA256, path, 'an all-zero digest proves nothing')
    return v


def _dec(v, path):
    req(type(v) is str, path, 'decimal text required')
    try:
        d = Decimal(v)
    except ArithmeticError:
        raise InvalidRecord(path, f'{show(v)} is not a decimal') from None
    req(d.is_finite() and d > 0, path, 'must be a finite positive decimal')
    return d


def _bounded(v, path, hi):
    d = _dec(v, path)
    req(d <= hi, path, f'above the contract bound {hi}')
    return d


def _value(obj, path):
    unit = obj['unit']
    req(unit in UNIT_MAX, path + '.unit', f'unknown unit {show(unit)}')
    d = _bounded(obj['value'], path + '.value', UNIT_MAX[unit])
    if unit == 'ticks':
        req(d == d.to_integral_value(), path + '.value', 'ticks are whole numbers')
    return unit


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


# ----------------------------------------------------------------------------------------------------- signal intent
def _level(obj, path):
    method, lookback = obj['method'], obj.get('lookback_bars')
    unit = _value(obj['distance'], path + '.distance')
    if method == 'fixed':
        req(unit != 'atr', path, 'a fixed level is in price, percent or ticks (method atr carries ATR units)')
        req(lookback is None, path + '.lookback_bars', 'a fixed level has no lookback')
    elif method == 'atr':
        req(unit == 'atr', path, 'method atr needs distance unit atr')
    elif method == 'recent_high_low':
        req(type(lookback) is int, path + '.lookback_bars', 'recent_high_low needs lookback_bars')
    else:
        raise InvalidRecord(path + '.method', f'unknown method {show(method)}')
    if lookback is not None:
        req(type(lookback) is int and 1 <= lookback <= 1000, path + '.lookback_bars', 'integer bars in [1, 1000]')


@_strict
def check_signal_intent(obj, now_ms=None):
    """Cross-field rules of signal_intent_v1. now_ms (the caller's trusted clock) adds the freshness checks."""
    gen = _ms(obj['generated_at_ms'], 'signal.generated_at_ms')
    exp = _ms(obj['expires_at_ms'], 'signal.expires_at_ms')
    req(gen < exp, 'signal.expires_at_ms', 'must be after generated_at_ms')
    req(exp - gen <= MAX_SIGNAL_TTL_MS, 'signal.expires_at_ms', f'TTL above {MAX_SIGNAL_TTL_MS} ms')
    if now_ms is not None:
        _ms(now_ms, 'now_ms')
        req(gen <= now_ms + CLOCK_SKEW_MS, 'signal.generated_at_ms', 'generated in the future')
        req(now_ms < exp, 'signal.expires_at_ms', 'expired')
    action = obj['action']
    if action == 'enter':
        order = obj['order']
        if 'price' in order:
            _value(order['price'], 'signal.order.price')
        if 'expires_at_ms' in order:
            oexp = _ms(order['expires_at_ms'], 'signal.order.expires_at_ms')
            req(gen < oexp <= exp, 'signal.order.expires_at_ms', 'must be after generation and not after the signal')
            if now_ms is not None:
                req(now_ms < oexp, 'signal.order.expires_at_ms', 'expired')
        risk = obj['risk']
        if 'risk_percent' in risk:
            _bounded(risk['risk_percent'], 'signal.risk.risk_percent', RISK_PERCENT_MAX)
        if 'max_risk_usd' in risk:
            _bounded(risk['max_risk_usd'], 'signal.risk.max_risk_usd', MAX_RISK_USD_MAX)
    if action == 'reduce':
        _bounded(obj['reduce_percent'], 'signal.reduce_percent', REDUCE_PERCENT_MAX)
    for name in ('stop', 'target'):
        if name in obj:
            _level(obj[name], f'signal.{name}')
    if 'trail' in obj:
        trail = obj['trail']
        units = {_value(trail[k], f'signal.trail.{k}') for k in ('trigger', 'distance', 'update_step') if k in trail}
        req(len(units) == 1, 'signal.trail', 'trigger, distance and update_step use one unit')
    return obj


# ----------------------------------------------------------------------------------------------------- signal result
def check_result_detail(v, path='result.detail'):
    """The display-text boundary: the NEWCORE domain detail rule (printable ASCII, <= 160, no key-shaped token) plus a
    refusal of credential words. Refused, never scrubbed; the error never echoes the text (base.show)."""
    check_detail(v, path)
    if v is not None:
        req(CREDENTIAL_WORDS.search(v) is None, path, 'display text never names a credential')


@_strict
def check_signal_result(obj):
    status = obj['status']
    _ms(obj['recorded_at_ms'], 'result.recorded_at_ms')
    _reason(obj['reason_code'], 'result.reason_code', f'result:{status}')
    req(type(obj['retryable']) is bool, 'result.retryable', 'boolean required')
    if status in ('accepted', 'validated', 'held', 'duplicate'):
        req(obj['retryable'] is False, 'result.retryable', f'a {status} result is never retried by the sender')
    if status in ('accepted', 'validated'):
        req(type(obj.get('decision_id')) is str, 'result.decision_id', f'a {status} result names its decision')
    if status == 'duplicate':
        req(obj['original_result_id'] != obj['result_id'], 'result.original_result_id', 'a duplicate never cites itself')
        req(obj['original_status'] != 'duplicate', 'result.original_status', 'cites the original, not another replay')
    else:
        req('original_result_id' not in obj and 'original_status' not in obj, 'result.original_result_id',
            'only a duplicate cites an original result')
    check_result_detail(obj.get('detail'))
    return obj


def encode_result(obj):
    """The ONLY way a signal result leaves the process: full semantic validation (detail boundary included), then
    canonical bytes. A refused result raises InvalidRecord without echoing its text."""
    return canonical_bytes(check_signal_result(obj))


# --------------------------------------------------------------------------------------------------- candidate score
@_strict
def check_candidate_score(obj):
    p = 'candidate'
    as_of = _ms(obj['as_of_ms'], p + '.as_of_ms')
    exp = _ms(obj['expires_at_ms'], p + '.expires_at_ms')
    uni = _ms(obj['universe_as_of_ms'], p + '.universe_as_of_ms')
    req(as_of < exp, p + '.expires_at_ms', 'must be after as_of_ms')
    req(exp - as_of <= MAX_CANDIDATE_TTL_MS, p + '.expires_at_ms', f'TTL above {MAX_CANDIDATE_TTL_MS} ms')
    req(uni <= as_of, p + '.universe_as_of_ms', 'the universe snapshot cannot postdate the candidate')
    for name in ('universe_snapshot_sha256', 'weights_sha256', 'source_manifest_sha256'):
        _hash(obj[name], f'{p}.{name}')
    evidence, ceiling = obj['evidence_class'], obj['automation_ceiling']
    req(evidence in CEILING_BY_EVIDENCE and ceiling in STAGES, p + '.automation_ceiling', 'unknown class / stage')
    req(STAGES.index(ceiling) <= STAGES.index(CEILING_BY_EVIDENCE[evidence]), p + '.automation_ceiling',
        f'{evidence} evidence caps automation at {CEILING_BY_EVIDENCE[evidence]}')
    scores = obj['scores']
    req(set(scores) == set(SCORE_DIMENSIONS), p + '.scores', 'exactly the six dimensions')
    for k in SCORE_DIMENSIONS:
        req(type(scores[k]) is int and 0 <= scores[k] <= 100, f'{p}.scores.{k}', 'integer in [0, 100]')
    codes = obj['reason_codes']
    for i, code in enumerate(codes):
        _reason(code, f'{p}.reason_codes[{i}]', 'candidate')
    if obj['entry_state'] == 'STAND_DOWN':
        req(any('stand_down' in reason_scopes(c) for c in codes), p + '.reason_codes',
            'STAND_DOWN names the veto that caused it (a stand_down.* or gate reason)')
    stop = obj.get('structural_stop')
    if stop is not None:
        _value(stop, p + '.structural_stop')
    return obj


# ---------------------------------------------------------------------------------------------- candidate evaluation
def commitment_digest(evaluation, candidates):
    """The proof-of-prior digest: the evaluation (minus sealed / prior_commitment) plus every candidate record."""
    body = {k: v for k, v in evaluation.items() if k not in ('sealed', 'prior_commitment')}
    return sha256_of({'evaluation': body, 'candidates': sorted(candidates, key=lambda c: c['candidate_id'])})


@_strict
def check_candidate_evaluation(obj, candidates):
    """Binds one evaluation run: universe / weights / benchmark hashes recomputed, every member covered by a candidate
    or a reason-coded exclusion, counts exact, every candidate consistent with the run, proof-of-prior ordering."""
    p = 'evaluation'
    as_of = _ms(obj['as_of_ms'], p + '.as_of_ms')
    universe = obj['universe']
    uni_as_of = _ms(universe['as_of_ms'], p + '.universe.as_of_ms')
    req(uni_as_of <= as_of, p + '.universe.as_of_ms', 'the universe snapshot cannot postdate the evaluation')
    members = universe['members']
    symbols = [m['symbol'] for m in members]
    req(symbols == sorted(set(symbols)), p + '.universe.members', 'members are unique and sorted by symbol')
    status = {m['symbol']: m['status'] for m in members}
    req(_hash(obj['universe_snapshot_sha256'], p + '.universe_snapshot_sha256') == sha256_of(universe),
        p + '.universe_snapshot_sha256', 'does not match the universe snapshot')
    weights = obj['scoring']['weights']
    req(set(weights) == set(SCORE_DIMENSIONS), p + '.scoring.weights', 'exactly the six dimensions')
    req(sum(Decimal(weights[k]) for k in SCORE_DIMENSIONS) == 1, p + '.scoring.weights', 'weights sum to exactly 1')
    req(_hash(obj['weights_sha256'], p + '.weights_sha256') == sha256_of(obj['scoring']), p + '.weights_sha256',
        'does not match the scoring version + weights')
    bench = obj['benchmarks']
    req(len({b['benchmark_id'] for b in bench}) == len(bench), p + '.benchmarks', 'duplicate benchmark_id')
    req(_hash(obj['benchmark_set_sha256'], p + '.benchmark_set_sha256') == sha256_of(bench),
        p + '.benchmark_set_sha256', 'does not match the benchmark set')
    # exclusions: registered, on members, counted exactly
    excluded = collections.defaultdict(set)
    for i, ex in enumerate(obj['exclusions']):
        _reason(ex['reason_code'], f'{p}.exclusions[{i}].reason_code', 'exclusion')
        req(ex['symbol'] in status, f'{p}.exclusions[{i}].symbol', 'not a universe member')
        excluded[ex['symbol']].add(ex['reason_code'])
    tally = collections.Counter(ex['reason_code'] for ex in obj['exclusions'])
    req(dict(tally) == obj['exclusion_counts'], p + '.exclusion_counts', 'does not equal the reason-coded exclusions')
    # candidates: exactly the listed ids, each valid and consistent with this run
    ids = obj['candidate_ids']
    req(type(candidates) in (list, tuple), p + '.candidates', 'the candidate records are required')
    seen = [c['candidate_id'] for c in candidates]
    req(len(seen) == len(set(seen)) and set(seen) == set(ids), p + '.candidate_ids',
        'must list exactly the candidate records of this run')
    bind = {'evidence_class': obj['evidence_class'], 'universe_id': universe['universe_id'],
            'universe_snapshot_sha256': obj['universe_snapshot_sha256'], 'universe_as_of_ms': uni_as_of,
            'scoring_version': obj['scoring']['scoring_version'], 'weights_sha256': obj['weights_sha256'],
            'profile_id': obj['profile_id'], 'profile_version': obj['profile_version'], 'as_of_ms': as_of}
    covered = set(excluded)
    for c in candidates:
        cp = f'{p}.candidates[{c["candidate_id"]}]' if type(c) is dict else p + '.candidates[?]'
        check_candidate_score(c)
        for k, v in bind.items():
            req(c[k] == v, f'{cp}.{k}', 'does not match the evaluation run')
        req(status.get(c['symbol']) == 'listed', cp + '.symbol', 'only a listed universe member can be a candidate')
        covered.add(c['symbol'])
    missing = sorted(set(status) - covered)
    req(not missing, p + '.universe.members', f'{len(missing)} member(s) neither scored nor excluded')
    for sym, st in status.items():
        if st != 'listed':
            req(f'exclusion.{st}' in excluded.get(sym, ()), f'{p}.universe.members[{sym}]',
                f'a {st} member is kept and excluded as exclusion.{st}')
    # proof-of-prior
    evidence, sealed, prior = obj['evidence_class'], obj['sealed'], obj['prior_commitment']
    if evidence == 'backtest':
        req(not sealed and prior is None, p + '.prior_commitment', 'proof-of-prior is never evidence for a backtest')
    if sealed:
        req(prior is not None, p + '.prior_commitment', 'a sealed evaluation needs its prior commitment')
    if prior is not None:
        pp = p + '.prior_commitment'
        anchored = _ms(prior['anchored_at_ms'], pp + '.anchored_at_ms')
        start = _ms(prior['window_start_ms'], pp + '.window_start_ms')
        end = _ms(prior['window_end_ms'], pp + '.window_end_ms')
        req(as_of <= anchored, pp + '.anchored_at_ms', 'anchored before the candidates it commits were scored')
        req(anchored < start, pp + '.anchored_at_ms', 'must be anchored before the evaluated forward window opens')
        req(start < end, pp + '.window_end_ms', 'the window must be non-empty')
        req(_hash(prior['benchmark_set_sha256'], pp + '.benchmark_set_sha256') == obj['benchmark_set_sha256'],
            pp + '.benchmark_set_sha256', 'the benchmarks were not the pre-registered set')
        req(_hash(prior['commitment_sha256'], pp + '.commitment_sha256') == commitment_digest(obj, candidates),
            pp + '.commitment_sha256', 'does not commit to this evaluation and its candidates')
    return obj
