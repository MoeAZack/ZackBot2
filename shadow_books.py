"""TRUE-BOOK-02 (TB02a): strategy-isolated OBSERVATION-ONLY shadow ledgers - identity, evidence log, ranking gate.

Pure helpers plus one append-only file store. Nothing here places, sizes, gates or cancels an order; nothing reads the
exchange. The five installed $2,000 family books stay the real PAPER execution boundary (true_books.py).

Identity (Codex 6246971221 #2): an instance is strategy key + a digest of its frozen causal config (timeframe, sides,
symbol spec, risk, effective merged management, signal params). Two sleeves with the same identity are ONE instance that
keeps every sleeve id as provenance. Capital share / slot caps / enabled flags are not part of it: each instance gets its
own normalized virtual $2,000 book and no slot cap may suppress a signal. A material config change = a new digest = a new
lineage; nothing is carried across.

Costs (#3): one frozen named policy, hashed into every row. Values are the live engine's (engine.FEE_EST taker per fill,
trade_audit.EXEC_SLIP_BPS adverse per fill); a test pins them together.

Evidence (#5): the only non-reproducible inputs (observed decision marks, the testnet funding/mark source) are appended to
one JSONL file per cohort, never rewritten or rotated, with a per-cycle watermark. Replay is idempotent (records dedupe by
id); a torn last line or a missing watermark is reported, never repaired silently.
"""
import json
import os
import threading

import strategies as S
import trade_audit as TA
from ml_shadow.common import canonical_json, sha256_hex

MODEL = 'shadow-model-v1'
START = 2000.0
MIN_N = 20                                   # closed episodes before an instance may be ranked (Codex contract)
COST_POLICY = dict(name='live-taker-v1', fee_per_fill=TA.FEE_EST, slip_bps_per_fill=TA.EXEC_SLIP_BPS,
                   tick='against_trader', entry_ref='decision_mark', funding='testnet_timeline_exact')
COST_POLICY_HASH = sha256_hex(canonical_json(COST_POLICY))[:16]
CAUSAL_FIELDS = ('key', 'tf', 'sides', 'symbols', 'risk')


def config_of(sleeve):
    """The frozen causal config of one sleeve: what changes which signals fire and how a lot is sized and managed."""
    key = sleeve['key']
    st = S.STRATEGIES[key]
    sides = sleeve.get('sides') or st.get('sides')
    sym = sleeve.get('symbols', 'all')
    return dict(key=key, tf=sleeve.get('tf', '4h'), sides=sorted(sides) if isinstance(sides, (list, tuple)) else sides,
                symbols=sorted(sym) if isinstance(sym, (list, tuple)) else sym, risk=float(sleeve['risk']),
                mgmt=S.merge_mgmt(key, sleeve.get('mgmt')), params=dict(st.get('params') or {}))


def identity(sleeve):
    cfg = config_of(sleeve)
    return f"{cfg['key']}@{sha256_hex(canonical_json(cfg))[:12]}", cfg


def instances(sleeves):
    """{instance_id: {key, config, sleeves:[ids]}} over every sleeve whose strategy is registered; duplicates dedupe."""
    out = {}
    for sl in sleeves or ():
        if sl.get('key') not in S.STRATEGIES:
            continue
        iid, cfg = identity(sl)
        x = out.setdefault(iid, dict(instance=iid, key=cfg['key'], config=cfg, sleeves=[]))
        if sl.get('id') not in x['sleeves']:
            x['sleeves'].append(sl.get('id'))
    for x in out.values():
        x['sleeves'].sort(key=str)
    return out


class EvidenceLog:
    """Append-only JSONL evidence for one cohort. Never rotated, never rewritten. Records dedupe by `id`."""
    KINDS = ('mark', 'funding', 'watermark')

    def __init__(self, root, cohort_id):
        if not cohort_id or any(c in str(cohort_id) for c in '/\\:'):
            raise ValueError('bad cohort id')
        self.path = os.path.join(str(root), str(cohort_id), 'inputs.jsonl')
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.Lock()
        self.torn = 0
        self._seen = {}
        for r in self._read():
            self._seen.setdefault(r['id'], r)

    def _read(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, 'r', encoding='utf-8') as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    self.torn += 1
                    continue
                if isinstance(r, dict) and r.get('kind') in self.KINDS and r.get('id'):
                    yield r

    def append(self, rec):
        """Append one record; returns 'added', 'replay' (same id, same content) or 'conflict' (same id, other content)."""
        if rec.get('kind') not in self.KINDS or not rec.get('id'):
            raise ValueError('record needs a known kind and an id')
        with self._lock:
            old = self._seen.get(rec['id'])
            if old is not None:
                return 'replay' if canonical_json(old) == canonical_json(rec) else 'conflict'
            line = canonical_json(rec)
            with open(self.path, 'a', encoding='utf-8', newline='\n') as fh:
                fh.write(line + '\n')
                fh.flush()
                os.fsync(fh.fileno())
            self._seen[rec['id']] = json.loads(line)
            return 'added'

    def mark(self, instance, sym, decision_ts, close_ts, px, mark_ts, side):
        return self.append(dict(kind='mark', id=f'm|{instance}|{sym}|{close_ts}|{side}', instance=instance, sym=sym,
                                side=side, decision_ts=int(decision_ts), close_ts=int(close_ts), mark_ts=int(mark_ts),
                                px=float(px)))

    def watermark(self, cycle_close_ts, instances_seen):
        return self.append(dict(kind='watermark', id=f'w|{int(cycle_close_ts)}', close_ts=int(cycle_close_ts),
                                instances=sorted(instances_seen)))

    def records(self, kind=None):
        return [r for r in self._seen.values() if kind is None or r['kind'] == kind]

    def marks(self, instance):
        """{(sym, close_ts, side): record} for one instance."""
        return {(r['sym'], r['close_ts'], r['side']): r for r in self.records('mark') if r['instance'] == instance}

    def coverage(self, expected_closes, instance):
        """Closed candles (ms) the cycle never watermarked for this instance - a visible gap, never backfilled."""
        seen = {r['close_ts'] for r in self.records('watermark') if instance in r['instances']}
        return sorted(t for t in expected_closes if t not in seen)


def mark_usable(rec, close_ts, max_age_ms):
    """A decision mark is usable only when observed at/after the decision close and not stale (Codex DECISION 2)."""
    if rec is None:
        return False, 'missing_decision_mark'
    if rec['mark_ts'] < close_ts or rec['decision_ts'] < close_ts:
        return False, 'mark_before_close'
    if rec['decision_ts'] - rec['mark_ts'] > max_age_ms or rec['px'] <= 0:
        return False, 'stale_decision_mark'
    return True, None


def rank(rows, min_n=MIN_N):
    """Order complete instances with >= min_n closed episodes by net R; every other row is listed unranked with why."""
    ok, out = [], []
    for r in rows:
        why = 'incomplete' if r.get('incomplete') else ('n<%d' % min_n if (r.get('closed') or 0) < min_n else None)
        (out if why else ok).append(dict(r, rank=None, unranked=why) if why else r)
    ok.sort(key=lambda r: (-(r.get('net_r') or 0.0), r['instance']))
    return [dict(r, rank=i + 1, unranked=None) for i, r in enumerate(ok)] + sorted(out, key=lambda r: r['instance'])
