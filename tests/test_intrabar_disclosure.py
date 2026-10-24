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
    pk, ident = _packet(raw, m)
    res = D.disclose(raw, {SYM: m}, _sleeves(), T0, start=500.0, packet=pk, identity=ident)
    assert (res['segments'], res['by_label'], res['unresolved']) == \
        (pk['intrabar']['segments'], pk['intrabar']['by_label'], pk['intrabar']['unresolved'])
    assert res['unresolved_share'] == pk['intrabar']['unresolved_share']
    bad = copy.deepcopy(pk)
    bad['intrabar']['unresolved'] += 1
    with pytest.raises(D.DisclosureError, match='does not reproduce'):
        D.disclose(raw, {SYM: m}, _sleeves(), T0, start=500.0, packet=bad, identity=ident)


def _packet(raw, m):
    """A run_cell packet with main()'s identity fields, and the identity the extractor's main() would request."""
    pk = X.run_cell(raw, {SYM: m}, _sleeves(), T0, model=C.CostModel(_cal(), {SYM: 'crypto'}), start=500.0)
    ident = dict(key='exp_scalp', side='long', window='2025-01-06/2025-01-10', symbols=[SYM], latency_s=0,
                 config_sha256=X.CONFIG_SHA256, config_digest=X.config_digest(_sleeves()), manifest_digest='m' * 64,
                 static_digest='s' * 64, code=X.code_ids())
    pk.update({k: ident[k] for k in ('key', 'side', 'window', 'symbols', 'config_sha256', 'manifest_digest',
                                     'static_digest')})
    assert all(pk[k] == ident[k] for k in D.IDENTITY)    # latency_s, config_digest and code come from run_cell itself
    return pk, ident


@pytest.mark.parametrize('field,other', [('key', 'range_scalp'), ('side', 'both'), ('window', '2025-01-06/2025-01-11'),
                                         ('symbols', [SYM, 'XRPUSDT']), ('latency_s', 60), ('config_sha256', '0' * 64),
                                         ('config_digest', '0' * 64), ('manifest_digest', '0' * 64),
                                         ('static_digest', '0' * 64), ('code', {'engine.py': '0' * 40})])
def test_a_packet_of_another_run_with_equal_counts_is_refused(inject, field, other):
    m, _ = _same_minute()
    raw = {SYM: _agg(m)}
    inject(raw)
    pk, ident = _packet(raw, m)
    bad = dict(pk, **{field: other})                    # every count still equal
    with pytest.raises(D.DisclosureError, match=f"identity differs.*'{field}'"):
        D.disclose(raw, {SYM: m}, _sleeves(), T0, start=500.0, packet=bad, identity=ident)
    with pytest.raises(D.DisclosureError, match='needs the requested run identity'):
        D.disclose(raw, {SYM: m}, _sleeves(), T0, start=500.0, packet=pk)


def test_exit_overlap_is_side_specific():
    t = 1_000 * X.TF_MS
    ex = {(SYM, 'SHORT'): [(t + 60_000, 'stop')]}
    assert D.exit_overlap(ex, SYM, 'LONG', t) == []                       # only the opposite side exited there
    assert D.exit_overlap(ex, SYM, 'SHORT', t) == ['stop']
    assert D.exit_overlap(ex, SYM, 'BOTH', t) == ['stop']                 # two-sided context: deliberate union
    ex[(SYM, 'LONG')] = [(t, 'tp'), (t + X.TF_MS, 'stop')]                # candle open counts, next candle does not
    assert D.exit_overlap(ex, SYM, 'LONG', t) == ['tp']
    assert D.exit_overlap(ex, SYM, 'BOTH', t) == ['stop', 'tp']
    assert D.exit_overlap(ex, 'XRPUSDT', 'BOTH', t) == []


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


def test_exits_are_keyed_by_the_trade_side():
    t = 1_000 * X.TF_MS
    trades = [dict(sym=SYM, ps='SHORT', closes=[dict(t_ms=t + 60_000, kind='stop')]),
              dict(sym=SYM, ps='LONG', closes=[dict(t_ms=t + 2 * X.TF_MS, kind='tp'), dict(t_ms=t + 3 * X.TF_MS,
                                                                                        kind='stop')])]
    ex = D.exits_by_side(trades)
    assert ex == {(SYM, 'SHORT'): [(t + 60_000, 'stop')], (SYM, 'LONG'): [(t + 2 * X.TF_MS, 'tp'),
                                                                         (t + 3 * X.TF_MS, 'stop')]}
    assert D.exit_overlap(ex, SYM, 'LONG', t) == []       # a LONG segment is not flagged by the SHORT trade's exit



def test_disclose_does_not_flag_a_segment_by_the_opposite_sides_exit(inject, monkeypatch):
    m, t_e = _same_minute()
    raw = {SYM: _agg(m)}
    inject(raw)
    real = X.trades_of
    monkeypatch.setattr(X, 'trades_of', lambda *a, **k: [dict(t, ps='SHORT') for t in real(*a, **k)])
    seg, = D.disclose(raw, {SYM: m}, _sleeves(), T0, start=500.0)['symbols'][SYM]['segments']
    assert seg['side'] == 'LONG' and seg['overlaps_closed_trade_exit'] is False and seg['exit_kinds'] == []


def test_a_two_sided_held_set_is_disclosed_as_both_and_its_overlap_unions_both_sides():
    assert D._side({1}) == 'LONG' and D._side({-1}) == 'SHORT' and D._side({1, -1}) == 'BOTH'
    t = 1_000 * X.TF_MS
    ex = D.exits_by_side([dict(sym=SYM, ps='LONG', closes=[dict(t_ms=t + 60_000, kind='tp')]),
                          dict(sym=SYM, ps='SHORT', closes=[dict(t_ms=t + 120_000, kind='stop')])])
    assert D.exit_overlap(ex, SYM, D._side({1, -1}), t) == ['stop', 'tp']


@pytest.mark.parametrize('field', ['key', 'static_digest', 'code'])
def test_an_identity_field_missing_on_either_side_is_refused_even_if_both_are_none(field):
    full = {k: None for k in D.IDENTITY}
    D.check_identity(dict(full), dict(full))                             # all present: equal None is equal
    for pk, ident in ((full, {k: v for k, v in full.items() if k != field}),
                      ({k: v for k, v in full.items() if k != field}, full),
                      ({k: v for k, v in full.items() if k != field}, {k: v for k, v in full.items() if k != field})):
        with pytest.raises(D.DisclosureError, match=f"identity incomplete.*'{field}'"):
            D.check_identity(pk, ident)
