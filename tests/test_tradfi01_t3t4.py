"""TRADFI-01 T3 (gold-family exposure cap) + T4 (TradFi slots in the B2 breadth rotation).

Codex rulings: (a) allow-list + opt-in flag, (b) static NYSE calendar - UNKNOWN => no new stock entries, (c) ONE rotation
(B2), max six active, (d) XAUUSDT + PAXGUSDT share one family budget, second same-direction gold position refused
('gold_family_cap'). Strategy mapping 6186554256. PAPER only; synthetic fake exchange, no network, no real data."""
import os, sys
from datetime import datetime, timezone
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_safety as TSF                              # noqa: E402  (FakeX)
import engine as E                                     # noqa: E402
import feasibility as F                                # noqa: E402
import strategies as S                                 # noqa: E402
import tradfi_universe as TU                           # noqa: E402
import tradfi_session as TS                            # noqa: E402

OPEN_TS = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc).timestamp()      # Wed 11:00 ET
CLOSED_TS = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc).timestamp()    # Saturday
UNKNOWN_TS = datetime(2031, 3, 5, 15, 0, tzinfo=timezone.utc).timestamp()     # beyond the verified table
PX = {'BTCUSDT': 100.0, 'ETHUSDT': 50.0, 'XAUUSDT': 4000.0, 'PAXGUSDT': 4000.0, 'SPYUSDT': 600.0, 'TSLAUSDT': 400.0,
      'NVDAUSDT': 180.0}


class TFX(TSF.FakeX):
    """FakeX plus TradFi contracts: XAU + stocks as TRADIFI_PERPETUAL, PAXG an ordinary PERPETUAL, an oil and a leveraged
    ETF contract that must never be admitted."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.mark = dict(PX)

    def exchange_info(self):
        def row(s, ct):
            return {'symbol': s, 'contractType': ct, 'status': 'TRADING', 'filters': [
                {'filterType': 'MARKET_LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001'},
                {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'}, {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]}
        rows = [row(s, 'PERPETUAL') for s in ('BTCUSDT', 'ETHUSDT', 'PAXGUSDT')]
        rows += [row(s, 'TRADIFI_PERPETUAL') for s in ('XAUUSDT', 'SPYUSDT', 'TSLAUSDT', 'NVDAUSDT', 'CLUSDT', 'SOXLUSDT')]
        return {'symbols': rows}


@pytest.fixture
def mk(monkeypatch, tmp_path):
    monkeypatch.setattr(E, 'Futures', TFX)

    def _mk(on=False, live=False, preset=None, **S_):
        e = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), str(tmp_path))
        e.live = live
        e.data = e.trade
        e.S.update(dict(UNIVERSE=['BTCUSDT', 'ETHUSDT'], SYMBOLS_ON={'BTCUSDT': True, 'ETHUSDT': True}, CAPITAL_CAP=500.0,
                        TRADFI_ENABLED=on), **S_)
        if preset: e.apply_preset(preset) if not live else None
        e.connect(); e.marks = e.trade.marks()
        return e
    return _mk


def slot(id_, key, symbols='tradfi'):
    return E.sleeve(id_, key, 1 / 6, E.BREADTH_RISK, 4, symbols, sides='both')


def sg(px, atr=20.0, le=True):
    return dict(close=px, atr=atr, time='2026-10-14 12:00:00', le=le, se=not le, lx=False, sx=False)


def open_(e, sl, sym, side):
    assert e.open_lot(sl, sym, side, sg(PX[sym], le=side == 'LONG'), None, e.equity()), e.last_skip
    return next(l for l in e.state['lots'].values() if l['symbol'] == sym and l['side'] == side)


# ------------------------------------------------------------------ flag off = B2 bit-identical / live refused
def candidates_off_outside_rotation(e, tradfi=False):
    """FACTORY-CAND-01 appends C-F01/C-F02 on PAPER connect: disabled, own share/risk, never in the TRADFI rotation."""
    rot = {x['id'] for x in E.breadth_sleeves(tradfi=tradfi)}
    got = {x['id']: x for x in e.S['SLEEVES'] if x['id'] in E.CANDIDATE_SLOTS}
    assert sorted(got) == sorted(E.CANDIDATE_SLOTS) and not set(got) & rot
    for x in got.values():
        assert x['enabled'] is False and x['share'] == E.CANDIDATE_SHARE and x['risk'] == E.CANDIDATE_RISK


def test_flag_off_rules_universe_and_slots_unchanged(mk):
    e = mk(on=False, preset=E.ROTATION_PRESET)
    assert e.rules == F.rules_from_exchange_info(e.trade.exchange_info())
    assert not any(r.get('kind') == 'tradfi' for r in e.rules.values()) and 'XAUUSDT' not in e.rules
    assert e.tradfi_universe == [] and e.tradfi_on() is False
    assert [x['id'] for x in e.S['SLEEVES']] == [x['id'] for x in E.with_candidate_slots(E.breadth_sleeves())[0]]
    candidates_off_outside_rotation(e)
    assert E.breadth_sleeves() == E.breadth_sleeves(tradfi=False) and E.breadth_groups() == E.breadth_groups(tradfi=False)
    assert not set(E.TRADFI_SLOTS) & {x['id'] for x in e.S['SLEEVES']}
    for tier in E.TRADFI_TIERS: assert e.sleeve_symbols(dict(symbols=tier)) == []
    assert e.rotation_status()['tradfi'] is False
    assert e.tradfi_block(slot('X', 'ema_st'), 'PAXGUSDT', 'LONG') is None                  # ordinary perp untouched


def test_flag_on_in_live_is_refused(mk):
    e = mk(on=True, live=True)
    assert e.tradfi_on() is False and e.tradfi_universe == []
    assert not any(r.get('kind') == 'tradfi' for r in e.rules.values())
    assert 'not tradable' in e.entry_block(slot('X', 'ema_st'), 'XAUUSDT', 'LONG')
    assert 'PAPER' in e.tradfi_block(slot('X', 'ema_st'), 'SPYUSDT', 'LONG', ts=OPEN_TS)
    assert all(e.sleeve_symbols(dict(symbols=t)) == [] for t in E.TRADFI_TIERS)
    assert E.Engine.tradfi_on(e) is False


def test_flag_on_admits_only_the_allow_list_and_all_tier_stays_crypto(mk):
    e = mk(on=True, UNIVERSE=['BTCUSDT', 'ETHUSDT', 'XAUUSDT', 'SPYUSDT', 'PAXGUSDT'])
    assert {s for s, r in e.rules.items() if r.get('kind') == 'tradfi'} == {'XAUUSDT', 'SPYUSDT', 'TSLAUSDT', 'NVDAUSDT'}
    assert 'CLUSDT' not in e.rules and 'SOXLUSDT' not in e.rules
    assert set(e.tradfi_universe) == {'XAUUSDT', 'PAXGUSDT', 'SPYUSDT', 'TSLAUSDT', 'NVDAUSDT'}
    all_ = e.sleeve_symbols(dict(symbols='all'))
    assert 'XAUUSDT' not in all_ and 'SPYUSDT' not in all_ and 'BTCUSDT' in all_
    assert not set(e.sleeve_symbols(dict(symbols='core8'))) & set(TU.TRADFI_ALLOW_LIST)
    assert e.sleeve_symbols(dict(symbols='gold')) == ['XAUUSDT', 'PAXGUSDT']
    assert set(e.sleeve_symbols(dict(symbols='stocks12'))) == {'SPYUSDT', 'TSLAUSDT', 'NVDAUSDT'}
    assert 'NVDAUSDT' not in e.sleeve_symbols(dict(symbols='tradfi_range'))                 # high beta: no range
    assert 'XAUUSDT' not in e.sleeve_symbols(dict(symbols='tradfi_mom'))                     # gold: no momentum slot


# ------------------------------------------------------------------ T3 gold family
def test_second_same_direction_gold_position_refused(mk):
    e = mk(on=True)
    a, b = slot('TF-TREND', 'ema_st'), slot('TF-BRK', 'breakout_pyramid')
    open_(e, a, 'XAUUSDT', 'LONG')
    assert e.entry_block(b, 'PAXGUSDT', 'LONG').startswith('gold_family_cap')
    assert e.entry_block(b, 'XAUUSDT', 'LONG').startswith('gold_family_cap')
    assert not e.open_lot(b, 'PAXGUSDT', 'LONG', sg(4000.0), None, e.equity())
    assert e.last_skip.startswith('gold_family_cap')
    assert e.tradfi_block(None, 'PAXGUSDT', 'LONG', manual=True).startswith('gold_family_cap')   # hand entries too


def test_opposite_gold_only_with_hedge_and_inside_the_family_budget(mk):
    e = mk(on=True)
    a, b = slot('TF-TREND', 'ema_st'), slot('TF-BRK', 'breakout_pyramid')
    l = open_(e, a, 'XAUUSDT', 'SHORT')
    e.hedge = False
    assert 'hedge' in e.entry_block(b, 'PAXGUSDT', 'LONG')
    e.hedge = True
    assert e.entry_block(b, 'PAXGUSDT', 'LONG') is None
    eq = e.guard_eq
    small = dict(notional=100.0, risk=1.0)
    assert e.entry_block(b, 'PAXGUSDT', 'LONG', size=small) is None
    big_risk = dict(notional=100.0, risk=E.GOLD_FAMILY_RISK_EQ * eq)                         # + the open lot's risk -> over
    assert 'family risk' in e.entry_block(b, 'PAXGUSDT', 'LONG', size=big_risk)
    big_not = dict(notional=e.S['MAX_LEVERAGE'] * eq / E.ROTATION_MAX_ACTIVE, risk=0.1)
    assert 'family notional' in e.entry_block(b, 'PAXGUSDT', 'LONG', size=big_not)
    l2 = open_(e, b, 'PAXGUSDT', 'LONG')                                                     # the real sized hedge fits
    assert l2['side'] == 'LONG' and l['side'] == 'SHORT'
    assert e.entry_block(slot('TF-RANGE', 'exp_vwaprev'), 'XAUUSDT', 'LONG').startswith('gold_family_cap')



def test_gold_adds_count_against_the_family_budget(mk):
    """Ruling d is an aggregate budget: a pyramid / DCA add on a gold lot is new family exposure, so _add_block applies
    the same shared risk / notional budget (it does not go through entry_block)."""
    e = mk(on=True)
    l = open_(e, slot('TF-BRK', 'breakout_pyramid'), 'XAUUSDT', 'LONG')
    l['stop_dirty'] = False
    assert not (e._add_block(l, 0.0001, 4000.0) or '').startswith('gold_family_cap')        # a tiny add fits
    cap = e.S['MAX_LEVERAGE'] * e.guard_eq / E.ROTATION_MAX_ACTIVE
    at_stop = l['stop']                                       # add priced at the stop: zero added risk, notional only
    assert 'family notional' in (e._add_block(l, cap / at_stop + 1.0, at_stop) or '')
    risky = E.GOLD_FAMILY_RISK_EQ * e.guard_eq / abs(4000.0 - l['stop']) + 0.01
    assert (e._add_block(l, risky, 4000.0) or '').startswith('gold_family_cap')

# ------------------------------------------------------------------ session gate + mapping
def test_stock_session_gate_per_family(mk):
    e = mk(on=True)
    rng, scp, trd = slot('R', 'exp_vwaprev'), slot('S', 'exp_scalp'), slot('T', 'ema_st')
    for ts in (CLOSED_TS, UNKNOWN_TS):
        assert e.tradfi_block(rng, 'SPYUSDT', 'LONG', ts=ts).startswith('tradfi_session')
        assert e.tradfi_block(scp, 'SPYUSDT', 'LONG', ts=ts).startswith('tradfi_session')
    assert e.tradfi_block(rng, 'SPYUSDT', 'LONG', ts=OPEN_TS) is None
    assert e.tradfi_block(trd, 'SPYUSDT', 'LONG', ts=OPEN_TS) is None
    assert e.tradfi_block(trd, 'SPYUSDT', 'LONG', ts=CLOSED_TS) is None
    assert e.tradfi_block(trd, 'SPYUSDT', 'LONG', ts=UNKNOWN_TS).startswith('tradfi_session')
    assert e.tradfi_block(trd, 'XAUUSDT', 'LONG', ts=UNKNOWN_TS) is None                     # gold: no cash-session gate
    assert e.tradfi_block(scp, 'SPYUSDT', 'LONG', ts=OPEN_TS).startswith('tradfi_policy')     # scalp not in the mapping


def test_mapping_high_beta_no_dca_no_range(mk):
    e = mk(on=True)
    for key in ('dca_dip', 'exp_vwaprev', 'exp_range', 'hot_coin', 'rotation'):
        assert e.tradfi_block(slot('X', key), 'NVDAUSDT', 'LONG', ts=OPEN_TS).startswith('tradfi_policy'), key
    for key in ('ema_st', 'breakout_pyramid', 'ema_mom'):
        assert e.tradfi_block(slot('X', key), 'TSLAUSDT', 'LONG', ts=OPEN_TS) is None, key
    assert e.tradfi_block(slot('X', 'ema_mom'), 'XAUUSDT', 'LONG').startswith('tradfi_policy')
    assert e.tradfi_block(slot('X', 'dca_dip'), 'SPYUSDT', 'LONG', ts=OPEN_TS).startswith('tradfi_policy')
    dca = dict(slot('X', 'ema_st'), mgmt=dict(dca=dict(n=3, scale=1.5, stop_atr=3)))
    assert e.tradfi_block(dca, 'SPYUSDT', 'LONG', ts=OPEN_TS).startswith('tradfi_policy')


def test_no_high_beta_symbol_in_any_dca_slot(mk):
    e = mk(on=True, preset=E.ROTATION_PRESET)
    for sl in e.S['SLEEVES']:
        if sl['key'] == 'dca_dip' or 'dca' in e.mgmt(sl):
            assert not set(e.sleeve_symbols(sl, include_off=True)) & TU.HIGH_BETA, sl['id']
            assert not set(e.sleeve_symbols(sl, include_off=True)) & set(TU.TRADFI_ALLOW_LIST), sl['id']


def test_open_stock_lot_managed_while_session_closed(mk, monkeypatch):
    e = mk(on=True)
    sl = slot('TF-TREND', 'ema_st', 'stocks12'); e.S['SLEEVES'] = [sl]
    monkeypatch.setattr(TS, 'us_cash_session', lambda ts: 'OPEN')
    open_(e, sl, 'SPYUSDT', 'LONG')
    monkeypatch.setattr(TS, 'us_cash_session', lambda ts: 'CLOSED')
    l = next(iter(e.state['lots'].values()))
    assert l['stop_id'] in e.trade.stops                                                     # stop placed and kept
    rng = slot('TF-RANGE', 'exp_vwaprev', 'tradfi_range')
    assert e.entry_block(rng, 'TSLAUSDT', 'LONG').startswith('tradfi_session')               # no new range entry
    sigs = {f'TF-TREND|SPYUSDT': dict(sg(600.0, le=False), se=False, lx=True)}
    e.compute_signals = lambda tf, syms, extra=(): (dict(sigs) if tf == '4h' else {}, {})
    e.cycle('4h')                                                                            # its exit signal still closes it
    assert not e.state['lots'] and e.history[-1]['symbol'] == 'SPYUSDT' and e.history[-1]['exit_reason'] == 'exit_signal'


def test_restart_with_flag_off_adopts_held_tradfi_lot_but_opens_nothing_new(mk, tmp_path):
    e = mk(on=True)
    open_(e, slot('TF-TREND', 'ema_st'), 'XAUUSDT', 'LONG')
    e.S['TRADFI_ENABLED'] = False; e.save_settings(); e.save_state()
    e2 = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), str(tmp_path))
    e2.data = e2.trade; e2.trade.pos = dict(e.trade.pos); e2.trade.stops = dict(e.trade.stops)
    e2.connect()
    assert 'XAUUSDT' in e2.rules and 'SPYUSDT' not in e2.rules                              # held symbol only, for management
    assert any(l['symbol'] == 'XAUUSDT' for l in e2.state['lots'].values())
    assert 'TRADFI disabled' in e2.entry_block(slot('Y', 'ema_st'), 'XAUUSDT', 'SHORT')
    assert 'XAUUSDT' not in e2.sleeve_symbols(dict(symbols='all'), include_off=True)


# ------------------------------------------------------------------ T4 rotation
def test_rotation_with_tradfi_slots_caps_six_active(mk):
    groups = E.breadth_groups(tradfi=True)
    flat = [k for g in groups for k in g]
    expected = [k for k in S.STRATEGIES if k not in E.SCALP_KEYS and k not in E.RESEARCH_ONLY_KEYS]
    assert sorted(flat) == sorted(expected + list(E.TRADFI_SLOTS)) and len(flat) == len(set(flat))
    assert all(1 <= len(g) <= E.ROTATION_MAX_ACTIVE for g in groups)
    for i in range(len(groups)):
        r = E.rotation_at(i * E.ROTATION_PERIOD_S + 60, tradfi=True)
        assert len(r['active']) <= E.ROTATION_MAX_ACTIVE and r['index'] == i
    assert len({next(i for i, g in enumerate(groups) if k in g) for k in E.TRADFI_SLOTS}) > 1   # spread across groups
    e = mk(on=True, preset=E.ROTATION_PRESET)
    ids = [x['id'] for x in e.S['SLEEVES']]
    assert set(E.TRADFI_SLOTS) <= set(ids) and len(ids) == len(set(ids))
    st = e.rotation_status()
    assert st['tradfi'] is True and len(st['active']) <= E.ROTATION_MAX_ACTIVE
    tf = {x['id']: x for x in e.S['SLEEVES'] if x['id'] in E.TRADFI_SLOTS}
    assert tf['TF-MOM']['key'] == 'ema_mom' and tf['TF-RANGE']['key'] == 'exp_vwaprev'
    for x in e.S['SLEEVES']:
        if x['id'] not in E.TRADFI_SLOTS:
            assert not set(e.sleeve_symbols(x, include_off=True)) & set(TU.TRADFI_ALLOW_LIST), x['id']


def test_enabling_later_syncs_the_slots_into_the_same_rotation(mk):
    e = mk(on=False, preset=E.ROTATION_PRESET)
    assert not set(E.TRADFI_SLOTS) & {x['id'] for x in e.S['SLEEVES']}
    e.S['TRADFI_ENABLED'] = True
    assert sorted(e.sync_tradfi_slots()) == sorted(E.TRADFI_SLOTS)
    want = {x['id']: x['share'] for x in E.with_candidate_slots(E.breadth_sleeves(tradfi=True))[0]}
    assert {x['id']: x['share'] for x in e.S['SLEEVES']} == want
    candidates_off_outside_rotation(e, tradfi=True)
    assert e.sync_tradfi_slots() == []
    e.live = True
    assert e.rotation_block(e.S['SLEEVES'][0]) and e.tradfi_on() is False


# ------------------------------------------------------------------ oil
def test_no_oil_anywhere(mk):
    assert TU.TRADFI_OIL == [] and TU.TRADFI_OIL_ENABLED is False
    oilish = ('CL', 'WTI', 'BRENT', 'OIL', 'USO')
    every = set(TU.TRADFI_ALLOW_LIST) | {s for t in E.TRADFI_TIERS for s in E.tradfi_tier_symbols(t)}
    assert not any(s.startswith(oilish) for s in every)
    e = mk(on=True, preset=E.ROTATION_PRESET)
    assert 'CLUSDT' not in e.rules
    for sl in e.S['SLEEVES']:
        assert not any(s.startswith(oilish) for s in e.sleeve_symbols(sl, include_off=True))
    with pytest.raises(E.SchemaError):
        E.validate_settings(dict(schema_version=E.SETTINGS_SCHEMA, TRADFI_OIL_ENABLED=True))
    E.validate_settings(dict(schema_version=E.SETTINGS_SCHEMA, TRADFI_OIL_ENABLED=False, TRADFI_ENABLED=True))
    assert TS.entry_allowed('CLUSDT', 'oil', OPEN_TS) is False


def test_skip_reasons_have_funnel_codes():
    import trade_audit as TA
    assert TA.reason_code('gold_family_cap: a LONG gold position (XAUUSDT/PAXGUSDT) is already open or working')[1] == 'gold_family_cap'
    assert TA.reason_code('tradfi_session: US cash session CLOSED - no new range entry on SPYUSDT')[1] == 'tradfi_session'
    assert TA.reason_code('tradfi_policy: dca_dip is not an approved strategy for NVDAUSDT')[1] == 'tradfi_policy'
    assert TA.reason_code('TRADFI is PAPER/testnet only - refused in live mode')[1] == 'tradfi_off'



@pytest.mark.parametrize('ts', [CLOSED_TS, datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp(), UNKNOWN_TS])
def test_engine_session_gate_uses_real_breadth_family(mk, ts):
    """Codex 6187427899: the engine passes the real engine.BREADTH_FAMILY label; Saturday / holiday / UNKNOWN block new
    stock VWAPREV (Range) entries, an unmapped key fails closed, and trend is blocked only when UNKNOWN."""
    assert E.BREADTH_FAMILY['exp_vwaprev'] == 'Range' and E.BREADTH_FAMILY['ema_st'] == 'Trend'
    e = mk(on=True)
    assert e.tradfi_block(slot('TF-RANGE', 'exp_vwaprev'), 'SPYUSDT', 'LONG', ts=ts).startswith('tradfi_session')
    assert e.tradfi_block(slot('TF-RANGE', 'exp_vwaprev'), 'SPYUSDT', 'LONG', ts=OPEN_TS) is None
    trend = e.tradfi_block(slot('TF-TREND', 'ema_st'), 'SPYUSDT', 'LONG', ts=ts)
    assert (trend or '').startswith('tradfi_session') is (ts == UNKNOWN_TS)
