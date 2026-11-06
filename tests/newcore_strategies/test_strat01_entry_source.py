"""STRAT-01 strategy EntrySource: parity with the research signal path, fail-closed freshness, NC-06 request shape."""
from __future__ import annotations

import dataclasses
import os
import sys
from decimal import Decimal as D
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import strategies as ST  # noqa: E402
from newcore import risk as nc06  # noqa: E402
from newcore.domain.orders import Purpose, Side  # noqa: E402
from newcore.runtime.ports import EntryCandidate, EntrySource  # noqa: E402
from newcore_strategies import (CORE_20, CsvCandleFeed, RiskContext, StrategyConfigError,  # noqa: E402
                                StrategyEntrySource, StrategySlot, build_slots, build_strategy_entries,
                                slot_from_dict)
from newcore_strategies import entry_source as ES  # noqa: E402

H4 = 14_400_000
ACCT = 'acct_' + 'a' * 32
DATA4H = ROOT / 'data'
UNIVERSE = tuple(s for s in CORE_20 if (DATA4H / f'{s}_4h.csv').exists())


def _load(symbol):
    d = pd.read_csv(DATA4H / f'{symbol}_4h.csv', encoding='utf-8')
    d['t'] = pd.to_datetime(d['t'])
    d['qv'] = d['v'] * (d['o'] + d['c']) / 2      # fixture quote volume (the CSVs carry none); same input both paths
    return d


FRAMES = {s: _load(s) for s in UNIVERSE}


def _ms(ts):
    return int(pd.Timestamp(ts).value // 1_000_000)


class MemFeed:
    """Closed candles from in-memory frames, with optional per-symbol tampering."""

    def __init__(self, frames, tamper=None):
        self.frames, self.tamper, self.calls = frames, tamper or {}, 0

    def closed(self, symbol, tf, bars, now_wall_ms):
        self.calls += 1
        d = self.frames.get(symbol)
        if d is None:
            return None
        opens = d['t'].astype('datetime64[ms]').astype('int64')
        out = d[opens + H4 <= now_wall_ms].iloc[-(bars + 10):].reset_index(drop=True)   # a few extra, like real feeds
        fn = self.tamper.get(symbol)
        return fn(out) if fn else out


def account(**kw):
    return dataclasses.replace(nc06.AccountRiskSnapshot(
        account_id=ACCT, environment=nc06.Environment.TESTNET, generation=3, confirmed=True, fresh=True,
        hedge_mode=True, equity=D('10000'), available_margin=D('9000'), reserved_capital=D('0')), **kw)


def policy(strategy_ids):
    return nc06.RiskPolicySnapshot(
        policy_id='policy-v1', account_id=ACCT, environment=nc06.Environment.TESTNET, generation=5, fresh=True,
        capital_cap=D('10000'), max_trade_risk_fraction=D('.01'), max_portfolio_risk_fraction=D('.05'),
        max_symbol_side_risk_fraction=D('.02'), max_gross_multiple=D('3'), max_net_multiple=D('3'),
        max_symbol_fraction=D('1'), max_symbol_side_fraction=D('1'), max_leverage=D('3'),
        allocations=tuple(nc06.Allocation(strategy_id=s, book_id='testnet_strat01', notional_fraction=D('1'),
                                          risk_fraction=D('.05')) for s in strategy_ids),
        correlation_map=tuple(nc06.CorrelationAssignment(symbol=s, bucket='crypto') for s in UNIVERSE),
        correlation_caps=(nc06.CorrelationCap(bucket='crypto', exposure_fraction=D('1')),))


def filters(symbol, tick='0.01'):
    return nc06.VenueRiskFilters(
        filter_id=f'f-{symbol}', symbol=symbol, generation=7, fresh=True, verified=True, step_size=D('0.001'),
        tick_size=D(tick), min_quantity=D('0.001'), max_quantity=D('1000000000'), min_notional=D('5'),
        max_notional=D('100000000'), max_leverage=D('3'))


class Risk:
    def __init__(self, slots, tick='0.00001', missing=(), boom=False):
        self.ids = tuple(s.strategy_id for s in slots)
        self.tick, self.missing, self.boom, self.calls = tick, set(missing), boom, []

    def risk_context(self, account_id, slot, symbol):
        self.calls.append((account_id, slot.name, symbol))
        if self.boom:
            raise RuntimeError('boom')
        if symbol in self.missing:
            return None
        return RiskContext(account=account(account_id=account_id),
                           portfolio=nc06.PortfolioRiskSnapshot(account_id=account_id, generation=11, fresh=True,
                                                                exposures=(), reservations=()),
                           policy=policy(self.ids), filters=filters(symbol, self.tick))


def slot(key='exp_range', name='range4h_long', side='LONG', mult='0.5', symbols=UNIVERSE, tf='4h'):
    return StrategySlot(key=key, name=name, version='v1', tf=tf, side=nc06.PositionSide(side), symbols=tuple(symbols),
                        risk_mult=D(mult), strategy_id=name, book_id='testnet_strat01')


def source(slots, feed=None, risk=None, now=None, **kw):
    clock = {'now': now}
    kw.setdefault('base_risk_fraction', D('0.005'))
    kw.setdefault('leverage', D('3'))
    src = StrategyEntrySource(slots, feed=feed or MemFeed(FRAMES), risk=risk or Risk(slots),
                              wall_clock=lambda: clock['now'], **kw)
    return src, clock


_REF = {}


def reference(key, side, close_ms, symbols=UNIVERSE):
    """Full-history research path: signal of every symbol on the candle that closes at close_ms."""
    if key not in _REF:
        dfs = {s: ST.indicators(FRAMES[s]) for s in symbols}
        ctx = ST.build_context(dfs)
        _REF[key] = {s: (d['t'].astype('datetime64[ms]').astype('int64').to_numpy() + H4, ST.signals(key, d, ctx[s]))
                     for s, d in dfs.items()}
    out = set()
    for s, (closes, sig) in _REF[key].items():
        i = np.flatnonzero(closes == close_ms)
        if len(i) == 1 and (sig['le'] if side == 'LONG' else sig['se'])[i[0]]:
            out.add(s)
    return out


def _common_closes():
    sets = [set(d['t'].astype('datetime64[ms]').astype('int64') + H4) for d in FRAMES.values()]
    return sorted(set.intersection(*sets))


def _contiguous_closes(warm=1500):
    """Close times whose 1500-bar window is gap-free in every universe frame."""
    good = None
    for d in FRAMES.values():
        opens = d['t'].astype('datetime64[ms]').astype('int64').to_numpy()
        ok = set()
        gap = np.r_[False, np.diff(opens) != H4]
        last_gap = -1
        for i in range(len(opens)):
            if gap[i]:
                last_gap = i
            if i - last_gap >= warm - 1 and i + 1 >= warm:
                ok.add(int(opens[i] + H4))
        good = ok if good is None else good & ok
    return sorted(good)


CLOSES = _contiguous_closes()


# ---------------------------------------------------------------------------------------------------- parity
@pytest.mark.parametrize('key,side', [('exp_range', 'LONG'), ('exp_range', 'SHORT'), ('exp_vwaprev', 'LONG'),
                                      ('exp_vwaprev', 'SHORT'), ('rotation_long_h2', 'LONG')])
def test_last_bar_decision_matches_full_history_research_signal(key, side):
    assert len(CLOSES) > 100, 'fixture data must cover warmup + sample'
    full = {c: reference(key, side, c) for c in CLOSES}
    fired = [c for c, v in full.items() if v]
    quiet = [c for c, v in full.items() if not v]
    sample = fired[-8:] + quiet[::60][:2]
    assert fired, f'{key} {side}: fixture never fires; the parity test would be vacuous'
    for close in sample:
        s = slot(key=key, name=f'{key[:10]}_{side.lower()}', side=side)
        src, clock = source([s], now=close + 60_000, max_entries_per_candle=5)
        got = {x.symbol for x in src._current(s, close + 60_000)}          # every signal, before the per-candle cap
        assert got == full[close], (key, side, pd.Timestamp(close, unit='ms'))
        offered = [c.key.symbol for c in src.candidates(ACCT, 1)]
        assert offered == sorted(full[close], key=UNIVERSE.index)[:5]


# ---------------------------------------------------------------------------------------------------- request
def _first_fire(key='exp_range', side='LONG'):
    for c in reversed(CLOSES[-200:]):
        hits = reference(key, side, c)
        if hits:
            return c, sorted(hits, key=UNIVERSE.index)
    raise AssertionError('no fire')


def test_candidate_shape_stop_risk_and_nc06_approval():
    close, hits = _first_fire()
    s = slot()
    src, _ = source([s], now=close + 1_000)
    (cand,) = src.candidates(ACCT, 5)
    assert type(cand) is EntryCandidate and cand.key.purpose is Purpose.ENTRY
    assert cand.key.symbol == hits[0] and cand.key.side is Side.LONG and cand.key.candle_close_ms == close
    assert cand.key.strategy == 'range4h_long@4h' and cand.key.strategy_version == 'v1'
    req = cand.evaluation.request
    assert (req.symbol, req.side) == (cand.key.symbol, nc06.PositionSide.LONG)
    d = ST.indicators(FRAMES[req.symbol][FRAMES[req.symbol]['t'].astype('datetime64[ms]').astype('int64') + H4 <= close]
                      .iloc[-1500:].reset_index(drop=True))
    entry = D(repr(float(d['c'].iloc[-1])))
    assert req.entry_price == entry
    raw_stop = entry - D(repr(1.5 * float(d['atr'].iloc[-1])))
    assert req.stop_price <= raw_stop and raw_stop - req.stop_price < D('0.00001')   # floored to tick, away from entry
    assert req.stop_price % D('0.00001') == 0
    assert req.requested_risk == D('10000') * D('0.005') * D('0.5')
    assert req.requested_leverage == D('3') and req.action is nc06.RiskAction.ENTRY
    assert req.request_id.startswith('req_') and len(req.request_id) == 36
    assert isinstance(nc06.evaluate(cand.evaluation), nc06.ApprovedDecision)
    assert isinstance(src, EntrySource)


def test_short_stop_rounds_up_away_from_entry():
    close, _ = _first_fire('exp_range', 'SHORT')
    s = slot(name='range4h_short', side='SHORT')
    src, _ = source([s], now=close + 1_000)
    (cand,) = src.candidates(ACCT, 5)
    req = cand.evaluation.request
    assert req.stop_price > req.entry_price and req.stop_price % D('0.00001') == 0
    sym = cand.key.symbol
    d = ST.indicators(FRAMES[sym][FRAMES[sym]['t'].astype('datetime64[ms]').astype('int64') + H4 <= close]
                      .iloc[-1500:].reset_index(drop=True))
    raw_stop = req.entry_price + D(repr(1.5 * float(d['atr'].iloc[-1])))
    assert req.stop_price >= raw_stop and req.stop_price - raw_stop < D('0.00001')   # ceiled, away from entry
    assert cand.key.side is Side.SHORT


def test_request_id_is_deterministic_and_account_scoped():
    close, _ = _first_fire()
    a1, _ = source([slot()], now=close + 1)
    a2, _ = source([slot()], now=close + 2)
    r1 = a1.candidates(ACCT, 1)[0].evaluation.request.request_id
    assert r1 == a2.candidates(ACCT, 1)[0].evaluation.request.request_id


def test_risk_context_for_other_account_is_refused():
    close, _ = _first_fire()
    s = slot()

    class Wrong(Risk):
        def risk_context(self, account_id, slot_, symbol):
            ctx = super().risk_context(account_id, slot_, symbol)
            return dataclasses.replace(ctx, account=account(account_id='acct_' + 'c' * 32))
    src, _ = source([s], risk=Wrong([s]), now=close + 1)
    assert src.candidates(ACCT, 1) == ()
    assert src.reasons['range4h_long:risk_context_missing'] >= 1


@pytest.mark.parametrize('kind', ['none', 'raise', 'wrong_symbol'])
def test_missing_or_broken_risk_context_yields_nothing(kind):
    close, hits = _first_fire()
    s = slot()
    if kind == 'none':
        risk = Risk([s], missing=set(UNIVERSE))
    elif kind == 'raise':
        risk = Risk([s], boom=True)
    else:
        class R(Risk):
            def risk_context(self, a, sl, sym):
                ctx = super().risk_context(a, sl, sym)
                return dataclasses.replace(ctx, filters=filters('ZZZUSDT'))
        risk = R([s])
    src, _ = source([s], risk=risk, now=close + 1)
    assert src.candidates(ACCT, 1) == ()


# ---------------------------------------------------------------------------------------------------- once / caps
def test_key_reoffered_only_after_reoffer_interval_and_evaluated_once_per_candle():
    close, _ = _first_fire()
    feed = MemFeed(FRAMES)
    src, clock = source([slot()], feed=feed, now=close + 1)
    first = src.candidates(ACCT, 1)
    calls = feed.calls
    clock['now'] = close + 1 + 59_999
    assert first and src.candidates(ACCT, 2) == ()
    clock['now'] = close + 1 + 60_000
    again = src.candidates(ACCT, 3)
    assert [c.key for c in again] == [c.key for c in first]         # a busy sink can still take it later
    assert feed.calls == calls                      # cached: no re-read until the next candle closes
    clock['now'] = close + 15 * 60_000 + 1
    assert src.candidates(ACCT, 4) == ()            # never after the age window


def test_late_feed_is_retried_not_cached():
    close, hits = _first_fire()
    state = {'late': True}
    feed = MemFeed(FRAMES, tamper={s: (lambda d: d.iloc[:-1] if state['late'] else d) for s in UNIVERSE})
    src, clock = source([slot()], feed=feed, now=close + 1)
    assert src.candidates(ACCT, 1) == ()
    state['late'] = False
    clock['now'] = close + 1 + 29_999
    assert src.candidates(ACCT, 2) == ()            # retry is throttled
    clock['now'] = close + 1 + 30_000
    assert [c.key.symbol for c in src.candidates(ACCT, 3)] == hits[:1]


def test_late_single_symbol_is_retried_and_joins_later():
    close, hits = _first_fire()
    sym = hits[0]
    state = {'late': True}
    feed = MemFeed(FRAMES, tamper={sym: lambda d: d.iloc[:-1] if state['late'] else d})
    src, clock = source([slot()], feed=feed, now=close + 1, max_entries_per_candle=5)
    assert sym not in {c.key.symbol for c in src.candidates(ACCT, 1)}
    state['late'] = False
    clock['now'] = close + 1 + 30_000
    assert sym in {c.key.symbol for c in src.candidates(ACCT, 2)}


def test_failing_slot_never_raises_and_never_drops_other_slots():
    close, hits = _first_fire()
    good, bad = slot(), slot(name='range4h_short', side='SHORT')

    class Mixed(Risk):
        def risk_context(self, a, sl, sym):
            ctx = super().risk_context(a, sl, sym)
            return dataclasses.replace(ctx, account=account(equity=10000.0)) if sl.name == 'range4h_short' else ctx
    src, clock = source([bad, good], risk=Mixed([good, bad]), now=close + 1)
    got = src.candidates(ACCT, 1)
    assert [c.key.symbol for c in got] == hits[:1]
    assert src.reasons['range4h_short:risk_context_missing'] >= 1


@pytest.mark.parametrize('mutate', [
    lambda c: dataclasses.replace(c, account=account(equity=10000.0)),
    lambda c: dataclasses.replace(c, account=account(equity=D('NaN'))),
    lambda c: dataclasses.replace(c, filters=dataclasses.replace(c.filters, tick_size=D('NaN'))),
    lambda c: dataclasses.replace(c, filters=dataclasses.replace(c.filters, tick_size=D('0'))),
    lambda c: dataclasses.replace(c, account=None),
    lambda c: dataclasses.replace(c, followers=[]),
    lambda c: dataclasses.replace(c, policy=None),
])
def test_malformed_risk_context_gives_nothing_and_does_not_raise(mutate):
    close, _ = _first_fire()
    s = slot()

    class R(Risk):
        def risk_context(self, a, sl, sym):
            return mutate(super().risk_context(a, sl, sym))
    src, _ = source([s], risk=R([s]), now=close + 1)
    assert src.candidates(ACCT, 1) == ()
    assert src.reasons['range4h_long:risk_context_missing'] >= 1


def test_signal_exception_in_one_slot_is_contained(monkeypatch):
    close, hits = _first_fire()
    orig = ST.signals

    def flaky(key, d, ctx, *a, **k):
        if key == 'exp_vwaprev':
            raise RuntimeError('signals')
        return orig(key, d, ctx, *a, **k)
    monkeypatch.setattr(ST, 'signals', flaky)
    src, _ = source([slot(key='exp_vwaprev', name='vw'), slot()], now=close + 1)
    assert [c.key.symbol for c in src.candidates(ACCT, 1)] == hits[:1]
    assert src.reasons['vw:signal_error'] >= 1


@pytest.mark.parametrize('kind', ['str', 'decimal', 'object_float'])
def test_non_float_ohlcv_columns_are_converted(kind):
    close, hits = _first_fire()

    def conv(d):
        d = d.copy()
        for c in ('o', 'h', 'l', 'c', 'v', 'qv'):
            if kind == 'str':
                d[c] = d[c].map(repr)
            elif kind == 'decimal':
                d[c] = d[c].map(lambda x: D(repr(x)))
            else:
                d[c] = d[c].astype(object)
        return d
    src, _ = source([slot()], feed=MemFeed(FRAMES, tamper={s: conv for s in UNIVERSE}), now=close + 1)
    assert [c.key.symbol for c in src.candidates(ACCT, 1)] == hits[:1]


def test_tz_aware_times_are_converted_to_utc_and_numbers_refused():
    close, hits = _first_fire()

    def tokyo(d):
        d = d.copy()
        d['t'] = d['t'].dt.tz_localize('UTC').dt.tz_convert('Asia/Tokyo')
        return d
    src, _ = source([slot()], feed=MemFeed(FRAMES, tamper={s: tokyo for s in UNIVERSE}), now=close + 1)
    assert [c.key.symbol for c in src.candidates(ACCT, 1)] == hits[:1]

    def shifted(d):                  # forming candle relabelled by a wrong offset must not be accepted as closed
        d = d.copy()
        d['t'] = d['t'].dt.tz_localize('America/New_York', ambiguous='NaT', nonexistent='NaT')
        return d
    src2, _ = source([slot()], feed=MemFeed(FRAMES, tamper={s: shifted for s in UNIVERSE}), now=close + 1)
    assert src2.candidates(ACCT, 1) == ()

    def numeric(d):
        d = d.copy()
        d['t'] = d['t'].astype('datetime64[ms]').astype('int64')
        return d
    src3, _ = source([slot()], feed=MemFeed(FRAMES, tamper={s: numeric for s in UNIVERSE}), now=close + 1)
    assert src3.candidates(ACCT, 1) == ()
    with pytest.raises(ValueError):
        ES._to_ms(1_700_000_000_000)


def test_per_candle_cap_keeps_first_in_universe_order():
    close = next(c for c in reversed(CLOSES[-200:]) if len(reference('exp_range', 'LONG', c)) >= 2)
    hits = sorted(reference('exp_range', 'LONG', close), key=UNIVERSE.index)
    src, _ = source([slot()], now=close + 1, max_entries_per_candle=1)
    got = src.candidates(ACCT, 1)
    assert [c.key.symbol for c in got] == hits[:1]
    assert src.reasons['range4h_long:per_candle_cap'] == 1
    src2, _ = source([slot()], now=close + 1, max_entries_per_candle=5)
    assert {c.key.symbol for c in src2.candidates(ACCT, 1)} == set(hits)


def test_cap_is_per_account():
    close = next(c for c in reversed(CLOSES[-200:]) if reference('exp_range', 'LONG', c))
    s = slot()

    class Any(Risk):
        def risk_context(self, a, sl, sym):
            ctx = super().risk_context(a, sl, sym)
            return dataclasses.replace(ctx, account=account(account_id=a))
    src, _ = source([s], risk=Any([s]), now=close + 1)
    assert len(src.candidates(ACCT, 1)) == 1
    assert len(src.candidates('acct_' + 'd' * 32, 1)) == 1


def test_account_filter():
    close, _ = _first_fire()
    src, _ = source([slot()], now=close + 1, account_ids=('acct_' + 'e' * 32,))
    assert src.candidates(ACCT, 1) == ()


# ---------------------------------------------------------------------------------------------------- freshness
def test_late_wall_clock_gives_nothing():
    close, _ = _first_fire()
    src, _ = source([slot()], now=close + 15 * 60_000 + 1)
    assert src.candidates(ACCT, 1) == ()
    src2, _ = source([slot()], now=close + 15 * 60_000)
    assert len(src2.candidates(ACCT, 1)) == 1


def test_missing_newest_candle_gives_nothing():
    close, _ = _first_fire()
    feed = MemFeed(FRAMES, tamper={s: (lambda d: d.iloc[:-1]) for s in UNIVERSE})
    src, _ = source([slot()], feed=feed, now=close + 1)
    assert src.candidates(ACCT, 1) == ()


def test_feed_stuck_one_candle_behind_gives_nothing_even_when_that_candle_fired():
    prev = next(c for c in reversed(CLOSES[-200:]) if reference('exp_range', 'LONG', c))
    src, _ = source([slot()], feed=MemFeed(FRAMES, tamper={s: (lambda d: d.iloc[:-1]) for s in UNIVERSE}),
                    now=prev + H4 + 1)
    assert src.candidates(ACCT, 1) == ()
    ok, _ = source([slot()], now=prev + 1)
    assert ok.candidates(ACCT, 1)                   # the same candle is a signal when it is the newest close


def test_gap_in_window_skips_symbol_and_btc_gap_stops_slot():
    close, hits = _first_fire()
    sym = hits[0]
    drop_mid = lambda d: d.drop(index=len(d) - 50).reset_index(drop=True)  # noqa: E731
    src, _ = source([slot()], feed=MemFeed(FRAMES, tamper={sym: drop_mid}), now=close + 1,
                    max_entries_per_candle=5)
    assert sym not in {c.key.symbol for c in src.candidates(ACCT, 1)}
    src2, _ = source([slot()], feed=MemFeed(FRAMES, tamper={'BTCUSDT': drop_mid}), now=close + 1)
    assert src2.candidates(ACCT, 1) == ()


def test_partial_universe_stops_cross_sectional_rotation():
    close = next(c for c in reversed(CLOSES[-200:]) if reference('rotation_long_h2', 'LONG', c))
    other = next(s for s in UNIVERSE if s != 'BTCUSDT')
    s = slot(key='rotation_long_h2', name='c24_rotation_long', mult='0.806')
    src, _ = source([s], feed=MemFeed(FRAMES, tamper={other: lambda d: None}), now=close + 1)
    assert src.candidates(ACCT, 1) == ()


def test_vwaprev_without_quote_volume_fails_closed():
    s = slot(key='exp_vwaprev', name='vwaprev4h_long')
    src, _ = source([s], feed=MemFeed(FRAMES, tamper={'BTCUSDT': lambda d: d.drop(columns=['qv'])}),
                    now=CLOSES[-1] + 1)
    assert src.candidates(ACCT, 1) == ()
    assert src.reasons['vwaprev4h_long:candles_unusable'] == 1


@pytest.mark.parametrize('bad', ['nan', 'nonpositive', 'high_below_close', 'missing_col', 'short', 'feed_raises'])
def test_corrupt_candles_fail_closed(bad):
    close, hits = _first_fire()
    sym = hits[0]

    def t(d):
        d = d.copy()
        if bad == 'nan':
            d.loc[len(d) - 3, 'c'] = float('nan')
        elif bad == 'nonpositive':
            d.loc[len(d) - 3, 'l'] = 0.0
        elif bad == 'high_below_close':           # high just under the body top, still above the low
            row = d.loc[len(d) - 1]
            top, low = max(row['o'], row['c']), row['l']
            d.loc[len(d) - 1, 'h'] = top - (top - low) * 0.01 if top > low else top * 0.999
        elif bad == 'missing_col':
            d = d.drop(columns=['v'])
        elif bad == 'short':
            d = d.iloc[-100:]
        else:
            raise RuntimeError('feed')
        return d
    src, _ = source([slot()], feed=MemFeed(FRAMES, tamper={sym: t}), now=close + 1, max_entries_per_candle=5)
    assert sym not in {c.key.symbol for c in src.candidates(ACCT, 1)}
    assert src.reasons['range4h_long:candles_unusable'] >= 1


def test_invalid_wall_clock():
    src, clock = source([slot()], now=None)
    assert src.candidates(ACCT, 1) == ()
    for bad in (1.5e12, -5, 0, True):
        clock['now'] = bad
        assert src.candidates(ACCT, 1) == ()
    assert src.reasons['wall_clock_invalid'] == 5


def test_entry_is_put_on_a_coarse_tick_grid_and_degenerate_prices_give_nothing():
    close, _ = _first_fire()
    s = slot()
    src, _ = source([s], risk=Risk([s], tick='0.1'), now=close + 1, max_entries_per_candle=5)
    got = src.candidates(ACCT, 1)
    assert got and all(c.evaluation.request.entry_price % D('0.1') == 0 for c in got)
    raw = {c.key.symbol: D(repr(float(FRAMES[c.key.symbol].set_index('t')['c']
                                      [pd.Timestamp(close - H4, unit='ms')]))) for c in got}
    assert any(raw[c.key.symbol] != c.evaluation.request.entry_price for c in got)
    huge, _ = source([s], risk=Risk([s], tick='1000000'), now=close + 1, max_entries_per_candle=5)
    assert huge.candidates(ACCT, 1) == ()
    assert huge.reasons['range4h_long:stop_invalid'] >= 1


def test_vwaprev_stop_uses_its_own_atr_series():
    s = slot(key='exp_vwaprev', name='vwaprev4h_long')
    close = next(c for c in reversed(CLOSES) if reference('exp_vwaprev', 'LONG', c))
    src, _ = source([s], now=close + 1)
    (cand,) = src.candidates(ACCT, 1)
    sym = cand.key.symbol
    d = ST.indicators(FRAMES[sym][FRAMES[sym]['t'].astype('datetime64[ms]').astype('int64') + H4 <= close]
                      .iloc[-1500:].reset_index(drop=True))
    own = ST.signals('exp_vwaprev', d, ST.build_context({'BTCUSDT': ST.indicators(
        FRAMES['BTCUSDT'][FRAMES['BTCUSDT']['t'].astype('datetime64[ms]').astype('int64') + H4 <= close]
        .iloc[-1500:].reset_index(drop=True)), sym: d})[sym])['atr'][-1]
    assert np.isfinite(own) and abs(own - float(d['atr'].iloc[-1])) > 1e-9
    req = cand.evaluation.request
    expect = req.entry_price - D(repr(1.5 * float(own)))
    assert req.stop_price <= expect and expect - req.stop_price < D('0.00001')


# ---------------------------------------------------------------------------------------------------- config
@pytest.mark.parametrize('changes,code', [
    (dict(key='nope'), 'strategy_unsupported'),
    (dict(key='exp_mom_short_c27', side='SHORT'), 'strategy_unsupported'),
    (dict(key='rank_breadth_reversal_4h'), 'strategy_unsupported'),
    (dict(tf='1h'), 'tf_mismatch'),
    (dict(key='rotation_long_h2', side='SHORT'), 'side_not_allowed'),
    (dict(mult='1.01'), 'risk_mult_invalid'),
    (dict(mult='0'), 'risk_mult_invalid'),
    (dict(symbols=('ETHUSDT', 'SOLUSDT')), 'symbols_invalid'),
    (dict(symbols=('BTCUSDT', 'BTCUSDT')), 'symbols_invalid'),
    (dict(symbols=('BTCUSDT', 'eth')), 'symbols_invalid'),
    (dict(name='Bad-Name'), 'name_invalid'),
    (dict(name='x' * 25), 'name_invalid'),
])
def test_slot_validation(changes, code):
    with pytest.raises(StrategyConfigError) as e:
        slot(**changes)
    assert e.value.code == code


def test_slot_without_stop_atr_is_refused(monkeypatch):
    monkeypatch.setitem(ST.STRATEGIES, 'tmp_nostop', dict(ST.STRATEGIES['exp_range'], mgmt={}))
    monkeypatch.setattr(ES, 'SUPPORTED', ES.SUPPORTED | {'tmp_nostop'})
    with pytest.raises(StrategyConfigError) as e:
        slot(key='tmp_nostop', name='tmp_nostop')
    assert e.value.code == 'stop_atr_missing'


@pytest.mark.parametrize('kw,code', [
    (dict(base_risk_fraction=D('0.0101')), 'base_risk_invalid'),
    (dict(base_risk_fraction=D('0')), 'base_risk_invalid'),
    (dict(base_risk_fraction=0.005), 'base_risk_invalid'),
    (dict(leverage=D('3.01')), 'leverage_invalid'),
    (dict(leverage=D('0.5')), 'leverage_invalid'),
    (dict(warmup_bars=399), 'warmup_invalid'),
    (dict(max_signal_age_ms=H4 // 2 + 1), 'max_signal_age_invalid'),
    (dict(max_signal_age_ms=0), 'max_signal_age_invalid'),
    (dict(max_entries_per_candle=0), 'max_entries_per_candle_invalid'),
    (dict(max_entries_per_candle=6), 'max_entries_per_candle_invalid'),
    (dict(account_ids=[ACCT]), 'account_ids_invalid'),
    (dict(retry_ms=999), 'retry_ms_invalid'),
    (dict(reoffer_ms=15 * 60_000 + 1), 'reoffer_ms_invalid'),
])
def test_source_validation(kw, code):
    with pytest.raises(StrategyConfigError) as e:
        source([slot()], **kw)
    assert e.value.code == code


def test_duplicate_slot_names_refused():
    with pytest.raises(StrategyConfigError) as e:
        source([slot(), slot(side='SHORT')])
    assert e.value.code == 'slot_name_duplicate'


def test_slot_from_dict_exact_fields():
    row = dict(key='exp_range', name='r', version='v1', tf='4h', side='LONG', symbols=list(UNIVERSE),
               risk_mult='0.5', strategy_id='r', book_id='b')
    assert slot_from_dict(row).risk_mult == D('0.5')
    with pytest.raises(StrategyConfigError):
        slot_from_dict({**row, 'extra': 1})
    with pytest.raises(StrategyConfigError):
        slot_from_dict({**row, 'risk_mult': 0.5})
    with pytest.raises(StrategyConfigError):
        slot_from_dict({**row, 'side': 'BOTH'})


# ---------------------------------------------------------------------------------------------------- presets
def test_preset_caps_never_exceed_dd_policy_and_unavailable_keys_are_reported():
    caps = {'c101_kalman_long': D('1')}
    from newcore_strategies.presets import TESTNET_SET_2026_11_05
    for row in TESTNET_SET_2026_11_05:
        if row['name'] in caps:
            assert D(row['risk_mult']) <= caps[row['name']].quantize(D('0.0001')) + D('0.0001'), row['name']
        assert D(row['risk_mult']) <= 1
    slots, refused = build_slots()
    names = {s.name for s in slots} | set(refused)
    assert names == {r['name'] for r in TESTNET_SET_2026_11_05}
    for name, code in refused.items():
        assert code == 'strategy_unavailable'
        key = next(r['key'] for r in TESTNET_SET_2026_11_05 if r['name'] == name)
        assert key not in ST.STRATEGIES


def test_holdout_failures_are_not_in_the_preset_and_are_refused_if_passed():
    from newcore_strategies.presets import HOLDOUT_FAILED_2026_11_05, TESTNET_SET_2026_11_05
    assert HOLDOUT_FAILED_2026_11_05 == {'rotation_long_h2', 'evening_star_short_1h', 'starc_band_breakout_long_1h'}
    assert not {r['key'] for r in TESTNET_SET_2026_11_05} & HOLDOUT_FAILED_2026_11_05
    row = dict(key='rotation_long_h2', name='c24_rotation_long', version='v1', tf='4h', side='LONG',
               symbols=['BTCUSDT', 'ETHUSDT'], risk_mult='0.806', strategy_id='c24', book_id='b')
    slots, refused = build_slots((row,))
    assert slots == () and refused == {'c24_rotation_long': 'holdout_failed'}


def test_factory_returns_source_and_refusals():
    s, refused = build_slots()
    src, ref = build_strategy_entries(feed=MemFeed(FRAMES), risk=Risk(s), wall_clock=lambda: 1,
                                      base_risk_fraction=D('0.005'), leverage=D('3'))
    assert isinstance(src, StrategyEntrySource) and ref == refused


# ---------------------------------------------------------------------------------------------------- csv feed
def test_csv_feed_returns_only_closed_candles(tmp_path):
    d = FRAMES['BTCUSDT'].iloc[-30:].copy()
    d['t'] = d['t'].dt.strftime('%Y-%m-%d %H:%M:%S')
    d.to_csv(tmp_path / 'BTCUSDT_4h.csv', index=False, encoding='utf-8')
    feed = CsvCandleFeed(str(tmp_path))
    last_open = _ms(FRAMES['BTCUSDT']['t'].iloc[-1])
    assert feed.closed('BTCUSDT', '4h', 10, last_open + H4 - 1)['t'].iloc[-1] == FRAMES['BTCUSDT']['t'].iloc[-2]
    assert feed.closed('BTCUSDT', '4h', 10, last_open + H4)['t'].iloc[-1] == FRAMES['BTCUSDT']['t'].iloc[-1]
    assert len(feed.closed('BTCUSDT', '4h', 10, last_open + H4)) == 10
    assert feed.closed('ETHUSDT', '4h', 10, last_open) is None
    assert feed.closed('../BTCUSDT', '4h', 10, last_open) is None
    assert feed.closed('BTCUSDT', '7h', 10, last_open) is None


def test_csv_feed_reads_epoch_millisecond_times(tmp_path):
    d = FRAMES['BTCUSDT'].iloc[-30:].copy()
    d['t'] = d['t'].astype('datetime64[ms]').astype('int64').astype(float)
    d.to_csv(tmp_path / 'BTCUSDT_4h.csv', index=False, encoding='utf-8')
    feed = CsvCandleFeed(str(tmp_path))
    last_open = _ms(FRAMES['BTCUSDT']['t'].iloc[-1])
    out = feed.closed('BTCUSDT', '4h', 10, last_open + H4)
    assert list(out['t']) == list(FRAMES['BTCUSDT']['t'].iloc[-10:])


def test_module_does_not_live_inside_runtime_boundary():
    assert not (ROOT / 'newcore' / 'runtime' / 'strategies_entry.py').exists()
    src = (ROOT / 'newcore_strategies' / 'entry_source.py').read_text(encoding='utf-8')
    for banned in ('subprocess', 'socket', 'requests', 'urllib', 'binance_client'):
        assert f'import {banned}' not in src
