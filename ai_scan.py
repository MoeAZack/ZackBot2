"""AI-SCAN-01 (#199): the immutable, causal signal-ledger schema of the shadow chart scanner. PAPER, shadow, report-only.

Pure functions over plain values: nothing here places, sizes, gates or cancels an order, imports the engine, or calls a
model, and nothing in the trading code reads what it builds. Three append-only record kinds join on the ML-SHADOW decision
id (ml_shadow.ledger.decision_id - deterministic, so a restart re-derives the same id):

- signal   one per candidate/decision: decision instant, cutoff, the event and availability time of every
           point-in-time input (event <= cutoff, event <= available <= decision), the deterministic plan + decision copied from the engine, the cost
           estimate, the snapshot's absolute age, and INSUFFICIENT (with its reasons) when the inputs cannot support a
           read - including a snapshot older than STALE_BARS bars of its timeframe;
- shadow   one per (signal, epoch). The epoch hashes the immutable model id, prompt-template hash, sampling parameters,
           feature/schema hash, label definition and cost-model hash - any change opens a new, unverified epoch. Either
           an opinion with coverage, disagreement, raw-response hash and a probability that stays UNVERIFIED unless the
           epoch's calibration passes the frozen gate, or INSUFFICIENT (queue full / timeout / budget / remote error).
           It names the plan and snapshot hashes it read and can never carry plan, sizing or order fields;
- outcome  measured (the engine's trade) joined by the immutable decision id + its actual-cost revision identifier
           `cost_revision_id` (computed from the canonical actual-cost payload; net P&L and net R are derived from
           it, never supplied); valid only for a `taken` decision; the engine's monotonic `rev` (#196 cost_rev,
           engine.py _row_changed; 0 = booked) orders and deduplicates revisions only, never the economic identity; or
           simulated (a deterministic causal replay under the frozen
           policy and cost model - the only outcome an untaken candidate gets) keyed by that cost-model hash. The two are
           never merged. Closed strictly after the decision instant.

fold() replays records into one view per signal: an exact duplicate is a no-op (restart idempotency); a conflicting
replay, a join to an unknown signal, a hash mismatch, a forged cost identity or a look-ahead outcome fails closed with
AIScanError. fitting_view() and calibration_passes() are bound to a sealed manifest of ordered fit / calibration /
holdout intervals (seal_manifest); fitting refuses any calibration or holdout row. The contract is docs/AI_SCAN_01.md (#200); the record/API detail is
docs/AI_SCAN_01_SCHEMA.md.
"""
import collections
import math
import re
import threading

from ml_shadow.common import SECRET_NAME_RE, canonical_json, sha256_hex, to_utc
from ml_shadow.ledger import decision_id

SCHEMA_VERSION = 'ai-scan-schema-v1'
FLAG = 'AI_SCAN'                                     # default OFF; report-only API/UI surface
TF_SEC = {'5m': 300, '15m': 900, '1h': 3600, '4h': 14400}
SIDES = ('LONG', 'SHORT')
RULE_STATUSES = ('taken', 'skipped', 'pending')     # ml_shadow.common.STATUSES
OPINIONS = ('LONG', 'SHORT', 'NO_TRADE')
CAUSAL_INPUTS = ('funding', 'universe', 'exchange_rules')   # each {event_at, available_at} (Codex 6455506454 #1)
STALE_BARS = 2                                       # timeframe-relative freshness ceiling (Codex 6452143340 #1)
LABEL_RULES, LABEL_AI, LABEL_UNVERIFIED, LABEL_INSUFFICIENT = 'RULES', 'AI SHADOW', 'UNVERIFIED', 'INSUFFICIENT'
PROB_UNVERIFIED, PROB_CALIBRATED = 'UNVERIFIED', 'CALIBRATED'
# #200: P(net_R > 0 after actual-model fees, funding and fills at the earlier of the strategy exit or its max-bar horizon)
TARGET = dict(label='net_r_gt_0', horizon='strategy_exit_or_max_bars', cost_basis='actual_cost_model')
GATE = dict(min_n=500, min_bin_n=50, max_ece=0.05)  # + Brier CI wholly above 0, window = the sealed calibration interval
DEMOTE_ECE, DEMOTE_FAILS = 0.08, 2                   # rolling ECE above, or this many consecutive failures to beat base
SHADOW_FAILURES = ('queue_full', 'timeout', 'budget_exhausted', 'remote_error')
COST_BASES = {'measured': ('booked', 'measured'), 'simulated': ('modelled',)}
RATIONALE_MAX = 400
# cost_revision_id input (Codex 6453104005): versioned, pinned field list AND order. Bump the schema to change either.
# v2 (Codex 6455506454 #2): the payload is the engine row's own inputs; net P&L / R are derived, never hashed or supplied.
# funding_actual is signed as the repo books it (+ = received): actual_costs.measured / true_books net
#   pnl_net = pnl_gross + pnl_fill_adj - fees_actual + funding_actual        net_r = pnl_net / risk_usd  (risk_usd > 0)
COST_REV_SCHEMA = 'ai-scan-cost-rev-v2'
COST_REV_FIELDS = ('fees_actual', 'funding_actual', 'pnl_gross', 'pnl_fill_adj', 'risk_usd')
NET_TOL = 1e-9                                       # a supplied net_pnl / net_r must equal the derived one to this
SAMPLING_KEYS = ('temperature', 'top_p', 'top_k', 'max_tokens', 'stop_sequences', 'seed')
MANIFEST_SCHEMA = 'ai-scan-manifest-v1'
WINDOWS = ('fit', 'calibration', 'holdout')          # sealed, strictly in this order, never overlapping
_HEX64 = re.compile(r'^[0-9a-f]{64}$')
_ID_RE = re.compile(r'(order|client|trade|exchange)[\s_\-]?(o?id|key)|orderid|clientoid', re.I)
_AUTHORITY_KEYS = ('plan', 'entry', 'stop', 'target', 'targets', 'size', 'qty', 'lot', 'leverage', 'order', 'exit',
                   'risk', 'settings', 'eligible', 'rotation')


class AIScanError(ValueError):
    """Fail-closed refusal: ambiguous join, conflicting replay, look-ahead, holdout, authority or secret breach."""


def _iso(t):
    return to_utc(t).strftime('%Y-%m-%dT%H:%M:%SZ')


def _ms(t):
    return int(to_utc(t).value // 1_000_000)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _hex64(*hs):
    return all(isinstance(h, str) and _HEX64.match(h) for h in hs)


def _seal(rec):
    rec['record_hash'] = sha256_hex(canonical_json({k: v for k, v in rec.items() if k != 'record_hash'}))
    return rec


def _no_secrets(d, where):
    """Recursive: no key at any depth (dicts inside dicts / lists) may name a credential or an exchange identifier."""
    if isinstance(d, dict):
        for k, v in d.items():
            if SECRET_NAME_RE.search(str(k)) or _ID_RE.search(str(k)):
                raise AIScanError(f'{where}: field {k!r} may carry a credential or exchange identifier')
            _no_secrets(v, f'{where}.{k}')
    elif isinstance(d, (list, tuple)):
        for i, v in enumerate(d): _no_secrets(v, f'{where}[{i}]')


def _no_authority(d, where):
    """Recursive: no key at any depth may name plan / order / sizing / risk / settings authority."""
    if isinstance(d, dict):
        for k, v in d.items():
            if any(a in str(k).lower() for a in _AUTHORITY_KEYS):
                raise AIScanError(f'{where}: field {k!r} would carry plan or order authority')
            _no_authority(v, f'{where}.{k}')
    elif isinstance(d, (list, tuple)):
        for i, v in enumerate(d): _no_authority(v, f'{where}[{i}]')


def _check_sampling(sampling):
    """Sampling parameters: a flat allow-list of scalar knobs (stop_sequences: a list of strings). Anything else - an
    unknown or nested key, which could smuggle authority, a secret or an id - refuses."""
    if not isinstance(sampling, dict): raise AIScanError('sampling parameters must be a dict')
    for k, v in sampling.items():
        if k not in SAMPLING_KEYS: raise AIScanError(f'sampling: unknown parameter {k!r}')
        if k == 'stop_sequences':
            if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                raise AIScanError('sampling: stop_sequences must be a list of strings')
        elif not (v is None or _num(v)):
            raise AIScanError(f'sampling: {k!r} must be a finite number')


def epoch_of(model_id, prompt_hash, sampling, feature_schema_hash, cost_model_hash):
    """The calibration epoch. A change of the model id, prompt template, sampling parameters, feature/schema, label
    definition (TARGET) or cost model is a new epoch, which starts UNVERIFIED."""
    return sha256_hex(canonical_json(dict(model_id=model_id, prompt_hash=prompt_hash, sampling=sampling,
                                          feature_schema_hash=feature_schema_hash, target=TARGET,
                                          cost_model_hash=cost_model_hash)))[:16]


def api_off():
    """The stable response of the scanner endpoint while the flag is off (Codex 6452143340 #3): exactly these two keys."""
    return dict(on=False, rows=[])


def stale_after_s(tf):
    """The freshness ceiling in seconds: STALE_BARS bars of the timeframe. Exactly this old is fresh; older is not."""
    return STALE_BARS * TF_SEC[tf]


def measured_net(actual_costs):
    """-> (pnl_net, net_r) from the canonical actual-cost payload, the repo's own signed formula (actual_costs.measured):
    pnl_net = pnl_gross + pnl_fill_adj - fees_actual + funding_actual (funding + = received); net_r = pnl_net / risk_usd.
    Every field must be a finite number and risk_usd > 0."""
    if not isinstance(actual_costs, dict): raise AIScanError('a measured outcome needs the actual-cost payload')
    for f in COST_REV_FIELDS:
        if not _num(actual_costs.get(f)): raise AIScanError(f'cost field {f!r} missing or not a finite number')
    if not actual_costs['risk_usd'] > 0: raise AIScanError('risk_usd must be a finite positive number')
    c = {f: float(actual_costs[f]) for f in COST_REV_FIELDS}
    net = c['pnl_gross'] + c['pnl_fill_adj'] - c['fees_actual'] + c['funding_actual']
    return net, net / c['risk_usd']


def _close(a, b):
    return abs(a - b) <= NET_TOL * max(1.0, abs(a), abs(b))


def cost_revision_id(actual_costs, *, net_basis, cost_model_id):
    """The actual-cost revision identifier (Codex 6453104005): sha256 over the canonical, versioned tuple
    [COST_REV_SCHEMA, [[field, value] in COST_REV_FIELDS order], net_basis, cost_model_id]. Only the pinned fields are
    read - timestamps, ids and presentation fields are ignored, so they never change the id; any pinned cost field, the
    net basis, the cost-model id or a schema bump does. Each pinned field must be a finite number (a missing or partial
    cost fails closed - it has no identity yet). With the decision id this is the economic join key; the integer
    cost_rev only orders and deduplicates. Collision rule: two different records under one (decision id, id) are a
    conflicting replay and refuse (fold and the ledger), never a silent overwrite."""
    measured_net(actual_costs)                                   # every field finite, risk_usd > 0
    vals = [[f, float(actual_costs[f])] for f in COST_REV_FIELDS]
    if net_basis not in COST_BASES['measured']: raise AIScanError(f'unknown net basis {net_basis!r}')
    if not (isinstance(cost_model_id, str) and cost_model_id):
        raise AIScanError('a cost revision identifier needs the cost-model id')
    return sha256_hex(canonical_json([COST_REV_SCHEMA, vals, net_basis, cost_model_id]))


_cost_rev_id = cost_revision_id                     # build_outcome's parameter of the same name shadows it


# ------------------------------------------------------------------ inputs -> INSUFFICIENT reasons
def features_hash(features):
    return sha256_hex(canonical_json(features))


def provenance_hash(as_of, f_hash):
    """sha256 over the snapshot's causal provenance {event_at = last_close, available_at} + its features hash."""
    return sha256_hex(canonical_json({'as_of': as_of, 'features_hash': f_hash}))


def snapshot_as_of(snapshot):
    """{event_at: last_close, available_at} (ISO), or None when either is missing / malformed."""
    try: return dict(event_at=_iso(snapshot['last_close']), available_at=_iso(snapshot['available_at']))
    except (KeyError, TypeError, ValueError): return None


def snapshot_hash(snapshot):
    """The immutable causal input the shadow layer may read, bound to BOTH its timestamps (Codex 6455762348): the
    provenance hash of {event_at: last_close, available_at} + the flat numeric features. Changing either time changes
    the snapshot identity, so it cannot be replayed as the same snapshot."""
    as_of = snapshot_as_of(snapshot)
    if as_of is None: raise AIScanError('a snapshot needs last_close and available_at')
    return provenance_hash(as_of, features_hash(snapshot['features']))


def _times(v):
    """{event_at, available_at} -> (event_ms, available_ms), or None when missing / malformed."""
    if not isinstance(v, dict): return None
    try: return _ms(v['event_at']), _ms(v['available_at'])
    except (KeyError, TypeError, ValueError): return None


def _causal(why, name, ev, av, cutoff_ms, decided_ms):
    """event <= cutoff and event <= available <= decided; each breach is its own reason, never a guess."""
    if ev > cutoff_ms: why.append(f'after_cutoff:{name}')
    if av < ev: why.append(f'available_before_event:{name}')
    if av > decided_ms: why.append(f'after_decision:{name}')


def check_inputs(side, tf, cutoff_ms, decided_ms, snapshot, plan, inputs):
    """Reasons the causal inputs cannot support a read (empty = sufficient). Never infers or fills a value."""
    why = []
    if side not in SIDES: why.append('malformed:side')
    if tf not in TF_SEC: why.append('malformed:tf')
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('features'), dict):
        why.append('missing:snapshot')
    else:
        try:
            lc = _ms(snapshot['last_close'])
            if lc > cutoff_ms: why.append('after_cutoff')                # a candle not closed at the decision cutoff
            elif tf in TF_SEC and cutoff_ms - lc > stale_after_s(tf) * 1000:
                why.append('stale_snapshot')                             # older than STALE_BARS bars of its timeframe
        except (KeyError, TypeError, ValueError):
            lc = None
            why.append('missing:last_close')
        try: av = _ms(snapshot['available_at'])
        except (KeyError, TypeError, ValueError): av = None; why.append('missing:snapshot')
        if av is not None:
            if lc is not None and av < lc: why.append('available_before_event:snapshot')
            if av > decided_ms: why.append('after_decision:snapshot')
        feats = snapshot['features']
        if not feats: why.append('missing:features')
        bad = sorted(k for k, v in feats.items() if v is not None and not _num(v))
        why += [f'malformed:feature:{k}' for k in bad]
    for name in CAUSAL_INPUTS:                                          # point-in-time funding / universe / filters
        t = _times((inputs or {}).get(name) if isinstance(inputs, dict) else None)
        if t is None: why.append(f'missing:{name}')
        else: _causal(why, name, t[0], t[1], cutoff_ms, decided_ms)
    if not isinstance(plan, dict):
        return why + ['missing:plan']
    e_lo, e_hi, stop, tg = plan.get('entry_lo'), plan.get('entry_hi'), plan.get('stop'), plan.get('targets')
    if not all(_num(x) and x > 0 for x in (e_lo, e_hi, stop)) or e_lo > e_hi:
        return why + ['malformed:plan']
    if not isinstance(tg, list) or not tg or not all(_num(x) and x > 0 for x in tg):
        return why + ['malformed:targets']
    sd = 1 if side == 'LONG' else -1
    if side in SIDES and not sd * (e_lo if sd == 1 else e_hi) > sd * stop: why.append('malformed:stop_side')
    if side in SIDES and not all(sd * x > sd * (e_hi if sd == 1 else e_lo) for x in tg): why.append('malformed:target_side')
    return why


# ------------------------------------------------------------------ record builders
def build_signal(*, sleeve, strategy, family, symbol, side, tf, candle_open, decided_at, rules_status, rules_reason,
                 snapshot, plan, inputs=None, costs=None):
    """One immutable signal record. candle_open = the decision candle's OPEN (engine sg['time'], engine.py _utc_ms
    'dec_open'); cutoff = its close; decided_at = the instant the engine decided (>= cutoff). Every input must have been
    ingested by decided_at. plan / rules / costs are copied from the deterministic engine snapshot."""
    side, tf = str(side).upper(), str(tf)
    if rules_status not in RULE_STATUSES: raise AIScanError(f'unknown rules status {rules_status!r}')
    for name, d in (('snapshot', snapshot or {}), ('plan', plan or {}), ('costs', costs or {}), ('inputs', inputs or {})):
        _no_secrets(d, name)
    cutoff_ms, decided_ms = _ms(candle_open) + TF_SEC.get(tf, 0) * 1000, _ms(decided_at)
    if decided_ms < cutoff_ms: raise AIScanError('a decision before its candle closed')
    why = check_inputs(side, tf, cutoff_ms, decided_ms, snapshot, plan, inputs)
    feats = (snapshot or {}).get('features') or {}
    present = sum(1 for v in feats.values() if _num(v))
    as_of = {}
    for name in CAUSAL_INPUTS:
        v = (inputs or {}).get(name) if isinstance(inputs, dict) else None
        t = _times(v)
        as_of[name] = None if t is None else dict(event_at=_iso(t[0]), available_at=_iso(t[1]))
    try:
        age_s = (cutoff_ms - _ms((snapshot or {}).get('last_close'))) // 1000
    except (TypeError, ValueError):
        age_s = None
    snap_bad = any(w.startswith(('missing:snapshot', 'missing:last_close', 'malformed:feature')) for w in why)
    snap_as_of = None if snap_bad else snapshot_as_of(snapshot)
    rec = dict(kind='signal', schema=SCHEMA_VERSION,
               signal_id=decision_id(sleeve, strategy, symbol, side, tf, candle_open),
               sleeve=str(sleeve), strategy=str(strategy), family=str(family), symbol=str(symbol).upper(), side=side,
               tf=tf, candle_open=_iso(candle_open), cutoff=_iso(cutoff_ms), decided_at=_iso(decided_ms), inputs_as_of=as_of,
               rules=dict(status=rules_status, reason=str(rules_reason or '')[:200]),
               input_coverage=[present, len(feats)], snapshot_age_s=age_s, snapshot_as_of=snap_as_of,
               features_hash=None if snap_bad else features_hash(snapshot['features']),
               snapshot_hash=None if snap_bad else snapshot_hash(snapshot),
               plan=None if any(w.startswith(('missing:plan', 'malformed:plan', 'malformed:targets')) for w in why)
               else dict(entry_lo=plan['entry_lo'], entry_hi=plan['entry_hi'], stop=plan['stop'], targets=list(plan['targets'])),
               costs=dict(costs) if costs else None,
               status='INSUFFICIENT' if why else 'OK', insufficient=why)
    rec['plan_hash'] = sha256_hex(canonical_json(rec['plan'])) if rec['plan'] else None
    return _seal(rec)


def demoted(windows):
    """Rolling demotion: any resolved window with ECE above DEMOTE_ECE, or DEMOTE_FAILS consecutive windows that do not
    beat the frozen base-rate predictor. windows = [{ece, beats_base}] in time order; a malformed window demotes."""
    run = 0
    for w in windows or ():
        if not (isinstance(w, dict) and _num(w.get('ece')) and isinstance(w.get('beats_base'), bool)): return True
        if w['ece'] > DEMOTE_ECE: return True
        run = 0 if w['beats_base'] else run + 1
        if run >= DEMOTE_FAILS: return True
    return False


def _interval(v, name):
    try: lo, hi = _ms(v[0]), _ms(v[1])
    except (TypeError, ValueError, IndexError, KeyError): raise AIScanError(f'{name}: needs [start, end)')
    if lo >= hi: raise AIScanError(f'{name}: start must be before end')
    return lo, hi


def _manifest_body(m):
    return {k: m[k] for k in ('schema', 'prereg_hash') + WINDOWS}


def seal_manifest(*, fit, calibration, holdout, prereg_hash):
    """The immutable research manifest: explicit [start, end) fit, calibration and holdout intervals, strictly ordered
    fit < calibration < holdout and non-overlapping (computed here, never caller-asserted), bound to the preregistration
    hash. manifest_hash = sha256 of the rest; any edit breaks it."""
    if not _hex64(prereg_hash): raise AIScanError('a manifest needs the sha256 preregistration hash')
    m = dict(schema=MANIFEST_SCHEMA, prereg_hash=prereg_hash)
    for name, v in zip(WINDOWS, (fit, calibration, holdout)):
        lo, hi = _interval(v, name)
        m[name] = [_iso(lo), _iso(hi)]
    m['manifest_hash'] = sha256_hex(canonical_json(_manifest_body(m)))
    return verify_manifest(m)


def verify_manifest(m):
    """-> the parsed {window: (lo, hi)} of a sealed manifest, or AIScanError (forged / edited / reversed / overlapping)."""
    if not isinstance(m, dict) or m.get('schema') != MANIFEST_SCHEMA: raise AIScanError('fitting requires the sealed manifest')
    try: body = _manifest_body(m)
    except KeyError: raise AIScanError('the sealed manifest is incomplete')
    if not _hex64(m.get('prereg_hash')) or m.get('manifest_hash') != sha256_hex(canonical_json(body)):
        raise AIScanError('manifest hash mismatch (not the sealed manifest)')
    iv = {name: _interval(m[name], name) for name in WINDOWS}
    if not (iv['fit'][1] <= iv['calibration'][0] and iv['calibration'][1] <= iv['holdout'][0]):
        raise AIScanError('manifest windows must be ordered fit < calibration < holdout and never overlap')
    return m


def calibration_id(c):
    """A calibration record's id: sha256 of everything else in it - an arbitrary or reused id never matches."""
    try: return sha256_hex(canonical_json({k: v for k, v in c.items() if k != 'id'}))
    except (TypeError, ValueError): raise AIScanError('a calibration record must be plain finite JSON')


def calibration_passes(c, epoch, manifest=None):
    """The frozen #200 gate for one epoch: the record is bound to the sealed manifest (its hash) and its window IS the
    manifest's calibration interval (non-overlap with fit / holdout computed by verify_manifest); its id is its own content hash; >= 500
    resolved labels and >= 50 per reported bin; ECE <= 0.05; the paired-bootstrap 95% CI of the Brier improvement over
    the base-rate predictor wholly above zero; the target is TARGET; and the epoch has not been demoted since."""
    if not isinstance(c, dict): return False
    try:
        verify_manifest(manifest)
        w = _interval(c.get('window'), 'calibration window')
        iv = {'calibration': _interval(manifest['calibration'], 'calibration')}
    except AIScanError:
        return False
    if c.get('manifest_hash') != manifest['manifest_hash'] or w != iv['calibration']:   # non-overlap: verify_manifest
        return False
    try: cid = calibration_id(c)
    except AIScanError: return False
    n, b, ece, lo = c.get('n'), c.get('min_bin_n'), c.get('ece'), c.get('brier_improvement_ci_lo')
    return (c.get('id') == cid and c.get('epoch') == epoch and c.get('target') == TARGET
            and _num(n) and n >= GATE['min_n'] and _num(b) and b >= GATE['min_bin_n'] and _num(ece) and ece <= GATE['max_ece']
            and _num(lo) and lo > 0 and not demoted(c.get('windows')))


def _identity(model_id, prompt_hash, sampling, feature_schema_hash, cost_model_hash):
    if not (isinstance(model_id, str) and model_id and isinstance(sampling, dict)
            and _hex64(prompt_hash, feature_schema_hash, cost_model_hash)):
        raise AIScanError('a shadow needs an immutable model id, sampling parameters and sha256 prompt / feature-schema /'
                          ' cost-model hashes')
    _check_sampling(sampling)
    return epoch_of(model_id, prompt_hash, sampling, feature_schema_hash, cost_model_hash)


def _shadow_base(signal, created_at, read_snapshot_hash, read_plan_hash, extra):
    if extra:
        bad = [k for k in extra if any(a in k.lower() for a in _AUTHORITY_KEYS)]
        raise AIScanError(f'shadow output may not carry {bad or sorted(extra)} (no plan or order authority)')
    for k, v in extra.items(): _no_secrets(v, k); _no_authority(v, k)
    if signal.get('status') != 'OK':
        raise AIScanError('an INSUFFICIENT signal gets no shadow score')
    if read_snapshot_hash != signal['snapshot_hash'] or read_plan_hash != signal['plan_hash']:
        raise AIScanError('the shadow read a different snapshot or plan than the signal recorded')
    if _ms(created_at) < _ms(signal['decided_at']):
        raise AIScanError('a shadow result created before the decision')


def build_shadow(signal, *, model_id, prompt_hash, sampling, feature_schema_hash, cost_model_hash, raw_response_hash,
                 created_at, opinion, coverage, disagreement, rationale='', probability=None, calibration=None,
                 manifest=None, read_snapshot_hash, read_plan_hash, **extra):
    """One shadow-committee result over the signal's immutable snapshot. Any plan/order/sizing field is refused; the
    probability is kept only when this epoch's calibration passes the frozen gate, otherwise it is dropped (UNVERIFIED)."""
    _shadow_base(signal, created_at, read_snapshot_hash, read_plan_hash, extra)
    ep = _identity(model_id, prompt_hash, sampling, feature_schema_hash, cost_model_hash)
    if not _hex64(raw_response_hash): raise AIScanError('a shadow result needs the sha256 of its raw response')
    if opinion not in OPINIONS: raise AIScanError(f'unknown opinion {opinion!r}')
    if not (_num(coverage) and 0 <= coverage <= 1 and _num(disagreement) and 0 <= disagreement <= 1):
        raise AIScanError('coverage and disagreement must be in [0, 1]')
    prob = (dict(state=PROB_CALIBRATED, p=float(probability), calibration_id=str(calibration['id']), target=dict(TARGET))
            if calibration_passes(calibration, ep, manifest) and _num(probability) and 0 <= probability <= 1
            else dict(state=PROB_UNVERIFIED))
    rec = dict(kind='shadow', schema=SCHEMA_VERSION, signal_id=signal['signal_id'], status='OK', epoch=ep,
               model_id=model_id, prompt_hash=prompt_hash, sampling=dict(sampling), feature_schema_hash=feature_schema_hash,
               cost_model_hash=cost_model_hash, raw_response_hash=raw_response_hash, created_at=_iso(created_at),
               opinion=opinion, coverage=float(coverage), disagreement=float(disagreement), probability=prob,
               rationale=str(rationale or '')[:RATIONALE_MAX], snapshot_hash=read_snapshot_hash, plan_hash=read_plan_hash)
    return _seal(rec)


def build_shadow_failure(signal, *, model_id, prompt_hash, sampling, feature_schema_hash, cost_model_hash, created_at,
                         reason, read_snapshot_hash, read_plan_hash, **extra):
    """The queue could not produce a read (full / timeout / spend budget / remote error): an INSUFFICIENT shadow record,
    never a score, and never anything that waits on or fails the engine."""
    _shadow_base(signal, created_at, read_snapshot_hash, read_plan_hash, extra)
    if reason not in SHADOW_FAILURES: raise AIScanError(f'unknown shadow failure {reason!r}')
    ep = _identity(model_id, prompt_hash, sampling, feature_schema_hash, cost_model_hash)
    rec = dict(kind='shadow', schema=SCHEMA_VERSION, signal_id=signal['signal_id'], status='INSUFFICIENT', reason=reason,
               epoch=ep, model_id=model_id, prompt_hash=prompt_hash, sampling=dict(sampling),
               feature_schema_hash=feature_schema_hash, cost_model_hash=cost_model_hash, created_at=_iso(created_at),
               snapshot_hash=read_snapshot_hash, plan_hash=read_plan_hash)
    return _seal(rec)


def build_outcome(signal, *, source, closed_at, exit_reason, cost_basis, net_r=None, net_pnl=None, actual_costs=None,
                  cost_model_id=None, cost_revision_id=None, rev=None, cost_model_hash=None, partial_exits=0, adds=0):
    """measured: the engine's own trade on a `taken` decision. It receives the canonical actual-cost payload
    (COST_REV_FIELDS) + its net basis (cost_basis) + cost_model_id; cost_revision_id, net_pnl and net_r are computed
    here (measured_net) - a supplied id or net that differs is refused. rev is the history row's monotonic cost_rev
    (0 = booked, each restatement +1), kept only to order and deduplicate. simulated: a deterministic causal replay under
    the frozen policy, keyed by its cost-model hash, net_r / net_pnl supplied - the only outcome an untaken candidate
    may get."""
    if source not in COST_BASES: raise AIScanError(f'unknown outcome source {source!r}')
    if cost_basis not in COST_BASES[source]: raise AIScanError(f'cost basis {cost_basis!r} is not valid for {source}')
    if source == 'measured':
        if not (isinstance(rev, int) and not isinstance(rev, bool) and rev >= 0 and cost_model_hash is None):
            raise AIScanError('a measured outcome is ordered by an int rev >= 0 (and has no cost-model hash)')
        if signal['rules']['status'] != 'taken':
            raise AIScanError(f"a measured outcome on a {signal['rules']['status']} decision (taken only)")
        cid = _cost_rev_id(actual_costs, net_basis=cost_basis, cost_model_id=cost_model_id)
        if cost_revision_id is not None and cost_revision_id != cid:
            raise AIScanError('the supplied cost_revision_id does not match the actual-cost payload')
        pnl, r = measured_net(actual_costs)
        if (net_pnl is not None and not (_num(net_pnl) and _close(net_pnl, pnl))) or \
                (net_r is not None and not (_num(net_r) and _close(net_r, r))):
            raise AIScanError('the supplied net_pnl / net_r do not match the actual-cost payload')
        cost_revision_id, net_pnl, net_r = cid, pnl, r
        actual_costs = {f: float(actual_costs[f]) for f in COST_REV_FIELDS}
    elif not (cost_revision_id is None and rev is None and actual_costs is None and cost_model_id is None
              and _hex64(cost_model_hash)):
        raise AIScanError('a simulated outcome is keyed by its sha256 cost-model hash (and no cost revision)')
    if _ms(closed_at) <= _ms(signal['decided_at']): raise AIScanError('an outcome closed at or before the decision')
    if not (_num(net_r) and _num(net_pnl)): raise AIScanError('outcome net_r / net_pnl must be finite numbers')
    rec = dict(kind='outcome', schema=SCHEMA_VERSION, signal_id=signal['signal_id'], source=source,
               cost_revision_id=cost_revision_id, rev=rev, actual_costs=actual_costs, cost_model_id=cost_model_id,
               cost_model_hash=cost_model_hash, closed_at=_iso(closed_at), net_r=float(net_r), net_pnl=float(net_pnl),
               exit_reason=str(exit_reason or '')[:64], cost_basis=cost_basis, partial_exits=int(partial_exits),
               adds=int(adds))
    return _seal(rec)


# ------------------------------------------------------------------ bounded, non-blocking analysis queue
class ScanQueue:
    """The remote-analysis hand-off: bounded, and offer() never blocks or raises. A refused offer is the caller's cue to
    record build_shadow_failure(..., reason='queue_full'); the 15-minute cycle never waits on analysis."""

    def __init__(self, maxsize):
        if not (isinstance(maxsize, int) and maxsize > 0): raise AIScanError('the queue must be bounded')
        self.maxsize, self._q, self._lock = maxsize, collections.deque(), threading.Lock()

    def offer(self, item):
        if not self._lock.acquire(blocking=False): return False      # contended: refuse rather than wait
        try:
            if len(self._q) >= self.maxsize: return False
            self._q.append(item)
            return True
        finally:
            self._lock.release()

    def take(self):
        with self._lock:
            return self._q.popleft() if self._q else None

    def __len__(self):
        return len(self._q)


# ------------------------------------------------------------------ replay
def _verify(rec):
    if rec.get('schema') != SCHEMA_VERSION: raise AIScanError(f"unknown schema {rec.get('schema')!r}")
    h = sha256_hex(canonical_json({k: v for k, v in rec.items() if k != 'record_hash'}))
    if rec.get('record_hash') != h: raise AIScanError(f"{rec.get('signal_id')}: record hash mismatch (edited record)")


def _check_provenance(rec, k):
    """A signal's snapshot identity must be re-derivable from its persisted snapshot_as_of + features_hash; an OK signal
    must carry it and satisfy last_close <= cutoff and last_close <= available_at <= decided_at."""
    as_of, fh, sh = rec.get('snapshot_as_of'), rec.get('features_hash'), rec.get('snapshot_hash')
    if as_of is None and fh is None and sh is None:
        if rec.get('status') == 'OK': raise AIScanError(f'{k}: an OK signal without snapshot_as_of')
        return
    try:
        ev, av = _ms(as_of['event_at']), _ms(as_of['available_at'])
        norm = dict(event_at=_iso(ev), available_at=_iso(av))
        ok = _hex64(fh) and as_of == norm and sh == provenance_hash(norm, fh)
    except (KeyError, TypeError, ValueError):
        ok = False
    if not ok: raise AIScanError(f'{k}: snapshot provenance does not match the snapshot hash')
    if rec.get('status') == 'OK' and not (ev <= _ms(rec['cutoff']) and ev <= av <= _ms(rec['decided_at'])):
        raise AIScanError(f'{k}: snapshot provenance is not causal')


def record_key(rec):
    """The immutable identity of one record: signal -> decision id; shadow -> + epoch; outcome -> + source + cost
    revision id / cost-model hash (never the ordering rev). The replay and the ledger dedupe on this key."""
    sid, kind = rec.get('signal_id'), rec.get('kind')
    key = {'signal': (sid,), 'shadow': (sid, rec.get('epoch')),
           'outcome': (sid, rec.get('source'), rec.get('cost_revision_id'), rec.get('cost_model_hash'))}.get(kind)
    if key is None: raise AIScanError(f'unknown record kind {kind!r}')
    return (kind,) + key


def fold(records):
    """Replay records (file order) into {signal_id: view}. Idempotent on exact duplicates; fails closed otherwise."""
    views, seen = {}, {}
    for rec in records:
        _verify(rec)
        sid, kind = rec['signal_id'], rec['kind']
        k = record_key(rec)
        if k in seen:
            if seen[k] != rec['record_hash']: raise AIScanError(f'{k}: conflicting replay of an immutable record')
            continue
        if kind != 'signal' and sid not in views: raise AIScanError(f'{k}: joins no recorded signal')
        seen[k] = rec['record_hash']
        if kind == 'signal':
            _check_provenance(rec, k)
            views[sid] = dict(signal=rec, shadows={}, measured=None, revisions={}, simulated={})
            continue
        v = views[sid]
        if kind == 'shadow':
            if (rec['snapshot_hash'], rec['plan_hash']) != (v['signal']['snapshot_hash'], v['signal']['plan_hash']):
                raise AIScanError(f'{k}: shadow read a different snapshot or plan')
            v['shadows'][rec['epoch']] = rec
            continue
        if _ms(rec['closed_at']) <= _ms(v['signal']['decided_at']): raise AIScanError(f'{k}: look-ahead outcome')
        if rec['source'] == 'measured':
            if v['signal']['rules']['status'] != 'taken':
                raise AIScanError(f"{k}: a measured outcome on a {v['signal']['rules']['status']} decision")
            try:
                cid = cost_revision_id(rec['actual_costs'], net_basis=rec['cost_basis'], cost_model_id=rec['cost_model_id'])
                pnl, r = measured_net(rec['actual_costs'])
            except (AIScanError, KeyError, TypeError) as ex:
                raise AIScanError(f'{k}: forged cost identity ({ex})')
            if cid != rec['cost_revision_id'] or not (_close(pnl, rec['net_pnl']) and _close(r, rec['net_r'])):
                raise AIScanError(f'{k}: forged cost identity (id or net does not match the actual-cost payload)')
            if any(o['rev'] == rec['rev'] for o in v['revisions'].values()):
                raise AIScanError(f"{k}: two actual-cost revisions share rev {rec['rev']} (ambiguous order)")
            v['revisions'][rec['cost_revision_id']] = rec           # the join: decision id + cost revision identifier
            if v['measured'] is None or rec['rev'] > v['measured']['rev']: v['measured'] = rec   # rev: order only
        else:
            v['simulated'][rec['cost_model_hash']] = rec
    return views


def fitting_view(records, manifest):
    """The ONLY replay the fitting path may use, bound to the sealed manifest: refuses (never silently filters) any
    signal whose decision candle lies inside the calibration or the holdout interval [start, end)."""
    verify_manifest(manifest)
    iv = {name: _interval(manifest[name], name) for name in ('calibration', 'holdout')}
    for rec in records:
        if rec.get('kind') != 'signal': continue
        t = _ms(rec['candle_open'])
        for name, (lo, hi) in iv.items():
            if lo <= t < hi: raise AIScanError(f"{rec['signal_id']}: a sealed-{name} row reached the fitting path")
    return fold(records)


def display(view, now=None):
    """Owner-facing labels. RULES (deterministic) and AI SHADOW are always separate; a probability is shown only when
    CALIBRATED (with its target), otherwise the literal UNVERIFIED; an INSUFFICIENT signal or shadow shows no score; a
    shadow older than STALE_BARS bars of its timeframe at `now` is INSUFFICIENT (stale), its absolute age always shown;
    measured and simulated outcomes never merge."""
    s = view['signal']
    out = dict(signal_id=s['signal_id'], rules=dict(label=LABEL_RULES, status=s['rules']['status'], plan=s['plan']),
               input_coverage=s['input_coverage'], snapshot_age_s=s['snapshot_age_s'])
    if s['status'] != 'OK':
        out['ai'] = dict(label=LABEL_INSUFFICIENT, reasons=list(s['insufficient']))
    else:
        ceil_s = stale_after_s(s['tf'])
        rows = []
        for _, r in sorted(view['shadows'].items(), key=lambda kv: (kv[1]['model_id'], kv[0])):
            age = None if now is None else (_ms(now) - _ms(r['created_at'])) // 1000
            row = dict(model_id=r['model_id'], epoch=r['epoch'], age_s=age)
            if r['status'] != 'OK':
                row.update(label=LABEL_INSUFFICIENT, reasons=[r['reason']])
            elif age is not None and age < 0:                         # created after `now`: a clock or forged record
                row.update(label=LABEL_INSUFFICIENT, reasons=['future_dated'])
            elif age is not None and age > ceil_s:
                row.update(label=LABEL_INSUFFICIENT, reasons=['stale'])
            else:
                row.update(label=LABEL_AI, opinion=r['opinion'], coverage=r['coverage'], disagreement=r['disagreement'],
                           probability=(dict(value=f"{r['probability']['p']:.0%}", target=r['probability']['target'])
                                        if r['probability']['state'] == PROB_CALIBRATED else LABEL_UNVERIFIED))
            rows.append(row)
        out['ai'] = rows
    m = view['measured']
    out['outcomes'] = dict(measured=m and {k: m[k] for k in ('cost_revision_id', 'rev', 'net_r', 'cost_basis',
                                                              'exit_reason')},
                           simulated={h: {k: o[k] for k in ('net_r', 'cost_basis', 'exit_reason')}
                                      for h, o in sorted(view['simulated'].items())})
    return out
