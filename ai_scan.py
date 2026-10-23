"""AI-SCAN-01 (#199): the immutable, causal signal-ledger schema of the shadow chart scanner. PAPER, shadow, report-only.

Pure functions over plain values: nothing here places, sizes, gates or cancels an order, imports the engine, or calls a
model, and nothing in the trading code reads what it builds. Three append-only record kinds join on the ML-SHADOW decision
id (ml_shadow.ledger.decision_id - deterministic, so a restart re-derives the same id):

- signal   one per candidate/decision: cutoff, snapshot hash, the deterministic plan + decision copied from the engine,
           the cost estimate, and INSUFFICIENT (with its reasons) when the causal inputs cannot support a read;
- shadow   one per (signal, model, prompt version): opinion, coverage, disagreement and a probability that stays
           UNVERIFIED unless a passed frozen calibration is attached. It names the plan and snapshot hashes it read and
           can never carry plan, sizing or order fields;
- outcome  revisioned (rev 0 = booked, higher = actual-cost revisions), closed strictly after the cutoff.

fold() replays records into one view per signal: an exact duplicate is a no-op (restart idempotency); a conflicting
replay, a join to an unknown signal, a hash mismatch or a look-ahead outcome fails closed with AIScanError.
See docs/AI_SCAN_01.md for the API contract.
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
LABEL_RULES, LABEL_AI, LABEL_UNVERIFIED, LABEL_INSUFFICIENT = 'RULES', 'AI SHADOW', 'UNVERIFIED', 'INSUFFICIENT'
PROB_UNVERIFIED, PROB_CALIBRATED = 'UNVERIFIED', 'CALIBRATED'
COST_BASES = ('booked', 'measured')
RATIONALE_MAX = 400
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


# ------------------------------------------------------------------ inputs -> INSUFFICIENT reasons
def snapshot_hash(snapshot):
    """The immutable causal input the shadow layer may read: last closed candle close + flat numeric features."""
    return sha256_hex(canonical_json({'last_close': _iso(snapshot['last_close']), 'features': snapshot['features']}))


def check_inputs(side, tf, cutoff_ms, snapshot, plan):
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
        feats = snapshot['features']
        if not feats: why.append('missing:features')
        bad = sorted(k for k, v in feats.items() if v is not None and not _num(v))
        why += [f'malformed:feature:{k}' for k in bad]
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
def build_signal(*, sleeve, strategy, family, symbol, side, tf, candle_open, rules_status, rules_reason, snapshot, plan,
                 costs=None):
    """One immutable signal record. candle_open = the decision candle's OPEN (engine sg['time'], engine.py _utc_ms
    'dec_open'); cutoff = its close. plan / rules / costs are copied from the deterministic engine snapshot."""
    side, tf = str(side).upper(), str(tf)
    if rules_status not in RULE_STATUSES: raise AIScanError(f'unknown rules status {rules_status!r}')
    for name, d in (('snapshot.features', (snapshot or {}).get('features') or {}), ('plan', plan or {}), ('costs', costs or {})):
        _no_secrets(d, name)
    cutoff_ms = _ms(candle_open) + TF_SEC.get(tf, 0) * 1000
    why = check_inputs(side, tf, cutoff_ms, snapshot, plan)
    feats = (snapshot or {}).get('features') or {}
    present = sum(1 for v in feats.values() if _num(v))
    rec = dict(kind='signal', schema=SCHEMA_VERSION,
               signal_id=decision_id(sleeve, strategy, symbol, side, tf, candle_open),
               sleeve=str(sleeve), strategy=str(strategy), family=str(family), symbol=str(symbol).upper(), side=side,
               tf=tf, candle_open=_iso(candle_open), cutoff=_iso(cutoff_ms),
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


def build_shadow(signal, *, model, prompt_version, created_at, opinion, coverage, disagreement, rationale='',
                 probability=None, calibration=None, read_snapshot_hash, read_plan_hash, **extra):
    """One shadow-committee result over the signal's immutable snapshot. Any plan/order/sizing field is refused; the
    probability is kept only with a PASSED frozen calibration, otherwise it is dropped and the state is UNVERIFIED."""
    if extra:
        bad = [k for k in extra if any(a in k.lower() for a in _AUTHORITY_KEYS)]
        raise AIScanError(f'shadow output may not carry {bad or sorted(extra)} (no plan or order authority)')
    if signal.get('status') != 'OK':
        raise AIScanError('an INSUFFICIENT signal gets no shadow score')
    if read_snapshot_hash != signal['snapshot_hash'] or read_plan_hash != signal['plan_hash']:
        raise AIScanError('the shadow read a different snapshot or plan than the signal recorded')
    if _ms(created_at) < _ms(signal['cutoff']):
        raise AIScanError('a shadow result created before the decision cutoff')
    if opinion not in OPINIONS: raise AIScanError(f'unknown opinion {opinion!r}')
    if not (_num(coverage) and 0 <= coverage <= 1 and _num(disagreement) and 0 <= disagreement <= 1):
        raise AIScanError('coverage and disagreement must be in [0, 1]')
    cal = calibration if isinstance(calibration, dict) and calibration.get('passed') is True and calibration.get('id') else None
    if cal is not None and _num(probability) and 0 <= probability <= 1:
        prob = dict(state=PROB_CALIBRATED, p=float(probability), calibration_id=str(cal['id']))
    else:
        prob = dict(state=PROB_UNVERIFIED)
    rec = dict(kind='shadow', schema=SCHEMA_VERSION, signal_id=signal['signal_id'], model=str(model),
               prompt_version=str(prompt_version), created_at=_iso(created_at), opinion=opinion, coverage=float(coverage),
               disagreement=float(disagreement), probability=prob, rationale=str(rationale or '')[:RATIONALE_MAX],
               snapshot_hash=read_snapshot_hash, plan_hash=read_plan_hash)
    return _seal(rec)


def build_outcome(signal, *, rev, closed_at, net_r, net_pnl, exit_reason, cost_basis, partial_exits=0, adds=0):
    """A revisioned outcome label: rev 0 = booked at close, each later rev = an actual-cost revision of the same trade."""
    if not (isinstance(rev, int) and not isinstance(rev, bool) and rev >= 0): raise AIScanError('rev must be an int >= 0')
    if cost_basis not in COST_BASES: raise AIScanError(f'unknown cost basis {cost_basis!r}')
    if _ms(closed_at) <= _ms(signal['cutoff']): raise AIScanError('an outcome closed at or before the decision cutoff')
    if not (_num(net_r) and _num(net_pnl)): raise AIScanError('outcome net_r / net_pnl must be finite numbers')
    rec = dict(kind='outcome', schema=SCHEMA_VERSION, signal_id=signal['signal_id'], rev=rev, closed_at=_iso(closed_at),
               net_r=float(net_r), net_pnl=float(net_pnl), exit_reason=str(exit_reason or '')[:64], cost_basis=cost_basis,
               partial_exits=int(partial_exits), adds=int(adds))
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
        key = {'signal': (sid,), 'shadow': (sid, rec.get('model'), rec.get('prompt_version')),
               'outcome': (sid, rec.get('rev'))}.get(kind)
        if key is None: raise AIScanError(f'unknown record kind {kind!r}')
        k = (kind,) + key
        if k in seen:
            if seen[k] != rec['record_hash']: raise AIScanError(f'{k}: conflicting replay of an immutable record')
            continue
        if kind != 'signal' and sid not in views: raise AIScanError(f'{k}: joins no recorded signal')
        seen[k] = rec['record_hash']
        if kind == 'signal':
            views[sid] = dict(signal=rec, shadows={}, outcome=None)
            continue
        v = views[sid]
        if kind == 'shadow':
            if (rec['snapshot_hash'], rec['plan_hash']) != (v['signal']['snapshot_hash'], v['signal']['plan_hash']):
                raise AIScanError(f'{k}: shadow read a different snapshot or plan')
            v['shadows'][f"{rec['model']}|{rec['prompt_version']}"] = rec
        else:
            if v['signal']['rules']['status'] == 'skipped': raise AIScanError(f'{k}: an outcome on a skipped decision')
            if _ms(rec['closed_at']) <= _ms(v['signal']['cutoff']): raise AIScanError(f'{k}: look-ahead outcome')
            if v['outcome'] is None or rec['rev'] > v['outcome']['rev']: v['outcome'] = rec
    return views


def display(view):
    """Owner-facing labels. RULES (deterministic) and AI SHADOW are always separate; a probability is shown only when
    CALIBRATED, otherwise the literal UNVERIFIED; an INSUFFICIENT signal shows no AI read at all."""
    s = view['signal']
    out = dict(signal_id=s['signal_id'], rules=dict(label=LABEL_RULES, status=s['rules']['status'], plan=s['plan']),
               input_coverage=s['input_coverage'])
    if s['status'] != 'OK':
        out['ai'] = dict(label=LABEL_INSUFFICIENT, reasons=list(s['insufficient']))
        return out
    out['ai'] = [dict(label=LABEL_AI, model=r['model'], opinion=r['opinion'], coverage=r['coverage'],
                      disagreement=r['disagreement'],
                      probability=(f"{r['probability']['p']:.0%}" if r['probability']['state'] == PROB_CALIBRATED
                                   else LABEL_UNVERIFIED))
                 for _, r in sorted(view['shadows'].items())]
    if view['outcome']: out['outcome'] = {k: view['outcome'][k] for k in ('rev', 'net_r', 'cost_basis', 'exit_reason')}
    return out
