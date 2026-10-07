"""T05a (local draft): causal trade audit + opportunity funnel - OBSERVE ONLY, no trading authority.

Codex pre-design contract (PR #8) -> where it lives (docs/reviews/T05a_review_request.md maps it to the tests):
- Observe only: nothing here changes a lot's trading fields, targets, runners, DCA, orders, sizing, stops or strategy
  selection. The engine stores what these functions return under lot['ex'] (tracking) and lot['ap'] (policies
  declared at entry) and hands EVENTS to its bounded, non-blocking audit writer (trade_audit.jsonl, separate from
  fills.jsonl). Every function here returns None / a 'not evaluated' record on junk and never raises.
- Events (one JSON line each, schema version AUDIT_VERSION in 'v', finite-or-null only - see clean()): EVENT_KINDS.
- PRICE excursion (MFE/MAE of the mark samples) is kept apart from the LIFECYCLE net-P&L peak/trough; causal
  policies (kind='causal_policy', predeclared at entry with parameters + version) apart from the labelled
  hindsight ceiling (kind='hindsight_ceiling') and from research what-ifs (kind='what_if', not predeclared).
- Every causal decision carries info_cutoff and decision_at (time + observation sequence) and is WITHHELD unless the
  information cutoff is before the decision (_cutoff()).
- Candle highs/lows are labelled upper bounds unless a lower-timeframe mark sample proves the touch (hold_eval()).
- Runner attribution compares the SAME remaining quantity through a FIFO quantity cohort ledger (cohort_ledger()).
- The mark loop does no disk/network I/O: observe() only updates memory and checkpoint() returns a rate-limited
  event for the writer's in-memory queue. load_checkpoints() is the single reader, used at engine start only.
- T05b outage state marks samples as missing (observe(missing=True)) instead of using possibly stale marks.
"""
import json
import math
import re
import statistics
from datetime import datetime, timedelta, timezone

AUDIT_VERSION = 3        # schema version of every trade_audit.jsonl line ('v')
POLICY_VERSION = 2       # version of the predeclared policy set stored on a lot at entry (lot['ap']['v']); v2 adds
#                          breakeven_after_costs + regime_exit (owner scope 2026-10-07); a v1 lot keeps its v1 set
EVENT_KINDS = ('funnel', 'hold_eval', 'excursion', 'cohort', 'runner', 'trade_audit', 'coverage')
FEE_EST = 0.0005         # must equal engine.FEE_EST (the engine passes its own value; a test pins the two together)
PATH_MAX = 64            # live price path kept on the lot: at most this many (t, mark) points, decimated in time order
SNAP_MAX = 48            # state snapshots (avg, qty, target, R, e0, realized, fees) kept on the lot
GAPS_MAX = 8             # tracking gaps (engine restarts) kept on the lot; more are only counted
MISS_MAX = 8             # missing-sample intervals (outage / no marks) kept on the lot; more are only counted
CKPT_MIN_S = 900         # excursion checkpoint events: at most one per lot per 15 min, plus the bounded forced ones
SAMPLE_GAP_S = 60        # no mark sample for longer than this (manage runs every ~8 s) = a missing-sample interval
COH_MAX = 64             # cohorts listed in a record (grid lots can have many); more are only counted
STR_MAX = 1000           # any string in an event is capped at this length
# Fills that ADD quantity to a lot: every `why` the engine books through _create_lot ('entry') or _add_qty/_apply_add
# (engine.py: pyramid_add, safety_order, entry_fallback; grid.py: grid_buy / grid_sell). Every other fill kind is a
# close (_apply_close / the reconcile 'stop' fill) - close texts are open-ended ('grid_<range reason>', exit reasons),
# so the finite ADD set is the whitelist. tests/test_trade_audit.py scans engine.py + grid.py for every add `why`
# literal and fails if one is missing here; cohort_ledger() also reports qty_mismatch if the fills ever disagree
# with the lot quantity (an unknown add kind would show up there).
ENTRY_KINDS = ('entry', 'pyramid_add', 'safety_order', 'entry_fallback', 'grid_buy', 'grid_sell')
RUNNER_WHY = ('take_profit_1', 'basket_tp_part', 'take_profit_ladder')   # partial take-profits that leave a runner
TRAIL_ATR_DEFAULT = 2.0        # predeclared: trailing stop k x ATR behind the running best when the lot has no trail_atr
TIME_CAP_BARS_DEFAULT = 48     # predeclared: close at the first observation at/after entry + N bars
RULE_TARGET = 'close the remainder at the planned target'
RULE_TRAIL = 'trailing stop k x ATR from the running best since entry'
RULE_RUNNER = 'close the runner quantity at its activation'
RULE_BE = 'breakeven after costs: once +arm_r x R in favour, close when the net P&L after fees falls back to zero'
RULE_REGIME = 'regime exit: close at the first mark after a closed candle of the lot tf with the trend against the position'
BE_ARM_R = 1.0                 # predeclared: the breakeven-after-costs stop arms at +1R (mark vs the entry price)
EXEC_SLIP_BPS = 2.0            # modelled adverse slippage (bps) for the executable MFE (metrics.executable_mfe_usd)
REGIME_STALE_BARS = 2          # a closed-candle trend snapshot older than this many bars at a decision = 'unknown'
CTX_ADDS_MAX = 16              # add-fill regime snapshots kept on a lot (lot['ap']['ctx']['adds']); more are only counted
FLAGS = ('never_green', 'green_to_red', 'gave_back_gt_50pct_mfe', 'dca_into_trend', 'long_in_bear_regime',
         'short_in_bull_regime')          # failure classes on a trade_audit record (definitions: docs/reviews/T05a_review_request.md)
SEG_DIMS = ('strategy', 'side', 'symbol', 'tf', 'regime', 'dca', 'runner')
_TF_UNIT = {'m': 60, 'h': 3600, 'd': 86400, 'w': 604800}


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# ------------------------------------------------------------------ event hygiene: finite-or-null, no secrets / raw dumps
# Allowed in audit files: symbol, side, sleeve id, lot id ('<sleeve>|<symbol>|<side>|<epoch>'), times, prices, quantities,
# P&L numbers, reason texts/codes (URL query strings, {...} blobs and long tokens removed), policy parameters.
# Never: API keys/secrets/signatures, Telegram token/chat ids, exchange order ids / client ids / stop tags, raw exchange
# responses, account balances or identifiers. Keys matching _DENY are dropped wherever they appear.
_DENY = re.compile(r'api_?key|api_?secret|secret|signature|token|chat|telegram|listenkey|password|e_?mail|orderid|order_id|'
                   r'clientorder|client_order|stop_id|^cid$|^tag$|account|balance|^uid$|^raw$|response|executedqty|avgprice', re.I)
_QS = re.compile(r'\?\S*')
_BLOB = re.compile(r'[{\[][^{}\[\]]*[}\]]')
_LONG = re.compile(r'[A-Za-z0-9]{32,}')                     # API keys / signatures; slugs with _ or - survive
# Binance -2015 texts carry the bot host's public IP ('request ip: x.x.x.x'); order errors carry numeric order / client
# ids. Removed from every free-text string (all strings except the lot id under the key 'id', whose last part is an
# epoch). IPv6 needs '::' or all 8 groups, so ISO times ('12:00:00') never match.
_REQ_IP = re.compile(r'request\s*ip\s*[:=]?\s*[0-9A-Fa-f:.\[\]%]+', re.I)
_IPV4 = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
_IPV6 = re.compile(r'(?<![0-9A-Fa-f:])(?:(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|(?=[0-9A-Fa-f:]*::)(?:[0-9A-Fa-f]{1,4}:{1,2}){1,7}[0-9A-Fa-f]{0,4}|::[0-9A-Fa-f]{0,4})(?![0-9A-Fa-f:])')
_DIGITS = re.compile(r'(?<![\d.])\d{8,}(?!\d|\.\d)')       # order / client ids (8+ digit integers); decimals are kept


def _safe_text(s, n=STR_MAX, keep_ids=False):
    s = _QS.sub('', str(s))
    for _ in range(3): s = _BLOB.sub('{...}', s)
    s = _LONG.sub('[redacted]', s)
    if not keep_ids:
        s = _REQ_IP.sub('request ip: [ip]', s)
        s = _IPV6.sub('[ip]', _IPV4.sub('[ip]', s))
        s = _DIGITS.sub('[id]', s)
    return s[:n]


def clean(x, _d=0, _k=None):
    """A JSON-safe deep copy of an event: non-finite numbers -> None, denied keys dropped, strings scrubbed (IPs and
    8+ digit ids too, except the lot id under 'id') and capped, depth/length bounded, unknown types -> None. Also the
    copy that decouples the queued event from live lot state."""
    try:
        if _d > 8: return None
        if x is None or isinstance(x, bool): return x
        if isinstance(x, int): return x
        if isinstance(x, float): return x if math.isfinite(x) else None
        if isinstance(x, str): return _safe_text(x, keep_ids=_k == 'id')
        if isinstance(x, dict): return {str(k)[:60]: clean(v, _d + 1, str(k)) for k, v in list(x.items())[:200] if not _DENY.search(str(k))}
        if isinstance(x, (list, tuple)): return [clean(v, _d + 1) for v in list(x)[:300]]
        if hasattr(x, 'item'): return clean(x.item(), _d + 1, _k)            # numpy scalars
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ predeclared policies (stored on the lot AT ENTRY)
def _atr_now(lot):
    """(atr, src): atr0, else the latest atr_now, else the initial stop distance R."""
    for k in ('atr0', 'atr_now', 'R'):
        if _num(lot.get(k)) and lot[k] > 0: return float(lot[k]), k
    return None, None


def declare(lot, t, late=False, why=None, seq=-1):
    """The causal policy set of this lot, fixed with its parameters and version BEFORE any decision: called by the
    engine when the lot is created (seq -1 = before the first observation). An upgraded lot (opened before T05a) is
    declared late with coverage 'partial' and seq = the next observation index - 0.5. Returns a dict or None."""
    try:
        bar = tf_seconds(lot.get('tf'))
        t0 = _ts(lot.get('opened'))
        g = lot.get('mgmt') if isinstance(lot.get('mgmt'), dict) else {}
        if _num(g.get('trail_atr')) and g['trail_atr'] > 0: k, k_src = float(g['trail_atr']), 'mgmt.trail_atr'
        else: k, k_src = TRAIL_ATR_DEFAULT, 'default'
        atr, src = _atr_now(lot)
        b = lot.get('entry0', lot.get('e0', lot.get('avg')))
        cap = (t0 + timedelta(seconds=TIME_CAP_BARS_DEFAULT * bar)).isoformat(timespec='seconds') if bar and t0 else None
        return dict(v=POLICY_VERSION, declared_at=t, seq=seq, coverage='partial' if late else 'full', why=why, policies=dict(
            target=dict(v=1, rule=RULE_TARGET, params=dict(level='the lot target valid at each observation (tp, else e0 + tp_r x R)',
                                                          fill='at the target level (resting order), modelled slippage')),
            trailing=dict(v=1, rule=RULE_TRAIL, params=dict(k=k, k_src=k_src, atr=atr, atr_src=src,
                                                           best_from=float(b) if _num(b) else None,
                                                           fill='the observed mark that crossed the stop (never improved)')),
            time_cap=dict(v=1, rule=f'time cap: close at the first observation at/after entry + {TIME_CAP_BARS_DEFAULT} bars',
                          params=dict(bars=TIME_CAP_BARS_DEFAULT, tf_s=bar, cap_t=cap)),
            runner=dict(v=1, rule=RULE_RUNNER, params=dict(qty='open quantity right after the first partial take-profit',
                                                          price='that partial close fill', cohorts='FIFO')),
            breakeven=dict(v=1, rule=RULE_BE, params=dict(arm_r=BE_ARM_R, R=float(lot['R']) if _num(lot.get('R')) and lot['R'] > 0 else None,
                                                         e0=float(b) if _num(b) else None,
                                                         level='net P&L after fees paid and the estimated exit fee = 0, with the lot state at each observation',
                                                         fill='the observed mark that crossed it (never improved)')),
            regime_exit=dict(v=1, rule=RULE_REGIME, params=dict(tf=lot.get('tf'), against='LONG: close < EMA200 and EMA20 < EMA50 '
                                                               '(SHORT mirrored) on the last CLOSED candle of the lot tf',
                                                               fill='the first mark sample at/after the candle-close decision'))))
    except Exception:
        return None


def _declared(lot):
    ap = lot.get('ap')
    return ap if isinstance(ap, dict) and isinstance(ap.get('policies'), dict) else None


def _dparams(lot, name):
    ap = _declared(lot)
    p = (ap or {}).get('policies', {}).get(name)
    return p.get('params') if isinstance(p, dict) and isinstance(p.get('params'), dict) else None


# ------------------------------------------------------------------ online tracking (mark loop: memory only)
def _state_now(lot):
    """The lot fields every policy value depends on, as they are right now: [avg, qty, target, R, e0, realized, fees].
    A non-finite realized/fees is stored as None (never NaN: NaN != NaN would add a snapshot on every observation)."""
    g = lambda k: float(lot[k]) if _num(lot.get(k)) else None
    z = lambda k: float(lot[k]) if _num(lot.get(k)) else (0.0 if lot.get(k) is None else None)
    return [float(lot['avg']), float(lot['qty']), target_price(lot), g('R'), g('e0'), z('realized'), z('fees')]


def _online_init(lot, t):
    """Online policy state from the DECLARED parameters (never re-derived later): the time-cap instant and the trailing
    stop's k / ATR plus its running best since entry."""
    ap = _declared(lot) or declare(lot, t) or {}
    pol = ap.get('policies') or {}
    tr = (pol.get('trailing') or {}).get('params') or {}
    tc = (pol.get('time_cap') or {}).get('params') or {}
    cap = _ts(tc.get('cap_t'))
    return dict(tc_n=tc.get('bars', TIME_CAP_BARS_DEFAULT), tc_e=cap.timestamp() if cap else None, tc=None,
                tk=tr.get('k', TRAIL_ATR_DEFAULT), atr=tr.get('atr'), atr_src=tr.get('atr_src'),
                tb=tr.get('best_from'), tb_pt=None, tr=None, **_online_init_v2(pol))


def _online_init_v2(pol):
    """Online state of the v2 policies (only when DECLARED: a v1 lot gets none and its record says so)."""
    out = {}
    be = (pol.get('breakeven') or {}).get('params')
    if isinstance(be, dict): out.update(be_r=be.get('arm_r'), be_R=be.get('R'), be_e0=be.get('e0'), be_arm=None, be=None)
    if isinstance((pol.get('regime_exit') or {}).get('params'), dict): out.update(rx_dec=None, rx=None)
    return out


def _online_step(lot, on, sd, mark, t, si, i, after_gap, net=None):
    """One observation for the online policies. Causal by construction: the trailing stop comes from the best mark seen
    BEFORE this observation and the ATR declared at entry; the time cap fires at the first observation at/after the
    declared cap; breakeven-after-costs arms at an EARLIER observation (+arm_r x R from the declared entry) and exits when
    this observation's net P&L (state before it, estimated exit fee) is <= 0; the regime exit fills at the first
    observation at/after a candle-close decision (regime_trigger, cycle path). Arithmetic only. Returns (changed, decided)."""
    chg = dec = False
    if 'be' in on and on.get('be') is None and _num(on.get('be_R')) and on['be_R'] > 0 and _num(on.get('be_e0')) and _num(on.get('be_r')):
        if on.get('be_arm') is None:
            if sd * (mark - on['be_e0']) >= on['be_r'] * on['be_R']: on['be_arm'] = [t, mark, si, i]; chg = True
        elif _num(net) and net <= 0:
            on['be'] = [t, mark, si, i]; on['be_gap'] = after_gap; chg = dec = True
    rx = on.get('rx_dec')
    if on.get('rx') is None and isinstance(rx, (list, tuple)) and len(rx) >= 2:
        d, dd = _ts(t), _ts(rx[1])
        if d is not None and dd is not None and d >= dd:
            on['rx'] = [t, mark, si, i]; on['rx_gap'] = after_gap; chg = dec = True
    if on.get('tc') is None and on.get('tc_e') is not None:
        d = _ts(t)
        if d is not None and d.timestamp() >= on['tc_e']:
            on['tc'] = [t, mark, si, i]; on['tc_gap'] = after_gap; chg = dec = True
    if on.get('tr') is None and _num(on.get('tb')):
        if 'atr' in on: atr, src = (on['atr'], on.get('atr_src')) if _num(on.get('atr')) and on['atr'] > 0 else (None, None)
        else: atr, src = _atr_now(lot)                                   # tracking state from before v3 (no declared ATR)
        stop = on['tb'] - sd * on['tk'] * atr if atr else None
        if stop is not None and sd * (mark - stop) <= 0:
            bp = on.get('tb_pt')
            on['tr'] = [t, mark, si, i, round(stop, 10), atr, src]
            on['tr_cut'] = [bp[0], bp[3]] if isinstance(bp, (list, tuple)) and len(bp) >= 4 else None
            on['tr_gap'] = after_gap; chg = dec = True
        elif sd * (mark - on['tb']) > 0:
            on['tb'], on['tb_pt'] = mark, [t, mark, si, i]; chg = True
    return chg, dec


def _miss_add(ex, a, b, cause):
    ms = ex.setdefault('missing', [])
    if len(ms) < MISS_MAX: ms.append([a, b, cause])
    ex['missing_n'] = ex.get('missing_n', 0) + 1


def observe(lot, mark, t, fee_rate=FEE_EST, restored=False, missing=False, down_last=None):
    """Update lot['ex'] with one mark observation at time t (ISO string). Memory only. Returns the excursion dict or None.
    Records (bounded): PRICE MFE/MAE with times; the LIFECYCLE peak and trough of the net P&L (realized - fees paid +
    open P&L - estimated exit fee, the trade's final net basis); a decimated price path; time-stamped snapshots of the
    lot state; the online time-cap / trailing decisions; tracking gaps and missing-sample intervals.
    Every recorded point is [t, mark, snapshot index, observation index n]; n orders points within one timestamp.
    missing=True (T05b says the exchange is in outage): the sample is NOT used, only counted, and the interval is
    recorded. A pause longer than SAMPLE_GAP_S between samples is recorded too (cause 'exchange_outage' when the T05b
    exchange-down incident's last failure, down_last, falls inside it, else 'no_samples'). ex['chg'] is bumped when
    something worth checkpointing changed; ex['ck_force'] asks for an immediate (bounded) checkpoint.
    restored: the lot was loaded at a restart without any tracking data (flagged late)."""
    try:
        if not _num(mark) or mark <= 0: return None
        if not (_num(lot.get('avg')) and lot['avg'] > 0 and _num(lot.get('qty')) and lot['qty'] >= 0): return None
        sd = 1 if lot['side'] == 'LONG' else -1
        avg, qty = float(lot['avg']), float(lot['qty'])
        ex = lot.get('ex')
        if missing:                                                # outage: a possibly stale mark is never used
            if not isinstance(ex, dict): return None
            ex['miss_obs'] = ex.get('miss_obs', 0) + 1
            if ex.get('miss_open') is None:
                last = ex.get('last')
                ex['miss_open'] = [last[0] if isinstance(last, (list, tuple)) and last else t, 'exchange_outage']
                ex['chg'] = ex.get('chg', 0) + 1
            return ex
        if not isinstance(ex, dict):
            late = True if restored else _tracking_late(lot, t)
            if _declared(lot) is None:
                d = declare(lot, t, late=bool(late), why='declared at the first observation (lot opened before T05a or '
                            'restored without tracking data)' if late else None, seq=-0.5 if late else -1)
                if d is not None: lot['ap'] = d
            ex = lot['ex'] = dict(v=AUDIT_VERSION, n=0, mfe_px=mark, mfe_t=t, mae_px=mark, mae_t=t, peak_pnl=None, peak_r=None,
                                  peak_t=None, trough_pnl=None, trough_r=None, trough_t=None, target_touch_t=None, t0=t,
                                  late=late, path=[], stride=1, st=[], chg=0, on=_online_init(lot, t))
            if restored: ex['late_why'] = 'lot restored at a restart without tracking data'
        chg = force = after_gap = False
        prev = ex.get('last')
        if ex.get('gap_open') is not None:                       # first observation after a restart: close the gap
            gaps = ex.setdefault('gaps', [])
            if len(gaps) < GAPS_MAX: gaps.append([ex['gap_open'], t])
            ex['gaps_n'] = ex.get('gaps_n', 0) + 1; ex.pop('gap_open'); chg = force = after_gap = True
        mo = ex.pop('miss_open', None)
        if isinstance(mo, (list, tuple)) and len(mo) >= 2:
            _miss_add(ex, mo[0], t, mo[1]); chg = after_gap = True; force = len(ex['missing']) < MISS_MAX or force
        elif not after_gap and isinstance(prev, (list, tuple)) and prev:
            dp, dn = _ts(prev[0]), _ts(t)
            if dp is not None and dn is not None and (dn - dp).total_seconds() > SAMPLE_GAP_S:
                cause = 'exchange_outage' if down_last and _before_or_at(prev[0], down_last) and _before_or_at(down_last, t) else 'no_samples'
                _miss_add(ex, prev[0], t, cause); chg = after_gap = True
                force = force or (len(ex['missing']) < MISS_MAX and cause == 'exchange_outage')
        i = ex['n']; ex['n'] = i + 1
        if i == 0: force = True
        cur = _state_now(lot)
        st = ex.setdefault('st', [])
        if not st or st[-1][1:8] != cur:
            if len(st) < SNAP_MAX: st.append([t] + cur + [i]); chg = force = True
            elif not ex.get('st_full_t'): ex['st_full_t'] = t; chg = force = True
        si = len(st) - 1 if st and not ex.get('st_full_t') else None   # the snapshot valid AT this observation (None: unknown)
        if i == 0: ex['mfe_si'] = ex['mae_si'] = si; ex['mfe_n'] = ex['mae_n'] = i; chg = True
        if sd * (mark - ex['mfe_px']) > 0: ex['mfe_px'], ex['mfe_t'], ex['mfe_si'], ex['mfe_n'] = mark, t, si, i; chg = True
        if sd * (mark - ex['mae_px']) < 0: ex['mae_px'], ex['mae_t'], ex['mae_si'], ex['mae_n'] = mark, t, si, i; chg = True
        path = ex.setdefault('path', [])
        stride = ex.get('stride') or 1
        if i % stride == 0:
            path.append([t, mark, si, i])
            if len(path) > PATH_MAX: path[:] = path[::2]; ex['stride'] = stride * 2
        ex['last'] = [t, mark, si, i]
        pnl = None
        if cur[5] is not None and cur[6] is not None:
            pnl = cur[5] - cur[6] + sd * (mark - avg) * qty - qty * mark * fee_rate
            risk = lot.get('risk_usd')
            rr = lambda v: round(v / risk, 4) if _num(risk) and risk > 0 else None
            if ex['peak_pnl'] is None or pnl > ex['peak_pnl']:
                ex['peak_pnl'], ex['peak_t'], ex['peak_r'] = round(pnl, 6), t, rr(pnl); chg = True
                ex['peak_px'], ex['peak_q'], ex['peak_n'] = mark, qty, i      # the mark + quantity of the peak (executable MFE)
            if ex.get('trough_pnl') is None or pnl < ex['trough_pnl']:
                ex['trough_pnl'], ex['trough_t'], ex['trough_r'] = round(pnl, 6), t, rr(pnl); chg = True
        tgt = cur[2]
        if ex['target_touch_t'] is None and tgt is not None and sd * (mark - tgt) >= 0:
            ex['target_touch_t'], ex['target_touch_px'], ex['target_touch_si'], ex['target_touch_n'] = t, mark, si, i; chg = True
        on = ex.get('on')
        if isinstance(on, dict):
            try:
                c_, d_ = _online_step(lot, on, sd, mark, t, si, i, after_gap, net=pnl)
                chg, force = chg or c_, force or d_
            except Exception: ex['on'] = None                     # online policies off for this lot; path fallback remains
        if chg: ex['chg'] = ex.get('chg', 0) + 1
        if force: ex['ck_force'] = True
        return ex
    except Exception:
        return None


_CK_SKIP = ('path', 'chg', 'ck_chg', 'ck_t', 'ck_force')


def checkpoint(lot, key, t):
    """A bounded 'excursion' event when the tracking state changed since the last one AND (a forced reason - first
    observation, new state snapshot, online decision, closed gap/outage interval - or CKPT_MIN_S passed). Marks it as
    taken. Memory only (the engine queues the event on the non-blocking writer). Returns the event or None.
    Bound per lot: 1 + SNAP_MAX + 1 + 2 + GAPS_MAX + MISS_MAX forced + lifetime / CKPT_MIN_S + 1 timed checkpoints."""
    try:
        ex = lot.get('ex')
        if not isinstance(ex, dict): return None
        if ex.get('chg', 0) == ex.get('ck_chg') and not ex.get('ck_force'): return None
        d0, d1 = _ts(ex.get('ck_t')), _ts(t)
        if not (ex.get('ck_force') or d0 is None or d1 is None or (d1 - d0).total_seconds() >= CKPT_MIN_S): return None
        ex['ck_chg'], ex['ck_t'] = ex.get('chg', 0), t
        ex.pop('ck_force', None)
        return dict(v=AUDIT_VERSION, kind='excursion', t=t, id=key, symbol=lot.get('symbol'), side=lot.get('side'),
                    n=ex.get('n'), ex={k: v for k, v in ex.items() if k not in _CK_SKIP})
    except Exception:
        return None


def load_checkpoints(paths, ids):
    """ENGINE START ONLY (the one reader): the newest 'excursion' event per open lot id from the audit files (.1 first,
    then current). Malformed lines are skipped. Returns {id: event}."""
    out = {}
    ids = set(ids or ())
    for p in paths:
        try:
            with open(p, encoding='utf-8', errors='replace') as fh:
                for ln in fh:
                    if '"excursion"' not in ln: continue
                    try: r = json.loads(ln)
                    except Exception: continue
                    if not (isinstance(r, dict) and r.get('kind') == 'excursion' and r.get('id') in ids and isinstance(r.get('ex'), dict)
                            and isinstance(r.get('n'), int)): continue
                    old = out.get(r['id'])
                    if old is None or r['n'] >= old['n']: out[r['id']] = r
        except Exception:
            continue
    return out


def restore_checkpoint(lot, ck):
    """Restart: when the newest checkpoint event is AHEAD of the tracking state saved in state.json (observations after
    the last state save), continue from it. The decimated path keeps only saved points up to the checkpoint. The
    restart gap itself is opened by mark_restart() from the restored last observation. Returns True if restored."""
    try:
        cx = ck.get('ex') if isinstance(ck, dict) else None
        if not isinstance(cx, dict) or not isinstance(cx.get('n'), int): return False
        ex = lot.get('ex')
        if isinstance(ex, dict) and isinstance(ex.get('n'), int) and ex['n'] >= cx['n']: return False
        new = json.loads(json.dumps(cx))
        old = (ex or {}).get('path') if isinstance(ex, dict) else None
        new['path'] = [p for p in (old or ()) if isinstance(p, list) and len(p) >= 4 and isinstance(p[3], int) and p[3] < cx['n']]
        new['stride'] = max(int((ex or {}).get('stride') or 1), int(cx.get('stride') or 1))
        new['chg'] = new['ck_chg'] = 0
        new['ck_t'] = ck.get('t'); new['restored_from'] = ck.get('t')
        lot['ex'] = new
        return True
    except Exception:
        return False


def checkpoint_lost(lot):
    """At a restart: the lot was checkpointed before (ex.ck_t) but no checkpoint for it is left in trade_audit.jsonl(.1)
    (rotated away by the line / byte cap, or the file was removed). Tracking continues from state.json; observations
    after its last save are covered by the restart gap, and the record lists this as a limitation. Returns True if flagged."""
    try:
        ex = lot.get('ex')
        if not (isinstance(ex, dict) and ex.get('ck_t')): return False
        ex['ck_lost'] = ex['ck_t']; return True
    except Exception:
        return None


def mark_restart(lot):
    """At a restart: a lot restored WITH tracking data gets an open tracking gap from its last known observation (closed
    by the next observe()); observations after the last state save / checkpoint and before the restart are lost.
    Returns False for a lot without tracking data (the caller flags it late via observe(restored=True)), None on junk."""
    try:
        ex = lot.get('ex')
        if not isinstance(ex, dict): return False
        if ex.get('gap_open') is None:
            last = ex.get('last')
            ex['gap_open'] = last[0] if isinstance(last, (list, tuple)) and last else ex.get('t0')
        return True
    except Exception:
        return None


def _tracking_late(lot, t):
    """True when the first observation came after the lot had already changed (a non-entry fill) or more than one bar
    (min 10 min) after entry - e.g. a lot opened before T05a or restored without its 'ex'."""
    try:
        if any(f[1] not in ENTRY_KINDS for f in lot.get('fills') or ()): return True
        t0, d = _ts(lot.get('opened')), _ts(t)
        if t0 is None or d is None: return None
        return (d - t0).total_seconds() > max(600, tf_seconds(lot.get('tf')) or 0)
    except Exception:
        return None


def target_price(lot):
    """The planned target as the lot defines it now: a fixed/basket tp, else e0 + tp_r * R (None if there is none)."""
    try:
        if _num(lot.get('tp')): return float(lot['tp'])
        g = lot.get('mgmt') or {}
        sd = 1 if lot['side'] == 'LONG' else -1
        if _num(g.get('tp_r')) and _num(lot.get('R')) and _num(lot.get('e0')): return float(lot['e0']) + sd * g['tp_r'] * lot['R']
    except Exception:
        pass
    return None


def _pt4(p):
    """A stored point as (t, mark, si, n); n=None for a point stored before observation indices existed."""
    if isinstance(p, (list, tuple)) and len(p) >= 3:
        n = p[3] if len(p) > 3 and isinstance(p[3], int) and not isinstance(p[3], bool) else None
        return (p[0], p[1], p[2], n)
    return None


def recorded_path(lot):
    """The causal live path sampled on the lot: the decimated samples plus the MFE, MAE, first target touch, last
    sample and the online policy points (all genuinely observed marks), de-duplicated and in OBSERVATION order. Each
    point is (t, mark, snapshot index, observation index n). [] when nothing was recorded."""
    try:
        ex = lot.get('ex') or {}
        raw = list(ex.get('path') or ())
        for a, b, c, d in (('mfe_t', 'mfe_px', 'mfe_si', 'mfe_n'), ('mae_t', 'mae_px', 'mae_si', 'mae_n'),
                           ('target_touch_t', 'target_touch_px', 'target_touch_si', 'target_touch_n')):
            if ex.get(a) is not None and _num(ex.get(b)) and c in ex: raw.append((ex[a], ex[b], ex[c], ex.get(d)))
        raw.append(ex.get('last'))
        on = ex.get('on') if isinstance(ex.get('on'), dict) else {}
        for k in ('tc', 'tr', 'tb_pt', 'be', 'be_arm', 'rx'):
            if isinstance(on.get(k), (list, tuple)): raw.append(on[k][:4])
        seen, out = set(), []
        for p in raw:
            q = _pt4(p)
            if q is None: continue
            key = ('n', q[3]) if q[3] is not None else q
            if key not in seen: seen.add(key); out.append(q)
        tkey = lambda p: (_ts(p[0]) or datetime.min.replace(tzinfo=timezone.utc), str(p[0]))
        if all(p[3] is not None for p in out): out.sort(key=lambda p: p[3])
        else: out.sort(key=lambda p: tkey(p) + (-1 if p[3] is None else p[3],))
        return out
    except Exception:
        return []


def _live(path):
    """True for a live path from recorded_path (every point carries its observation index)."""
    return bool(path) and all(isinstance(p, tuple) and len(p) >= 4 and isinstance(p[3], int) for p in path)


def _before_or_at(a, b):
    da, db = _ts(a), _ts(b)
    return da <= db if da is not None and db is not None else str(a) <= str(b)


_ST_KEYS = ('t', 'avg', 'qty', 'tgt', 'R', 'e0', 'realized', 'fees', 'n')
_BY_TIME = object()


def state_at(lot, t, si=_BY_TIME):
    """(state dict, None) valid at a decision, or (None, why) when it cannot be known causally. si: the snapshot index
    recorded with a live path point (exact); without it (an externally supplied path) the snapshot is looked up by time:
    the last one strictly before t, else the FIRST one at t (a tie never resolves to a later state). The state's 'n'
    is the observation that recorded it: it holds the management done BEFORE that observation (sequence n - 0.5)."""
    ex = lot.get('ex') or {}
    st = ex.get('st')
    if not st: return None, 'no state snapshots on this lot (tracked before T05a v2): value withheld rather than using the final state'
    if si is not _BY_TIME:
        if isinstance(si, int) and not isinstance(si, bool) and 0 <= si < len(st): return dict(zip(_ST_KEYS, st[si])), None
        return None, 'state at this observation unknown (state history full)'
    if ex.get('st_full_t') and _before_or_at(ex['st_full_t'], t):
        return None, f"state history full from {ex['st_full_t']}: later state unknown"
    cur = None
    for s in st:
        if not _before_or_at(s[0], t): break                     # after t: never used
        cur = s
        if _before_or_at(t, s[0]): break                         # exactly at t: the first such snapshot, never a later one
    if cur is None: return None, 'decision before tracking started'
    return dict(zip(_ST_KEYS, cur)), None


def final_close_state(lot, fee_rate=FEE_EST):
    """(state, note) of the FINAL close, the same on every close path: from the last close fill [t, why, q, px] the
    quantity it closed and the realized P&L / fees booked BEFORE it (the lot's totals minus that fill's part)."""
    sd = 1 if lot['side'] == 'LONG' else -1
    avg, real, fees = float(lot['avg']), float(lot.get('realized') or 0.0), float(lot.get('fees') or 0.0)
    fl = lot.get('fills') or []
    last = fl[-1] if fl and isinstance(fl[-1], (list, tuple)) and len(fl[-1]) >= 4 else None
    if last is not None and last[1] not in ENTRY_KINDS and _num(last[2]) and _num(last[3]) and last[2] > 0:
        q, px = float(last[2]), float(last[3])
        return dict(avg=avg, qty=q, realized=real - sd * (px - avg) * q, fees=fees - q * px * fee_rate), None
    return dict(avg=avg, qty=float(lot.get('qty') or 0.0), realized=real, fees=fees), \
        'final close fill not found: the lot quantity and totals at close are used'


# ------------------------------------------------------------------ quantity cohort ledger + runner attribution
def cohort_ledger(lot, fee_rate=FEE_EST):
    """FIFO quantity cohorts rebuilt from the lot's own fills [t, why, qty, px] (persisted with the lot, so the ledger is
    the same after any restart). Entries/adds open cohorts; every close consumes the oldest open quantity. The FIRST
    partial take-profit (RUNNER_WHY) that leaves quantity open activates the runner: its quantity is exactly the open
    quantity then (tagged by cohort). Runner attribution compares that SAME quantity: what it actually earned from the
    activation price to its later exits vs closing it at the activation fill (predeclared RULE_RUNNER):
        value_vs_activation = sum over its later exits of  sd x (exit - activation) x q  -  fee x q x (exit - activation)
    The entry basis cancels out, so FIFO vs the engine's average price does not change the runner value."""
    try:
        sd = _side(lot)
        coh, runner, unmatched = [], None, 0.0
        fills = [f for f in (lot.get('fills') or ()) if isinstance(f, (list, tuple)) and len(f) >= 4]
        tol = 1e-9 * max([abs(float(f[2])) for f in fills if _num(f[2])] + [1.0])
        for j, f in enumerate(fills):
            if not (_num(f[2]) and _num(f[3]) and f[2] > 0): continue
            t, why, q, px = f[0], f[1], float(f[2]), float(f[3])
            if why in ENTRY_KINDS:
                coh.append(dict(i=len(coh), t=t, kind=why, px=px, qty=q, open=q, closed=[])); continue
            left, took = q, []
            for c in coh:
                if left <= tol: break
                if c['open'] <= tol: continue
                k = min(c['open'], left); c['open'] -= k; left -= k
                c['closed'].append([t, why, round(k, 12), px]); took.append((c['i'], k))
            unmatched += max(0.0, left) if left > tol else 0.0
            if runner is not None:
                rq = sum(k for i_, k in took if i_ in runner['_ids'])
                if rq > tol: runner['closes'].append([t, why, round(rq, 12), px])
            open_q = sum(c['open'] for c in coh)
            if runner is None and why in RUNNER_WHY and open_q > tol:
                ids = [c['i'] for c in coh if c['open'] > tol]
                runner = dict(t=t, why=why, px=px, fill_i=j, qty=round(open_q, 12), _ids=set(ids),
                              cohorts=[[c['i'], round(c['open'], 12)] for c in coh if c['open'] > tol], closes=[])
        for c in coh: c['open'] = round(c['open'], 12)
        if runner is not None:
            runner.pop('_ids')
            cl = runner['closes']
            qc = sum(x[2] for x in cl)
            val = sum(sd * (x[3] - runner['px']) * x[2] - fee_rate * x[2] * (x[3] - runner['px']) for x in cl)
            runner.update(qty_closed=round(qc, 12), qty_open=round(max(0.0, runner['qty'] - qc), 12),
                          exit_avg=round(sum(x[2] * x[3] for x in cl) / qc, 10) if qc > tol else None,
                          value_vs_activation=round(val, 6) if cl else None, rule=RULE_RUNNER,
                          basis='same quantity: the open quantity at activation, FIFO cohorts; vs closing it at the activation fill')
        out = dict(cohorts=coh, runner=runner, unmatched_close_qty=round(unmatched, 12) or None)
        oq = sum(c['open'] for c in coh)                       # fills vs the lot: a misclassified fill kind shows up here
        if _num(lot.get('qty')) and abs(oq - float(lot['qty'])) > max(tol, 1e-6 * max([abs(float(f[2])) for f in fills if _num(f[2])] + [1.0])):
            out['qty_mismatch'] = round(oq - float(lot['qty']), 12)
        return out
    except Exception:
        return dict(cohorts=[], runner=None, unmatched_close_qty=None, error='ledger not evaluated')


def fill_events(lot, key, fee_rate=FEE_EST):
    """Events for the lot's LAST fill (called by the engine right after it books an add or a close; memory only):
    a 'cohort' event for an add / partial close and, when this fill activated the runner, a 'runner' event. A final
    close (nothing left open) gives nothing here: the 'trade_audit' record follows. Never raises."""
    try:
        fl = lot.get('fills') or []
        if not fl: return []
        f = fl[-1]
        if not (isinstance(f, (list, tuple)) and len(f) >= 4): return []
        add = f[1] in ENTRY_KINDS
        if not add and not (_num(lot.get('qty')) and lot['qty'] > 0): return []
        led = cohort_ledger(lot, fee_rate)
        open_c = [[c['i'], c['open']] for c in led['cohorts'] if c['open'] > 0]
        out = [dict(v=AUDIT_VERSION, kind='cohort', t=f[0], id=key, symbol=lot.get('symbol'), side=lot.get('side'),
                    move='add' if add else 'partial_close', why=f[1], qty=f[2], px=f[3], qty_after=lot.get('qty'),
                    avg_after=lot.get('avg'), open_cohorts=open_c[:COH_MAX], open_cohorts_n=len(open_c))]
        r = led.get('runner')
        if r and r.get('fill_i') == len(fl) - 1:
            out.append(dict(v=AUDIT_VERSION, kind='runner', t=f[0], id=key, symbol=lot.get('symbol'), side=lot.get('side'),
                            activated_by=r['why'], px=r['px'], qty=r['qty'], cohorts=r['cohorts'][:COH_MAX], rule=RULE_RUNNER,
                            note='the runner is the open quantity right after this partial take-profit; attributed at the final close'))
        return out
    except Exception:
        return []


# ------------------------------------------------------------------ the final trade audit record
def close_record(lot, net_pnl, fee_rate=FEE_EST, slip_bps=0.0, path=None, cap_bars=None, trail_k=None):
    """The audit record at the final close. path: list of (t, price) marks in time order; None = the live path recorded
    on the lot (recorded_path). cap_bars / trail_k other than the declared ones give kind='what_if' entries (research
    only - the engine never passes them). Returns a dict or None (never raises)."""
    try:
        ex = lot.get('ex') or {}
        risk = lot.get('risk_usd') if _num(lot.get('risk_usd')) and lot['risk_usd'] > 0 else None
        peak = ex.get('peak_pnl')
        if path is None: path = recorded_path(lot)
        ap = _declared(lot)
        lim, why_partial = [], []
        if ex.get('late'):
            lim.append('tracking started after entry: excursions before the first observation are not seen' +
                       (f" ({ex['late_why']})" if ex.get('late_why') else '')); why_partial.append('tracking started after entry')
        if ap is None: why_partial.append('no predeclared policies'); lim.append('no policies were declared for this lot')
        elif ap.get('coverage') != 'full': why_partial.append('policies declared late (lot opened before T05a)')
        gaps = [list(g) for g in (ex.get('gaps') or ()) if isinstance(g, (list, tuple)) and len(g) == 2]
        if ex.get('gap_open') is not None: gaps.append([ex['gap_open'], None])       # restarted, never observed again
        if gaps:
            more = (ex.get('gaps_n') or 0) - len(ex.get('gaps') or ())
            lim.append('tracking gap(s) at engine restart: ' + ', '.join(f"{a} -> {b or 'close'}" for a, b in gaps) +
                       (f' (+{more} more)' if more > 0 else '') + '; observations after the last state save / checkpoint and during the gap are not seen')
            why_partial.append('restart gap')
        miss = [list(m) for m in (ex.get('missing') or ()) if isinstance(m, (list, tuple)) and len(m) == 3]
        if ex.get('miss_open'): miss.append([ex['miss_open'][0], None, ex['miss_open'][1]])
        if miss:
            more = (ex.get('missing_n') or 0) - len(ex.get('missing') or ())
            lim.append('missing samples: ' + ', '.join(f"{a} -> {b or 'close'} ({c})" for a, b, c in miss) +
                       (f' (+{more} more)' if more > 0 else '') + '; prices and policy triggers inside them are not seen')
            if any(m[2] == 'exchange_outage' for m in miss): why_partial.append('exchange outage (T05b) during the trade')
        if ex.get('st_full_t'):
            lim.append(f"state history full from {ex['st_full_t']} ({SNAP_MAX} snapshots, e.g. many grid/DCA adds): later decisions are withheld")
            why_partial.append('state history full')
        if not ex.get('st'): lim.append('no state snapshots: causal policies are withheld')
        if ex.get('ck_lost'):
            lim.append(f"newest checkpoint (after {ex['ck_lost']}) not found at a restart (audit file rotated): tracking continued "
                       'from the last state save; the restart gap covers what was lost')
        if (ex.get('stride') or 1) > 1: lim.append(f"path decimated (every {ex['stride']} samples kept, plus MFE/MAE/last)")
        led = cohort_ledger(lot, fee_rate)
        coh = led.get('cohorts') or []
        if led.get('qty_mismatch') is not None:
            lim.append(f"cohort ledger: the fills leave {led['qty_mismatch']:+g} more open quantity than the lot (unknown fill kind?)")
        rec = dict(v=AUDIT_VERSION, kind='trade_audit', audit_available=True, coverage='partial' if why_partial else 'full',
                   coverage_why=why_partial or None,
                   symbol=lot.get('symbol'), side=lot.get('side'), sleeve=lot.get('sleeve'), tf=lot.get('tf'),
                   net_pnl=round(net_pnl, 6), r=round(net_pnl / risk, 4) if risk else None,
                   price_excursion=dict(mfe_px=ex.get('mfe_px'), mfe_t=ex.get('mfe_t'), mae_px=ex.get('mae_px'), mae_t=ex.get('mae_t'),
                                        basis='mark price samples (manage loop, ~8 s); intrabar extremes between samples are not seen'),
                   lifecycle_pnl=dict(peak=peak, peak_t=ex.get('peak_t'), peak_r=ex.get('peak_r'), trough=ex.get('trough_pnl'),
                                      trough_t=ex.get('trough_t'), trough_r=ex.get('trough_r'),
                                      basis='net: realized - fees paid + open P&L - estimated exit fee, at each sample (quantity changes included)'),
                   mfe_px=ex.get('mfe_px'), mfe_t=ex.get('mfe_t'), mae_px=ex.get('mae_px'), mae_t=ex.get('mae_t'),
                   peak_pnl=peak, peak_r=ex.get('peak_r'), peak_t=ex.get('peak_t'), trough_pnl=ex.get('trough_pnl'),
                   observations=ex.get('n', 0), missing_obs=ex.get('miss_obs', 0),
                   pnl_basis='net: realized - fees paid; peak/trough/policies include the estimated exit fee',
                   target=target_price(lot), target_touch_t=ex.get('target_touch_t'),
                   tracking_start=ex.get('t0'), tracking_late=ex.get('late'), tracking_gaps=gaps or None, missing_samples=miss or None,
                   path_points=len(path or ()),
                   policies_declared=dict(v=ap.get('v'), declared_at=ap.get('declared_at'), coverage=ap.get('coverage')) if ap else None,
                   cohorts=[{k: c[k] for k in ('i', 't', 'kind', 'px', 'qty', 'open')} for c in coh[:COH_MAX]], cohorts_n=len(coh),
                   runner=led.get('runner'), unmatched_close_qty=led.get('unmatched_close_qty'), qty_mismatch=led.get('qty_mismatch'),
                   resolution='mark samples (manage loop); intrabar extremes between samples are not seen', limitations=lim)
        if _num(peak):
            gb = peak - net_pnl
            rec.update(giveback=round(gb, 6), giveback_pct=round(gb / peak * 100, 2) if peak > 0 else None,
                       giveback_r=round(gb / risk, 4) if risk else None)
        rec['counterfactuals'] = counterfactuals(lot, net_pnl, fee_rate, slip_bps, path, cap_bars, trail_k, ledger=led)
        try:                                                   # owner scope 2026-10-07: metrics, failure classes, segment keys
            m = trade_metrics(lot, net_pnl, fee_rate)
            fl = failure_flags(lot, net_pnl, m)
            rec.update(metrics=m, flags=fl['flags'], flags_unknown=fl['unknown'] or None, flag_detail=fl['detail'],
                       segment=segment_of(lot, led, fl.get('regime')))
        except Exception:
            rec.update(metrics=None, flags=None, flags_unknown=['not evaluated'], segment=None)
        return rec
    except Exception:
        return None


def _exit_value(lot, px, fee_rate, slip_bps, state):
    """Net P&L if the whole quantity of `state` (avg, qty, realized, fees booked so far) closed at px, incl. the exit fee
    and modelled adverse slippage. The same formula as the engine's final net P&L."""
    sd = 1 if lot['side'] == 'LONG' else -1
    q = float(state['qty'])
    fill = px * (1 - sd * slip_bps / 1e4)
    return float(state['realized']) + sd * (fill - float(state['avg'])) * q - q * fill * fee_rate - float(state['fees'])


def _side(lot):
    return 1 if lot['side'] == 'LONG' else -1


def tf_seconds(tf):
    """'15m' / '1h' / '4h' / '1d' -> seconds, or None for anything else (never raises)."""
    try:
        tf = str(tf).strip().lower()
        n, u = int(tf[:-1]), tf[-1]
        return n * _TF_UNIT[u] if n > 0 and u in _TF_UNIT else None
    except Exception:
        return None


def _ts(t):
    """ISO string (or datetime) -> aware datetime (naive = UTC), or None."""
    try:
        d = t if isinstance(t, datetime) else datetime.fromisoformat(str(t).replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ------------------------------------------------------------------ causal cutoff: info_cutoff < decision_at, or withheld
def _cutoff(parts, dec):
    """parts: [(t, seq, what)] = every piece of information a decision used; dec = dict(t, seq). With observation
    sequences on all of them the proof is STRICT: max(seq) < dec.seq (an observation never decides on itself: the
    stop / target level comes from earlier observations or the declaration, the state from management done before
    the observation). Without sequences (an external path) it falls back to time: every t <= dec.t, ties resolved to
    the earliest state. Returns (info_cutoff dict, ok)."""
    parts = [p for p in parts if p is not None]
    if not parts: return dict(t=None, seq=None, what='nothing', basis='none'), False
    seqd = dec.get('seq')
    if seqd is not None and all(isinstance(p[1], (int, float)) for p in parts):
        top = max(parts, key=lambda p: p[1])
        ok = top[1] < seqd and all(p[0] is None or _before_or_at(p[0], dec['t']) for p in parts)
        return dict(t=top[0], seq=top[1], what=top[2], basis='observation order'), ok
    ts = [p for p in parts if p[0] is not None]
    top = max(ts, key=lambda p: (_ts(p[0]) or datetime.min.replace(tzinfo=timezone.utc))) if ts else parts[-1]
    ok = all(_before_or_at(p[0], dec['t']) for p in ts)
    return dict(t=top[0], seq=None, what=top[2], basis='time (ties resolved to the earliest state)'), ok


def _decl_part(lot):
    ap = _declared(lot)
    if ap is None: return None
    s = ap.get('seq')
    return (ap.get('declared_at'), s if isinstance(s, (int, float)) and not isinstance(s, bool) else None, 'policy declared at entry')


def _policy(rule, decision_t, value, net_pnl, limitations, dec=None, cut=None, predeclared=True, **extra):
    d = dict(kind='causal_policy' if predeclared else 'what_if', rule=rule, policy_v=1, predeclared=predeclared,
             decision_t=decision_t, decision_at=dec, info_cutoff=cut,
             causal_ok=None if dec is None else bool(cut is not None and value is not None),
             value=None if value is None else round(value, 6),
             vs_actual=None if value is None or not _num(net_pnl) else round(value - net_pnl, 6), limitations=limitations)
    d.update(extra)
    return d


def _pt(pt):
    """(t, price, si) of a path point; si = _BY_TIME for a plain (t, price) pair."""
    return pt[0], pt[1], (pt[2] if len(pt) > 2 else _BY_TIME)


def _decide(lot, rule, pt, px, fee_rate, slip_bps, net_pnl, lim, parts=(), predeclared=True, **extra):
    """A policy decided at path point pt: valued with the lot state valid AT that observation (never the final state),
    and only if every input (declaration, levels from earlier observations, the state) is before the decision."""
    t, _, si = _pt(pt)
    n = pt[3] if len(pt) > 3 and isinstance(pt[3], int) and not isinstance(pt[3], bool) else None
    st, why = state_at(lot, t, si)
    if st is None: return _policy(rule, None, None, net_pnl, f'decided at {t} but {why}', predeclared=predeclared, **extra)
    sn = st.get('n')
    sseq = sn - 0.5 if isinstance(sn, int) and not isinstance(sn, bool) else None
    dec = dict(t=t, seq=n)
    cut, ok = _cutoff(list(parts) + [(st['t'], sseq, 'lot state (management before this observation)')], dec)
    if not ok:
        return _policy(rule, None, None, net_pnl, f'withheld: information cutoff {cut} is not before the decision {dec}',
                       dec=dec, cut=cut, predeclared=predeclared, **extra)
    return _policy(rule, t, _exit_value(lot, px, fee_rate, slip_bps, st), net_pnl, lim, dec=dec, cut=cut,
                   predeclared=predeclared, state_t=st['t'], **extra)


def policy_target(lot, net_pnl, fee_rate, slip_bps, path):
    """Close the remaining position at the planned target valid at each path point (from the state snapshots), decided
    at the FIRST path point at/through it. The level comes from the state recorded before that observation."""
    pre = _declared(lot) is not None
    no = lambda why: _policy(RULE_TARGET, None, None, net_pnl, why, predeclared=pre)
    try:
        if not path: return no('no time-ordered price path recorded for this lot')
        sd, had = _side(lot), False
        for pt in path:
            t, p, si = _pt(pt)
            if not (_num(p) and p > 0): continue
            st, why = state_at(lot, t, si)
            if st is None: return no(why)
            tgt = st['tgt']
            if tgt is None: continue
            had = True
            if sd * (p - tgt) >= 0:
                return _decide(lot, RULE_TARGET, pt, tgt, fee_rate, slip_bps, net_pnl, 'assumes a fill at the target with modelled slippage',
                               parts=[_decl_part(lot)], predeclared=pre, target=round(tgt, 10))
        return no('target never touched within the recorded path' if had else 'the lot had no planned target')
    except Exception as e:
        return no(f'not evaluated ({type(e).__name__})')


def policy_trailing(lot, net_pnl, fee_rate, slip_bps, path, k=None):
    """Fixed trailing stop k x ATR behind the running best price since entry, k and ATR as DECLARED at entry. At each
    path point the stop level comes only from the best price seen BEFORE that point; the exit is the first point
    at/through it, filled at that observed price (a gap through the stop is not improved to the stop level). A k other
    than the declared one is a research what-if (kind='what_if')."""
    rule = RULE_TRAIL
    try:
        dp = _dparams(lot, 'trailing')
        g = lot.get('mgmt') if isinstance(lot.get('mgmt'), dict) else {}
        lim = []
        dk = dp.get('k') if dp else None
        if k is None:
            if _num(dk): k = float(dk); (lim.append(f'k={k:g} default (lot has no trail_atr)') if dp.get('k_src') == 'default' else None)
            elif _num(g.get('trail_atr')) and g['trail_atr'] > 0: k = float(g['trail_atr'])
            else: k = TRAIL_ATR_DEFAULT; lim.append(f'k={TRAIL_ATR_DEFAULT:g} default (lot has no trail_atr)')
        pre = dp is not None and _num(dk) and float(k) == float(dk)
        if dp and _num(dp.get('atr')) and dp['atr'] > 0:
            atr, src = float(dp['atr']), dp.get('atr_src')
            if src == 'atr_now': lim.append('ATR = atr_now as known at the decision (declared at entry)')
            elif src == 'R': lim.append('ATR unavailable, the initial stop distance R is used')
        elif _num(lot.get('atr0')) and lot['atr0'] > 0: atr, src = float(lot['atr0']), 'atr0'
        elif _num(lot.get('atr_now')) and lot['atr_now'] > 0:
            atr, src = float(lot['atr_now']), 'atr_now'
            lim.append('ATR = atr_now, the latest refresh (may postdate early decisions)')
        elif _num(lot.get('R')) and lot['R'] > 0: atr, src = float(lot['R']), 'R'; lim.append('ATR unavailable, the initial stop distance R is used')
        else: return _policy(rule, None, None, net_pnl, 'no ATR or R on the lot', predeclared=pre, k=k)
        if not path: return _policy(rule, None, None, net_pnl, 'no time-ordered price path recorded for this lot', predeclared=pre, k=k, atr=atr, atr_src=src)
        best = dp.get('best_from') if dp and _num(dp.get('best_from')) else lot.get('entry0', lot.get('e0', lot.get('avg')))
        if not _num(best): return _policy(rule, None, None, net_pnl, 'no entry price on the lot', predeclared=pre, k=k, atr=atr, atr_src=src)
        if (lot.get('ex') or {}).get('late'): lim.append('tracking started after entry: the best price before it is not seen')
        on = (lot.get('ex') or {}).get('on')
        if pre and _live(path) and isinstance(on, dict) and _num(on.get('tk')) and float(k) == on['tk'] and _num(on.get('tb')):
            tr = on.get('tr')                                          # evaluated online at every observation
            if isinstance(tr, (list, tuple)) and len(tr) >= 7:
                lim.append('evaluated online at every observation; filled at the observed mark that crossed the stop; samples miss intrabar moves')
                if on.get('tr_gap'): lim.append('decided at the first sample after a gap / missing samples: the real trigger may have come earlier at another price')
                c = on.get('tr_cut')
                parts = [_decl_part(lot)] + ([(c[0], c[1], 'running best (earlier observation)')] if isinstance(c, (list, tuple)) and len(c) == 2 else [])
                return _decide(lot, rule, tuple(tr[:4]), tr[1], fee_rate, slip_bps, net_pnl, '; '.join(lim), parts=parts, k=k, atr=tr[5],
                               atr_src=tr[6], stop=tr[4], online=True, after_gap=bool(on.get('tr_gap')))
            return _policy(rule, None, None, net_pnl, '; '.join(lim + ['not triggered at any observation (evaluated online)']),
                           k=k, atr=atr, atr_src=src, online=True)
        sd, dist, bpt = _side(lot), float(k) * atr, None
        for j, pt in enumerate(path):
            t, p, _ = _pt(pt)
            if not (_num(p) and p > 0): continue
            stop = best - sd * dist                                    # known before this observation
            if sd * (p - stop) <= 0:
                lim.append('filled at the observed mark that crossed the stop; samples miss intrabar moves')
                parts = [_decl_part(lot)] + ([(bpt[0], bpt[3] if len(bpt) > 3 and isinstance(bpt[3], int) else None, 'running best (earlier observation)')] if bpt else [])
                return _decide(lot, rule, pt, p, fee_rate, slip_bps, net_pnl, '; '.join(lim), parts=parts, predeclared=pre, k=k, atr=atr,
                               atr_src=src, stop=round(stop, 10))
            if sd * (p - best) > 0: best, bpt = p, pt
        return _policy(rule, None, None, net_pnl, '; '.join(lim + ['not triggered within the recorded path']), predeclared=pre, k=k, atr=atr, atr_src=src)
    except Exception as e:
        return _policy(rule, None, None, net_pnl, f'not evaluated ({type(e).__name__})')


def policy_time_cap(lot, net_pnl, fee_rate, slip_bps, path, n=None):
    """Close at the first path point at/after entry ('opened') + N bars of the lot's tf, at that observed price. N as
    DECLARED at entry; another N is a research what-if (kind='what_if')."""
    dp = _dparams(lot, 'time_cap')
    dn = dp.get('bars') if dp else None
    n = (dn if isinstance(dn, int) else TIME_CAP_BARS_DEFAULT) if n is None else n
    pre = dp is not None and n == dn
    rule = f'time cap: close at the first observation at/after entry + {n} bars'
    try:
        bar = tf_seconds(lot.get('tf'))
        t0 = _ts(lot.get('opened'))
        if bar is None: return _policy(rule, None, None, net_pnl, f"unknown bar length for tf {lot.get('tf')!r}", predeclared=pre, bars=n)
        if t0 is None: return _policy(rule, None, None, net_pnl, 'no parseable entry time (lot.opened)', predeclared=pre, bars=n)
        if not path: return _policy(rule, None, None, net_pnl, 'no time-ordered price path recorded for this lot', predeclared=pre, bars=n)
        cap = t0 + timedelta(seconds=n * bar)
        on = (lot.get('ex') or {}).get('on')
        if pre and _live(path) and isinstance(on, dict) and on.get('tc_n') == n and on.get('tc_e') is not None:
            tc = on.get('tc')                                          # evaluated online at every observation
            if isinstance(tc, (list, tuple)) and len(tc) >= 4:
                lim = 'filled at the first observed mark at/after the cap (evaluated online at every observation)'
                if on.get('tc_gap'): lim += '; decided at the first sample after a gap / missing samples'
                return _decide(lot, rule, tuple(tc[:4]), tc[1], fee_rate, slip_bps, net_pnl, lim, parts=[_decl_part(lot)],
                               bars=n, cap_t=cap.isoformat(timespec='seconds'), online=True, after_gap=bool(on.get('tc_gap')))
            return _policy(rule, None, None, net_pnl, 'no observation at/after the cap (the actual exit came first; evaluated online)',
                           bars=n, cap_t=cap.isoformat(timespec='seconds'), online=True)
        bad = 0
        for pt in path:
            t, p, _ = _pt(pt)
            d = _ts(t)
            if d is None: bad += 1; continue
            if d >= cap and _num(p) and p > 0:
                lim = 'filled at the first observed mark at/after the cap' + (f'; {bad} path points had no parseable time' if bad else '')
                return _decide(lot, rule, pt, p, fee_rate, slip_bps, net_pnl, lim, parts=[_decl_part(lot)], predeclared=pre, bars=n,
                               cap_t=cap.isoformat(timespec='seconds'))
        return _policy(rule, None, None, net_pnl, 'the recorded path ends before the cap (the actual exit came first)' +
                       (f'; {bad} path points had no parseable time' if bad else ''), predeclared=pre, bars=n, cap_t=cap.isoformat(timespec='seconds'))
    except Exception as e:
        return _policy(rule, None, None, net_pnl, f'not evaluated ({type(e).__name__})', predeclared=pre)


def policy_runner(lot, net_pnl, ledger):
    """Close the runner quantity at its activation fill instead of letting it run (same quantity, predeclared)."""
    try:
        r = (ledger or {}).get('runner')
        if not r: return _policy(RULE_RUNNER, None, None, net_pnl, 'no runner: no partial take-profit left quantity open')
        v = r.get('value_vs_activation')
        if v is None: return _policy(RULE_RUNNER, None, None, net_pnl, 'runner quantity has no recorded exit yet')
        dec = dict(t=r['t'], seq=None)
        cut, ok = _cutoff([_decl_part(lot), (r['t'], None, 'the activation fill itself')], dec)
        lim = 'same quantity (open at activation, FIFO cohorts) closed at the activation fill price; decided at that fill'
        if r.get('qty_open'): lim += f"; {r['qty_open']} of the runner quantity has no matching exit fill"
        return _policy(RULE_RUNNER, r['t'] if ok else None, net_pnl - v if ok else None, net_pnl, lim, dec=dec, cut=cut,
                       predeclared=_declared(lot) is not None, qty=r['qty'], activation_px=r['px'])
    except Exception as e:
        return _policy(RULE_RUNNER, None, None, net_pnl, f'not evaluated ({type(e).__name__})')


def counterfactuals(lot, net_pnl, fee_rate=FEE_EST, slip_bps=0.0, path=None, cap_bars=None, trail_k=None, ledger=None):
    """Labelled counterfactuals. Never raises: a policy that cannot be evaluated says why (value=None)."""
    out = []
    try:
        ex = lot.get('ex') or {}
        if _num(ex.get('mfe_px')):
            fs, note = final_close_state(lot, fee_rate)
            v = _exit_value(lot, ex['mfe_px'], fee_rate, slip_bps, fs)
            lim = 'upper bound for the final close (same quantity, same booked partials and fees) at the best observed mark; ' \
                  'the live bot could not know this was the extreme; an exit fill better than any sampled mark can beat it'
            out.append(dict(kind='hindsight_ceiling', rule='best observed mark', decision_t=ex.get('mfe_t'),
                            info_cutoff='full future path (NOT available live)', value=round(v, 6), vs_actual=round(v - net_pnl, 6),
                            final_qty=fs['qty'], limitations=lim + (f'; {note}' if note else '')))
    except Exception:
        pass
    path = list(path) if path else []
    out.append(policy_target(lot, net_pnl, fee_rate, slip_bps, path))
    out.append(policy_trailing(lot, net_pnl, fee_rate, slip_bps, path, trail_k))
    out.append(policy_time_cap(lot, net_pnl, fee_rate, slip_bps, path, cap_bars))
    try: out.append(policy_runner(lot, net_pnl, ledger if ledger is not None else cohort_ledger(lot, fee_rate)))
    except Exception: pass
    out.append(policy_breakeven(lot, net_pnl, fee_rate, slip_bps, path))
    out.append(policy_regime_exit(lot, net_pnl, fee_rate, slip_bps, path))
    return out


def policy_breakeven(lot, net_pnl, fee_rate, slip_bps, path):
    """Breakeven after costs (predeclared in policy set v2): arms once a mark is +arm_r x R in favour of the declared
    entry price; from the NEXT observation on, closes at the first mark where the net P&L after fees (state valid at that
    observation, estimated exit fee) is <= 0, filled at that observed mark. Online (every observation) for a live path;
    the path-based fallback walks the recorded points. Not declared (v1 lot) -> value None, says why."""
    rule = RULE_BE
    try:
        dp = _dparams(lot, 'breakeven')
        if dp is None: return _policy(rule, None, None, net_pnl, 'not declared for this lot (policy set v1, opened before v2)', predeclared=False)
        R, e0, arm = dp.get('R'), dp.get('e0'), dp.get('arm_r')
        if not (_num(R) and R > 0 and _num(e0) and _num(arm)):
            return _policy(rule, None, None, net_pnl, 'no entry price / initial risk R declared', arm_r=arm)
        on = (lot.get('ex') or {}).get('on')
        if _live(path) and isinstance(on, dict) and 'be' in on:
            b, a = on.get('be'), on.get('be_arm')
            if isinstance(b, (list, tuple)) and len(b) >= 4 and isinstance(a, (list, tuple)) and len(a) >= 4:
                lim = 'evaluated online at every observation; filled at the observed mark at/below net zero; samples miss intrabar moves'
                if on.get('be_gap'): lim += '; decided at the first sample after a gap / missing samples'
                return _decide(lot, rule, tuple(b[:4]), b[1], fee_rate, slip_bps, net_pnl, lim,
                               parts=[_decl_part(lot), (a[0], a[3], 'armed at an earlier observation')], arm_r=arm, R=R,
                               armed_t=a[0], online=True, after_gap=bool(on.get('be_gap')))
            return _policy(rule, None, None, net_pnl, ('armed, ' if a else 'never armed (+%gR not reached), ' % arm) +
                           'no observation at/below net zero after arming (evaluated online)', arm_r=arm, R=R, online=True,
                           armed_t=a[0] if isinstance(a, (list, tuple)) and a else None)
        if not path: return _policy(rule, None, None, net_pnl, 'no time-ordered price path recorded for this lot', arm_r=arm, R=R)
        sd, apt = _side(lot), None
        for pt in path:
            t, p, si = _pt(pt)
            if not (_num(p) and p > 0): continue
            if apt is None:
                if sd * (p - e0) >= arm * R: apt = pt
                continue
            st, why = state_at(lot, t, si)
            if st is None: return _policy(rule, None, None, net_pnl, f'decided at {t} but {why}', arm_r=arm, R=R)
            if st['realized'] is None or st['fees'] is None: continue
            net = st['realized'] - st['fees'] + sd * (p - st['avg']) * st['qty'] - st['qty'] * p * fee_rate
            if net <= 0:
                n = apt[3] if len(apt) > 3 and isinstance(apt[3], int) else None
                return _decide(lot, rule, pt, p, fee_rate, slip_bps, net_pnl, 'filled at the observed mark at/below net zero; samples miss intrabar moves',
                               parts=[_decl_part(lot), (apt[0], n, 'armed at an earlier observation')], arm_r=arm, R=R, armed_t=apt[0])
        return _policy(rule, None, None, net_pnl, ('armed, never back to net zero' if apt else 'never armed') + ' within the recorded path', arm_r=arm, R=R)
    except Exception as e:
        return _policy(rule, None, None, net_pnl, f'not evaluated ({type(e).__name__})')


def policy_regime_exit(lot, net_pnl, fee_rate, slip_bps, path):
    """Regime exit (predeclared in policy set v2): at a closed candle of the lot tf whose trend is against the position
    (regime_trigger, info cutoff = that candle close < the cycle's decision time), close at the first mark sample at/after
    the decision. Needs the candle-close trend, so it is evaluated online only (live path)."""
    rule = RULE_REGIME
    try:
        if _dparams(lot, 'regime_exit') is None:
            return _policy(rule, None, None, net_pnl, 'not declared for this lot (policy set v1, opened before v2)', predeclared=False)
        on = (lot.get('ex') or {}).get('on')
        if not (_live(path) and isinstance(on, dict) and 'rx' in on):
            return _policy(rule, None, None, net_pnl, 'evaluated online only (needs the closed-candle trend at each cycle); no online state')
        rx, d = on.get('rx'), on.get('rx_dec')
        if not (isinstance(d, (list, tuple)) and len(d) >= 3):
            return _policy(rule, None, None, net_pnl, 'no closed candle with the trend against the position before the actual exit', online=True)
        if not (isinstance(rx, (list, tuple)) and len(rx) >= 4):
            return _policy(rule, None, None, net_pnl, 'trend turned against at a candle close but no mark sample followed before the actual exit',
                           online=True, candle_cutoff=d[0], trend=d[2])
        lim = 'filled at the first mark sample at/after the candle-close decision; samples miss intrabar moves'
        if on.get('rx_gap'): lim += '; first sample after a gap / missing samples'
        return _decide(lot, rule, tuple(rx[:4]), rx[1], fee_rate, slip_bps, net_pnl, lim,
                       parts=[_decl_part(lot), (d[0], None, 'closed candle (trend against the position)'),
                              (d[1], None, 'cycle decision after the candle close')],
                       online=True, candle_cutoff=d[0], trend=d[2], after_gap=bool(on.get('rx_gap')))
    except Exception as e:
        return _policy(rule, None, None, net_pnl, f'not evaluated ({type(e).__name__})')


# ------------------------------------------------------------------ owner scope 2026-10-07: regime snapshots (closed candles only)
def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def regime_snapshot(symbol, tf, candle_t, tf_s, c, e20, e50, e200):
    """Trend state of ONE closed candle (the engine passes the last closed candle of its cached frame; cycle path, never
    per mark): above200 = close > EMA200, ema20_gt_ema50 = EMA20 > EMA50; trend 'up' (both), 'down' (neither), 'mixed',
    or 'unknown' (an input missing / not finite). cutoff_t = the candle close = the information cutoff. None on junk."""
    try:
        t0, bar = _ts(candle_t), tf_s or tf_seconds(tf)
        if t0 is None or not bar: return None
        c, e20, e50, e200 = _f(c), _f(e20), _f(e50), _f(e200)
        a = None if c is None or e200 is None else c > e200
        u = None if e20 is None or e50 is None else e20 > e50
        tr = 'unknown' if a is None or u is None else 'up' if a and u else 'down' if not a and not u else 'mixed'
        return dict(symbol=symbol, tf=tf, tf_s=bar, candle_t=t0.isoformat(timespec='seconds'),
                    cutoff_t=(t0 + timedelta(seconds=bar)).isoformat(timespec='seconds'), c=c, e20=e20, e50=e50, e200=e200,
                    above200=a, ema20_gt_ema50=u, trend=tr)
    except Exception:
        return None


def regime_at(snap, decision_t, stale_bars=REGIME_STALE_BARS):
    """The snapshot as known at a decision: status 'ok' only when its cutoff (candle close) is at/before the decision
    and it is at most stale_bars bars old; otherwise status 'unknown' with why (never guessed). Pure."""
    try:
        if not isinstance(snap, dict): return dict(status='unknown', why='no closed-candle snapshot for this symbol / timeframe', decision_at=decision_t)
        out = dict(snap, decision_at=decision_t, info_cutoff=snap.get('cutoff_t'))
        cut, dec, bar = _ts(snap.get('cutoff_t')), _ts(decision_t), snap.get('tf_s')
        if cut is None or dec is None or not bar: return dict(out, status='unknown', causal_ok=None, why='unparseable times')
        age = (dec - cut).total_seconds() / bar
        out.update(causal_ok=cut <= dec, age_bars=round(age, 3))
        if cut > dec: return dict(out, status='unknown', why='snapshot cutoff is after the decision (not causal)')
        if age > stale_bars: return dict(out, status='unknown', why=f'snapshot older than {stale_bars} bars')
        if snap.get('trend') == 'unknown': return dict(out, status='unknown', why='indicator values missing')
        return dict(out, status='ok')
    except Exception:
        return dict(status='unknown', why='not evaluated', decision_at=decision_t)


def entry_context(sym_at, btc_at, btc_basis):
    """Regime context of an entry: the symbol on the lot tf and BTC (4h preferred) as known at the entry. label:
    'bear' = both closes below their EMA200, 'bull' = both above, 'mixed' = they differ, 'unknown' = either not usable."""
    try:
        a = sym_at.get('above200') if isinstance(sym_at, dict) and sym_at.get('status') == 'ok' else None
        b = btc_at.get('above200') if isinstance(btc_at, dict) and btc_at.get('status') == 'ok' else None
        lab = 'unknown' if a is None or b is None else 'bull' if a and b else 'bear' if not a and not b else 'mixed'
        return dict(symbol=sym_at, btc=btc_at, btc_basis=btc_basis, label=lab,
                    rule="bear: symbol close < EMA200 (lot tf) AND BTC close < EMA200 (btc_basis); bull mirrored; else mixed / unknown")
    except Exception:
        return dict(label='unknown')


def trend_against(side, s):
    """True when a usable snapshot shows the trend against the side (LONG: close < EMA200 OR EMA20 < EMA50; SHORT
    mirrored), False when both are known and neither is against, None when unknown (never guessed)."""
    if not isinstance(s, dict) or s.get('status') != 'ok': return None
    a, u = s.get('above200'), s.get('ema20_gt_ema50')
    bad = (a is False, u is False) if side == 'LONG' else (a is True, u is True)
    if any(bad): return True
    return None if a is None or u is None else False


def note_add(lot, fill, snap_at):
    """Store the regime snapshot known at an add fill on lot['ap']['ctx']['adds'] (bounded). Memory only, called by the
    engine right after it books the add (not per mark). Returns True if stored."""
    try:
        ap = lot.get('ap')
        if not isinstance(ap, dict): return False
        ctx = ap.setdefault('ctx', dict(entry=None))
        adds = ctx.setdefault('adds', [])
        ctx['adds_n'] = ctx.get('adds_n', 0) + 1
        if len(adds) >= CTX_ADDS_MAX: return False
        i = len(lot.get('fills') or ()) - 1
        adds.append(dict(fill_i=i, t=fill[0], why=fill[1], regime=_slim_snap(snap_at)))
        return True
    except Exception:
        return False


def _slim_snap(s):
    if not isinstance(s, dict): return None
    return {k: s.get(k) for k in ('status', 'why', 'tf', 'candle_t', 'info_cutoff', 'decision_at', 'causal_ok', 'age_bars',
                                  'c', 'e20', 'e50', 'e200', 'above200', 'ema20_gt_ema50', 'trend') if k in s}


def regime_trigger(lot, snap, now_t):
    """Cycle path (candle close, never per mark): when the lot declared the regime exit and the just-closed candle's trend
    is against the position (LONG: trend 'down', SHORT: 'up'), store the decision on the online state; the next mark
    sample fills it (_online_step). Only if the candle close is strictly before now. Returns the decision or None."""
    try:
        if _dparams(lot, 'regime_exit') is None or not isinstance(snap, dict): return None
        ex = lot.get('ex')
        on = ex.get('on') if isinstance(ex, dict) else None
        if not isinstance(on, dict) or 'rx' not in on or on.get('rx_dec') is not None: return None
        cut, dn = _ts(snap.get('cutoff_t')), _ts(now_t)
        if cut is None or dn is None or not cut < dn: return None
        if snap.get('trend') != ('down' if lot.get('side') == 'LONG' else 'up'): return None
        on['rx_dec'] = [snap['cutoff_t'], now_t, snap['trend'], snap.get('candle_t')]
        ex['chg'] = ex.get('chg', 0) + 1; ex['ck_force'] = True
        return on['rx_dec']
    except Exception:
        return None


# ------------------------------------------------------------------ owner scope 2026-10-07: per-trade metrics + failure classes
def trade_metrics(lot, net_pnl, fee_rate=FEE_EST, slip_bps=EXEC_SLIP_BPS):
    """MFE/MAE of the mark samples in price (distance from the average entry valid AT that observation), USD (on the
    quantity open at that observation, gross of fees), % of that average and R (risk_usd); time to MFE / MAE / net peak;
    realized net; give-back from the lifecycle net peak to the exit; executable MFE = the lifecycle net peak (a whole-
    position close at a recorded mark sample, after fees paid + exit fee) minus modelled adverse slippage at that mark.
    Candle extremes are never used. Hindsight: these describe the trade after the fact (no live decision uses them)."""
    ex = lot.get('ex') or {}
    sd = _side(lot)
    risk = float(lot['risk_usd']) if _num(lot.get('risk_usd')) and lot['risk_usd'] > 0 else None
    st = ex.get('st') or []
    t_entry = _ts(lot.get('opened'))
    t_ref, t_basis = (t_entry, 'entry (lot.opened)') if t_entry else (_ts(ex.get('t0')), 'tracking start (entry time unknown)')
    rnd = lambda v, n=6: round(v, n) if _num(v) else None

    def exc(px, t, si):
        if not _num(px): return None
        d = dict(px=px, t=t)
        if isinstance(si, int) and not isinstance(si, bool) and 0 <= si < len(st) and _num(st[si][1]) and st[si][1] > 0 and _num(st[si][2]):
            avg, q = float(st[si][1]), float(st[si][2])
            dist = sd * (px - avg)
            d.update(avg_at=avg, qty_at=q, price=rnd(dist, 10), usd=rnd(dist * q), pct=rnd(dist / avg * 100, 4),
                     r=rnd(dist * q / risk, 4) if risk else None, basis='state_at_obs')
        else:
            e = lot.get('entry0', lot.get('e0'))
            dist = sd * (px - float(e)) if _num(e) and e > 0 else None
            d.update(price=rnd(dist, 10), usd=None, pct=rnd(dist / float(e) * 100, 4) if dist is not None else None, r=None,
                     basis='entry0_state_unknown')
        dt = _ts(t)
        d['time_from_entry_s'] = round((dt - t_ref).total_seconds()) if dt is not None and t_ref is not None else None
        return d

    mfe, mae = exc(ex.get('mfe_px'), ex.get('mfe_t'), ex.get('mfe_si')), exc(ex.get('mae_px'), ex.get('mae_t'), ex.get('mae_si'))
    peak = ex.get('peak_pnl') if _num(ex.get('peak_pnl')) else None
    dp = _ts(ex.get('peak_t'))
    out = dict(mfe=mfe, mae=mae, time_basis=t_basis, excursion_basis='mark samples; price = distance from the average entry at that '
               'observation (state_at_obs; entry0_state_unknown = USD/R withheld), USD on the open quantity then, gross of fees',
               time_to_mfe_s=(mfe or {}).get('time_from_entry_s'), time_to_mae_s=(mae or {}).get('time_from_entry_s'),
               time_to_peak_net_s=round((dp - t_ref).total_seconds()) if dp is not None and t_ref is not None else None,
               net_usd=rnd(net_pnl), net_r=rnd(net_pnl / risk, 4) if risk and _num(net_pnl) else None,
               peak_net_usd=peak, peak_net_r=rnd(peak / risk, 4) if risk and peak is not None else None,
               giveback_usd=rnd(peak - net_pnl) if peak is not None and _num(net_pnl) else None,
               giveback_pct_of_mfe=rnd((peak - net_pnl) / peak * 100, 2) if peak is not None and peak > 0 and _num(net_pnl) else None,
               label='hindsight (describes the finished trade; no live decision input)')
    px, q = ex.get('peak_px'), ex.get('peak_q')
    if peak is not None and _num(px) and _num(q):
        s = slip_bps / 1e4
        ev = peak - s * px * q * (1 - sd * fee_rate)          # the same close at px * (1 - sd x slip): adverse fill, its fee
        out.update(executable_mfe_usd=rnd(ev), executable_mfe_r=rnd(ev / risk, 4) if risk else None, executable_mfe_t=ex.get('peak_t'),
                   executable_px=px, executable_qty=q, executable_slip_bps=slip_bps,
                   executable_basis='net peak at a recorded mark sample, after fees + exit fee, minus modelled slippage; no candle extremes')
    else:
        out.update(executable_mfe_usd=None, executable_mfe_r=None, executable_mfe_t=None,
                   executable_basis='not available: no net peak mark/quantity recorded')
    return out


def _green(m):
    """(value, basis) the green tests use: the executable MFE, else the lifecycle net peak."""
    if _num(m.get('executable_mfe_usd')): return float(m['executable_mfe_usd']), 'executable_mfe_usd'
    if _num(m.get('peak_net_usd')): return float(m['peak_net_usd']), 'peak_net_usd (no executable MFE recorded)'
    return None, None


def failure_flags(lot, net_pnl, m):
    """Deterministic failure classes (FLAGS) of a finished trade. A class that cannot be evaluated goes to 'unknown'
    (never guessed). See docs/reviews/T05a_review_request.md for the definitions."""
    flags, unknown, det = [], [], {}
    peak = m.get('peak_net_usd')
    if _num(peak):
        if peak <= 0: flags.append('never_green')
    else: unknown.append('never_green')
    g, gb = _green(m)
    if g is None or not _num(net_pnl): unknown += ['green_to_red', 'gave_back_gt_50pct_mfe']
    else:
        det['green_basis'] = gb
        if g > 0 and net_pnl < 0: flags.append('green_to_red')
        if g > 0 and (g - net_pnl) > 0.5 * g: flags.append('gave_back_gt_50pct_mfe')
    side = lot.get('side')
    ap = lot.get('ap') if isinstance(lot.get('ap'), dict) else {}
    ctx = ap.get('ctx') if isinstance(ap.get('ctx'), dict) else {}
    so = [i for i, f in enumerate(lot.get('fills') or ()) if isinstance(f, (list, tuple)) and len(f) >= 2 and f[1] == 'safety_order']
    if so:
        adds = {a.get('fill_i'): a for a in (ctx.get('adds') or ()) if isinstance(a, dict)}
        res = [trend_against(side, (adds.get(i) or {}).get('regime')) for i in so]
        det['dca_adds'] = [dict(fill_i=i, against=r) for i, r in zip(so, res)][:CTX_ADDS_MAX]
        if any(r is True for r in res): flags.append('dca_into_trend')
        elif any(r is None for r in res): unknown.append('dca_into_trend')
    ent = ctx.get('entry') if isinstance(ctx.get('entry'), dict) else None
    lab = ent.get('label') if ent else 'unknown'
    name = 'long_in_bear_regime' if side == 'LONG' else 'short_in_bull_regime'
    if lab in ('bull', 'bear', 'mixed'):
        if (side == 'LONG' and lab == 'bear') or (side == 'SHORT' and lab == 'bull'): flags.append(name)
    else: unknown.append(name)
    det['entry_regime'] = lab
    return dict(flags=flags, unknown=unknown, detail=det, regime=lab)


def _dca_bucket(n):
    return '?' if not isinstance(n, int) else '3+' if n >= 3 else str(n)


def segment_of(lot, ledger, regime=None):
    """The segment keys of a finished trade (SEG_DIMS)."""
    n = sum(1 for f in (lot.get('fills') or ()) if isinstance(f, (list, tuple)) and len(f) >= 2 and f[1] == 'safety_order')
    return dict(strategy=_key_str(lot.get('sleeve')), side=_key_str(lot.get('side')), symbol=_key_str(lot.get('symbol')),
                tf=_key_str(lot.get('tf')), regime=regime or 'unknown', dca=_dca_bucket(n),
                runner='runner' if (ledger or {}).get('runner') else 'no_runner')


def segments(records, top=12, samples=3):
    """Aggregate trade_audit records by every SEG_DIMS dimension: n, win rate, expectancy (mean net USD and mean net R),
    flag counts; plus per flag the count and up to `samples` most recent audit ids. Pure; never raises."""
    dims = {d: {} for d in SEG_DIMS}
    fl, unk, n_all = {f: dict(n=0, samples=[]) for f in FLAGS}, {}, 0
    for r in records or ():
        try:
            if not (isinstance(r, dict) and r.get('kind') == 'trade_audit'): continue
            n_all += 1
            sg = dict(strategy=_key_str(r.get('sleeve')), side=_key_str(r.get('side')), symbol=_key_str(r.get('symbol')),
                      tf=_key_str(r.get('tf')), regime='unknown', dca='?', runner='runner' if isinstance(r.get('runner'), dict) else 'no_runner')
            if isinstance(r.get('segment'), dict): sg.update(r['segment'])                  # a full record
            elif isinstance(r.get('seg'), str) and r['seg'].count('|') == 2:              # a slimmed window record
                sg.update(zip(('regime', 'dca', 'runner'), r['seg'].split('|')))
            net = r.get('net_pnl') if _num(r.get('net_pnl')) else None
            rr = r.get('r') if _num(r.get('r')) else None
            sp = lambda v: v.split(',') if isinstance(v, str) else v if isinstance(v, list) else ()
            fs = [f for f in sp(r.get('flags')) if f in fl]
            for f in fs:
                fl[f]['n'] += 1
                if isinstance(r.get('id'), str): fl[f]['samples'] = (fl[f]['samples'] + [r['id']])[-samples:]
            for f in sp(r.get('flags_unknown')):
                if isinstance(f, str): unk[f[:40]] = unk.get(f[:40], 0) + 1
            for d in SEG_DIMS:
                g = dims[d].setdefault(_key_str(sg.get(d)), dict(n=0, _net=[], _r=[], fl={}))
                g['n'] += 1
                if net is not None: g['_net'].append(float(net))
                if rr is not None: g['_r'].append(float(rr))
                for f in fs: g['fl'][f] = g['fl'].get(f, 0) + 1
        except Exception:
            continue
    out = {}
    for d, gs in dims.items():
        rows = []
        for k, g in gs.items():
            ns, rs = g['_net'], g['_r']
            rows.append(dict(key=k, n=g['n'], n_net=len(ns), wins=sum(1 for x in ns if x > 0),
                             win_rate=round(sum(1 for x in ns if x > 0) / len(ns), 4) if ns else None,
                             exp_usd=round(sum(ns) / len(ns), 6) if ns else None, exp_r=round(sum(rs) / len(rs), 4) if rs else None,
                             n_r=len(rs), net_usd=round(sum(ns), 6) if ns else None, flags=g['fl']))
        rows.sort(key=lambda x: (-x['n'], x['key']))
        out[d] = rows[:top]
    return dict(trades=n_all, by=out, flags=fl, flags_unknown=unk,
                basis='trade_audit records in the audit window; expectancy = mean net P&L after fees (USD) and mean net R')


def missed_short(decision, side, raw=None):
    """Why a funnel candidate is a missed short opportunity (the RAW short signal fired, or a SHORT candidate, and no
    short was taken), or None: 'side_masked' (the slot's sides hide shorts), 'not_taken' (a SHORT candidate stopped by a
    gate), 'long_preferred' (both raw sides fired; the engine took / tried the long)."""
    try:
        rs = isinstance(raw, dict) and bool(raw.get('se'))
        if decision == 'side_masked': return 'side_masked' if rs else None
        if decision == 'not_taken': return 'not_taken' if side == 'SHORT' else ('long_preferred' if rs else None)
        if decision == 'taken' and side == 'LONG' and rs: return 'long_preferred'
    except Exception:
        pass
    return None


def missed_short_summary(items, missed=None, samples=3):
    """Counts of missed short opportunities (engine memory since start, one per sleeve/symbol/candle) by why and by
    gate code, plus the SHORT not-taken records in the persisted missed list. Counts only: no hindsight outcome."""
    try:
        it = [x for x in (items or ()) if isinstance(x, dict)]
        by, codes = {}, {}
        for x in it:
            by[x.get('why') or '?'] = by.get(x.get('why') or '?', 0) + 1
            if x.get('code'): codes[x['code']] = codes.get(x['code'], 0) + 1
        ms = sum(1 for m in (missed or ()) if isinstance(m, dict) and m.get('side') == 'SHORT' and not m.get('kind'))
        return dict(n=len(it), by_why=by, by_code=dict(sorted(codes.items(), key=lambda kv: (-kv[1], kv[0]))),
                    samples=[{k: x.get(k) for k in ('sleeve', 'symbol', 'candle', 'why', 'code')} for x in it[-samples:]],
                    short_not_taken_in_missed_list=ms, basis='since engine start (memory, bounded); counts only, no hindsight outcome')
    except Exception:
        return dict(error='missed short summary not evaluated')


# ------------------------------------------------------------------ candle-close hold/close evaluation (cycle; memory only)
def hold_eval(lot, key, candle, now_t, action, exit_signal=None, runner_override=None, missing=False, fee_rate=FEE_EST, regime=None):
    """One 'hold_eval' event per open lot per closed candle of its tf: what the bot did (action: 'hold' / 'close_exit_signal'
    / 'close_time_exit') and what the predeclared rules saw at the candle close. info_cutoff = the candle close,
    decision_at = now; withheld unless close < now. Close-price checks are executable; checks on the candle high/low
    are labelled 'upper_bound' (intrabar order unknown) unless a mark sample inside the candle proves the touch.
    missing=True (T05b outage): a missing sample, nothing evaluated. Returns the event or None."""
    try:
        sd = _side(lot)
        bar = candle.get('tf_s') or tf_seconds(lot.get('tf'))
        t_open = _ts(candle.get('t'))
        t_close = t_open + timedelta(seconds=bar) if t_open is not None and bar else None
        dn = _ts(now_t)
        ok = t_close is not None and dn is not None and t_close < dn
        ev = dict(v=AUDIT_VERSION, kind='hold_eval', t=now_t, id=key, symbol=lot.get('symbol'), side=lot.get('side'), tf=lot.get('tf'),
                  candle_t=candle.get('t'), info_cutoff=dict(t=t_close.isoformat(timespec='seconds') if t_close else None, what='candle close'),
                  decision_at=dict(t=now_t), causal_ok=bool(ok and not missing), missing_sample=bool(missing), action=action,
                  exit_signal=exit_signal, runner_override=runner_override, qty=lot.get('qty'), avg=lot.get('avg'))
        if isinstance(regime, dict):                       # the closed candle's trend state (same cutoff as the candle)
            ev['regime'] = {k: regime.get(k) for k in ('trend', 'above200', 'ema20_gt_ema50')}
        if missing: ev['withheld'] = 'exchange outage (T05b): missing sample'; return ev
        if not ok: ev['withheld'] = 'candle close is not before the decision'; return ev
        c, h, l = (float(candle[k]) if _num(candle.get(k)) else None for k in ('c', 'h', 'l'))
        ev.update(close=c, high=h, low=l, hl_label='candle high/low: upper bounds (intrabar order unknown)')
        if c is None: ev['withheld'] = 'no candle close'; return ev
        real, fees = lot.get('realized') or 0.0, lot.get('fees') or 0.0
        if _num(real) and _num(fees) and _num(lot.get('qty')) and _num(lot.get('avg')):
            ev['open_net_at_close'] = round(real - fees + sd * (c - lot['avg']) * lot['qty'] - lot['qty'] * c * fee_rate, 6)
        fav, adv = (h, l) if sd == 1 else (l, h)
        ex = lot.get('ex') or {}
        tgt = target_price(lot)
        if tgt is not None:
            tt = _ts(ex.get('target_touch_t'))
            proven = tt is not None and t_open <= tt <= t_close
            ev['target'] = dict(level=round(tgt, 10), at_close=sd * (c - tgt) >= 0,
                                by_extreme=None if fav is None else sd * (fav - tgt) >= 0,
                                extreme_label='proven by a mark sample in this candle' if proven else 'upper_bound')
        if _num(lot.get('stop')):
            ev['stop'] = dict(level=lot['stop'], by_extreme=None if adv is None else sd * (adv - lot['stop']) <= 0, extreme_label='upper_bound')
        tc = _dparams(lot, 'time_cap') or {}
        if isinstance(tc.get('bars'), int) and _ts(lot.get('opened')) and bar:
            held = (t_close - _ts(lot['opened'])).total_seconds() / bar
            ev['time_cap'] = dict(bars=tc['bars'], bars_held=round(held, 2), reached=held >= tc['bars'])
        return ev
    except Exception:
        return None


# ------------------------------------------------------------------ funnel events (candidates, incl. RAW signals before masking)
def funnel_event(t, sleeve, symbol, side, candle, decision, reason=None, raw=None):
    """A candidate decision. Terminal (exactly one per candidate): 'taken' (a lot was created), 'not_taken'. Interim:
    'armed_trailing' (trailing entry armed), 'order_placed' (post-only maker order resting; 'taken' / 'not_taken'
    follows when it is finalized). Other: 'warning' (taken with a risk warning), 'side_masked'. raw: the
    signal BEFORE side masking, dict(le, se, sides[, le_m, se_m]) - so a short/long the slot config hides is still
    counted (decision 'side_masked'), and a candle where both sides fired keeps the unused side (both_raw)."""
    try:
        ev = dict(v=AUDIT_VERSION, kind='funnel', t=t, sleeve=sleeve, symbol=symbol, side=side, candle=candle, decision=decision)
        if reason is not None:
            i = reason_info(_safe_text(reason, 600))                      # scrub first: a cut-off blob must not survive
            ev.update(stage=i['stage'], code=i['code'])
            if i.get('detail'): ev['detail'] = _safe_text(i['detail'], 160)
        if isinstance(raw, dict):
            ev.update(raw_long=bool(raw.get('le')), raw_short=bool(raw.get('se')), sides=raw.get('sides'),
                      masked_long=raw.get('le_m'), masked_short=raw.get('se_m'), both_raw=bool(raw.get('le')) and bool(raw.get('se')))
        ms = missed_short(decision, side, raw)
        if ms: ev['missed_short_opportunity'] = ms
        return ev
    except Exception:
        return None


def coverage_event(t, key, lot, why):
    try:
        ap = _declared(lot) or {}
        return dict(v=AUDIT_VERSION, kind='coverage', t=t, id=key, symbol=lot.get('symbol'), side=lot.get('side'),
                    coverage=ap.get('coverage', 'partial'), why=why)
    except Exception:
        return None


# ------------------------------------------------------------------ attribution summary (pure; for /api/status health.audit)
def _key_str(x):
    return x if isinstance(x, str) and x else '?'


_SLIM_KEYS = ('kind', 'id', 't', 'coverage', 'sleeve', 'tf', 'side', 'r', 'giveback_pct', 'target', 'symbol', 'net_pnl')


def slim_record(r):
    """The in-memory summary window keeps ONLY final trade records, reduced to what attribution()/coverage() read.
    Other audit events (EVENT_KINDS) -> None (not kept in the window; they stay in the file). Anything else is
    returned unchanged. Never raises."""
    try:
        if not isinstance(r, dict): return r
        if r.get('kind') in EVENT_KINDS and r.get('kind') != 'trade_audit': return None
        if r.get('kind') != 'trade_audit': return r
        s = {k: r.get(k) for k in _SLIM_KEYS}
        s['target_touch_t'] = bool(r.get('target_touch_t'))
        cfs = r.get('counterfactuals')
        s['counterfactuals'] = [dict(kind=c['kind'], rule=c['rule'], vs_actual=c.get('vs_actual')) for c in (cfs if isinstance(cfs, list) else ())
                                if isinstance(c, dict) and c.get('kind') == 'causal_policy' and c.get('rule') == RULE_TARGET]
        ru = r.get('runner')
        s['runner'] = dict(value_vs_activation=ru.get('value_vs_activation')) if isinstance(ru, dict) else None
        j = lambda v: ','.join(x for x in v if isinstance(x, str)) or None if isinstance(v, list) else None
        s['flags'], s['flags_unknown'] = j(r.get('flags')), j(r.get('flags_unknown'))       # compact strings (window memory)
        sg = r.get('segment')
        if isinstance(sg, dict): s['seg'] = f"{sg.get('regime')}|{sg.get('dca')}|{sg.get('runner')}"
        return s
    except Exception:
        return r


def coverage(history, records, since=None):
    """Closed trades vs audit records: a closed trade WITHOUT a trade_audit record is audit_available False, split by why:
    'legacy' = closed before the audit existed (before `since`, the first engine start with T05a; all of them when
    since is unknown), 'audit_rotated' = closed with the audit but before the oldest record still in the window (its
    record rotated out of trade_audit.jsonl(.1)), 'audit_missing' = closed inside the window yet no record (e.g. a
    write dropped by the bounded queue). 'partial' = the record says partial coverage (upgraded open lot, gaps, outage)."""
    try:
        ids, first = {}, None
        for r in records or ():
            if isinstance(r, dict) and r.get('kind') == 'trade_audit' and isinstance(r.get('id'), str):
                ids[r['id']] = r.get('coverage')
                if r.get('t') is not None and (first is None or _before_or_at(r['t'], first)): first = r['t']
        hs = [h for h in (history or ()) if isinstance(h, dict)]
        avail = [h for h in hs if h.get('id') in ids]
        why = dict(legacy=0, audit_rotated=0, audit_missing=0)
        for h in hs:
            if h.get('id') in ids: continue
            c = h.get('closed')
            if since is None or c is None or not _before_or_at(since, c): why['legacy'] += 1
            elif first is None or not _before_or_at(first, c): why['audit_rotated'] += 1
            else: why['audit_missing'] += 1
        return dict(closed=len(hs), audit_available=len(avail), audit_unavailable=len(hs) - len(avail),
                    partial=sum(1 for h in avail if ids[h['id']] == 'partial'), **why)
    except Exception:
        return dict(error='coverage not evaluated')


def audit_available(hist_rec, records):
    """False for a legacy closed trade (no trade_audit record with its id)."""
    try: return any(isinstance(r, dict) and r.get('kind') == 'trade_audit' and r.get('id') == hist_rec.get('id') for r in records or ())
    except Exception: return False


def attribution(records, top=50):
    """Aggregate close records (kind='trade_audit') by (sleeve, tf, side) and not-taken signals (missed records: a reason
    and no kind) into a 'stage/code' histogram (old records without a code are classified from their text). Other kinds
    ('warning' = taken with a warning, 'add_blocked', audit events) are only counted. Malformed entries are counted.
    runner_value = sum of the SAME-quantity runner attribution (record['runner']['value_vs_activation'])."""
    groups, funnel, bad, other = {}, {}, 0, {}
    try: records = list(records or ())
    except Exception: records, bad = [], 1
    for r in records:
        try:
            if not isinstance(r, dict): bad += 1; continue
            if r.get('kind') == 'trade_audit':
                key = (_key_str(r.get('sleeve')), _key_str(r.get('tf')), _key_str(r.get('side')))
                g = groups.setdefault(key, dict(n=0, _r=[], _gb=[], _tw=0, _tt=0, _rv=[], _ru=[]))
                g['n'] += 1
                if _num(r.get('r')): g['_r'].append(float(r['r']))
                if _num(r.get('giveback_pct')): g['_gb'].append(float(r['giveback_pct']))
                if _num(r.get('target')):
                    g['_tw'] += 1; g['_tt'] += bool(r.get('target_touch_t'))
                cfs = r.get('counterfactuals')
                for c in (cfs if isinstance(cfs, list) else ()):
                    if isinstance(c, dict) and c.get('kind') == 'causal_policy' and c.get('rule') == RULE_TARGET and _num(c.get('vs_actual')):
                        g['_rv'].append(float(c['vs_actual']))
                ru = r.get('runner')
                if isinstance(ru, dict) and _num(ru.get('value_vs_activation')): g['_ru'].append(float(ru['value_vs_activation']))
            elif r.get('kind') is None and ('reason' in r or 'code' in r):
                st, cd = r.get('stage'), r.get('code')
                if not (isinstance(st, str) and isinstance(cd, str)): st, cd = reason_code(r.get('reason'))
                funnel[f'{st}/{cd}'] = funnel.get(f'{st}/{cd}', 0) + 1
            elif isinstance(r.get('kind'), str) and r['kind']:            # e.g. 'warning' (trade WAS taken), 'add_blocked'
                other[r['kind'][:40]] = other.get(r['kind'][:40], 0) + 1
            else:
                bad += 1
        except Exception:
            bad += 1
    rows = []
    for (sl, tf, side), g in groups.items():
        rs, gb, rv, ru = g['_r'], g['_gb'], g['_rv'], g['_ru']
        rows.append(dict(sleeve=sl, tf=tf, side=side, n=g['n'], n_r=len(rs),
                         avg_r=round(sum(rs) / len(rs), 4) if rs else None, median_r=round(statistics.median(rs), 4) if rs else None,
                         avg_giveback_pct=round(sum(gb) / len(gb), 2) if gb else None,
                         target_touch_rate=round(g['_tt'] / g['_tw'], 4) if g['_tw'] else None, with_target=g['_tw'],
                         close_at_target_minus_actual=round(sum(rv), 6) if rv else None, n_target_policy=len(rv),
                         runner_value=round(sum(ru), 6) if ru else None, n_runner=len(ru)))
    rows.sort(key=lambda x: (-x['n'], x['sleeve'], x['tf'], x['side']))
    return dict(groups=rows[:top], n_groups=len(rows), closes=sum(x['n'] for x in rows),
                funnel=dict(sorted(funnel.items(), key=lambda kv: (-kv[1], kv[0]))), not_taken=sum(funnel.values()),
                other_kinds=other, malformed=bad)


# ------------------------------------------------------------------ funnel: stable reason codes for not-taken signals
# Observe only: the text and behaviour of every gate are unchanged; the code is derived from the existing text.
# 1) wrappers that carry an inner cause are unwrapped first (so 'order failed: ... leverage ...' is an order failure and
#    'trailing entry cancelled: max positions reached (2)' is a trailing-stage cancel by the max-positions gate);
# 2) then exact texts / fixed prefixes, no substring guessing. Unknown -> ('other', 'other') with the raw text kept.
# tests/test_trade_audit.py scans engine.py for every reason literal and fails only if one lands in other/other: a new
# gate text that starts with a known prefix inherits that code silently; a genuinely new text must be added here.
_EXACT = {
    'not connected to Binance': ('connectivity', 'not_connected'),
    'Binance outage - no new entries until it answers again': ('connectivity', 'exchange_outage'),   # T05b gate
    'bad direction': ('input', 'bad_direction'),
    'hedge mode off (shorts unavailable)': ('side_mask', 'hedge_off'),
    'daily loss halt': ('filter', 'halt'), 'daily loss halt is active': ('filter', 'halt'),
    'an open trade on this coin is waiting for its stop to be confirmed': ('risk_gateway', 'stop_unconfirmed'),
    'entries paused': ('filter', 'paused'), 'coin switched off': ('filter', 'coin_off'),
    'strategy slot switched off': ('filter', 'slot_off'), 'outside entry hours': ('filter', 'hours'),
    'volatility filter': ('filter', 'volatility'), 'already in a trade on this coin': ('capacity', 'in_trade'),
    'an entry is already working on this coin': ('capacity', 'entry_working'),
    'leverage cap reached for this slot': ('capacity', 'leverage_cap'),
    'entry order unconfirmed': ('execution', 'entry_unconfirmed'),
    'stop order failed - trade closed again': ('execution', 'stop_failed'),
    'maker entry not filled (no market fallback)': ('execution', 'maker_unfilled'),
    'strategy slot removed': ('filter', 'slot_removed'),                                  # trailing entry whose slot was deleted
}
_PREFIX = (
    ('Binance holds an untracked position', 'risk_gateway', 'untracked_position'),
    ('max positions reached', 'capacity', 'max_positions'),
    ('regime: ', 'regime', 'regime'),
    ('regime unknown', 'regime', 'regime_unknown'),
    ('pump guard', 'risk_gateway', 'pump_guard'),
    ('DCA basket without a hard stop', 'config', 'dca_no_stop'),
    ('DCA settings out of range', 'config', 'dca_range'),
    ('DCA paused', 'filter', 'dca_paused'),                  # owner decision 2026-10-07: DCA off unless DCA_ENABLED
    ('size below Binance minimum', 'execution', 'size_min'),
    ('trailing entry expired', 'trailing', 'expired'),
)
# wrappers: (prefix, stage, code or None = the inner gate's code, inner is a gate text)
_WRAP = (
    ('WARNING: ', 'filter', 'warning', False),
    ('trailing entry cancelled: ', 'trailing', None, True),
    ('maker entry not filled; market fallback blocked: ', 'execution', 'maker_fallback_blocked', True),
    ('maker entry not filled; ', 'execution', 'maker_unfilled', False),   # T03c r2: no fallback, the why is the detail
    ('order failed', 'execution', 'order_failed', False),
    ('could not set leverage/margin on Binance', 'execution', 'leverage', False),
    ('Claude review vetoed', 'filter', 'ai_veto', False),
)


def _slug(s):
    s = ''.join(c if c.isalnum() or c == '_' else '_' for c in str(s).strip().lower())[:40].strip('_')
    return s or 'unnamed'


def reason_info(reason, _depth=0):
    """dict(stage, code[, detail]) for a not-taken signal's reason text. detail: the inner cause of a wrapper text
    (the inner gate as 'stage/code' for trailing cancels and blocked fallbacks). Never raises."""
    try:
        r = str(reason or '').strip()
        for pre, stage, code, nested in _WRAP:
            if r.startswith(pre):
                rest = r[len(pre):].lstrip(' :')
                out = dict(stage=stage, code=code)
                if nested and _depth < 3:
                    inner = reason_info(rest, _depth + 1)
                    if code is None: out['code'] = inner['code']
                    out['detail'] = f"{inner['stage']}/{inner['code']}" + (f": {inner['detail']}" if inner.get('detail') else '')
                elif rest: out['detail'] = rest[:160]
                if out['code'] is None: out['code'] = 'other'
                return out
        if r.startswith('risk rule '):
            name, _, msg = r[len('risk rule '):].partition(':')
            return dict(stage='risk_gateway', code=_slug(name), detail=msg.strip()[:160])
        if r in _EXACT:
            st, cd = _EXACT[r]; return dict(stage=st, code=cd)
        if r.endswith(' is not tradable'): return dict(stage='risk_gateway', code='not_tradable')
        for pre, st, cd in _PREFIX:
            if r.startswith(pre): return dict(stage=st, code=cd)
    except Exception:
        pass
    return dict(stage='other', code='other')


def reason_code(reason):
    """(stage, code) for a not-taken signal's reason text. Never raises."""
    i = reason_info(reason)
    return i['stage'], i['code']
