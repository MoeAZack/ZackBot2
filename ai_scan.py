"""AI-SCAN-01 (#199): the immutable, causal signal-ledger schema of the shadow chart scanner. PAPER, shadow, report-only.

Pure functions over plain values: nothing here places, sizes, gates or cancels an order, imports the engine, or calls a
model, and nothing in the trading code reads what it builds. Three append-only record kinds join on the ML-SHADOW decision
id (ml_shadow.ledger.decision_id - deterministic, so a restart re-derives the same id):

- signal   one per candidate/decision: decision instant, cutoff, the availability time of every causal input, snapshot
           hash, the deterministic plan + decision copied from the engine, the cost estimate, and INSUFFICIENT (with its
           reasons) when the inputs cannot support a read;
- shadow   one per (signal, epoch). The epoch hashes the pinned model id, prompt template hash and sampling parameters,
           so any change opens a new calibration epoch. Opinion, coverage, disagreement, a raw-response hash, and a
           probability that stays UNVERIFIED unless a PASSED calibration of the same epoch and a declared target is
           attached. It names the plan and snapshot hashes it read and can never carry plan, sizing or order fields;
- outcome  per (signal, source, rev): source 'measured' (the engine's trade; rev 0 = booked, higher = actual-cost
           revisions) or 'simulated' (a causal replay on the same cost model, the only outcome an untraded candidate
           gets). The two are never merged. Closed strictly after the decision instant.

fold() replays records into one view per signal: an exact duplicate is a no-op (restart idempotency); a conflicting
replay, a join to an unknown signal, a hash mismatch or a look-ahead outcome fails closed with AIScanError.
The owner-facing contract is docs/AI_SCAN_01.md (#200); the record/API detail is docs/AI_SCAN_01_SCHEMA.md.
"""
import math
import re

from ml_shadow.common import SECRET_NAME_RE, canonical_json, sha256_hex, to_utc
from ml_shadow.ledger import decision_id

SCHEMA_VERSION = 'ai-scan-schema-v1'
FLAG = 'AI_SCAN'                                     # default OFF; report-only API/UI surface
TF_SEC = {'5m': 300, '15m': 900, '1h': 3600, '4h': 14400}
SIDES = ('LONG', 'SHORT')
RULE_STATUSES = ('taken', 'skipped', 'pending')     # ml_shadow.common.STATUSES
OPINIONS = ('LONG', 'SHORT', 'NO_TRADE')
STALE_BARS = 2                                      # newest closed candle older than this many bars before cutoff: stale
CAUSAL_INPUTS = ('funding', 'universe', 'exchange_rules')   # each carries its own availability (as-of) time
LABEL_RULES, LABEL_AI, LABEL_UNVERIFIED, LABEL_INSUFFICIENT = 'RULES', 'AI SHADOW', 'UNVERIFIED', 'INSUFFICIENT'
PROB_UNVERIFIED, PROB_CALIBRATED = 'UNVERIFIED', 'CALIBRATED'
SOURCES = ('measured', 'simulated')
COST_BASES = {'measured': ('booked', 'measured'), 'simulated': ('modelled',)}
TARGET_KEYS = ('label', 'horizon_bars', 'cost_basis')   # what a calibrated probability is a probability OF
RATIONALE_MAX = 400
_HEX64 = re.compile(r'^[0-9a-f]{64}$')
_ID_RE = re.compile(r'(order|client|trade|exchange)[\s_\-]?(o?id|key)|orderid|clientoid', re.I)
_AUTHORITY_KEYS = ('plan', 'entry', 'stop', 'target', 'targets', 'size', 'qty', 'lot', 'leverage', 'order', 'exit',
                   'risk', 'settings', 'eligible', 'rotation')


class AIScanError(ValueError):
    """Fail-closed refusal: ambiguous join, conflicting replay, look-ahead, authority or secret breach."""


def _iso(t):
    return to_utc(t).strftime('%Y-%m-%dT%H:%M:%SZ')


def _ms(t):
    return int(to_utc(t).value // 1_000_000)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _seal(rec):
    rec['record_hash'] = sha256_hex(canonical_json({k: v for k, v in rec.items() if k != 'record_hash'}))
    return rec


def _no_secrets(d, where):
    for k in d:
        if SECRET_NAME_RE.search(str(k)) or _ID_RE.search(str(k)):
            raise AIScanError(f'{where}: field {k!r} may carry a credential or exchange identifier')


def epoch_of(model_id, prompt_hash, sampling):
    """The calibration epoch: any change of the pinned model id, prompt template hash or sampling parameters is a new one."""
    return sha256_hex(canonical_json(dict(model_id=model_id, prompt_hash=prompt_hash, sampling=sampling)))[:16]


# ------------------------------------------------------------------ inputs -> INSUFFICIENT reasons
def snapshot_hash(snapshot):
    """The immutable causal input the shadow layer may read: last closed candle close + flat numeric features."""
    return sha256_hex(canonical_json({'last_close': _iso(snapshot['last_close']), 'features': snapshot['features']}))


def _as_of(why, name, t, decided_ms):
    """Availability (ingest) time of one input: missing or later than the decision instant is a reason, never a guess."""
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
            elif tf in TF_SEC and cutoff_ms - lc > STALE_BARS * TF_SEC[tf] * 1000: why.append('stale:snapshot')
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
    AVAILABLE (ingested) by decided_at. plan / rules / costs are copied from the deterministic engine snapshot."""
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
    rec = dict(kind='signal', schema=SCHEMA_VERSION,
               signal_id=decision_id(sleeve, strategy, symbol, side, tf, candle_open),
               sleeve=str(sleeve), strategy=str(strategy), family=str(family), symbol=str(symbol).upper(), side=side,
               tf=tf, candle_open=_iso(candle_open), cutoff=_iso(cutoff_ms), decided_at=_iso(decided_ms), inputs_as_of=as_of,
               rules=dict(status=rules_status, reason=str(rules_reason or '')[:200]),
               input_coverage=[present, len(feats)],
               snapshot_hash=None if any(w.startswith(('missing:snapshot', 'missing:last_close', 'malformed:feature'))
                                         for w in why) else snapshot_hash(snapshot),
               plan=None if any(w.startswith(('missing:plan', 'malformed:plan', 'malformed:targets')) for w in why)
               else dict(entry_lo=plan['entry_lo'], entry_hi=plan['entry_hi'], stop=plan['stop'], targets=list(plan['targets'])),
               costs=dict(costs) if costs else None,
               status='INSUFFICIENT' if why else 'OK', insufficient=why)
    rec['plan_hash'] = sha256_hex(canonical_json(rec['plan'])) if rec['plan'] else None
    return _seal(rec)


def _calibrated(calibration, epoch, probability):
    """A probability is kept only for a PASSED calibration of this exact epoch with a declared target (the pass bar -
    n per bin, ECE/Brier vs the B0 base rate, disjoint window - is the harness's verdict, recorded as `passed`)."""
    c = calibration if isinstance(calibration, dict) else {}
    tgt = c.get('target')
    ok = (c.get('passed') is True and c.get('id') and c.get('epoch') == epoch and isinstance(tgt, dict)
          and all(tgt.get(k) not in (None, '') for k in TARGET_KEYS) and _num(probability) and 0 <= probability <= 1)
    if not ok: return dict(state=PROB_UNVERIFIED)
    return dict(state=PROB_CALIBRATED, p=float(probability), calibration_id=str(c['id']),
                target={k: tgt[k] for k in TARGET_KEYS})


def build_shadow(signal, *, model_id, prompt_hash, sampling, raw_response_hash, created_at, opinion, coverage,
                 disagreement, rationale='', probability=None, calibration=None, read_snapshot_hash, read_plan_hash, **extra):
    """One shadow-committee result over the signal's immutable snapshot. Any plan/order/sizing field is refused; the
    probability is kept only with a passed calibration of the same epoch, otherwise it is dropped and UNVERIFIED."""
    if extra:
        bad = [k for k in extra if any(a in k.lower() for a in _AUTHORITY_KEYS)]
        raise AIScanError(f'shadow output may not carry {bad or sorted(extra)} (no plan or order authority)')
    if signal.get('status') != 'OK':
        raise AIScanError('an INSUFFICIENT signal gets no shadow score')
    if read_snapshot_hash != signal['snapshot_hash'] or read_plan_hash != signal['plan_hash']:
        raise AIScanError('the shadow read a different snapshot or plan than the signal recorded')
    if _ms(created_at) < _ms(signal['decided_at']):
        raise AIScanError('a shadow result created before the decision')
    if not (isinstance(model_id, str) and model_id and isinstance(sampling, dict)
            and all(isinstance(h, str) and _HEX64.match(h) for h in (prompt_hash, raw_response_hash))):
        raise AIScanError('a shadow needs a pinned model id, sampling parameters and sha256 prompt / raw-response hashes')
    if opinion not in OPINIONS: raise AIScanError(f'unknown opinion {opinion!r}')
    if not (_num(coverage) and 0 <= coverage <= 1 and _num(disagreement) and 0 <= disagreement <= 1):
        raise AIScanError('coverage and disagreement must be in [0, 1]')
    ep = epoch_of(model_id, prompt_hash, sampling)
    rec = dict(kind='shadow', schema=SCHEMA_VERSION, signal_id=signal['signal_id'], epoch=ep, model_id=model_id,
               prompt_hash=prompt_hash, sampling=dict(sampling), raw_response_hash=raw_response_hash,
               created_at=_iso(created_at), opinion=opinion, coverage=float(coverage), disagreement=float(disagreement),
               probability=_calibrated(calibration, ep, probability), rationale=str(rationale or '')[:RATIONALE_MAX],
               snapshot_hash=read_snapshot_hash, plan_hash=read_plan_hash)
    return _seal(rec)


def build_outcome(signal, *, source, rev, closed_at, net_r, net_pnl, exit_reason, cost_basis, partial_exits=0, adds=0):
    """A revisioned outcome label. measured: the engine's own trade (rev 0 booked, later revs actual-cost revisions);
    simulated: a causal replay on the same cost model - the only outcome an untraded (skipped) candidate may get."""
    if source not in SOURCES: raise AIScanError(f'unknown outcome source {source!r}')
    if not (isinstance(rev, int) and not isinstance(rev, bool) and rev >= 0): raise AIScanError('rev must be an int >= 0')
    if cost_basis not in COST_BASES[source]: raise AIScanError(f'cost basis {cost_basis!r} is not valid for {source}')
    if source == 'measured' and signal['rules']['status'] == 'skipped':
        raise AIScanError('a measured outcome on a skipped decision')
    if _ms(closed_at) <= _ms(signal['decided_at']): raise AIScanError('an outcome closed at or before the decision')
    if not (_num(net_r) and _num(net_pnl)): raise AIScanError('outcome net_r / net_pnl must be finite numbers')
    rec = dict(kind='outcome', schema=SCHEMA_VERSION, signal_id=signal['signal_id'], source=source, rev=rev,
               closed_at=_iso(closed_at), net_r=float(net_r), net_pnl=float(net_pnl), exit_reason=str(exit_reason or '')[:64],
               cost_basis=cost_basis, partial_exits=int(partial_exits), adds=int(adds))
    return _seal(rec)


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
        key = {'signal': (sid,), 'shadow': (sid, rec.get('epoch')), 'outcome': (sid, rec.get('source'), rec.get('rev'))}.get(kind)
        if key is None: raise AIScanError(f'unknown record kind {kind!r}')
        k = (kind,) + key
        if k in seen:
            if seen[k] != rec['record_hash']: raise AIScanError(f'{k}: conflicting replay of an immutable record')
            continue
        if kind != 'signal' and sid not in views: raise AIScanError(f'{k}: joins no recorded signal')
        seen[k] = rec['record_hash']
        if kind == 'signal':
            views[sid] = dict(signal=rec, shadows={}, outcomes={})
            continue
        v = views[sid]
        if kind == 'shadow':
            if (rec['snapshot_hash'], rec['plan_hash']) != (v['signal']['snapshot_hash'], v['signal']['plan_hash']):
                raise AIScanError(f'{k}: shadow read a different snapshot or plan')
            v['shadows'][rec['epoch']] = rec
        else:
            if rec['source'] == 'measured' and v['signal']['rules']['status'] == 'skipped':
                raise AIScanError(f'{k}: a measured outcome on a skipped decision')
            if _ms(rec['closed_at']) <= _ms(v['signal']['decided_at']): raise AIScanError(f'{k}: look-ahead outcome')
            cur = v['outcomes'].get(rec['source'])
            if cur is None or rec['rev'] > cur['rev']: v['outcomes'][rec['source']] = rec
    return views


def display(view):
    """Owner-facing labels. RULES (deterministic) and AI SHADOW are always separate; a probability is shown only when
    CALIBRATED (with what it is a probability of), otherwise the literal UNVERIFIED; an INSUFFICIENT signal shows no AI
    read at all; measured and simulated outcomes are shown separately, never merged."""
    s = view['signal']
    out = dict(signal_id=s['signal_id'], rules=dict(label=LABEL_RULES, status=s['rules']['status'], plan=s['plan']),
               input_coverage=s['input_coverage'])
    if s['status'] != 'OK':
        out['ai'] = dict(label=LABEL_INSUFFICIENT, reasons=list(s['insufficient']))
    else:
        out['ai'] = [dict(label=LABEL_AI, model_id=r['model_id'], epoch=r['epoch'], opinion=r['opinion'],
                          coverage=r['coverage'], disagreement=r['disagreement'],
                          probability=(dict(value=f"{r['probability']['p']:.0%}", target=r['probability']['target'])
                                       if r['probability']['state'] == PROB_CALIBRATED else LABEL_UNVERIFIED))
                     for _, r in sorted(view['shadows'].items(), key=lambda kv: (kv[1]['model_id'], kv[0]))]
    out['outcomes'] = {src: {k: o[k] for k in ('rev', 'net_r', 'cost_basis', 'exit_reason')}
                       for src, o in sorted(view['outcomes'].items())}
    return out
