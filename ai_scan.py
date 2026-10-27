"""AI-SCAN-01 (#199): the immutable, causal signal-ledger schema of the shadow chart scanner. PAPER, shadow, report-only.

Pure functions over plain values: nothing here places, sizes, gates or cancels an order, imports the engine, or calls a
model, and nothing in the trading code reads what it builds. Three append-only record kinds join on the ML-SHADOW decision
id (ml_shadow.ledger.decision_id - deterministic, so a restart re-derives the same id):

- signal   one per candidate/decision: decision instant, cutoff, the ingest time of every point-in-time input, a
           snapshot pinned to the exact cutoff, the deterministic plan + decision copied from the engine, the cost
           estimate, the snapshot's absolute age, and INSUFFICIENT (with its reasons) when the inputs cannot support a
           read - including a snapshot older than STALE_BARS bars of its timeframe;
- shadow   one per (signal, epoch). The epoch hashes the immutable model id, prompt-template hash, sampling parameters,
           feature/schema hash, label definition and cost-model hash - any change opens a new, unverified epoch. Either
           an opinion with coverage, disagreement, raw-response hash and a probability that stays UNVERIFIED unless the
           epoch's calibration passes the frozen gate, or INSUFFICIENT (queue full / timeout / budget / remote error).
           It names the plan and snapshot hashes it read and can never carry plan, sizing or order fields;
- outcome  measured (the engine's trade) joined by the immutable decision id + its actual-cost revision identifier
           `cost_revision_id` (sha256 of the revision's actual-cost fields); the engine's monotonic `rev` (#196 cost_rev,
           engine.py _row_changed; 0 = booked) orders and deduplicates revisions only, never the economic identity; or
           simulated (a deterministic causal replay under the frozen
           policy and cost model - the only outcome an untaken candidate gets) keyed by that cost-model hash. The two are
           never merged. Closed strictly after the decision instant.

fold() replays records into one view per signal: an exact duplicate is a no-op (restart idempotency); a conflicting
replay, a join to an unknown signal, a hash mismatch or a look-ahead outcome fails closed with AIScanError. fitting_view()
refuses any record inside the sealed holdout. The contract is docs/AI_SCAN_01.md (#200); the record/API detail is
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
CAUSAL_INPUTS = ('funding', 'universe', 'exchange_rules')   # each carries its own ingest (availability) time
STALE_BARS = 2                                       # timeframe-relative freshness ceiling (Codex 6452143340 #1)
LABEL_RULES, LABEL_AI, LABEL_UNVERIFIED, LABEL_INSUFFICIENT = 'RULES', 'AI SHADOW', 'UNVERIFIED', 'INSUFFICIENT'
PROB_UNVERIFIED, PROB_CALIBRATED = 'UNVERIFIED', 'CALIBRATED'
# #200: P(net_R > 0 after actual-model fees, funding and fills at the earlier of the strategy exit or its max-bar horizon)
TARGET = dict(label='net_r_gt_0', horizon='strategy_exit_or_max_bars', cost_basis='actual_cost_model')
GATE = dict(min_n=500, min_bin_n=50, max_ece=0.05)  # + Brier improvement CI wholly above 0, window disjoint
DEMOTE_ECE, DEMOTE_FAILS = 0.08, 2                   # rolling ECE above, or this many consecutive failures to beat base
SHADOW_FAILURES = ('queue_full', 'timeout', 'budget_exhausted', 'remote_error')
COST_BASES = {'measured': ('booked', 'measured'), 'simulated': ('modelled',)}
RATIONALE_MAX = 400
# cost_revision_id input (Codex 6453104005): versioned, pinned field list AND order. Bump the schema to change either.
COST_REV_SCHEMA = 'ai-scan-cost-rev-v1'
COST_REV_FIELDS = ('fees_actual', 'funding', 'pnl_gross', 'pnl_fill_adj', 'pnl_net', 'net_r')
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
    for k in d:
        if SECRET_NAME_RE.search(str(k)) or _ID_RE.search(str(k)):
            raise AIScanError(f'{where}: field {k!r} may carry a credential or exchange identifier')


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


def cost_revision_id(actual_costs, *, net_basis, cost_model_id):
    """The actual-cost revision identifier (Codex 6453104005): sha256 over the canonical, versioned tuple
    [COST_REV_SCHEMA, [[field, value] in COST_REV_FIELDS order], net_basis, cost_model_id]. Only the pinned fields are
    read - timestamps, ids and presentation fields are ignored, so they never change the id; any pinned cost field, the
    net basis, the cost-model id or a schema bump does. Each pinned field must be a finite number (a missing or partial
    cost fails closed - it has no identity yet). With the decision id this is the economic join key; the integer
    cost_rev only orders and deduplicates. Collision rule: two different records under one (decision id, id) are a
    conflicting replay and refuse (fold and the ledger), never a silent overwrite."""
    if not isinstance(actual_costs, dict): raise AIScanError('a cost revision identifier needs the actual-cost fields')
    vals = []
    for f in COST_REV_FIELDS:
        v = actual_costs.get(f)
        if not _num(v): raise AIScanError(f'cost revision field {f!r} missing or not a finite number')
        vals.append([f, float(v)])
    if net_basis not in COST_BASES['measured']: raise AIScanError(f'unknown net basis {net_basis!r}')
    if not (isinstance(cost_model_id, str) and cost_model_id):
        raise AIScanError('a cost revision identifier needs the cost-model id')
    return sha256_hex(canonical_json([COST_REV_SCHEMA, vals, net_basis, cost_model_id]))


# ------------------------------------------------------------------ inputs -> INSUFFICIENT reasons
def snapshot_hash(snapshot):
    """The immutable causal input the shadow layer may read: the cutoff candle close + flat numeric features."""
    return sha256_hex(canonical_json({'last_close': _iso(snapshot['last_close']), 'features': snapshot['features']}))


def _as_of(why, name, t, decided_ms):
    """Ingest time of one input: missing or later than the decision instant is a reason, never a guess."""
    try:
        if _ms(t) > decided_ms: why.append(f'after_decision:{name}')
    except (TypeError, ValueError):
        why.append(f'missing:{name}')


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
            why.append('missing:last_close')
        _as_of(why, 'snapshot', snapshot.get('available_at'), decided_ms)
        feats = snapshot['features']
        if not feats: why.append('missing:features')
        bad = sorted(k for k, v in feats.items() if v is not None and not _num(v))
        why += [f'malformed:feature:{k}' for k in bad]
    for name in CAUSAL_INPUTS:                                          # point-in-time funding / universe / filters
        _as_of(why, name, (inputs or {}).get(name), decided_ms)
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
    for name, d in (('snapshot.features', (snapshot or {}).get('features') or {}), ('plan', plan or {}), ('costs', costs or {})):
        _no_secrets(d, name)
    cutoff_ms, decided_ms = _ms(candle_open) + TF_SEC.get(tf, 0) * 1000, _ms(decided_at)
    if decided_ms < cutoff_ms: raise AIScanError('a decision before its candle closed')
    why = check_inputs(side, tf, cutoff_ms, decided_ms, snapshot, plan, inputs)
    feats = (snapshot or {}).get('features') or {}
    present = sum(1 for v in feats.values() if _num(v))
    as_of = {}
    for name in CAUSAL_INPUTS:
        try: as_of[name] = _iso((inputs or {}).get(name))
        except (TypeError, ValueError): as_of[name] = None
    try:
        age_s = (cutoff_ms - _ms((snapshot or {}).get('last_close'))) // 1000
    except (TypeError, ValueError):
        age_s = None
    rec = dict(kind='signal', schema=SCHEMA_VERSION,
               signal_id=decision_id(sleeve, strategy, symbol, side, tf, candle_open),
               sleeve=str(sleeve), strategy=str(strategy), family=str(family), symbol=str(symbol).upper(), side=side,
               tf=tf, candle_open=_iso(candle_open), cutoff=_iso(cutoff_ms), decided_at=_iso(decided_ms), inputs_as_of=as_of,
               rules=dict(status=rules_status, reason=str(rules_reason or '')[:200]),
               input_coverage=[present, len(feats)], snapshot_age_s=age_s,
               snapshot_hash=None if any(w.startswith(('missing:snapshot', 'missing:last_close', 'malformed:feature'))
                                         for w in why) else snapshot_hash(snapshot),
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


def calibration_passes(c, epoch):
    """The frozen #200 gate for one epoch: the window is disjoint from fitting, walk-forward and the holdout; >= 500
    resolved labels and >= 50 per reported bin; ECE <= 0.05; the paired-bootstrap 95% CI of the Brier improvement over
    the base-rate predictor wholly above zero; the target is TARGET; and the epoch has not been demoted since."""
    if not isinstance(c, dict): return False
    n, b, ece, lo = c.get('n'), c.get('min_bin_n'), c.get('ece'), c.get('brier_improvement_ci_lo')
    return (bool(c.get('id')) and c.get('epoch') == epoch and c.get('target') == TARGET and c.get('disjoint') is True
            and _num(n) and n >= GATE['min_n'] and _num(b) and b >= GATE['min_bin_n'] and _num(ece) and ece <= GATE['max_ece']
            and _num(lo) and lo > 0 and not demoted(c.get('windows')))


def _identity(model_id, prompt_hash, sampling, feature_schema_hash, cost_model_hash):
    if not (isinstance(model_id, str) and model_id and isinstance(sampling, dict)
            and _hex64(prompt_hash, feature_schema_hash, cost_model_hash)):
        raise AIScanError('a shadow needs an immutable model id, sampling parameters and sha256 prompt / feature-schema /'
                          ' cost-model hashes')
    return epoch_of(model_id, prompt_hash, sampling, feature_schema_hash, cost_model_hash)


def _shadow_base(signal, created_at, read_snapshot_hash, read_plan_hash, extra):
    if extra:
        bad = [k for k in extra if any(a in k.lower() for a in _AUTHORITY_KEYS)]
        raise AIScanError(f'shadow output may not carry {bad or sorted(extra)} (no plan or order authority)')
    if signal.get('status') != 'OK':
        raise AIScanError('an INSUFFICIENT signal gets no shadow score')
    if read_snapshot_hash != signal['snapshot_hash'] or read_plan_hash != signal['plan_hash']:
        raise AIScanError('the shadow read a different snapshot or plan than the signal recorded')
    if _ms(created_at) < _ms(signal['decided_at']):
        raise AIScanError('a shadow result created before the decision')


def build_shadow(signal, *, model_id, prompt_hash, sampling, feature_schema_hash, cost_model_hash, raw_response_hash,
                 created_at, opinion, coverage, disagreement, rationale='', probability=None, calibration=None,
                 read_snapshot_hash, read_plan_hash, **extra):
    """One shadow-committee result over the signal's immutable snapshot. Any plan/order/sizing field is refused; the
    probability is kept only when this epoch's calibration passes the frozen gate, otherwise it is dropped (UNVERIFIED)."""
    _shadow_base(signal, created_at, read_snapshot_hash, read_plan_hash, extra)
    ep = _identity(model_id, prompt_hash, sampling, feature_schema_hash, cost_model_hash)
    if not _hex64(raw_response_hash): raise AIScanError('a shadow result needs the sha256 of its raw response')
    if opinion not in OPINIONS: raise AIScanError(f'unknown opinion {opinion!r}')
    if not (_num(coverage) and 0 <= coverage <= 1 and _num(disagreement) and 0 <= disagreement <= 1):
        raise AIScanError('coverage and disagreement must be in [0, 1]')
    prob = (dict(state=PROB_CALIBRATED, p=float(probability), calibration_id=str(calibration['id']), target=dict(TARGET))
            if calibration_passes(calibration, ep) and _num(probability) and 0 <= probability <= 1
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


def build_outcome(signal, *, source, closed_at, net_r, net_pnl, exit_reason, cost_basis, cost_revision_id=None,
                  rev=None, cost_model_hash=None, partial_exits=0, adds=0):
    """measured: the engine's own trade, joined by (decision id, cost_revision_id) - the actual-cost revision identifier.
    rev is the history row's monotonic cost_rev (0 = booked, each restatement +1), kept only to order and deduplicate
    revisions. simulated: a deterministic causal replay under the frozen policy, keyed by its cost-model hash - the only
    outcome an untaken candidate may get."""
    if source not in COST_BASES: raise AIScanError(f'unknown outcome source {source!r}')
    if cost_basis not in COST_BASES[source]: raise AIScanError(f'cost basis {cost_basis!r} is not valid for {source}')
    if source == 'measured':
        if not (_hex64(cost_revision_id) and isinstance(rev, int) and not isinstance(rev, bool) and rev >= 0
                and cost_model_hash is None):
            raise AIScanError('a measured outcome is keyed by a sha256 cost_revision_id and ordered by an int rev >= 0'
                              ' (and no cost-model hash)')
        if signal['rules']['status'] == 'skipped': raise AIScanError('a measured outcome on a skipped decision')
    elif not (cost_revision_id is None and rev is None and _hex64(cost_model_hash)):
        raise AIScanError('a simulated outcome is keyed by its sha256 cost-model hash (and no cost revision)')
    if _ms(closed_at) <= _ms(signal['decided_at']): raise AIScanError('an outcome closed at or before the decision')
    if not (_num(net_r) and _num(net_pnl)): raise AIScanError('outcome net_r / net_pnl must be finite numbers')
    rec = dict(kind='outcome', schema=SCHEMA_VERSION, signal_id=signal['signal_id'], source=source,
               cost_revision_id=cost_revision_id, rev=rev, cost_model_hash=cost_model_hash, closed_at=_iso(closed_at), net_r=float(net_r), net_pnl=float(net_pnl),
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


def fold(records):
    """Replay records (file order) into {signal_id: view}. Idempotent on exact duplicates; fails closed otherwise."""
    views, seen = {}, {}
    for rec in records:
        _verify(rec)
        sid, kind = rec['signal_id'], rec['kind']
        key = {'signal': (sid,), 'shadow': (sid, rec.get('epoch')),
               'outcome': (sid, rec.get('source'), rec.get('cost_revision_id'), rec.get('cost_model_hash'))}.get(kind)
        if key is None: raise AIScanError(f'unknown record kind {kind!r}')
        k = (kind,) + key
        if k in seen:
            if seen[k] != rec['record_hash']: raise AIScanError(f'{k}: conflicting replay of an immutable record')
            continue
        if kind != 'signal' and sid not in views: raise AIScanError(f'{k}: joins no recorded signal')
        seen[k] = rec['record_hash']
        if kind == 'signal':
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
            if v['signal']['rules']['status'] == 'skipped': raise AIScanError(f'{k}: a measured outcome on a skipped decision')
            if any(o['rev'] == rec['rev'] for o in v['revisions'].values()):
                raise AIScanError(f"{k}: two actual-cost revisions share rev {rec['rev']} (ambiguous order)")
            v['revisions'][rec['cost_revision_id']] = rec           # the join: decision id + cost revision identifier
            if v['measured'] is None or rec['rev'] > v['measured']['rev']: v['measured'] = rec   # rev: order only
        else:
            v['simulated'][rec['cost_model_hash']] = rec
    return views


def fitting_view(records, holdout):
    """The ONLY replay the fitting / calibration paths may use: refuses (never silently filters) any signal whose
    decision candle lies inside the sealed AI-SCAN-01 holdout [start, end)."""
    if not (isinstance(holdout, dict) and holdout.get('start') and holdout.get('end')):
        raise AIScanError('fitting requires the sealed holdout bounds')
    lo, hi = _ms(holdout['start']), _ms(holdout['end'])
    for rec in records:
        if rec.get('kind') == 'signal' and lo <= _ms(rec['candle_open']) < hi:
            raise AIScanError(f"{rec['signal_id']}: a sealed-holdout row reached the fitting path")
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
