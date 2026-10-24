"""INTRABAR-DISCLOSURE-01 (Codex #206 comment 6388342400): the price-free unresolved-segment extractor.

Pins: it reports each unresolved segment with its label-log index, candle and spanning-minute open, label, side and
exit overlap; it changes no fill (the bar_path spy is decision-neutral and always removed); it reproduces run_cell's
aggregate labels and trade count and refuses a packet it does not reproduce; its output carries no price.
"""
import copy
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'tools', 'research')]

import costs as C                                         # noqa: E402
import intrabar_disclosure as D                           # noqa: E402
import scalp_exec_15m as X                                # noqa: E402
import strategies as S                                    # noqa: E402

SYM = 'SOLUSDT'
T0 = 260
SIG = 280


def _flat_minutes(n15, start='2025-01-06'):
    t = pd.date_range(start, periods=n15 * 15, freq='1min')
    return pd.DataFrame(dict(t=t, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0))


def _agg(m):
    g = m.groupby(m.t.dt.floor('15min'))
    return pd.DataFrame(dict(t=g.t.first().dt.floor('15min').values, o=g.o.first().values, h=g.h.max().values,
                             l=g.l.min().values, c=g.c.last().values, v=g.v.sum().values))


def _set(m, at, o, h, l, c):
    k = m.index[m.t == pd.Timestamp(at)][0]
    m.loc[k, ['o', 'h', 'l', 'c']] = [o, h, l, c]


def _sleeves(side=1):
    import engine as E
    return [E.sleeve('SC', 'exp_scalp', 1.0, 0.01, 1, [SYM], sides='long' if side == 1 else 'short', tf='15m')]


@pytest.fixture
def inject():
    """Patches strategies.signals to one entry signal on candle SIG of the given 15m frame."""
    real = S.signals

    def arm(raw15, side=1):
        sig_t = raw15[SYM].t[SIG]

        def fake(key, d, ctx, params=None, mask_sides=True):
            out = {k: np.zeros(len(d), bool) for k in ('le', 'se', 'lx', 'sx')}
            out['le' if side == 1 else 'se'][:] = (pd.to_datetime(d.t) == sig_t).values
            return out
        S.signals = fake
    yield arm
    S.signals = real


def _same_minute(side=1, minute=3):
    """A lot entered on candle SIG+1 whose minute `minute` spans both its stop and its target."""
    m = _flat_minutes(300)
    t_e = _agg(m).t[SIG + 1]
    _set(m, t_e + pd.Timedelta(minutes=minute), 100.0, 103.0, 97.0, 100.0)
    return m, t_e


def _ms(t):
    return int(pd.Timestamp(t).value // 10 ** 6)


def _floats(x, path=''):
    if isinstance(x, float):
        yield path
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from _floats(v, f'{path}.{k}')
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _floats(v, path + '[]')


@pytest.mark.parametrize('side,minute', [(1, 3), (-1, 3), (1, 0), (-1, 14)])
def test_same_minute_segment_is_disclosed_with_minute_side_and_exit(inject, side, minute):
    m, t_e = _same_minute(side, minute)
    raw = {SYM: _agg(m)}
    inject(raw, side)
    res = D.disclose(raw, {SYM: m}, _sleeves(side), T0, start=500.0)
    s = res['symbols'][SYM]
    assert res['unresolved'] == s['unresolved'] == 1 and res['by_label'][X.SAME_MINUTE] == 1
    seg, = s['segments']
    assert seg['label'] == X.SAME_MINUTE and seg['segment_open_ms'] == _ms(t_e)
    assert seg['minute_open_ms'] == _ms(t_e + pd.Timedelta(minutes=minute))
    assert seg['side'] == ('LONG' if side == 1 else 'SHORT')
    assert seg['overlaps_closed_trade_exit'] is True and seg['exit_kinds'] == ['stop']
    assert s['trades'] == res['trades'] == 1 and s['unresolved_share'] == 1.0


def test_index_is_the_position_in_the_reference_label_log(inject):
    m, t_e = _same_minute()
    raw = {SYM: _agg(m)}
    inject(raw)
    _, _, labels = X._engine(raw, {SYM: m}, _sleeves(), T0, start=500.0, rules=None, settings=None, latency_s=0)
    res = D.disclose(raw, {SYM: m}, _sleeves(), T0, start=500.0)
    seg, = res['symbols'][SYM]['segments']
    assert labels[seg['index']] == (SYM, _ms(t_e), X.SAME_MINUTE)


def test_missing_minutes_are_no_1m_with_no_spanning_minute(inject):
    m = _flat_minutes(300)
    t_e = _agg(m).t[SIG + 1]
    _set(m, t_e + pd.Timedelta(minutes=3), 100.0, 103.0, 97.0, 100.0)
    a = _agg(m)                                           # the 15m candle keeps the full range...
    m = m[m.t != t_e + pd.Timedelta(minutes=5)].reset_index(drop=True)   # ...its 1m data does not
    raw = {SYM: a}
    inject(raw)
    res = D.disclose(raw, {SYM: m}, _sleeves(), T0, start=500.0)
    seg, = res['symbols'][SYM]['segments']
    assert seg['label'] == X.NO_1M and seg['minute_open_ms'] is None and seg['segment_open_ms'] == _ms(t_e)
    assert seg['overlaps_closed_trade_exit'] is True


def test_spy_is_decision_neutral_and_always_removed(inject):
    m, _ = _same_minute()
    raw = {SYM: _agg(m)}
    inject(raw)
    real = X.bar_path
    _, f0, l0 = X._engine(copy.deepcopy(raw), {SYM: m}, _sleeves(), T0, start=500.0, rules=None, settings=None,
                          latency_s=0)
    _, f1, l1, ctx = D.reference_segments(copy.deepcopy(raw), {SYM: m}, _sleeves(), T0, start=500.0, rules=None,
                                          settings=None, latency_s=0)
    assert f1 == f0 and l1 == l0 and len(ctx) == sum(x[2] != X.NO_LATENCY for x in l0)
    assert X.bar_path is real

    def boom(*a, **k):
        raise RuntimeError('replay failed')
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(X, '_engine', boom)
        with pytest.raises(RuntimeError):
            D.reference_segments(raw, {SYM: m}, _sleeves(), T0, start=500.0, rules=None, settings=None, latency_s=0)
    assert X.bar_path is real


def _cal():
    import json
    with open(os.path.join(ROOT, 'research_evidence', 'prereg', 'trend_ema_mom.v1.json'), encoding='utf-8') as fh:
        return json.load(fh)['costs']['slip_cal_v1']


def test_reproduces_run_cell_aggregates_and_refuses_a_packet_it_does_not(inject):
    m, _ = _same_minute()
    raw = {SYM: _agg(m)}
    inject(raw)
    pk = X.run_cell(raw, {SYM: m}, _sleeves(), T0, model=C.CostModel(_cal(), {SYM: 'crypto'}), start=500.0)
    res = D.disclose(raw, {SYM: m}, _sleeves(), T0, start=500.0, packet=pk)
    assert (res['segments'], res['by_label'], res['unresolved']) == \
        (pk['intrabar']['segments'], pk['intrabar']['by_label'], pk['intrabar']['unresolved'])
    assert res['unresolved_share'] == pk['intrabar']['unresolved_share']
    bad = copy.deepcopy(pk)
    bad['intrabar']['unresolved'] += 1
    with pytest.raises(D.DisclosureError, match='does not reproduce'):
        D.disclose(raw, {SYM: m}, _sleeves(), T0, start=500.0, packet=bad)


def test_output_is_price_free(inject):
    m, _ = _same_minute()
    raw = {SYM: _agg(m)}
    inject(raw)
    res = D.disclose(raw, {SYM: m}, _sleeves(), T0, start=500.0)
    assert {p.rsplit('.', 1)[-1] for p in _floats(res)} <= {'unresolved_share'}
    seg = res['symbols'][SYM]['segments'][0]
    assert set(seg) == {'index', 'segment_open_ms', 'minute_open_ms', 'label', 'side', 'overlaps_closed_trade_exit',
                        'exit_kinds'}


def test_label_log_and_spy_calls_must_pair():
    ctx = [dict(open_ms=1, label=X.SAME_MINUTE, side='LONG', minute_open_ms=1)]
    assert D._pair([(SYM, 0, X.NO_LATENCY), (SYM, 1, X.SAME_MINUTE)], ctx)[1][4] is ctx[0]
    with pytest.raises(D.DisclosureError, match='does not match'):
        D._pair([(SYM, 2, X.SAME_MINUTE)], ctx)
    with pytest.raises(D.DisclosureError, match='without a label'):
        D._pair([], ctx)


def test_existing_runtime_and_harness_files_are_untouched():
    import subprocess
    base = 'd2c158c4aeb1f87527c7030e9367d5f4532850b9'
    out = subprocess.run(['git', 'diff', '--name-only', base, '--'], cwd=ROOT, capture_output=True, text=True, check=True)
    changed = {p for p in out.stdout.split() if p}
    assert changed <= {'tools/research/intrabar_disclosure.py', 'tests/test_intrabar_disclosure.py'}
