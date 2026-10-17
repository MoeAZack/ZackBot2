"""BR-01 (PAPER research only): bounded recovery / staged-entry variants layered over a slot's EXISTING base signal.

A slot opts in with mgmt['br01'] = {'variant': <name>[, 'deadline': 8..12]}. Off by default: no preset or slot carries it,
and the engine refuses it in live mode. The base signal, its stop and its exits are not touched - only HOW the slot's own
risk budget is put on is staged. The single-entry control is the same slot without 'br01'.

One pure decision module, shared by backtest.run and the engine (same arithmetic, same refusals):
  - budget: the control's sizing (feasibility.risk_qty, leverage cap, step) gives q_plan at the structural stop; the risk
    budget is q_plan x |e0 - stop|, exactly the control's initial risk. Leg k is the fixed fraction LEGS[k] of q_plan and
    the last leg is the exact remainder, so the filled total can never exceed q_plan. Leg sizes are constants of the plan,
    never a function of P&L, losses or a loss streak (no doubling, no recursive sizing, no multiplier);
  - at most MAX_ADDS adds; a leg fires at most once (admit());
  - the stop is never widened: after every add cap_stop() tightens it so that qty x |avg - stop| <= budget, and an add
    that would need a stop at / through price is refused;
  - a deadline of 8-12 bars from the first fill closes the staged aggregate;
  - decide() reads only the CLOSED candle it is given (called at that candle's close); the fill happens afterwards (next
    open in the backtest, market in the engine);
  - every add, refusal and cancel is an event carrying variant, leg and reason (refusals once per leg + reason).
No edge is claimed; research variants for PAPER evidence only."""
import copy

import numpy as np

import strategies as S

TAG = 'BR-01 EXPERIMENTAL / PAPER-only staged entry'
MAX_ADDS = 2
DEADLINE_MIN, DEADLINE_MAX, DEADLINE_DEFAULT = 8, 12, 12
# kind: when the next leg may be added (closed candle)
#   adverse    - close at / beyond e0 -/+ at_atr[k-1] x entry ATR (bounded_recovery; range_ladder also needs a confirmed range)
#   reconfirm  - after a closed candle reached >= adverse_atr ATR against the trade, a LATER closed candle that does not
#                extend that extreme and closes in the trade's direction (above its open and the previous close)
#   favourable - a closed candle closes >= at_r R in favour, in the trade's direction
#   confirm    - a closed candle after the entry closes in the trade's direction (above its open and the previous close)
VARIANTS = {
    'bounded_recovery': dict(legs=(0.60, 0.25, 0.15), kind='adverse', at_atr=(0.5, 1.0)),
    'range_ladder': dict(legs=(0.60, 0.25, 0.15), kind='adverse', at_atr=(0.5, 1.0), range=True),
    'reconfirm_recovery': dict(legs=(0.60, 0.40), kind='reconfirm', adverse_atr=0.5),
    'anti_martingale': dict(legs=(0.60, 0.40), kind='favourable', at_r=0.5),
    'failed_breakout_staged': dict(legs=(0.60, 0.40), kind='confirm', base='failed_breakout_scalp'),
}
for _v, _x in VARIANTS.items():
    assert abs(sum(_x['legs']) - 1.0) < 1e-12 and 2 <= len(_x['legs']) <= MAX_ADDS + 1, _v
# management blocks that change size or exits on their own: staging is not layered over them
EXCLUSIVE = ('dca', 'pyramid', 'runner', 'ttp', 'tps')


def validate(cfg, key, mgmt=None, sleeve=None):
    """Normalised dict(variant, deadline, legs) of a slot's mgmt['br01'], or ValueError."""
    if not isinstance(cfg, dict): raise ValueError('br01 must be a dict')
    extra = set(cfg) - {'variant', 'deadline'}
    if extra: raise ValueError(f'br01: unknown setting(s) {sorted(extra)}')
    v = cfg.get('variant')
    if v not in VARIANTS: raise ValueError(f'br01: unknown variant {v!r}')
    dl = cfg.get('deadline', DEADLINE_DEFAULT)
    if isinstance(dl, bool) or not isinstance(dl, int) or not DEADLINE_MIN <= dl <= DEADLINE_MAX:
        raise ValueError(f'br01: deadline must be {DEADLINE_MIN}-{DEADLINE_MAX} bars')
    base = VARIANTS[v].get('base')
    if base and key != base: raise ValueError(f'br01: {v} reuses the {base} signal only')
    clash = [k for k in EXCLUSIVE if (mgmt or {}).get(k)]
    if clash: raise ValueError(f'br01: not layered over a slot that already scales / trails ({", ".join(clash)})')
    if (sleeve or {}).get('kelly'): raise ValueError('br01: no Kelly / performance-based sizing on a staged slot')
    return dict(variant=v, deadline=dl, legs=list(VARIANTS[v]['legs']))


def new_state(cfg, sd, q_plan, q0, e0, stop, atr0, deadline, on=True):
    """Persisted leg state of a new staged position (JSON-safe). deadline: an ABSOLUTE bar key (backtest bar index, engine
    ISO time) - never a counter reset on restart. on=False: staging not applied (range_ladder outside a range) - the
    position is the plain single entry and nothing below acts on it."""
    return dict(v=cfg['variant'], legs=list(cfg['legs']), sd=int(sd), q_plan=float(q_plan), budget=float(q_plan) * abs(e0 - stop),
                e0=float(e0), R=abs(e0 - stop), atr0=float(atr0), filled=[float(q0)], adds=0,
                status='active' if on else 'off', deadline=deadline, adv=None, seen=[], ev=[])


def check_state(b):
    """Schema check of a persisted leg state: None or the reason it is unusable (engine state validation)."""
    try:
        if b.get('v') not in VARIANTS: return 'unknown variant'
        if b.get('status') not in ('active', 'off', 'done', 'cancelled'): return 'bad status'
        legs, filled = b['legs'], b['filled']
        if list(legs) != list(VARIANTS[b['v']]['legs']): return 'legs differ from the variant'
        if not 1 <= len(filled) <= len(legs) or int(b['adds']) != len(filled) - 1 or b['adds'] > MAX_ADDS: return 'leg count'
        if any(float(q) < 0 for q in filled) or sum(filled) > float(b['q_plan']) * (1 + 1e-9) + 1e-12: return 'legs exceed the plan'
        if not float(b['budget']) >= 0 or b.get('sd') not in (1, -1): return 'bad budget / side'
    except Exception as ex:
        return f'unreadable ({type(ex).__name__})'
    return None


def leg_qty(st, k):
    """Quantity of leg k: its fixed fraction of q_plan; the LAST leg is the exact remainder (never above the plan)."""
    if k == len(st['legs']) - 1: return max(0.0, st['q_plan'] - sum(st['filled']))
    return st['q_plan'] * st['legs'][k]


def admit(st, k):
    """None when leg k may be filled now, else the reason (a consumed leg, a third add, a finished plan)."""
    if st['status'] != 'active': return f"staging {st['status']}"
    if st['adds'] >= MAX_ADDS or k >= len(st['legs']): return f'max adds reached ({MAX_ADDS})'
    if k != len(st['filled']): return f'leg {k} already consumed'
    return None


def apply_fill(st, q, px, t):
    """Leg state after the next leg filled q at px (a copy). Raises when admit() refuses."""
    k = len(st['filled'])
    why = admit(st, k)
    if why: raise ValueError(why)
    st = copy.deepcopy(st)
    st['filled'].append(float(q)); st['adds'] += 1
    if len(st['filled']) == len(st['legs']): st['status'] = 'done'
    _log(st, dict(kind='add', leg=k, qty=float(q), px=float(px), t=str(t), reason=_trigger_text(st, k)))
    return st


def cap_stop(sd, qty, avg, stop, budget):
    """The stop tightened (never loosened) so that qty x |avg - stop| <= budget."""
    if qty <= 0: return stop
    lim = avg - sd * budget / qty
    return max(stop, lim) if sd == 1 else min(stop, lim)


def risk(sd, qty, avg, stop):
    """Open price risk to the stop (no costs): qty x adverse distance avg -> stop."""
    return qty * max(0.0, sd * (avg - stop))


def deadline_hit(st, now):
    return st['status'] != 'off' and now >= st['deadline']


def range_flags(d):
    """Confirmed range regime on every CLOSED candle (causal): ADX below the range scalp's adx_max and the close inside the
    range of the n candles BEFORE it (range_scalp's n / adx_max, not new constants). A breakout close or ADX turning up
    (trend transition) ends it."""
    n = int(S.RS['n'])
    hi, lo = d.h.rolling(n).max().shift(), d.l.rolling(n).min().shift()
    return np.asarray(((d.adx < S.RS['adx_max']) & (d.c <= hi) & (d.c >= lo)).fillna(False), dtype=bool)


def range_ok_last(d):
    """range_flags of the last closed candle of d (False without data: fail closed - no staging)."""
    if d is None or len(d) < int(S.RS['n']) + 2 or 'adx' not in d: return False
    return bool(range_flags(d)[-1])


def _trigger_text(st, k):
    x = VARIANTS[st['v']]
    if x['kind'] == 'adverse': return f"close {x['at_atr'][k - 1]:g} ATR against the entry"
    if x['kind'] == 'reconfirm': return 'closed-candle reversal after an adverse move'
    if x['kind'] == 'favourable': return f"close {x['at_r']:g}R in favour"
    return 'closed-candle confirmation'


def _log(st, ev):
    ev = dict(ev, v=st['v'])
    st['ev'] = (st.get('ev') or [])[-11:] + [ev]
    return ev


def refusal(st, k, why, t=None):
    """A refusal event for leg k (logged ONCE per leg + reason; None for a repeat). Mutates st."""
    key = f'{k}|{why}'
    if key in st['seen']: return None
    st['seen'] = st['seen'][-19:] + [key]
    return _log(st, dict(kind='refuse', leg=k, reason=why, t=str(t)))


def _dir_candle(bar, sd):
    return sd * (bar['c'] - bar['o']) > 0 and sd * (bar['c'] - bar['pc']) > 0


def decide(st, bar, sd, stop, avg, base_exit=False, regime_ok=True, tp_done=False):
    """One decision at the close of a CLOSED candle. bar: dict(t, o, h, l, c, pc = previous close, range_ok); stop / avg:
    the position's current stop and average entry.
    Returns (new state, event or None); event kind 'add' (leg, qty - fill it AFTER this candle), 'refuse' or 'cancel'."""
    st = copy.deepcopy(st)
    if st['status'] != 'active': return st, None
    k = len(st['filled'])
    if k >= len(st['legs']):
        st['status'] = 'done'; return st, None
    x = VARIANTS[st['v']]
    if x.get('range') and not bar.get('range_ok'):         # breakout / trend transition: every unfilled leg cancelled at once
        st['status'] = 'cancelled'
        return st, _log(st, dict(kind='cancel', leg=k, t=str(bar['t']),
                                 reason='range regime ended (breakout / trend transition) - remaining legs cancelled'))
    e0, atr0, R = st['e0'], st['atr0'], st['R']
    if x['kind'] == 'adverse':
        trig = sd * (bar['c'] - (e0 - sd * x['at_atr'][k - 1] * atr0)) <= 0
    elif x['kind'] == 'reconfirm':
        ext = bar['l'] if sd == 1 else bar['h']                 # this candle's adverse extreme
        trig = st['adv'] is not None and sd * (ext - st['adv']) > 0 and _dir_candle(bar, sd)
        if not trig:                                            # record / extend the adverse move (never on the same candle)
            if st['adv'] is not None: st['adv'] = min(st['adv'], ext) if sd == 1 else max(st['adv'], ext)
            elif sd * (e0 - ext) >= x['adverse_atr'] * atr0: st['adv'] = float(ext)
    elif x['kind'] == 'favourable':
        trig = sd * (bar['c'] - (e0 + sd * x['at_r'] * R)) >= 0 and sd * (bar['c'] - bar['o']) > 0
    else:
        trig = _dir_candle(bar, sd)
    if not trig: return st, None
    q = leg_qty(st, k)
    qn = sum(st['filled']) + q
    why = admit(st, k)
    if why is None:
        if tp_done: why = 'partial take-profit already taken'
        elif base_exit: why = 'base signal no longer valid (exit signal on the closed candle)'
        elif not regime_ok: why = "slot's regime filter no longer allows the trade"
        elif sd * (bar['c'] - stop) <= 0: why = 'price at / through the stop'
        elif q <= 0: why = 'nothing left of the plan'
    if why is None:                                             # prospective aggregate at the decision close
        ns = cap_stop(sd, qn, (avg * sum(st['filled']) + bar['c'] * q) / qn, stop, st['budget'])
        if sd * (bar['c'] - ns) <= 0: why = 'add would need a stop at / through price (risk budget)'
    if why: return st, refusal(st, k, why, bar['t'])
    return st, dict(kind='add', leg=k, qty=q, v=st['v'], t=str(bar['t']), reason=_trigger_text(st, k))
