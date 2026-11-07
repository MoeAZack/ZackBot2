"""Independent synthetic arithmetic/causality examples, never source rows."""
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import math

import pytest
from tools.research import c268_signal as S

H = S.HOUR_MS
T = 1_735_776_000_000
START, END = T - 200 * H, T + 100 * H


def paired(x, y):
    return tuple(S.PairedReturn(t, float(a), float(b)) for t, a, b in
        zip(range(T - 168 * H, T, H), x, y))


def fit(x, y, **kwargs):
    return S.fit_paired_returns('SYNTHUSDT', paired(x, y), entry_ms=T,
        window_start_ms=START, window_end_ms=END, **kwargs)


def values():
    x = [-2., -1., 0., 1., 2., 0.] * 28
    y = [2 * v if v < 0 else .5 * v for v in x]
    return x, y


def closes(returns):
    prices = [100.]
    for r in returns:
        prices.append(prices[-1] * math.exp(r))
    return tuple(S.HourlyClose(t, t + H - 1, price) for t, price in
        zip(range(T - 169 * H, T, H), prices))


def tapes():
    x, _ = values()
    x = [v / 1000 for v in x]
    positive = [2 * v if v < 0 else .5 * v for v in x]
    negative = [.5 * v if v < 0 else 2 * v for v in x]
    return closes(x), closes(positive), closes(negative)


def rank(btc, alts, members=None, **kwargs):
    return S.rank_signals(entry_ms=T, window_start_ms=START, window_end_ms=END,
        btc_closes=btc, alt_closes=alts,
        pit_filter_members=tuple(alts) if members is None else members, **kwargs)


def test_hand_covariance_exact_conditional_count_and_mean_equality_excluded():
    x, y = values()
    # Six equally represented states: meanX=0, meanY=-3/4,
    # E[XY]=25/12, E[X²]=5/3. Negative-state Y is exactly2X.
    result = fit(x, y)
    assert Fraction(25, 12) / Fraction(5, 3) == Fraction(5, 4)
    assert result.beta_all == 1.25 and result.beta_down == 2.
    assert result.score == .75 and result.downside_count == 56
    assert result.positive_eligible  #56 zero-mean equality observations excluded.


@pytest.mark.parametrize('n,passes', [(47, False), (48, True)])
def test_exact_downside_sample_boundary(n, passes):
    x = [-2.] * 24 + [-1.] * (n - 24) + [1.] * (168 - n)
    y = [2 * v if v < 0 else .5 * v for v in x]
    if passes:
        assert fit(x, y).downside_count == 48
    else:
        with pytest.raises(S.UndefinedFit, match='FEWER_THAN_48'):
            fit(x, y)


@pytest.mark.parametrize('case', ['zero_score', 'negative_score', 'negative_down', 'zero_down'])
def test_positive_score_and_positive_down_beta_are_separate_strict_gates(case):
    x, _ = values()
    if case == 'zero_score':
        y = [2 * v for v in x]
    elif case == 'negative_score':
        y = [.5 * v if v < 0 else 2 * v for v in x]
    elif case == 'negative_down':
        y = [-v if v < 0 else -4 * v for v in x]
    else:
        y = [0. if v < 0 else -2 * v for v in x]
    result = fit(x, y)
    assert not result.positive_eligible
    if case == 'zero_score':
        assert result.score == 0.
    if case in ('negative_down', 'zero_down'):
        assert result.score > 0. and result.beta_down <= 0.


@pytest.mark.parametrize('case', ['constant_full', 'constant_down'])
def test_undefined_variances_are_not_invented_fits(case):
    x = [0.] * 168 if case == 'constant_full' else [-1., 0., 1.] * 56
    with pytest.raises(S.UndefinedFit, match='VARIANCE'):
        fit(x, [float(i % 5) for i in range(168)])


def test_exact_169_closes_yield_exact_168_log_ratio_returns():
    btc, alt, _ = tapes()
    rows = S.paired_returns(btc, alt, entry_ms=T, window_start_ms=START, window_end_ms=END)
    assert len(rows) == 168 and rows[0].open_ms == T - 168 * H and rows[-1].open_ms == T - H
    assert rows[-1].btc == math.log(btc[-1].close / btc[-2].close)
    result = S.fit_paired_returns('SYNTHUSDT', rows, entry_ms=T, window_start_ms=START, window_end_ms=END)
    assert result.positive_eligible


@pytest.mark.parametrize('attack', ['missing_close', 'duplicate_hour', 'internal_gap',
    'future_native_close', 'nonpositive', 'nonfinite', 'bool_price', 'wrong_native_close'])
def test_close_history_integrity_failures_refuse_without_time_compression(attack):
    btc, alt, _ = tapes()
    alt = list(alt)
    if attack == 'missing_close':
        alt.pop(50)
    elif attack == 'duplicate_hour':
        alt[50] = alt[49]
    elif attack == 'internal_gap':
        alt[50] = replace(alt[50], open_ms=alt[50].open_ms + H)
    elif attack == 'future_native_close':
        alt[-1] = replace(alt[-1], close_ms=T)
    elif attack == 'wrong_native_close':
        alt[3] = replace(alt[3], close_ms=alt[3].close_ms - 1)
    else:
        alt[50] = replace(alt[50], close={'nonpositive': 0., 'nonfinite': math.nan, 'bool_price': True}[attack])
    with pytest.raises(S.SignalIncomplete):
        S.paired_returns(btc, alt, entry_ms=T, window_start_ms=START, window_end_ms=END)


def test_outside_split_and_extra_future_price_are_never_opened():
    btc, alt, _ = tapes()
    with pytest.raises(S.SignalIncomplete, match='WARMUP'):
        S.paired_returns(btc, alt, entry_ms=T, window_start_ms=T - 168 * H, window_end_ms=END)
    class Forbidden:
        def __getattribute__(self, name):
            pytest.fail('out-of-window price interpreted')
    with pytest.raises(S.SignalIncomplete, match='169'):
        S.paired_returns(btc, (*alt, Forbidden()), entry_ms=T, window_start_ms=START, window_end_ms=END)


@pytest.mark.parametrize('attack', ['count', 'gap', 'nan', 'bool', 'btc_symbol'])
def test_return_seam_requires_exact_finite_consecutive_paired_coordinates(attack):
    x, y = values()
    rows = list(paired(x, y))
    symbol = 'BTCUSDT' if attack == 'btc_symbol' else 'SYNTHUSDT'
    if attack == 'count':
        rows.pop()
    elif attack == 'gap':
        rows[2] = replace(rows[2], open_ms=rows[2].open_ms + H)
    elif attack in ('nan', 'bool'):
        rows[2] = replace(rows[2], btc=math.nan if attack == 'nan' else True)
    with pytest.raises(S.SignalIncomplete):
        S.fit_paired_returns(symbol, rows, entry_ms=T, window_start_ms=START, window_end_ms=END)


def test_utc_six_hour_open_not_native_close_or_local_clock():
    assert all(S.decision_clock(T + h * H) for h in (0, 6, 12, 18, 24))
    assert not any(S.decision_clock(t) for t in (T - 1, T + 1, T + H, True))
    btc, alt, _ = tapes()
    with pytest.raises(S.SignalIncomplete, match='SIX_HOUR'):
        S.rank_signals(entry_ms=T + H, window_start_ms=START, window_end_ms=END,
            btc_closes=btc, alt_closes={'A': alt}, pit_filter_members=('A',))


def test_minimum16_fits_precedes_positive_filter_and_never_pads_indices():
    btc, positive, negative = tapes()
    alts = {f'S{i:02}USDT': negative for i in range(15)}
    alts['WINUSDT'] = positive
    result = rank(btc, alts)
    assert result.statistically_fitted_count == 16 and result.positive_eligible_count == 1
    assert tuple(v.fit.symbol for v in result.indexed) == ('WINUSDT',)
    assert result.indexed[0].index == 1 and result.execution_authorized is False
    alts.pop('S00USDT')
    result = rank(btc, alts)
    assert result.statistically_fitted_count == 15 and result.positive_eligible_count == 1
    assert not result.indexed and result.status == 'INSUFFICIENT_STATISTICALLY_DEFINED_FITS'


def test_unicode_ascending_tiebreak_no_BTC_or_unregistered_stream_access():
    btc, positive, negative = tapes()
    alts = {f'S{i:02}USDT': negative for i in range(13)}
    alts.update({'猫USDT': positive, '哈基米USDT': positive, 'ZUSDT': positive})
    class Forbidden:
        def __iter__(self):
            pytest.fail('unregistered or BTC candidate stream opened')
    alts.update({'BTCUSDT': Forbidden(), 'UNREGISTERED': Forbidden()})
    members = ('BTCUSDT', '猫USDT', '哈基米USDT', 'ZUSDT', *(f'S{i:02}USDT' for i in range(13)))
    result = rank(btc, alts, members)
    assert result.statistically_fitted_count == 16
    assert tuple(v.fit.symbol for v in result.indexed) == ('ZUSDT', '哈基米USDT', '猫USDT')


def test_only_first3_indices_frozen_not_a_backfill_or_occupancy_api():
    btc, positive, _ = tapes()
    alts = {f'S{i:02}USDT': positive for i in range(16)}
    result = rank(btc, alts)
    assert [(p.index, p.fit.symbol) for p in result.indexed] == [(1, 'S00USDT'), (2, 'S01USDT'), (3, 'S02USDT')]
    skipped = {'S00USDT', 'S01USDT', 'S02USDT'}
    assert tuple(p for p in result.indexed if p.fit.symbol not in skipped) == ()
    with pytest.raises(TypeError):
        rank(btc, alts, occupied_symbols=skipped)
    with pytest.raises(FrozenInstanceError):
        result.indexed[0].index = 4


def test_missing_member_and_duplicate_members_are_integrity_failures_not_universe_narrowing():
    btc, positive, _ = tapes()
    alts = {f'S{i:02}USDT': positive for i in range(16)}
    for members in ((*alts, 'MISSING'), (*alts, 'S00USDT')):
        with pytest.raises(S.SignalIncomplete):
            rank(btc, alts, members)
