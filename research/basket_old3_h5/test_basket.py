"""Synthetic tests for BASKET-OLD3-H5 (no market data, no outcomes)."""
import os, sys, tempfile
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import basket as K
import feasibility as F, strategies as S

H1 = pd.Timedelta(hours=1)
T0 = pd.Timestamp('2025-03-01 00:00')


def mklot(sleeve, sym, side, t_in, hours, qty=1.0, e=100.0, risk=8.0, exits=None, closed=True, tag=''):
    t_out = t_in + hours * H1
    ex = exits if exits is not None else [dict(t=t_out, ref=e, qty=qty, i=0, why='x', gap=False)]
    return dict(sleeve=sleeve, sym=sym, side=side, t_in=t_in, t_out=t_out if closed else K.END, e_fill=e, qty=qty,
                risk_usd=risk, exits=ex, closed=closed, id=f'{sleeve}|{sym}|{t_in.isoformat()}{tag}', ref_in=e)


def closes(px=100.0, t0=T0 - 48 * H1, n=24 * 40, syms=K.COINS):
    return {s: {t0 + k * H1: px for k in range(n)} for s in syms}


def run(lots, book='COMBINED', seed=0, c=None, cost=7, t0=T0, n=24 * 10, monkey=None):
    old = (K.START, K.END)
    K.START, K.END = t0, t0 + n * H1
    try:
        return K.replay(lots, book, seed, c or closes(), cost, [t0 + k * H1 for k in range(n)])
    finally:
        K.START, K.END = old


def status(res):
    return {r['id']: (r['status'], r['reason']) for r in res['records']}


def test_to_4h_complete_groups_only():
    t = pd.date_range('2024-10-11 17:00', periods=12, freq='h')
    d = pd.DataFrame(dict(t=t, o=np.arange(12.), h=np.arange(12.) + 1, l=np.arange(12.) - 1, c=np.arange(12.) + .5,
                          v=1., qv=2.))
    a = K.to_4h(d)
    assert list(a.t) == [pd.Timestamp('2024-10-11 20:00'), pd.Timestamp('2024-10-12 00:00')]
    r = a.iloc[0]
    assert r.o == 3 and r.c == 6.5 and r.h == 7 and r.l == 2 and r.v == 4 and r.qv == 8
    d2 = d.drop(index=9)                                     # 02:00 missing -> 00:00 group incomplete
    assert list(K.to_4h(d2).t) == [pd.Timestamp('2024-10-11 20:00')]


def test_load_cutoff_drops_rows_at_or_after_end():
    dd = tempfile.mkdtemp()
    t = pd.date_range(K.END - 3 * H1, periods=6, freq='h')
    for s in K.COINS:
        pd.DataFrame(dict(t=t.strftime('%Y-%m-%d %H:%M:%S'), o=1., h=1., l=1., c=1., v=1., qv=1.)).to_csv(
            os.path.join(dd, f'{s}.csv'), index=False, encoding='utf-8')
    r = K.load_1h(dd)
    assert r['BTCUSDT'].t.max() == K.END - H1 and len(r['BTCUSDT']) == 3


def test_fixed_sizing_qty_cap_and_restore():
    orig = F.risk_qty
    m = dict(stop_atr=2.0)
    with K.fixed_sizing():
        z = F.risk_qty(m, 123456.0, 100.0, 1.0, 1)          # risk_usd ignored: 8 / (2*1) = 4 units, notional 400 < cap
        assert abs(z['qty'] - 4.0) < 1e-12
        z = F.risk_qty(m, 1.0, 100.0, 0.01, 1)              # 8/0.02 = 400 units = 40,000 notional -> capped 33.3333
        assert abs(z['qty'] - K.CAP_NOTIONAL / 100.0) < 1e-12
    assert F.risk_qty is orig
    try:
        with K.fixed_sizing(): F.risk_qty(dict(stop_atr=2, dca=dict(n=3)), 1, 100, 1, 1)
        assert False
    except ValueError:
        pass


def test_target_cash_partial_funding_and_fees():
    c = closes(px=110.0)
    ex = [dict(t=T0 + 2 * H1, ref=120.0, qty=0.5, i=0, why='tp1', gap=False),
          dict(t=T0 + 4 * H1, ref=90.0, qty=0.5, i=0, why='stop', gap=False)]
    l = mklot('HOT', 'BTCUSDT', 1, T0, 4, qty=1.0, e=100.0, exits=ex)
    ev = K.target_cash_events(l, c, 15)
    fee = 13e-4
    assert ev[0] == (T0, 'entry_fee', -100.0 * fee)
    fund = [a for t, k, a in ev if k == 'funding']
    assert len(fund) == 4 and abs(fund[0] + 110 * K.FUND_H) < 1e-15 and abs(fund[2] + 0.5 * 110 * K.FUND_H) < 1e-15
    x1 = 120 * (1 - K.SLIP); x2 = 90 * (1 - K.SLIP)
    exits = [a for t, k, a in ev if k == 'exit']
    assert abs(exits[0] - ((x1 - 100) * .5 - .5 * x1 * fee)) < 1e-12 and abs(exits[1] - ((x2 - 100) * .5 - .5 * x2 * fee)) < 1e-12
    s = K.target_cash_events(mklot('BEAR', 'ETHUSDT', -1, T0, 1, e=100.0, exits=[dict(t=T0 + H1, ref=95.0, qty=1.0)]), c, 7)
    xs = 95 * (1 + K.SLIP)
    assert abs([a for _, k, a in s if k == 'exit'][0] - ((100 - xs) - xs * 5e-4)) < 1e-12


def test_seed0_order_and_shuffle_deterministic():
    sl, co = K.orders(0)
    assert sl == ['BEAR', 'EMA', 'HOT', 'H5'] and co == sorted(K.COINS)
    assert K.orders(7) == K.orders(7) and K.orders(7) != K.orders(8)


def test_sleeve_cap_own_coin_phantom_and_exit_first():
    a = mklot('H5', 'BTCUSDT', 1, T0 + H1, 5)
    b = mklot('H5', 'ETHUSDT', 1, T0 + 2 * H1, 5)            # H5 cap 1 -> refused, phantom until its exit
    c2 = mklot('H5', 'SOLUSDT', 1, T0 + 6 * H1, 2)           # a exits at T0+6h, same timestamp -> exit first -> admitted
    r = run([a, b, c2])
    st = status(r)
    assert st[a['id']][0] == 'admitted' and st[b['id']] == ('refused', 'sleeve_position_cap')
    assert st[c2['id']][0] == 'admitted' and r['phantom_hours'] == 5


def test_aggregate_open_risk_cap_and_partial_keeps_risk():
    lots = [mklot('EMA', s, 1, T0 + H1, 10, risk=30.0) for s in ('ADAUSDT', 'BTCUSDT')]
    lots.append(mklot('HOT', 'ETHUSDT', 1, T0 + 2 * H1, 10, risk=30.0,
                      exits=[dict(t=T0 + 3 * H1, ref=100.0, qty=0.5), dict(t=T0 + 12 * H1, ref=100.0, qty=0.5)]))
    lots.append(mklot('BEAR', 'SOLUSDT', -1, T0 + 4 * H1, 3, risk=15.0))  # 60+30=90 open after partial; +15 > 100 (2.5%)
    st = status(run(lots))
    assert st[lots[3]['id']] == ('refused', 'aggregate_entry_risk_cap')


def test_stacking_and_opposite_side_coexist():
    lots = [mklot('EMA', 'BTCUSDT', 1, T0 + H1, 5), mklot('HOT', 'BTCUSDT', 1, T0 + H1, 5),
            mklot('H5', 'BTCUSDT', 1, T0 + H1, 5), mklot('BEAR', 'BTCUSDT', -1, T0 + H1, 5)]
    r = run(lots)
    assert all(v[0] == 'admitted' for v in status(r).values())


def test_cairo_day_halt_realized_net_and_dst_midnight():
    t0 = pd.Timestamp('2025-04-24 18:00')                    # Cairo DST starts 2025-04-25 00:00 local (UTC+3 after)
    c = closes(t0=t0 - 48 * H1)
    loss = mklot('EMA', 'BTCUSDT', 1, t0 + H1, 2, qty=1.0, e=100.0, exits=[dict(t=t0 + 3 * H1, ref=-20.0, qty=1.0)])
    # loss realized 21:00 UTC = 23:00 Cairo (UTC+2) on 24 Apr: -120 -> -3% of 4000 -> halt
    late = mklot('HOT', 'ETHUSDT', 1, t0 + 4 * H1, 1)          # 22:00 UTC = 00:00 Cairo 25 Apr (UTC+2 midnight) -> new day
    same = mklot('BEAR', 'SOLUSDT', -1, t0 + 3 * H1, 1, tag='s')  # 21:00 UTC, after the exit settles -> halted
    r = run([loss, late, same], c=c, t0=t0, n=24)
    st = status(r)
    assert st[same['id']] == ('refused', 'cairo_daily_loss_halt') and st[late['id']][0] == 'admitted'


def test_no_future_cash_in_admission():
    big = mklot('EMA', 'BTCUSDT', 1, T0 + H1, 3, qty=1.0, e=100.0, exits=[dict(t=T0 + 4 * H1, ref=-3000.0, qty=1.0)])
    nxt = mklot('HOT', 'ETHUSDT', 1, T0 + 3 * H1, 1, risk=8.0)   # before the loss realizes: equity still ~4000
    st = status(run([big, nxt]))
    assert st[nxt['id']][0] == 'admitted'


def test_terminal_mark_and_open_lot_not_a_close():
    c = closes(px=105.0)
    l = mklot('H5', 'BTCUSDT', 1, T0 + H1, 1000, qty=2.0, e=100.0, exits=[], closed=False)
    l['t_out'] = T0 + 10 * H1                                # = END inside run()
    r = run([l], c=c, n=10)
    assert r['closes'] == 0 and r['admitted'] == 1 and abs(r['open_mark'] - 10.0) < 1e-9


def test_marked_dd_counts_partial_once():
    c = closes(px=110.0)                                     # partial realized at 110: cash +5 must not ALSO stay marked
    ex = [dict(t=T0 + 2 * H1, ref=110.0, qty=0.5), dict(t=T0 + 5 * H1, ref=110.0, qty=0.5)]
    r = run([mklot('HOT', 'BTCUSDT', 1, T0 + H1, 4, qty=1.0, e=100.0, exits=ex)], c=c, n=8, cost=2)
    assert r['marked_dd_pct'] < 0.01                          # double counting would show ~0.12% DD


def _synthetic_book(n=400, px0=100.0, seed=1):
    rng = np.random.default_rng(seed)
    t = pd.date_range(T0 - 300 * H1, periods=n, freq='h')
    out = {}
    for s in ('BTCUSDT', 'ETHUSDT'):
        c = px0 * np.exp(np.cumsum(rng.normal(0, .004, n)))
        o = np.r_[c[0], c[:-1]]
        out[s] = pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) * 1.003, l=np.minimum(o, c) * .997, c=c, v=1.0, qv=100.0))
    return out


def _test_strategy(every=17):
    def fn(d, p, ctx):
        le = pd.Series(False, index=d.index); le.iloc[::every] = True
        f = pd.Series(False, index=d.index)
        return dict(le=le, se=f, lx=f.copy(), sx=f.copy())
    return dict(name='t', desc='t', style='t', fn=fn, sides='long', tf='1h', params={},
                mgmt=dict(stop_atr=1.0, tp1_r=0.5, tp1_frac=0.5, trail_atr=1.0))


def _native(rules, every=17, px0=100.0):
    S.STRATEGIES['zz_test'] = _test_strategy(every)
    old = (K.START, K.END)
    K.START, K.END = T0, T0 + 99 * H1
    try:
        raw = {s: d[d.t < K.END].reset_index(drop=True) for s, d in _synthetic_book(px0=px0).items()}
        return K.native_tape(raw, ('T', 'zz_test', 'long', '1h', 2), 15, 'path', rules, '1h')
    finally:
        K.START, K.END = old; S.STRATEGIES.pop('zz_test', None)


def test_native_tape_identity_start_flat_partial_at_actual_qty():
    rules = {s: dict(step=0.001, min_qty=0.001, min_notional=5.0) for s in ('BTCUSDT', 'ETHUSDT')}
    lots, feas, T = _native(rules)
    assert lots and all(l['t_in'] > T0 for l in lots)        # flat at START: first entry strictly after the first bar
    assert all(l['t_in'] < T0 + 99 * H1 for l in lots)
    for l in lots:
        assert l['qty'] * l['e_fill'] <= K.CAP_NOTIONAL + 1e-9
        assert abs(l['qty'] - F.round_step(l['qty'], 0.001)) < 1e-12
        part = [x for x in l['exits'] if x['why'] == 'tp1']
        for x in part: assert abs(x['qty'] - F.round_step(l['qty'] * 0.5, 0.001)) < 1e-12
    assert any(any(x['why'] == 'tp1' for x in l['exits']) for l in lots)
    assert any(l['closed'] for l in lots)                     # identity check (cash - native funding == native pnl) ran


def test_native_blocked_partial_follows_engine_at_actual_qty():
    rules = {s: dict(step=1.0, min_qty=1.0, min_notional=5.0) for s in ('BTCUSDT', 'ETHUSDT')}
    lots, feas, T = _native(rules, px0=1000.0)                # qty ~1.3 -> floored 1; tp1 half floors to 0 -> blocked
    assert lots and all(l['qty'] == 1.0 for l in lots)
    assert not any(x['why'] == 'tp1' for l in lots for x in l['exits'])
    assert all(abs(sum(x['qty'] for x in l['exits']) - 1.0) < 1e-12 for l in lots if l['closed'])


def test_lead_label_requires_all_conditions():
    def cell(net, e, l): return dict(net_usd=net, early_usd=e, later_usd=l)
    cells = {}
    for p in K.PATHS:
        for s in range(K.N_ORDERS):
            cells[('OLD3', 15, p, s)] = cell(10, 5, 5); cells[('H5', 15, p, s)] = cell(5, 2, 3)
            cells[('COMBINED', 15, p, s)] = cell(20, 10, 10); cells[('COMBINED', 23, p, s)] = cell(1, 1, 0)
    assert K.lead_label(cells)[0] == 'PORTFOLIO_RESEARCH_LEAD'
    cells[('COMBINED', 15, 'worst', 3)] = cell(20, -1, 21)
    assert K.lead_label(cells)[0] == 'NOT_A_LEAD'


if __name__ == '__main__':
    for k, f in sorted(globals().items()):
        if k.startswith('test_'): f(); print('PASS', k)
