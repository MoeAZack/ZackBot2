"""TRUE-BOOK-02 (TB02a): strategy-isolated OBSERVATION-ONLY shadow ledgers (`shadow-model-v1`).

The five installed $2,000 family books (true_books.py) stay the real PAPER execution boundary. This module never places,
sizes, gates or cancels an order and imports nothing executable: the engine hands it plain values and discards whatever it
returns (the hooks are exception-contained statements).

Instances (Codex 6246971221 #1/#2): one normalized virtual $2,000 ledger per strategy key + frozen causal config digest
(timeframe, sides, symbol spec, risk, effective merged management, signal params). Duplicate sleeves are one instance that
keeps every sleeve id as provenance; a material config change is a new digest and a new lineage. Capital share, slot caps
and enable flags are not causal here: every qualifying signal is its own episode, whatever the live book decided.

Entry (Codex 6247026077 DECISION 2): the observed executable mark at the post-close decision instant. The mark must be at
or after the decision close and no more than MAX_REF_LAG_S later; otherwise the episode is INCOMPLETE - never backfilled,
never a later bar open. Fill = ref adjusted by the frozen adverse slippage; fee = fee_rate x notional per fill.

Management (DECISION 1): an isolated reducer over the research primitives - backtest.path_points is THE intrabar path
policy; the per-leg order (gap through the stop at the open, adverse leg -> stop, favourable leg -> tp1 / tp_r, stop-first
when the open's stop is inside the candle, ratchet after each leg: be_r, trail_atr; signal / time exits at the close; the
trail uses the ATR of the last CLOSED candle) mirrors backtest.run's walk_path. Not modelled in v1: DCA baskets, pyramid
adds, runner, trailing TP, the TP ladder, trailing entries - an instance using one is INCOMPLETE (unsupported_mgmt:<x>).

Funding: settled on every open episode exposed at the funding instant from the supplied (testnet) timeline; a missing rate
makes only the exposed episodes INCOMPLETE. Never account income, never a silent substitute.

Durability (#5): every input is appended (one JSON line, fsynced) BEFORE it is applied; restart replays the file through the
same reducer, so replay is idempotent. A torn line, a foreign-cohort record, a conflicting replay or a cycle watermark that
misses a known instance is a coverage gap: the ledger stays readable but nothing is rankable.
"""
import hashlib
import json
import os
import threading
from datetime import datetime, timezone

import feasibility as F
import strategies as S
from backtest import path_points

MODEL_VERSION = 'shadow-model-v1'
MANAGEMENT = 'research-primitives-v1'
START = 2000.0
MIN_N = 20
MAX_REF_LAG_S = 300                  # decision mark observed at most this long after the decision close
UNSUPPORTED = ('dca', 'pyramid', 'runner', 'ttp', 'tps', 'trail_entry')
FUNDING_GRID_S = 8 * 3600              # Binance USD-M default settlement grid (00/08/16 UTC): exposure across it needs a record
MODEL_V1 = dict(version=MODEL_VERSION, management=MANAGEMENT, fee_rate=0.0005, adverse_slippage_bps=2.0,
                entry_reference='observed_post_close_mark')
TF_S = {'1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '2h': 7200, '4h': 14400, '1d': 86400}


def _cj(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False, default=str)


def _h(x, n=16):
    return hashlib.sha256(_cj(x).encode('utf-8')).hexdigest()[:n]


def _ts(x):
    """ISO-8601 (UTC assumed when naive) or epoch seconds/ms -> epoch seconds (float)."""
    if x is None: return None
    if isinstance(x, (int, float)): return float(x) / 1000.0 if x > 1e11 else float(x)
    d = datetime.fromisoformat(str(x).replace('Z', '+00:00'))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def _num(x):
    try: v = float(x)
    except (TypeError, ValueError): return None
    return v if v == v and v not in (float('inf'), float('-inf')) else None


def config_of(slot):
    """Frozen causal config of one sleeve/slot - what changes which signals fire and how an episode is sized and managed."""
    key = slot['key']
    st = S.STRATEGIES.get(key, {})
    sides = slot.get('sides') or st.get('sides')
    sym = slot.get('symbols', 'all')
    cfg = dict(key=key, tf=slot.get('tf', '4h'), sides=sorted(sides) if isinstance(sides, (list, tuple)) else sides,
               symbols=sorted(sym) if isinstance(sym, (list, tuple)) else sym, risk=float(slot['risk']),
               mgmt=S.merge_mgmt(key, slot.get('mgmt')), params={**(st.get('params') or {}), **(slot.get('params') or {})})
    for k in ('trail_entry', 'when', 'pump_guard'):
        if slot.get(k) is not None: cfg[k] = slot[k]
    return cfg


def identity(slot):
    cfg = config_of(slot)
    dg = _h(cfg)
    return f"{cfg['key']}@{dg[:12]}", dg, cfg


def _spread(vals):
    v = sorted(x for x in vals if x is not None)
    if not v: return dict(mean=None, median=None, n=0)
    m = len(v) // 2
    return dict(mean=round(sum(v) / len(v), 6), median=round(v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2, 6), n=len(v))


class ShadowBook:
    def __init__(self, path, cohort_id, model, start=START, min_n=MIN_N):
        if not cohort_id: raise ValueError('cohort_id required')
        if (model or {}).get('version') != MODEL_VERSION: raise ValueError(f'model must be {MODEL_VERSION}')
        self.path, self.cohort_id, self.start, self.min_n = str(path), str(cohort_id), float(start), int(min_n)
        self.model = dict(model)
        self.fee = float(model.get('fee_rate', 0.0005))
        self.slip = float(model.get('adverse_slippage_bps', 2.0)) / 1e4
        self.model_hash = _h(self.model)
        self._lock = threading.RLock()
        self.instances, self.episodes, self.gaps = {}, {}, []
        self.cycles, self.funding_seen, self.nav_path, self.marks = {}, {}, {}, {}
        self.seq, self.clock = 0, None
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        self._replay()

    # ------------------------------------------------------------------ store
    def _replay(self):
        if not os.path.exists(self.path): return
        with open(self.path, 'r', encoding='utf-8') as fh:
            for n, line in enumerate(fh, 1):
                if not line.strip(): continue
                try:
                    rec = json.loads(line)
                    if not isinstance(rec, dict) or 'kind' not in rec: raise ValueError
                except ValueError:
                    self.gaps.append(f'torn_record:line {n}'); continue
                if rec.get('cohort') != self.cohort_id:
                    self.gaps.append(f'foreign_cohort_record:line {n}'); continue
                if rec.get('model') != self.model_hash:
                    self.gaps.append(f'model_mismatch:line {n}'); continue
                self.seq = max(self.seq, int(rec.get('seq', 0)))
                self._apply(rec)

    def _append(self, rec):
        line = _cj(rec)
        with open(self.path, 'a', encoding='utf-8', newline='\n') as fh:
            fh.write(line + '\n'); fh.flush(); os.fsync(fh.fileno())

    def _record(self, kind, **body):
        """Write-ahead: the record is durable before the reducer sees it (a failed write changes nothing in memory)."""
        with self._lock:
            rec = dict(seq=self.seq + 1, cohort=self.cohort_id, model=self.model_hash, kind=kind, **body)
            self._append(rec)
            self.seq += 1
            return self._apply(rec)

    def _apply(self, rec):
        return getattr(self, '_a_' + rec['kind'])(rec)

    def _tick(self, t):
        if t is not None and (self.clock is None or t > self.clock): self.clock = t

    @staticmethod
    def _qty_at(ep, t):
        """Quantity held at instant t (entry strictly before t; an intrabar exit is stamped just before its candle close,
        a close-phase exit at the close itself, so a position closed by a signal at a funding instant still pays it)."""
        if not ep.get('qty_hist') or ep['t_ref'] >= t: return 0.0
        q = 0.0
        for tt, qq in ep['qty_hist']:
            if tt < t or (tt == t and qq > 0): q = qq
            else: break
        return q

    # ------------------------------------------------------------------ public observer API (values in, documents out)
    def on_signal(self, slot, symbol, side, signal, mark, live_decision, rule=None):
        iid, dg, cfg = identity(slot)
        dt = (signal or {}).get('time')
        cid = _h(dict(i=iid, s=symbol, d=side, t=dt), 24)
        doc = dict(candidate_id=cid, instance_id=iid, config_digest=dg, key=cfg['key'], symbol=symbol, side=side,
                   decision_time=dt)
        ep, inst = self.episodes.get(cid), self.instances.get(iid)
        if ep is not None and inst is not None and slot.get('id') in inst['sleeve_ids']:
            return dict(doc, status=ep['status'], replay=True)          # an exact replay appends nothing
        return self._record('signal', slot_id=slot.get('id'), config=cfg, instance_id=iid, config_digest=dg,
                            candidate_id=cid, symbol=symbol, side=side, signal=signal, mark=mark,
                            live_decision=live_decision, rule=rule)

    def on_bar(self, tf, bars, signals=None):
        return self._record('bar', tf=tf, bars=bars, signals=signals or {})

    def on_funding(self, symbol, at, rate, mark):
        return self._record('funding', symbol=symbol, at=at, rate=rate, mark=mark)

    def complete_cycle(self, cycle_id, tf, decision_time, instance_ids):
        return self._record('cycle', cycle_id=cycle_id, tf=tf, decision_time=decision_time,
                            instance_ids=sorted(instance_ids))

    def mark_incomplete(self, instance_id, reason):
        return self._record('incomplete', instance_id=instance_id, reason=str(reason))

    # ------------------------------------------------------------------ reducer
    def _a_signal(self, rec):
        iid, cfg = rec['instance_id'], rec['config']
        inst = self.instances.get(iid)
        if inst is None:
            inst = self.instances[iid] = dict(instance_id=iid, key=cfg['key'], config_digest=rec['config_digest'],
                                              config=cfg, tf=cfg['tf'], sleeve_ids=[], incomplete=[],
                                              first_seen=_ts(rec['signal'].get('time')) if rec.get('signal') else None)
            bad = [k for k in UNSUPPORTED if k in cfg['mgmt'] or k in cfg]
            if bad: inst['incomplete'].append('unsupported_mgmt:' + ','.join(bad))
        if rec.get('slot_id') not in inst['sleeve_ids']:
            inst['sleeve_ids'].append(rec.get('slot_id')); inst['sleeve_ids'].sort(key=str)
        cid = rec['candidate_id']
        doc = dict(candidate_id=cid, instance_id=iid, config_digest=rec['config_digest'], key=cfg['key'],
                   symbol=rec['symbol'], side=rec['side'], decision_time=rec['signal'].get('time'))
        if cid in self.episodes:
            return dict(doc, status=self.episodes[cid]['status'])
        ep = self.episodes[cid] = dict(candidate_id=cid, instance_id=iid, key=cfg['key'], symbol=rec['symbol'],
                                       side=rec['side'], decision_time=doc['decision_time'],
                                       live_decision=rec.get('live_decision'), incomplete=[], status='incomplete',
                                       entry_ref=None, entry_ref_time=None, entry=None, exit=None, exit_time=None,
                                       exit_reason=None, gross=0.0, fees=0.0, funding=0.0, bars=0)
        why = self._entry(ep, cfg, rec)
        if why: ep['incomplete'].append(why)
        doc['status'] = ep['status']
        return doc

    def _entry(self, ep, cfg, rec):
        sig, mk = rec.get('signal') or {}, rec.get('mark')
        sd = 1 if rec['side'] == 'LONG' else -1 if rec['side'] == 'SHORT' else 0
        if not sd: return 'bad_side'
        t_dec = _ts(sig.get('time'))
        if t_dec is None: return 'missing_decision_time'
        if not mk: return 'missing_decision_mark'
        t_ref, ref = _ts(mk.get('time')), _num(mk.get('price'))
        if t_ref is None or ref is None or ref <= 0: return 'missing_decision_mark'
        if t_ref < t_dec or t_ref - t_dec > MAX_REF_LAG_S: return 'stale_decision_mark'
        atr = _num(sig.get('atr'))
        if atr is None or atr <= 0: return 'missing_signal_atr'
        m = cfg['mgmt']
        px = ref * (1 + sd * self.slip)
        nav = self.start + sum(e['gross'] - e['fees'] + e['funding'] for e in self.episodes.values()
                               if e['instance_id'] == ep['instance_id'] and e['status'] == 'closed')
        risk_usd = nav * cfg['risk']
        z = F.risk_qty(m, risk_usd, px, atr, sd)
        qty = z['qty']
        rule = rec.get('rule')
        if rule is not None:
            d = F.size_check(qty, qty, px, rule)
            if d['ok'] is False:
                ep.update(status='rejected', exit_reason='exchange_filter:' + d['code']); return None
            qty = d['qty']
        if not qty or qty <= 0: return 'zero_quantity'
        ep.update(status='open', sd=sd, entry_ref=ref, entry_ref_time=mk.get('time'), t_ref=t_ref, entry=px, e0=px,
                  avg=px, qty=qty, q0=qty, stop=z['stop'], R=z['R'], risk=risk_usd, best=px, worst=px, tp1=False,
                  atr=atr, last=ref, last_t=t_ref, fees=qty * px * self.fee, notional=qty * px, qty_hist=[(t_ref, qty)],
                  funded={}, exit_t=None)
        self._tick(t_ref)
        return None

    def _close(self, ep, px_level, frac, why, t):
        """t: the candle close (epoch s). Intrabar fills are stamped just before it, close-phase fills at it."""
        sd = ep['sd']
        tt = t if why in ('signal', 'time') else t - 1e-3
        q = ep['qty'] if frac >= 1 else ep['qty'] * frac
        px = px_level * (1 - sd * self.slip)
        ep['gross'] += sd * (px - ep['avg']) * q
        ep['fees'] += q * px * self.fee
        ep['notional'] += q * px
        ep['qty'] -= q
        if frac >= 1 or ep['qty'] <= 1e-12:
            ep.update(qty=0.0, status='closed', exit=px, exit_time=self._iso(t), exit_t=tt, exit_reason=why, last=px)
            ep['qty_hist'].append((tt, 0.0))
            return True
        ep['qty_hist'].append((tt, ep['qty']))
        return False

    @staticmethod
    def _iso(t):
        return datetime.fromtimestamp(t, timezone.utc).isoformat()

    def _ratchet(self, ep, m):
        sd = ep['sd']
        tighten = (lambda x: max(ep['stop'], x)) if sd == 1 else (lambda x: min(ep['stop'], x))
        if m.get('be_r') and sd * (ep['best'] - (ep['e0'] + sd * m['be_r'] * ep['R'])) >= 0:
            ep['stop'] = tighten(ep['avg'])
        if m.get('trail_atr'):
            ep['stop'] = tighten(ep['best'] - sd * m['trail_atr'] * ep['atr'])

    def _manage(self, ep, m, b, t, sig):
        """One closed candle for one open episode: backtest.run's walk_path order for the v1 exit set."""
        sd = ep['sd']
        o, h, l, c = (_num(b.get(k)) for k in ('o', 'h', 'l', 'c'))
        if None in (o, h, l, c): ep['incomplete'].append('bad_bar'); return
        ep['bars'] += 1
        ep['worst'] = min(ep['worst'], l) if sd == 1 else max(ep['worst'], h)
        if (o <= ep['stop']) if sd == 1 else (o >= ep['stop']):
            ep['best'] = max(ep['best'], o) if sd == 1 else min(ep['best'], o)
            self._close(ep, o, 1, 'stop', t); return
        fav = lambda lv, x: sd * (x - lv) >= 0
        adv = lambda lv, x: sd * (lv - x) >= 0
        stop_in_candle = adv(ep['stop'], l if sd == 1 else h)
        pts = path_points(o, h, l, c, sd)
        for k in range(len(pts)):
            a_, b_ = pts[max(0, k - 1)], pts[k]
            mv = sd * (b_ - a_)
            if k and mv < 0 and adv(ep['stop'], b_):
                st = ep['stop']
                ep['best'] = max(ep['best'], a_) if sd == 1 else min(ep['best'], a_)
                self._close(ep, st if sd * (a_ - st) >= 0 else a_, 1, 'stop', t); return
            if (k == 0 or mv > 0) and not stop_in_candle:
                while True:
                    cand = []
                    if m.get('tp1_r') and not ep['tp1']:
                        lv = ep['e0'] + sd * m['tp1_r'] * ep['R']
                        if fav(lv, b_): cand.append((sd * lv, 2, 'tp1', lv))
                    if m.get('tp_r'):
                        lv = ep['e0'] + sd * m['tp_r'] * ep['R']
                        if fav(lv, b_): cand.append((sd * lv, 4, 'tp', lv))
                    if not cand: break
                    _, _, ev, lv = min(cand)
                    fill = lv                                    # backtest parity: target exits fill AT the level (gaps too)
                    if ev == 'tp1':
                        ep['tp1'] = True
                        if self._close(ep, fill, m.get('tp1_frac', 0.5), 'tp1', t):
                            ep['best'] = max(ep['best'], fill) if sd == 1 else min(ep['best'], fill); return
                    else:
                        ep['best'] = max(ep['best'], fill) if sd == 1 else min(ep['best'], fill)
                        self._close(ep, fill, 1, 'tp', t); return
            ep['best'] = max(ep['best'], b_) if sd == 1 else min(ep['best'], b_)
            if k: self._ratchet(ep, m)
        ex = bool(sig and sig.get('lx' if sd == 1 else 'sx'))
        if ex or (m.get('max_bars') and ep['bars'] - 1 >= m['max_bars']):
            self._close(ep, c, 1, 'signal' if ex else 'time', t)

    def _a_bar(self, rec):
        tf, bars, sigs = rec['tf'], rec.get('bars') or {}, rec.get('signals') or {}
        for ep in self.episodes.values():
            inst = self.instances[ep['instance_id']]
            if ep['status'] != 'open' or inst['tf'] != tf or ep['symbol'] not in bars: continue
            b = bars[ep['symbol']]
            t = _ts(b.get('time'))
            if t is None or t <= ep['last_t']: continue                 # a bar closed at/before the entry or replayed
            ss = sigs.get(ep['instance_id']) or sigs.get(ep['key']) or {}
            if 'unsupported_mgmt' not in ' '.join(inst['incomplete']):
                self._manage(ep, inst['config']['mgmt'], b, t, ss.get(ep['symbol']))
            if ep['status'] == 'open':
                ep['last'], ep['last_t'] = _num(b.get('c')) or ep['last'], t
                a = _num(b.get('atr'))
                if a and a > 0: ep['atr'] = a                            # ATR of this closed candle: used from the NEXT one
            else:
                ep['last_t'] = t
        for b in bars.values():
            self._tick(_ts((b or {}).get('time')))
        self._settle_known()
        for iid in {e['instance_id'] for e in self.episodes.values()}:
            self.nav_path.setdefault(iid, []).append(self._nav(iid))
        return dict(seq=self.seq)

    def _a_funding(self, rec):
        sym, at, rate, mk = rec['symbol'], _ts(rec.get('at')), _num(rec.get('rate')), _num(rec.get('mark'))
        key = (sym, at)
        prev = self.funding_seen.get(key)
        body = (rec.get('rate'), rec.get('mark'), rec.get('at'))
        if prev is not None:
            if prev != body: self.gaps.append(f'conflicting_funding:{sym}@{rec.get("at")}')
            return dict(seq=self.seq, replay=True)
        self.funding_seen[key] = body
        self._tick(at)
        self._settle_known()
        return dict(seq=self.seq)

    def _settle_known(self):
        """Settle every recorded funding instant on every episode holding the symbol at that instant - once per episode and
        instant, also when the record arrives after the episode closed (the collector may lag the bars)."""
        for ep in self.episodes.values():
            if not ep.get('qty_hist'): continue
            for (sym, at), (rate, mk, raw) in self.funding_seen.items():
                rate, mk = _num(rate), _num(mk)
                if sym != ep['symbol'] or at is None or at in ep['funded']: continue
                if ep['exit_t'] is None and (self.clock is None or at > self.clock): continue
                q = self._qty_at(ep, at)
                if q <= 0: continue
                ep['funded'][at] = True
                if rate is None or mk is None or mk <= 0:
                    ep['incomplete'].append(f'funding_missing:{sym}@{raw}'); continue
                ep['funding'] -= ep['sd'] * q * mk * rate                    # positive rate: longs pay, shorts receive

    def _a_cycle(self, rec):
        cid = rec['cycle_id']
        body = (rec['tf'], rec['decision_time'], tuple(rec['instance_ids']))
        if cid in self.cycles:
            if self.cycles[cid] != body: self.gaps.append(f'conflicting_cycle_replay:{cid}')
            return dict(seq=self.seq)
        prev = [v for v in self.cycles.values() if v[0] == rec['tf']]
        self.cycles[cid] = body
        t = _ts(rec['decision_time'])
        if prev and rec['tf'] in TF_S:
            last = max(_ts(v[1]) for v in prev)
            if t - last > TF_S[rec['tf']] * 1.5: self.gaps.append(f'missed_cycle:{rec["tf"]} after {last:.0f}')
        seen = set(rec['instance_ids'])
        for iid, inst in self.instances.items():
            if inst['tf'] == rec['tf'] and (inst['first_seen'] is None or inst['first_seen'] <= t) and iid not in seen:
                self.gaps.append(f'instance {iid} absent from cycle {cid}')
        return dict(seq=self.seq)

    def _a_incomplete(self, rec):
        inst = self.instances.get(rec['instance_id'])
        if inst is None: self.gaps.append(f'unknown_instance:{rec["instance_id"]}')
        elif rec['reason'] not in inst['incomplete']: inst['incomplete'].append(rec['reason'])
        return dict(seq=self.seq)

    # ------------------------------------------------------------------ read side
    def _unreal(self, ep):
        return ep['sd'] * (ep['last'] - ep['avg']) * ep['qty'] if ep['status'] == 'open' else 0.0

    def _nav(self, iid):
        eps = [e for e in self.episodes.values() if e['instance_id'] == iid]
        return self.start + sum(e['gross'] - e['fees'] + e['funding'] + self._unreal(e) for e in eps)

    def snapshot(self):
        with self._lock:
            return dict(instances={k: dict(v) for k, v in self.instances.items()},
                        episodes={k: dict(v) for k, v in self.episodes.items()},
                        coverage=dict(complete=not self.gaps, gaps=list(self.gaps), records=self.seq))

    def _funding_unread(self, ep):
        """Settlement-grid instants this episode was exposed at with no funding record for its symbol: never a silent zero."""
        if not ep.get('qty_hist'): return []
        end = ep['exit_t'] if ep['exit_t'] is not None else self.clock
        if end is None: return []
        b = (int(ep['t_ref'] // FUNDING_GRID_S) + 1) * FUNDING_GRID_S
        out = []
        while b <= end:
            if self._qty_at(ep, b) > 0 and (ep['symbol'], float(b)) not in self.funding_seen:
                out.append(f'funding_unread:{ep["symbol"]}@{self._iso(b)}')
            b += FUNDING_GRID_S
        return out

    def _row(self, iid):
        inst = self.instances[iid]
        eps = [e for e in self.episodes.values() if e['instance_id'] == iid]
        closed = [e for e in eps if e['status'] == 'closed']
        why = list(inst['incomplete']) + sorted({w for e in eps for w in e['incomplete'] + self._funding_unread(e)})
        nets = [e['gross'] - e['fees'] + e['funding'] for e in closed]
        rs = [n / e['risk'] for n, e in zip(nets, closed) if e.get('risk')]
        gross = sum(e['gross'] for e in eps)
        fees = sum(e['fees'] for e in eps)
        funding = sum(e['funding'] for e in eps)
        unreal = sum(self._unreal(e) for e in eps if e.get('sd'))
        nav = self.start + gross - fees + funding + unreal
        path, peak, dd = [self.start] + self.nav_path.get(iid, []) + [nav], self.start, 0.0
        for v in path:
            peak = max(peak, v); dd = min(dd, v / peak - 1)
        wins = [x for x in nets if x > 0]
        loss = -sum(x for x in nets if x <= 0)
        r_of = lambda e, px: e['sd'] * (px - e['e0']) / e['R'] if e.get('R') else None
        mfe = [r_of(e, e['best']) for e in closed]
        mae = [r_of(e, e['worst']) for e in closed]
        exit_r = [r_of(e, e['exit'] / (1 - e['sd'] * self.slip)) for e in closed]
        exits, rej = {}, {}
        for e in closed: exits[e['exit_reason']] = exits.get(e['exit_reason'], 0) + 1
        for e in eps:
            r = (e.get('live_decision') or {}).get('reason') or (e.get('live_decision') or {}).get('status')
            if r: rej[r] = rej.get(r, 0) + 1
        return dict(instance_id=iid, key=inst['key'], config_digest=inst['config_digest'], sleeve_ids=inst['sleeve_ids'],
                    model=MODEL_VERSION, model_hash=self.model_hash, episodes=len(eps), closed=len(closed),
                    open=sum(1 for e in eps if e['status'] == 'open'), nav=nav, realized=sum(nets), gross=gross, unrealized=unreal, fees=fees, funding=funding, roi=nav / self.start - 1,
                    max_dd=dd, net_r=sum(rs), avg_r=sum(rs) / len(rs) if rs else None,
                    expectancy=sum(nets) / len(nets) if nets else None,
                    win_rate=len(wins) / len(nets) if nets else None, pf=sum(wins) / loss if loss > 0 else None,
                    turnover=sum(e.get('notional', 0.0) for e in eps) / self.start,
                    hold_bars=_spread([e['bars'] for e in closed]), mfe_r=_spread(mfe), mae_r=_spread(mae),
                    giveback_r=_spread([a - b for a, b in zip(mfe, exit_r) if a is not None and b is not None]),
                    exits=exits, live_decisions=rej, incomplete=why, complete=not why and not self.gaps)

    def compute(self):
        with self._lock:
            rows = [self._row(iid) for iid in sorted(self.instances)]
            ok = [r for r in rows if r['complete'] and r['closed'] >= self.min_n]
            ok.sort(key=lambda r: (-r['net_r'], r['instance_id']))
            rk = {r['instance_id']: i + 1 for i, r in enumerate(ok)}
            for r in rows:
                r['rankable'] = r['instance_id'] in rk
                r['rank'] = rk.get(r['instance_id'])
            return dict(model=dict(self.model, hash=self.model_hash), min_n=self.min_n, cohort_id=self.cohort_id,
                        coverage=dict(complete=not self.gaps, gaps=list(self.gaps)), strategies=rows)
