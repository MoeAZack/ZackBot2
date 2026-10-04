"""ZackBot v3.1 engine/backtest features: TP ladder, trailing TP, trailing entry, maker entries, pump guard,
portfolio risk rules, regime filter, risk governor, martingale helper, liquidation. Uses the FakeX exchange of test_safety.
Run:  python -m pytest -q tests/"""
import os, sys, json, time
import numpy as np, pandas as pd, pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_safety import FakeX, mk_engine, SL, SG, opened, ambiguous_once      # noqa: E402  (no test_* names: not re-collected)
import binance_client as BC
import engine as E
import backtest as B
import strategies as S


def lot_of(e, sym='BTCUSDT'):
    return next(l for l in e.state['lots'].values() if l['symbol'] == sym)


def open_with(e, mgmt, sym='BTCUSDT', side='LONG', **extra):
    sl = dict(SL, mgmt=mgmt, **extra)
    assert e.open_lot(sl, sym, side, SG, None, e.equity()), e.last_skip
    return lot_of(e, sym)


def exch_stop(e, lot):
    return e.trade.stops[lot['stop_id']]


# ------------------------------------------------------------------ 1) take-profit ladder
def test_tp_ladder_each_level_once_and_stop_resized():
    e, _ = mk_engine(); lot = open_with(e, {'tps': [[1, .25], [2, .25], [3, .25]]})      # R = 2.5 ATR * 2 = 5
    q0 = lot['qty']
    e.trade.mark['BTCUSDT'] = 106.0; e.manage(e.trade.marks()); e.manage(e.trade.marks())
    assert lot['tps_done'] == [0] and abs(lot['qty'] - q0 * .75) < 0.002
    assert abs(exch_stop(e, lot)[2] - lot['qty']) < 1e-9                       # exchange stop follows the size
    e.trade.mark['BTCUSDT'] = 116.0; e.manage(e.trade.marks())                # levels 2 and 3 in one pass
    assert lot['tps_done'] == [0, 1, 2] and abs(lot['qty'] - q0 * .25) < 0.003
    assert abs(e.trade.pos[('BTCUSDT', 'LONG')] - lot['qty']) < 1e-9


def test_tp_ladder_lost_answer_does_not_fire_twice():
    e, _ = mk_engine(); lot = open_with(e, {'tps': [[1, .5], [3, .5]]}); q0 = lot['qty']
    e.trade.mark['BTCUSDT'] = 106.0
    ambiguous_once(e, 'close'); e.manage(e.trade.marks())
    assert lot.get('pending')
    for _ in range(3): e.manage(e.trade.marks())
    assert abs(e.trade.pos[('BTCUSDT', 'LONG')] - q0 / 2) < 0.002 and lot['tps_done'] == [0] and not lot.get('pending')


def test_tp_ladder_last_level_closes_lot_and_no_dust():
    e, _ = mk_engine(); lot = open_with(e, {'tps': [[1, .6], [2, .399]]}); k = next(iter(e.state['lots']))
    e.trade.mark['BTCUSDT'] = 111.0; e.manage(e.trade.marks())
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'take_profit_ladder'
    assert not e.trade.pos.get(('BTCUSDT', 'LONG')) and not e.trade.stops


def test_norm_tps_bounds():
    assert S.norm_tps([[3, .2], [1, .5], [0, .5], [2, 1.5], ['x', 1]] + [[i + 4, .1] for i in range(10)])[:2] == [(1.0, .5), (3.0, .2)]
    assert len(S.norm_tps([[i + 1, .1] for i in range(12)])) == 8


# ------------------------------------------------------------------ 2) trailing take-profit
def test_trailing_tp_tightens_exchange_stop_and_replaces_fixed_tp():
    e, _ = mk_engine(); lot = open_with(e, {'ttp': {'at_r': 1, 'dev_pct': 2}, 'tp_r': 1})
    k = next(iter(e.state['lots']))
    e.trade.mark['BTCUSDT'] = 104.0; e.manage(e.trade.marks())
    assert lot['stop'] == 95.0                                                # not active below 1R
    e.trade.mark['BTCUSDT'] = 108.0; e.manage(e.trade.marks())
    assert k in e.state['lots']                                               # tp_r ignored when ttp is set
    assert abs(lot['stop'] - 105.84) < 0.011 and abs(exch_stop(e, lot)[3] - lot['stop']) < 1e-9
    e.trade.mark['BTCUSDT'] = 112.0; e.manage(e.trade.marks())
    assert abs(lot['stop'] - 109.76) < 0.011


def test_trailing_tp_closes_when_price_already_pulled_back():
    e, _ = mk_engine(); lot = open_with(e, {'ttp': {'at_r': 1, 'dev_pct': 2}}); k = next(iter(e.state['lots']))
    lot['best'] = 120.0                                                       # peak seen between two passes
    e.trade.mark['BTCUSDT'] = 115.0; e.manage(e.trade.marks())
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'stop_crossed'


def test_trailing_tp_stop_failure_keeps_old_stop():
    e, _ = mk_engine(); lot = open_with(e, {'ttp': {'at_r': 1, 'dev_pct': 2}}); old = (lot['stop'], lot['stop_id'])
    e.trade.fail.add('stop'); e.trade.mark['BTCUSDT'] = 108.0; e.manage(e.trade.marks())
    assert (lot['stop'], lot['stop_id']) == old and lot['stop_dirty']
    e.trade.fail.clear(); e.manage(e.trade.marks())
    assert lot['stop'] > 105 and old[1] not in e.trade.stops


# ------------------------------------------------------------------ 3) trailing entry
TSL = dict(SL, trail_entry={'dev_atr': 0.5, 'max_bars': 2})


def cyc(e, sig=SG, sl=TSL):
    e.S['SLEEVES'] = [sl]
    e.compute_signals = lambda tf, syms, extra=(): ({f"{sl['id']}|BTCUSDT": dict(sig)}, {})
    e.cycle('4h')


def test_trailing_entry_waits_for_rebound_and_persists(tmp_path):
    e, tmp = mk_engine(str(tmp_path)); cyc(e)
    assert not e.state['lots'] and 'T|BTCUSDT' in e.state['pending_entries']
    assert 'T|BTCUSDT' in json.load(open(os.path.join(tmp, 'state.json')))['pending_entries']
    for px in (99.0, 97.0, 97.8):                                             # falls, rebound 0.8 < 1.0 (0.5 ATR)
        e.trade.mark['BTCUSDT'] = px; e.manage(e.trade.marks())
    assert not e.state['lots'] and e.state['pending_entries']['T|BTCUSDT']['ext'] == 97.0
    e.trade.mark['BTCUSDT'] = 98.1; e.manage(e.trade.marks())
    assert not e.state['pending_entries'] and lot_of(e)['avg'] == 98.1


def test_trailing_entry_expires_and_is_logged():
    e, _ = mk_engine(); cyc(e)
    e.state['pending_entries']['T|BTCUSDT']['until'] = time.time() - 1
    e.manage(e.trade.marks())
    assert not e.state['pending_entries'] and not e.state['lots'] and 'expired' in e.missed[-1]['reason']


def test_trailing_entry_cancelled_when_coin_switched_off():
    e, _ = mk_engine(); cyc(e)
    e.S['SYMBOLS_ON']['BTCUSDT'] = False; e.manage(e.trade.marks())
    assert not e.state['pending_entries'] and 'coin switched off' in e.missed[-1]['reason']


def test_trailing_entry_counts_toward_max_positions():
    e, _ = mk_engine(); cyc(e, sl=dict(TSL, max_pos=1))
    assert 'max positions' in (e.entry_block(dict(TSL, max_pos=1), 'ETHUSDT', 'LONG') or '')


def test_trailing_entry_failed_order_is_logged_not_retried():
    e, _ = mk_engine(); cyc(e); e.trade.fail.add('open')
    e.trade.mark['BTCUSDT'] = 101.5; e.manage(e.trade.marks())
    assert not e.state['pending_entries'] and not e.state['lots'] and 'failed' in e.missed[-1]['reason']


# ------------------------------------------------------------------ 4) maker entries
class Book:
    """Fake order book + resting limit orders on top of FakeX (what the engine reaches via _req/_order/get_order)."""
    def __init__(self, x):
        self.x, self.orders, self.reject = x, {}, 0
        x._req = self.req; x._order = self.order; x.get_order = self.get
        self._cancel = x.cancel; x.cancel = self.cancel
    def req(self, method, path, params=None, **k):
        m = self.x.mark[params['symbol']]; return {'bidPrice': str(m - 0.1), 'askPrice': str(m + 0.1)}
    def order(self, p):
        if self.reject: self.reject -= 1; raise BC.BinanceError(-5022, 'post only rejected')
        self.x.calls.append('limit')
        self.orders[p['newClientOrderId']] = dict(p, status='NEW', executedQty='0', avgPrice='0', clientOrderId=p['newClientOrderId'])
        return dict(self.orders[p['newClientOrderId']])
    def fill(self, frac=1.0):
        o = next(o for o in self.orders.values() if o['status'] in ('NEW', 'PARTIALLY_FILLED'))
        q = round(float(o['quantity']) * frac, 3)
        o.update(executedQty=str(q), avgPrice=o['price'], status='FILLED' if frac >= 1 else 'PARTIALLY_FILLED')
        self.x.pos[(o['symbol'], o['positionSide'])] = self.x.pos.get((o['symbol'], o['positionSide']), 0) + q
    def get(self, sym, cid): return dict(self.orders[cid]) if cid in self.orders else None
    def cancel(self, sym, tag):
        if tag.startswith('c:') and tag[2:] in self.orders:
            self.x._f('cancel'); o = self.orders[tag[2:]]
            if o['status'] in ('NEW', 'PARTIALLY_FILLED'): o['status'] = 'CANCELED'
            return True
        return self._cancel(sym, tag)


def maker_engine(**S_):
    e, _ = mk_engine(ENTRY_ORDER='maker', MAKER_REPRICE=2, MAKER_WAIT_S=30, **S_)
    return e, Book(e.trade)


def age(e, s=100, total=False):
    """Pretend the current order has waited s seconds (total=True: the whole maker window is used up too)."""
    for r in e.state['resting_entries'].values():
        r['placed_t'] -= s; r['cancel_t'] = r.get('cancel_t', 0) - s
        if total: r['t0'] -= s


def test_maker_full_fill_becomes_lot_with_maker_fee():
    e, bk = maker_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()) and not e.state['lots']
    rec = next(iter(e.state['resting_entries'].values()))
    assert rec['price'] == 99.9 and 'open' not in e.trade.calls                 # post-only at the bid, no market order
    bk.fill(); e.manage(e.trade.marks())
    lot = lot_of(e)
    assert not e.state['resting_entries'] and lot['maker_qty'] == lot['qty'] and lot['stop_id']
    assert abs(lot['fees'] - lot['qty'] * 99.9 * 0.0002) < 1e-9


def test_maker_partial_fill_then_timeout_falls_back_to_market():
    e, bk = maker_engine(); e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    target = next(iter(e.state['resting_entries'].values()))['qty']
    bk.fill(0.4); e.reconcile(500); e.reconcile(500)
    assert not e.untracked                                                    # a working entry is not "untracked"
    age(e); e.manage(e.trade.marks())                                         # timeout -> cancel
    assert next(iter(bk.orders.values()))['status'] == 'CANCELED'
    for _ in range(3): age(e, total=True); e.manage(e.trade.marks())          # window used up -> market fallback for the rest
    lot = lot_of(e)
    assert not e.state['resting_entries'] and abs(lot['qty'] - target) < 0.002 and abs(lot['maker_qty'] - round(target * .4, 3)) < 1e-9
    assert abs(e.trade.pos[('BTCUSDT', 'LONG')] - lot['qty']) < 1e-9 and abs(exch_stop(e, lot)[2] - lot['qty']) < 1e-9
    assert 'open' in e.trade.calls and not e.untracked


def test_maker_no_fill_without_fallback_is_skipped_and_cancelled():
    e, bk = maker_engine(MAKER_FALLBACK=False); e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    for _ in range(10): age(e); e.manage(e.trade.marks())                     # 1 + 2 re-prices, then give up
    assert not e.state['lots'] and not e.state['resting_entries'] and 'open' not in e.trade.calls
    assert all(o['status'] == 'CANCELED' for o in bk.orders.values()) and len(bk.orders) == 3   # 1 + MAKER_REPRICE
    assert 'maker entry not filled' in e.missed[-1]['reason']


def test_maker_post_only_reject_reprices():
    e, bk = maker_engine(); bk.reject = 1
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert not bk.orders and e.state['resting_entries']
    e.manage(e.trade.marks()); assert len(bk.orders) == 1
    bk.fill(); e.manage(e.trade.marks()); assert lot_of(e)['maker_qty'] > 0


def test_maker_lost_answer_is_polled_by_id_never_resent():
    e, bk = maker_engine(); orig = bk.order
    def lost(p): orig(p); raise BC.AmbiguousOrder('lost', 'c:' + p['newClientOrderId'])
    e.trade._order = lost
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()); e.trade._order = orig
    rec = next(iter(e.state['resting_entries'].values()))
    assert rec['status'] == 'open' and rec['cid'] in bk.orders                # kept, tracked by its client id
    bk.fill(); e.manage(e.trade.marks())
    assert len(bk.orders) == 1 and lot_of(e)['qty'] > 0 and not e.state['resting_entries']


def test_maker_cancel_failure_keeps_order_tracked():
    e, bk = maker_engine(); e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    e.trade.fail.add('cancel'); age(e); e.manage(e.trade.marks())
    assert e.state['resting_entries'] and next(iter(bk.orders.values()))['status'] == 'NEW'
    e.trade.fail.clear(); age(e); e.manage(e.trade.marks())
    assert next(iter(bk.orders.values()))['status'] == 'CANCELED'


def test_maker_entry_blocks_second_entry_same_coin():
    e, bk = maker_engine(); e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'already working' in e.entry_block(SL, 'BTCUSDT', 'LONG')


# ------------------------------------------------------------------ 5) pump / dump protection
def test_pump_guard_candle_and_btc():
    e, _ = mk_engine(PUMP_GUARD={'max_candle_atr': 3, 'btc_1h_pct': 2})
    e.btc_move_1h = lambda: 0.5
    assert 'pump guard: signal candle' in e.entry_block(SL, 'BTCUSDT', 'LONG', sg=dict(SG, rng_atr=4.2))
    assert e.entry_block(SL, 'BTCUSDT', 'LONG', sg=dict(SG, rng_atr=1.0)) is None
    e.btc_move_1h = lambda: 3.1
    assert 'BTC moved 3.1%' in e.entry_block(SL, 'BTCUSDT', 'LONG', sg=SG)
    assert e.entry_block(dict(SL, pump_guard={'max_candle_atr': 5}), 'BTCUSDT', 'LONG', sg=dict(SG, rng_atr=4.2)) is None  # slot wins


def test_pump_guard_logged_as_missed_in_cycle():
    e, _ = mk_engine(PUMP_GUARD={'max_candle_atr': 3}); cyc(e, sig=dict(SG, rng_atr=5.0), sl=SL)
    assert not e.state['lots'] and e.missed[-1]['reason'].startswith('pump guard')


# ------------------------------------------------------------------ 6) portfolio risk rules
def test_coin_cap_enforce_blocks_and_warn_still_trades():
    e, _ = mk_engine(RISK_RULES={'coin_cap': {'mode': 'enforce', 'x': 0.1}})      # 100 USDT trade > 0.1 x 500
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()) and 'coin_cap' in e.last_skip and not e.state['lots']
    e.S['RISK_RULES'] = {'coin_cap': {'mode': 'warn', 'x': 0.1}}
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    w = [m for m in e.missed if m.get('kind') == 'warning']
    assert w and w[-1]['reason'].startswith('WARNING:') and 'exposure' in w[-1]['reason']


def test_open_risk_cap_includes_new_trade():
    e, _ = mk_engine(RISK_RULES={'open_risk_cap': {'mode': 'enforce', 'pct': 1.5}})
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())                # 1% risk (2% x 0.5 share) fits
    assert not e.open_lot(SL, 'ETHUSDT', 'LONG', dict(SG, close=50.0, atr=1.0), None, e.equity()) and 'open_risk_cap' in e.last_skip


def test_correlated_cap_and_missing_matrix():
    e, _ = mk_engine(RISK_RULES={'correlated_cap': {'mode': 'enforce', 'n': 1, 'rho': 0.7}}); opened(e)
    assert e.entry_block(SL, 'ETHUSDT', 'LONG') is None                          # no matrix yet: not blocked
    e.corr = dict(symbols=['BTCUSDT', 'ETHUSDT'], m=[[1, .9], [.9, 1]])
    assert 'correlated' in e.entry_block(SL, 'ETHUSDT', 'LONG') and e.entry_block(SL, 'ETHUSDT', 'SHORT') is None
    st = e.risk_rules_status()['correlated_cap']; assert st['value'] == 1


def test_btc_breaker_pauses_and_tightens_winners():
    e, _ = mk_engine(RISK_RULES={'btc_breaker': {'mode': 'enforce', 'pct': 3, 'hours': 2, 'tighten': True}})
    k = opened(e); lot = e.state['lots'][k]; e.btc_move_1h = lambda: 1.0
    e.trade.mark['BTCUSDT'] = 103.0; e.manage(e.trade.marks())
    assert lot['stop'] < 100 and e.entry_block(SL, 'ETHUSDT', 'LONG') is None
    e.btc_move_1h = lambda: 4.0; e.manage(e.trade.marks())
    assert abs(lot['stop'] - 100.15) < 0.011                                     # moved to breakeven (+fee buffer)
    e.btc_move_1h = lambda: 0.5
    assert 'btc_breaker' in e.entry_block(SL, 'ETHUSDT', 'LONG')                  # still paused after the move calmed
    e.state['breaker_until'] = time.time() - 1
    assert e.entry_block(SL, 'ETHUSDT', 'LONG') is None


def test_btc_breaker_data_failure_does_not_block():
    e, _ = mk_engine(RISK_RULES={'btc_breaker': {'mode': 'enforce'}})
    def boom(): raise RuntimeError('no data')
    e.btc_move_1h = boom
    assert e.entry_block(SL, 'BTCUSDT', 'LONG') is None
    e.manage(e.trade.marks())                                                     # and management keeps running


def test_funding_filter():
    e, _ = mk_engine(RISK_RULES={'funding_filter': {'mode': 'enforce', 'rate': 0.00005}})   # FakeX funding = 0.0001
    assert 'funding' in e.entry_block(SL, 'BTCUSDT', 'LONG') and e.entry_block(SL, 'BTCUSDT', 'SHORT') is None
    calls = []; orig = e.data.premium; e.data.premium = lambda s: calls.append(s) or orig(s)
    e.entry_block(SL, 'ETHUSDT', 'LONG'); e.entry_block(SL, 'ETHUSDT', 'LONG')
    assert len(calls) == 1                                                        # cached 5 min


def test_rules_default_to_warn_and_status():
    e, _ = mk_engine()
    assert {v['mode'] for v in e.risk_rules_cfg().values()} == {'warn'}
    e.S['RISK_RULES'] = {'coin_cap': {'mode': 'enforce'}}
    assert e.risk_rules_cfg()['coin_cap'] == dict(mode='enforce', x=3.0)          # partial setting keeps defaults
    opened(e); st = e.risk_rules_status()
    assert set(st) == {'coin_cap', 'open_risk_cap', 'correlated_cap', 'btc_breaker', 'funding_filter'}
    assert st['coin_cap']['detail'] == 'BTCUSDT' and st['open_risk_cap']['value'] > 0


def test_manual_trade_respects_size_rules_only(monkeypatch):
    e, _ = mk_engine(RISK_RULES={'coin_cap': {'mode': 'enforce', 'x': 0.1}, 'funding_filter': {'mode': 'enforce', 'rate': 0.00001}})
    monkeypatch.setattr(e, 'candles', lambda s, tf: pd.DataFrame(dict(c=[100.0], atr=[2.0])))
    with pytest.raises(ValueError, match='coin_cap'): e.manual_trade('BTCUSDT', 'LONG', 0.01, 2.5)


# ------------------------------------------------------------------ 7a) market regime filter
def test_regime_filter_blocks_slot_and_logs():
    e, _ = mk_engine(); e.regime_now = lambda: dict(bull=False, bear=True, range=False)
    assert e.entry_block(dict(SL, when='bull'), 'BTCUSDT', 'LONG') == 'regime: bear market'
    assert e.entry_block(dict(SL, when='bear'), 'BTCUSDT', 'LONG') is None and e.entry_block(SL, 'BTCUSDT', 'LONG') is None
    cyc(e, sl=dict(SL, when='range')); assert e.missed[-1]['reason'] == 'regime: bear market' and not e.state['lots']


def test_regime_function_has_no_lookahead():
    t = pd.date_range('2025-01-01', periods=1800, freq='4h')
    c = 100 * np.exp(np.cumsum(np.r_[np.full(1000, .002), np.full(800, -.003)] + np.random.default_rng(1).normal(0, .01, len(t))))
    d = pd.DataFrame(dict(t=t, o=c, h=c * 1.01, l=c * .99, c=c, v=1.0))
    full = S.regime(d)
    assert (S.regime(d.iloc[:1200]).values == full.iloc[:1200].values).all()
    assert full.bull.any() and full.bear.any() and not (full.bull & full.bear).any() and not full.iloc[:150].bull.any()


# ------------------------------------------------------------------ 7b) risk governor
DD_RULE = {'if': 'dd_gte', 'value': 15, 'then': {'risk_mult': 0.5}, 'until': 'new_high'}


def gov_engine(mode, rules):
    e, _ = mk_engine(GOVERNOR={'mode': mode, 'rules': rules}); e.equity(); return e


def test_governor_drawdown_halves_risk_until_new_high():
    e = gov_engine('auto', [DD_RULE]); e.state['peak_equity'] = 600.0           # bot capital 500 -> dd 16.7%
    e.governor_tick(); assert e.governor_mult() == 0.5
    k = opened(e); assert e.state['lots'][k]['risk_usd'] == 2.5                  # 500 * 0.5 share * 2% * 0.5
    e.state['peak_equity'] = 520.0; e.governor_tick(); assert e.governor_mult() == 0.5   # recovered a bit: still on
    e.guard_eq = 601.0; e.governor_tick(); assert e.governor_mult() == 1.0                 # new high -> released


def test_governor_suggest_mode_only_notifies_once():
    e = gov_engine('suggest', [DD_RULE]); e.state['peak_equity'] = 600.0
    sent = []; e.notify = sent.append
    e.governor_tick(); e.governor_tick()
    assert e.governor_mult() == 1.0 and len(sent) == 1 and 'suggests' in sent[0]
    assert e.governor_status()['rules'][0]['suggestion']


def test_governor_martingale_capped_and_flagged():
    e = gov_engine('auto', [dict(DD_RULE, then={'risk_mult': 3})]); e.state['peak_equity'] = 600.0; e.governor_tick()
    st = e.governor_status()
    assert e.governor_mult() == 2.0 and st['high_risk'] and st['rules'][0]['capped']


def test_governor_profile_switch_auto_with_cooldown():
    e = gov_engine('auto', [{'if': 'dd_gte', 'value': 10, 'then': {'profile': 'calm'}, 'until': None}]); e.state['peak_equity'] = 600.0
    e.governor_tick(); assert e.S['PRESET'] == 'calm'
    e.state['governor']['rules']['0']['on'] = False; e.S['PRESET'] = 'boost'      # triggers again right away
    e.governor_tick(); assert e.S['PRESET'] == 'boost'                           # 24 h cooldown


def test_governor_growth_rule_resets_with_capital_cycle():
    e = gov_engine('auto', [{'if': 'growth_gte', 'value': 100, 'then': {'risk_mult': 0.33}, 'until': 'reset'}])
    e.S['CAP_SINCE'] = (E.now_utc() - pd.Timedelta(hours=1)).isoformat(timespec='seconds')
    e.history.append(dict(id='1', pnl=600.0, closed=(E.now_utc() - pd.Timedelta(minutes=10)).isoformat(timespec='seconds'), symbol='BTCUSDT', side='LONG'))
    e.balance = 5000; e.equity(); e.check_guards(); assert e.governor_mult() == 0.33
    e.capital_action('reset', 500); e.check_guards(); assert e.governor_mult() == 1.0


def test_governor_off_is_neutral():
    e, _ = mk_engine(); e.check_guards(); assert e.governor_mult() == 1.0 and e.governor_status()['mode'] == 'off'


# ------------------------------------------------------------------ 8) martingale DCA
def test_martingale_risk_metric_and_hard_stop_required():
    base = B.martingale_risk({'dca': dict(n=3, step_atr=1, scale=1.5, tp_atr=1, stop_atr=2)}, 0.02)
    deep = B.martingale_risk({'dca': dict(n=8, step_atr=1, scale=3, tp_atr=1, stop_atr=2)}, 0.02)
    assert 1.0 < base['worst_loss_r'] < 1.2 and deep['max_notional_r'] < base['max_notional_r'] * 2
    assert deep['last_order_share'] > 0.6 and deep['depth_pct'] > base['depth_pct']
    with pytest.raises(ValueError): B.martingale_risk({'dca': dict(n=3, step_atr=1, scale=1.5, tp_atr=1)}, 0.02)
    with pytest.raises(ValueError): B.martingale_risk({'dca': dict(n=9, step_atr=1, scale=1.5, tp_atr=1, stop_atr=2)}, 0.02)


def test_engine_refuses_dca_without_stop():
    e, _ = mk_engine(); sl = dict(SL, key='dca_dip', mgmt={'dca': dict(n=3, step_atr=1, scale=1.5, tp_atr=1, stop_atr=0)})
    assert not e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity()) and 'hard stop' in e.last_skip
    sl['mgmt'] = {'dca': dict(n=9, step_atr=0.5, scale=3, tp_atr=1, stop_atr=2)}
    assert not e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity()) and 'out of range' in e.last_skip
    sl['mgmt'] = {'dca': dict(n=4, step_atr=0.5, scale=3, tp_atr=1, stop_atr=2)}
    e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity()); assert 'stop' not in e.last_skip and 'range' not in e.last_skip


# ------------------------------------------------------------------ backtest mirrors (synthetic candles)
def synth(path, n_warm=260, sym='XUSDT'):
    """Flat warm-up at 100 (range 2 -> ATR ~2), then the given (o,h,l,c) candles; one long signal on the last warm-up bar."""
    rows = [(100.0, 101.0, 99.0, 100.0)] * n_warm + list(path) + [(path[-1][3],) * 4] * 2 + [(path[-1][3], path[-1][3], 40.0, 40.0)] + [(40.0,) * 4] * 3
    t = pd.date_range('2025-01-01', periods=len(rows), freq='4h')
    d = pd.DataFrame(rows, columns=['o', 'h', 'l', 'c']); d.insert(0, 't', t); d['v'] = 1.0
    bk = B.Book({'BTCUSDT': d.copy(), sym: d.copy()})
    bk.arr = {s_: {k: v.copy() for k, v in a.items()} for s_, a in bk.arr.items()}      # writable for the tests
    n = len(rows); z = np.zeros(n, bool); le = z.copy(); le[n_warm - 1] = True
    bk._sig[('ema_mom', sym, 'None')] = dict(le=le, se=z.copy(), lx=z.copy(), sx=z.copy())
    return bk


def bt(bk, mgmt=None, **kw):
    sl = [dict(key='ema_mom', share=1.0, risk=kw.pop('risk', 0.02), max_pos=1, symbols=['XUSDT'], mgmt=mgmt or {}, **kw.pop('sl', {}))]
    tr, cv = B.run(bk, sl, start=1000, warmup=30, fund_per_bar=0, **kw)
    return tr, cv


def test_bt_tp_ladder_and_trailing_tp():
    up = [(100 + 3 * k, 103 + 3 * k, 99.5 + 3 * k, 103 + 3 * k) for k in range(8)] + [(124, 124, 110, 110)]
    tr, _ = bt(synth(up), {'tps': [[1, .25], [2, .25]]})
    assert len(tr) == 1 and tr.R.iloc[0] > 0
    tr2, _ = bt(synth(up), {'ttp': {'at_r': 1, 'dev_pct': 3}})
    assert tr2.why.iloc[0] == 'stop' and tr2.pnl.iloc[0] > 0                     # trailed out with profit, not at the old stop


def test_bt_maker_entry_fee_and_fallback():
    flat = [(100.0, 101.0, 99.0, 100.0)] * 3
    tr_m, cv_m = bt(synth(flat), entry_order='maker')
    tr_t, cv_t = bt(synth(flat))
    assert len(tr_m) == len(tr_t) == 1 and tr_m.pnl.iloc[0] > tr_t.pnl.iloc[0]   # maker fee + no slippage on entry
    up_only = [(100.5, 102.0, 100.4, 101.5)] + [(101.5, 102, 101, 101.5)] * 3   # never trades back to the signal close
    tr_s, _ = bt(synth(up_only), entry_order='maker', maker_fallback=False)
    assert len(tr_s) == 0 and len(bt(synth(up_only), entry_order='maker')[0]) == 1


def test_bt_trailing_entry_and_expiry():
    dip = [(100, 100, 97, 97.5), (97.5, 99, 97.2, 98.8)] + [(98.8, 99, 98.5, 98.8)] * 3
    tr, _ = bt(synth(dip), sl={'trail_entry': {'dev_atr': 0.5, 'max_bars': 3}})
    assert len(tr) == 1 and tr.i_in.iloc[0] == 261                              # entered on the rebound candle, not at once
    falling = [(100 - k, 100 - k, 99 - k, 99 - k) for k in range(6)]
    tr2, _ = bt(synth(falling), sl={'trail_entry': {'dev_atr': 0.5, 'max_bars': 3}})
    assert len(tr2) == 0                                                          # never rebounded -> expired


def test_bt_pump_guard_regime_and_breaker():
    flat = [(100.0, 101.0, 99.0, 100.0)] * 3
    bk = synth(flat); bk.arr['XUSDT']['h'][259] = 110.0                           # huge signal candle
    assert len(bt(bk, pump_guard={'max_candle_atr': 3})[0]) == 0 and len(bt(synth(flat))[0]) == 1
    assert len(bt(synth(flat), sl={'when': 'bull'})[0]) == 0                     # flat BTC is neither bull nor bear
    bk = synth(flat); bk.d['BTCUSDT'].loc[259, 'c'] = 110.0
    rr = {'btc_breaker': {'mode': 'enforce', 'pct': 3, 'hours': 4}}
    assert len(bt(bk, risk_rules=rr)[0]) == 0 and len(bt(bk, risk_rules={'btc_breaker': {'mode': 'warn', 'pct': 3}})[0]) == 1


def test_bt_governor_scales_risk():
    t = pd.date_range('2025-01-01', periods=900, freq='4h'); rng = np.random.default_rng(3)
    c = 100 * np.exp(np.cumsum(rng.normal(0, .02, len(t))))
    d = pd.DataFrame(dict(t=t, o=c, h=c * 1.02, l=c * .98, c=c, v=1.0)); bk = B.Book({'BTCUSDT': d, 'XUSDT': d.copy()})
    sl = [dict(key='ema_mom', share=1.0, risk=0.05, max_pos=1, symbols=['XUSDT'], sides='both')]
    _, a = B.run(bk, sl, start=1000)
    _, b = B.run(bk, sl, start=1000, governor={'rules': [{'if': 'dd_gte', 'value': 1, 'then': {'risk_mult': 0.5}, 'until': 'new_high'}]})
    assert not a.equals(b)
    _, c0 = B.run(bk, sl, start=1000, governor={'mode': 'off', 'rules': [{'if': 'dd_gte', 'value': 1, 'then': {'risk_mult': 0.5}}]})
    assert a.equals(c0)


def test_bt_liquidation_recorded():
    # risk 150% of capital on one trade with a wide stop: the drop to just above the stop wipes the margin first
    crash = [(100, 100, 92.0, 93.0), (93, 94, 92, 93)]
    tr, cv = bt(synth(crash), {'stop_atr': 4.5}, risk=1.5, max_lev=100)
    st = B.stats(tr, cv, start=1000)
    assert st['liquidations'] == 1 and tr.why.iloc[0] == 'liquidated'
    tr2, cv2 = bt(synth(crash), {'stop_atr': 4.5}, risk=1.5, max_lev=100, maint_margin=0)
    assert B.stats(tr2, cv2, start=1000)['liquidations'] == 0
