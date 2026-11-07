"""Synthetic fixed C260 signal preparation; no market/outcome/identity access."""
from dataclasses import replace
import math

import pytest
from tools.research import c260_low_idio_variance as C


def cohort():
    decision = 35 * C.DAY_MS
    times = tuple(range(decision - 721 * C.HOUR_MS, decision, C.HOUR_MS))
    returns = tuple(.001 * math.sin(i * .07) + .0001 * math.cos(i * .03) for i in range(720))
    def bars(amplitude):
        closes = [100.0]
        for index, market_return in enumerate(returns):
            closes.append(closes[-1] * math.exp(.5 * market_return + amplitude * math.cos(index * .11)))
        return tuple(C.PACK.Bar(time, price, price * 1.001, price * .999, price) for time, price in zip(times, closes))
    btc = [100.0]
    for value in returns: btc.append(btc[-1] * math.exp(value))
    benchmark = tuple(C.PACK.Bar(time, price, price * 1.001, price * .999, price) for time, price in zip(times, btc))
    return dict(decision_ms=decision, fold_start_ms=0, pit_filter_eligible_symbols=('CCCUSDT', 'AAAUSDT', 'BTCUSDT', 'BBBUSDT'),
                btc_bars=benchmark, bars_by_symbol={'AAAUSDT': bars(.0001), 'BBBUSDT': bars(.0002), 'CCCUSDT': bars(.0003)})


def test_exact_proposal_locks_one_neutral_primary_and_stop_time_only():
    p = C.load_proposal_metadata()
    assert p['trial_accounting']['new_primary_trials'] == 1
    assert p['book_and_execution']['full_target_r'] is None
    assert p['book_and_execution']['holding_hours'] == 72
    assert p['trial_accounting']['protected_prior_trial_count'] is None
    assert p['economic_gates']['drawdown_tiers']['high'].startswith('30R<')
    assert p['bootstrap_exact']['seeds']['development_ci'] == 260202611071


def test_full_ranking_is_variance_order_and_not_input_order_or_btc_trade():
    result = C.rank_supplied_closed_cohort(**cohort())
    assert [row.symbol for row in result.ranked] == ['AAAUSDT', 'BBBUSDT', 'CCCUSDT']
    assert result.entry_due_ms == result.decision_ms + C.HOUR_MS
    assert result.scheduled_exit_ms == result.entry_due_ms + 72 * C.HOUR_MS
    assert not result.execution_authorized
    assert result.ranked[0].residual_variance < result.ranked[-1].residual_variance


def test_equal_variance_uses_unicode_tie_order_without_top_twenty_truncation():
    inputs = cohort(); tape = inputs['bars_by_symbol']['AAAUSDT']
    members = tuple(f'ALT{index:02d}USDT' for index in reversed(range(25)))
    inputs.update(pit_filter_eligible_symbols=members, bars_by_symbol={symbol: tape for symbol in members})
    assert [row.symbol for row in C.rank_supplied_closed_cohort(**inputs).ranked] == sorted(members)


def test_missing_past_is_explicit_skip_and_cannot_borrow_across_fold_reset():
    inputs = cohort(); inputs['bars_by_symbol']['AAAUSDT'] = inputs['bars_by_symbol']['AAAUSDT'][1:]
    result = C.rank_supplied_closed_cohort(**inputs)
    assert ('AAAUSDT', 'FOLD_RESET_SYMBOL_WARMUP_OR_MISSING_HISTORY') in result.skips
    inputs['fold_start_ms'] = inputs['btc_bars'][0].time_ms + C.HOUR_MS
    assert not C.rank_supplied_closed_cohort(**inputs).ranked


def test_entry_or_future_bar_poison_never_enters_the_fit():
    inputs = cohort()
    inputs['btc_bars'] = (*inputs['btc_bars'], replace(inputs['btc_bars'][-1], time_ms=inputs['decision_ms']))
    with pytest.raises(C.SignalInputError, match='ENTRY_OR_FUTURE'):
        C.rank_supplied_closed_cohort(**inputs)


def test_degenerate_btc_and_non_daily_decision_cannot_be_selected():
    inputs = cohort()
    inputs['btc_bars'] = tuple(replace(bar, open=100., high=101., low=99., close=100.) for bar in inputs['btc_bars'])
    assert not C.rank_supplied_closed_cohort(**inputs).ranked
    with pytest.raises(C.SignalInputError, match='FROZEN_DECISION'):
        C.rank_supplied_closed_cohort(**{**inputs, 'decision_ms': inputs['decision_ms'] + C.HOUR_MS})


def test_reused_accepted_execution_has_no_target_or_favorable_exit_factor():
    rule = C.PACK.ExchangeRule(tick=.01, step=.001, min_qty=.001, min_notional=1.)
    position = C.PACK.open_position(side='long', entry_index=0, entry_time_ms=0, reference_open=100.,
        atr_entry=1., risk_cash=20., leverage_notional_cap=3000., management={'stop_atr': 2.}, rule=rule)
    assert position.target is None and position.tp1 is None
    favorable = C.PACK.Bar(C.HOUR_MS, 100., 200., 99., 150.)
    assert C.PACK.walk_bar(position, favorable, 1, {'stop_atr': 2.}, rule) == []


def test_real_entry_point_refuses_before_any_file_or_identity_read(monkeypatch):
    monkeypatch.setattr('builtins.open', lambda *args, **kwargs: pytest.fail('real data access'))
    with pytest.raises(C.C260Locked, match='UNBOUND'):
        C.assert_real_evaluation_authorized()
