"""RES-01 R3: PIT view + holdout guard (tools/research/pit.py), costs + slip-v1 (costs.py), splits (splits.py), the 1m
intrabar resolver (intrabar.py) and the `zb-research-run/1` envelope (report.py). Small synthetic stores and bars only
(no network, no real data, no strategy, no returns)."""
import ast
import glob
import hashlib
import io
import json
import os
import random
import sys
import zipfile
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import costs as C                                                                           # noqa: E402
import intrabar as I                                                                        # noqa: E402
import ledger as L                                                                          # noqa: E402
import manifest as M                                                                        # noqa: E402
import pit as P                                                                             # noqa: E402
import report as R                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402
import universe as U                                                                        # noqa: E402

MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000
H4 = 4 * HOUR


def ms(s):
    return S.parse_utc(s if 'T' in s else s + 'T00:00:00Z')


def px(t):
    return 100 + ((t // MIN) % 97) * 0.05


def kl(t, step):
    o, c = px(t), px(t + step - MIN)
    return f'{t},{o},{max(o, c) + 1},{min(o, c) - 1},{c},10,{t + step - 1},{1000.0 + (t // step) % 7},5,4,1,0'


def put(store, rel, lines):
    p = os.path.join(store, *rel.split('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(rel.rsplit('/', 1)[1][:-4] + '.csv', '\n'.join(lines) + '\n')
    raw = buf.getvalue()
    with open(p, 'wb') as f:
        f.write(raw)
    with open(p + '.ok', 'w') as f:
        f.write(hashlib.sha256(raw).hexdigest())


MONTHS = [(2024, 1), (2024, 2), (2024, 3)]


def month_range(y, m):
    a = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return a, int(datetime(y + m // 12, m % 12 + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def build_store(store):
    for sym in ('AAAUSDT', 'XAUUSDT'):
        for y, m in MONTHS:
            a, b = month_range(y, m)
            tag = f'{y}-{m:02d}'
            for iv, step in (('1d', DAY), ('4h', H4)):
                put(store, f'um/monthly/klines/{sym}/{iv}/{sym}-{iv}-{tag}.zip', [kl(t, step) for t in range(a, b, step)])
            if not (sym == 'XAUUSDT' and m == 3):            # XAU: no mark bars in March (join gap test)
                put(store, f'um/monthly/markPriceKlines/{sym}/1h/{sym}-1h-{tag}.zip',
                    [kl(t, HOUR) for t in range(a, b, HOUR)])
            rate = 0.0001 if sym == 'AAAUSDT' else -0.0002
            put(store, f'um/monthly/fundingRate/{sym}/{sym}-fundingRate-{tag}.zip',
                ['calc_time,funding_interval_hours,last_funding_rate'] +
                [f'{t + 3},8,{rate}' for t in range(a, b, 8 * HOUR)])
    a, b = month_range(2024, 2)
    put(store, f'um/monthly/klines/AAAUSDT/1m/AAAUSDT-1m-2024-02.zip', [kl(t, MIN) for t in range(a, b, MIN)])


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    store = str(tmp_path_factory.mktemp('store'))
    build_store(store)
    m = M.build(['um/monthly'], manifest_id='fx-r3', source_class='archive-verified', base=store,
                data_root='binance_um', survivor_only=False, loader=M.ARCHIVE_LOADER)
    u = U.build(U.load_daily(m, store), manifest_digest=m['digest'])
    return store, m, u


def ds_of(world):
    store, m, u = world
    return P.Dataset(m, store, u)


PLAN_SPLITS = [{'name': 'train', 'start': '2024-02-05T00:00:00Z', 'end': '2024-02-19T00:00:00Z'},
               {'name': 'walk_forward', 'fold': 0, 'start': '2024-02-19T00:00:00Z', 'end': '2024-03-04T00:00:00Z'},
               {'name': 'holdout', 'start': '2024-03-04T00:00:00Z', 'end': '2024-03-25T00:00:00Z'}]


def plan():
    return S.SplitPlan(PLAN_SPLITS, interval='4h', lookback_bars=15, horizon_bars=6)


def dirs(tmp_path):
    (tmp_path / 'ledger').mkdir()
    (tmp_path / 'runs').mkdir()
    return str(tmp_path / 'ledger' / 'fam_x.jsonl'), str(tmp_path / 'runs')


def access(ds, path, pl='default'):
    return P.Access(ds, plan() if pl == 'default' else pl, path, candidate_id='c.v1', author='t', cairo_date='2026-10-09')


# ------------------------------------------------------------------ PIT view
def test_view_serves_only_closed_bars_available_at_t(world, tmp_path):
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    t = ms('2024-02-12') + 2 * H4                                  # a 4h close
    bars = w.view(t).bars('AAAUSDT', '4h', 20)
    assert len(bars) == 20 and bars[-1].available_ms == t and all(b.available_ms <= t for b in bars)
    assert w.view(t - 1).bars('AAAUSDT', '4h', 1)[-1].available_ms == t - H4      # the bar closing at t is not yet out
    assert bars[0].open_ms >= w.lo                                                  # never reads before the split
    with pytest.raises(P.PITError, match='outside the opened window'):
        w.view(w.hi + 1)
    with pytest.raises(AttributeError):
        w.view(t).t = t + H4


def test_future_perturbation_cannot_change_a_view_and_a_peeking_control_is_caught(world, tmp_path):
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    rnd = random.Random(7)
    lo, hi = plan().decision_range('train')
    times = sorted({lo + rnd.randrange((hi - lo) // H4 + 1) * H4 for _ in range(40)})

    def honest(v):
        b = v.bars('AAAUSDT', '4h', 15)
        return (b[-1].close > sum(x.close for x in b) / 15, round(C.slip_bps(b, 0.05), 9))

    def peeking(v):                                       # negative control: bypasses the view to read the raw rows
        rows = [r for f in ds.files('klines', 'AAAUSDT', '4h') for r in ds.rows(f)]
        nxt = [r for r in rows if r.open_ms == v.t]
        return nxt[0].close if nxt else None

    files = ds.files('klines', 'AAAUSDT', '4h')
    caught = 0
    for t in times:
        before = (honest(w.view(t)), peeking(w.view(t)))
        saved = {f['path']: list(ds.rows(f)) for f in files}
        for f in files:                                    # perturb every bar not yet closed at t
            rows = ds.rows(f)
            for i, r in enumerate(rows):
                if r.available_ms > t:
                    rows[i] = r._replace(close=r.close * 3, high=r.high * 3)
        assert honest(w.view(t)) == before[0]
        caught += peeking(w.view(t)) != before[1]
        for f in files:
            ds.rows(f)[:] = saved[f['path']]
    assert caught == len(times)                            # the peeking control is detected at every decision


def test_truncation_rerun_reproduces_every_decision(world, tmp_path):
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    acc = access(ds, path, pl=None)
    full = acc.open_development('2024-02-05T00:00:00Z', '2024-03-04T00:00:00Z', lineage='root')
    rnd = random.Random(11)
    for _ in range(12):
        t = full.lo + (15 + rnd.randrange(100)) * H4
        trunc = acc.open_development('2024-02-05T00:00:00Z', S.utc(t))
        assert full.view(t).bars('AAAUSDT', '4h', 15) == trunc.view(t).bars('AAAUSDT', '4h', 15)
    recs = L._check_state(path)['fam_x']
    assert len(recs) == 13 and {r['split'] for r in recs} == {'development'}


def test_membership_is_point_in_time_and_gold_is_served(world, tmp_path):
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    acc = access(ds, path, pl=None)
    early = acc.open_development('2024-01-02T00:00:00Z', '2024-01-20T00:00:00Z', lineage='root')
    with pytest.raises(P.PITError, match='not a universe member'):
        early.view(early.hi).bars('AAAUSDT', '4h', 2)              # no ranking before the first Monday
    w = acc.open_development('2024-02-05T00:00:00Z', '2024-03-01T00:00:00Z')
    v = w.view(w.hi)
    assert {'AAAUSDT', 'XAUUSDT'} <= v.members()
    assert len(v.bars('XAUUSDT', '4h', 5)) == 5
    with pytest.raises(P.PITError, match='not a universe member'):
        v.bars('BTCUSDT', '4h', 1)


def test_funding_events_timestamp_rule_and_mark_join(world, tmp_path):
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path, pl=None).open_development('2024-02-05T00:00:00Z', '2024-03-20T00:00:00Z', lineage='root')
    t0 = ms('2024-02-12') + 3                                            # a funding calc_time
    v = w.view(ms('2024-02-14'))
    ev = v.funding_events('AAAUSDT', t0, t0 + 16 * HOUR)            # entry == T counts, exit == T does not
    assert [e[0] for e in ev] == [t0, t0 + 8 * HOUR]
    assert ev[0][2] == px(t0 - 3 - MIN)                                 # close of the 1h mark bar closing at T
    long_ = C.funding_cost('long', 2.0, ev, C.STRESS['base'])
    short = C.funding_cost('short', 2.0, ev, C.STRESS['base'])
    assert long_ > 0 and short == -long_                                # longs pay a positive rate, shorts receive
    assert C.funding_cost('long', 2.0, ev, C.STRESS['funding_x2']) == pytest.approx(2 * long_)
    gold = v.funding_events('XAUUSDT', t0, t0 + 9 * HOUR)
    assert C.funding_cost('long', 1.0, gold, C.STRESS['base']) < 0     # negative rate: longs receive
    vm = w.view(ms('2024-03-19'))
    with pytest.raises(P.PITError, match='gap > 1 bar'):
        vm.funding_events('XAUUSDT', ms('2024-03-11'), ms('2024-03-12'))


def test_dataset_fails_closed_on_changed_bytes(world, tmp_path):
    store, m, u = world
    import shutil
    s2 = str(tmp_path / 's2')
    shutil.copytree(store, s2)
    rel = 'um/monthly/klines/AAAUSDT/4h/AAAUSDT-4h-2024-02.zip'
    a, b = month_range(2024, 2)
    put(s2, rel, [kl(t, H4) for t in range(a, b - H4, H4)])               # republished with its own matching .ok
    ds = P.Dataset(m, s2, u)
    path, _ = dirs(tmp_path)
    w = access(ds, path).open('train', lineage='root')
    with pytest.raises(P.PITError, match='no longer match'):
        w.view(w.hi).bars('AAAUSDT', '4h', 3)


def test_window_cannot_be_built_without_access(world):
    with pytest.raises(P.PITError, match='only through Access'):
        P.Window(ds_of(world), 0, 1, 'x')


def test_universe_must_match_manifest(world):
    store, m, u = world
    bad = dict(u, manifest_digest='0' * 64)
    bad['digest'] = U.digest_of(bad)
    with pytest.raises(P.PITError, match='different manifest'):
        P.Dataset(m, store, bad)
    with pytest.raises(P.PITError, match='PIT universe'):
        P.Dataset(m, store, None)


# ------------------------------------------------------------------ splits + holdout guard
def test_split_plan_purge_embargo_and_episode_membership():
    p = plan()
    assert p.purge_ms == 15 * H4 and p.embargo_ms == H4 and p.horizon_ms == 6 * H4
    first, last = p.decision_range('train')
    assert first == ms('2024-02-05') + 16 * H4 and last == ms('2024-02-19') - 6 * H4
    assert p.split_of_episode(first, first + 6 * H4) == 'train'
    for e, x in ((first - H4, first), (last, last + 7 * H4), (last + H4, last + 2 * H4)):
        with pytest.raises(S.SplitError):
            p.split_of_episode(e, x)
    assert p.doc()['digest'] == p.digest == plan().digest
    with pytest.raises(AttributeError):
        p.horizon_ms = 1


@pytest.mark.parametrize('splits,kw,msg', [
    (PLAN_SPLITS, dict(horizon_bars=None), 'finite ex-ante'),
    (PLAN_SPLITS, dict(lookback_bars=10), 'lookback_bars'),
    (PLAN_SPLITS[::-1], {}, 'time order'),
    ([PLAN_SPLITS[0], dict(PLAN_SPLITS[1], start='2024-02-18T00:00:00Z'), PLAN_SPLITS[2]], {}, 'non-overlapping'),
    ([PLAN_SPLITS[2], dict(PLAN_SPLITS[0], start='2024-03-25T00:00:00Z', end='2024-04-08T00:00:00Z')], {}, 'last'),
    (PLAN_SPLITS[:2], {}, 'exactly one holdout'),
    ([dict(PLAN_SPLITS[0], start='2024-02-05T01:00:00Z')] + PLAN_SPLITS[1:], {}, 'align'),
    ([dict(PLAN_SPLITS[0], end='2024-02-08T00:00:00Z'), dict(PLAN_SPLITS[1], start='2024-02-08T00:00:00Z'),
      PLAN_SPLITS[2]], {}, 'no decision fits'),
    ([PLAN_SPLITS[0], dict(PLAN_SPLITS[1], fold=1), PLAN_SPLITS[2]], {}, 'numbered'),
])
def test_split_plan_refusals(splits, kw, msg):
    args = dict(interval='4h', lookback_bars=15, horizon_bars=6)
    args.update(kw)
    with pytest.raises(S.SplitError, match=msg):
        S.SplitPlan(splits, **args)


def make_env(ds, pl, *, dirty=False, family='fam_x', window=None, eval_d='e' * 64):
    run = {'family': family, 'candidate_id': 'c.v1', 'split': 'holdout', 'window': window or pl.window('holdout'),
           'manifest_digest': ds.digest, 'universe_digest': ds.universe_digest, 'split_plan_digest': pl.digest,
           'cost_model_digest': 'c' * 64, 'slip_cal_digest': 'd' * 64, 'eval_digest': eval_d, 'seeds': [1, 2],
           'code': {'git_head': 'a' * 40, 'dirty': dirty, 'python': '3.14', 'libs': {}},
           'author': 't', 'cairo_date': '2026-10-09'}
    return R.envelope(run)


def reveal(path, env, kind='holdout_reveal'):
    r = env['run']
    L.append(path, kind=kind, candidate_id=r['candidate_id'], split='holdout', window=r['window'], author='t',
             cairo_date='2026-10-09', manifest_digest=r['manifest_digest'], run_digest=env['run_digest'],
             detail={'eval_digest': r['eval_digest']})


def test_holdout_is_sealed_without_the_atomic_reveal(world, tmp_path):
    ds = ds_of(world)
    path, runs = dirs(tmp_path)
    pl = plan()
    acc = access(ds, path, pl)
    acc.open('train', lineage='root')                                   # the family exists, no reveal yet
    env = make_env(ds, pl)
    with pytest.raises(P.PITError, match='sealed'):
        acc.open('holdout')                                             # no envelope at all
    with pytest.raises(P.PITError, match='sealed'):
        acc.open('holdout', envelope=env)                               # envelope not frozen in the runs store
    R.write_envelope(runs, env)
    with pytest.raises(P.PITError, match='no atomic holdout_reveal'):
        acc.open('holdout', envelope=env)
    with pytest.raises(P.PITError, match='may not touch the sealed holdout'):
        acc.open_development('2024-03-01T00:00:00Z', '2024-03-05T00:00:00Z')
    reveal(path, env)
    w = acc.open('holdout', envelope=env)
    assert w.view(w.hi).bars('AAAUSDT', '4h', 3)
    recs = L._check_state(path)['fam_x']
    assert [r['kind'] for r in recs][-2:] == ['holdout_reveal', 'data_access'] and recs[-1]['split'] == 'holdout'
    acc.open('train')                                                   # any other record closes the reveal group
    with pytest.raises(P.PITError, match='group is closed'):
        acc.open('holdout', envelope=env)
    reveal(path, env, 'holdout_rerun')                                  # a deterministic rerun reopens it
    acc.open('holdout', envelope=env)


@pytest.mark.parametrize('mutate,msg', [
    (dict(dirty=True), 'sealable'),
    (dict(family='fam_y'), 'identity'),
    (dict(window={'start': '2024-03-04T00:00:00Z', 'end': '2024-03-18T00:00:00Z'}), 'identity'),
])
def test_holdout_guard_refuses_dirty_or_foreign_envelopes(world, tmp_path, mutate, msg):
    ds = ds_of(world)
    path, runs = dirs(tmp_path)
    pl = plan()
    acc = access(ds, path, pl)
    acc.open('train', lineage='root')
    env = make_env(ds, pl, **mutate)
    R.write_envelope(runs, env)
    with pytest.raises(P.PITError, match=msg):
        acc.open('holdout', envelope=env)


def test_ledger_recomputes_run_digest_from_the_envelope(world, tmp_path):
    ds = ds_of(world)
    path, runs = dirs(tmp_path)
    pl = plan()
    access(ds, path, pl).open('train', lineage='root')
    env = make_env(ds, pl)
    with pytest.raises(L.LedgerError, match='frozen run envelope'):
        reveal(path, env)                                               # envelope not stored: refused
    R.write_envelope(runs, env)
    other = make_env(ds, pl, eval_d='f' * 64)
    R.write_envelope(runs, other)
    forged = dict(env, run=other['run'])                                # stored digest of A, identity of B
    with pytest.raises(L.LedgerError, match='frozen run envelope'):
        reveal(path, forged)
    reveal(path, env)
    assert L.recompute_run_digest(L._check_state(path)['fam_x'][-1], runs) == env['run_digest']
    assert L.recompute_run_digest({'run_digest': env['run_digest']}) is None    # no runs store: not checked


# ------------------------------------------------------------------ costs + slip-v1
CAL = {'id': 'slip-cal-v1', 'target': 'per-side fill-vs-reference bps', 'source_series': 'synthetic fixture',
       'estimator': 'median ratio', 'loss': 'absolute', 'pooling': 'per cost class', 'fallback': 'floor 2 bps',
       'limit_touch_rule': C.LIMIT_TOUCH_RULE,
       'calibration_window': {'start': '2022-01-01T00:00:00Z', 'end': '2022-07-01T00:00:00Z'},
       'c': {'crypto': 0.05, 'tradfi_gold': 0.05}}


def bars_of(rows):
    return [P.Bar(i * H4, o, h, lo, c, 1.0, 1.0, (i + 1) * H4) for i, (o, h, lo, c) in enumerate(rows)]


def test_slip_v1_formula_is_exact():
    rows = [(100, 101, 99, 100)] * 15
    b = bars_of(rows)
    assert C.atr14(b) == 2.0
    assert C.slip_bps(b, 0.05) == pytest.approx(max(2, 0.05 * 1e4 * 2 / 100))          # 10 bps
    assert C.slip_bps(b, 0.001) == 2.0                                                   # floor
    gap = bars_of([(100, 100, 100, 100)] + [(100, 100, 100, 100)] * 13 + [(110, 111, 109, 110)])
    assert C.atr14(gap) == pytest.approx(11 / 14)                                        # |h - prev close| counts
    with pytest.raises(C.CostError, match='15 closed bars'):
        C.atr14(b[:14])


def test_fill_prices_are_adverse_and_gap_stops_fill_at_open():
    base, x5 = C.STRESS['base'], C.STRESS['gap_slip_x5']
    assert C.fill_price(100.0, 'buy', 10, base, tick=0.01) == 100.1
    assert C.fill_price(100.0, 'sell', 10, base, tick=0.01) == 99.9
    assert C.fill_price(100.004, 'buy', 0, base, tick=0.01) == 100.01                   # rounded against us
    assert C.fill_price(100.006, 'sell', 0, base, tick=0.01) == 100.0
    assert C.fill_price(100.0, 'sell', 10, x5, gap=True) == pytest.approx(99.5)
    assert C.fill_price(100.0, 'sell', 10, C.STRESS['fees_slip_x2']) == pytest.approx(99.8)
    assert C.stop_reference('long', 95, 94) == (94, True) and C.stop_reference('long', 95, 96) == (95, False)
    assert C.stop_reference('short', 105, 106) == (106, True)


def test_limit_fill_rule_and_stress_rows():
    assert set(C.STRESS) == {'base', 'fees_slip_x2', 'funding_x2', 'gap_slip_x5', 'limit_no_fill', 'limit_as_taker',
                             'flat_funding_legacy'}
    assert C.limit_fill('touch', C.STRESS['base']) is None
    assert C.limit_fill('price_through', C.STRESS['base']) == 'maker'
    assert C.limit_fill('price_through', C.STRESS['limit_no_fill']) is None
    assert C.limit_fill('touch', C.STRESS['limit_as_taker']) == 'taker'
    m = C.CostModel(CAL)
    assert C.fee(1000, 'taker', m.row('BTCUSDT'), C.STRESS['base']) == pytest.approx(0.5)
    assert C.fee(1000, 'maker', m.row('BTCUSDT'), C.STRESS['fees_slip_x2']) == pytest.approx(0.4)
    assert C.funding_cost('short', 1, [], C.STRESS['flat_funding_legacy'], bars_held=10,
                          entry_notional=1000) == pytest.approx(0.5)


def test_cost_rows_per_class_with_gold_provisional():
    m = C.CostModel(CAL)
    gold, crypto = m.row('XAUUSDT'), m.row('BTCUSDT')
    assert gold.cost_class == 'tradfi_gold' and gold.status == 'PROVISIONAL'
    assert (gold.taker, gold.maker, gold.fee_tier, gold.funding) == (crypto.taker, crypto.maker, crypto.fee_tier,
                                                                     crypto.funding)
    assert m.labels('XAUUSDT') == ['COST-TRADFI_GOLD-PROVISIONAL'] and m.labels('BTCUSDT') == []
    rows = dict(C.DEFAULT_ROWS, tradfi_oil=C.CostRow('tradfi_oil', 0.0004, 0.0002, 'VIP0', 'actual', 'mon-fri',
                                                     'PROVISIONAL'))
    m2 = C.CostModel(dict(CAL, c=dict(CAL['c'], tradfi_oil=0.07)), rows=rows,
                     symbol_class=dict(C.DEFAULT_SYMBOL_CLASS, CLUSDT='tradfi_oil'))
    assert m2.row('CLUSDT').taker == 0.0004 and m2.slip_c('CLUSDT') == 0.07 and m2.digest != m.digest
    with pytest.raises(C.CostError, match='one coefficient per cost class'):
        C.CostModel(dict(CAL, c={'crypto': 0.05}))
    with pytest.raises(C.CostError, match='primary rule'):
        C.CostModel(dict(CAL, limit_touch_rule='touch = maker'))
    assert C.CostModel(CAL).digest == m.digest


def test_quantity_is_floored_never_upsized():
    rule = {'step': 0.001, 'min_qty': 0.001, 'min_notional': 5.0, 'tick': 0.1}
    assert C.size(0.0129, 1000.0, rule)['qty'] == 0.012
    r = C.size(0.0049, 1000.0, rule)
    assert r['ok'] is False and r['code'] == 'below_min_notional'


# ------------------------------------------------------------------ intrabar resolver
def mins(start, rows):
    return [P.Bar(start + i * MIN, o, h, lo, c, 1, 1, start + (i + 1) * MIN) for i, (o, h, lo, c) in enumerate(rows)]


SIG = P.Bar(0, 100, 110, 90, 100, 1, 1, 4 * MIN)                   # a 4-minute "signal bar" for brevity


def test_resolver_single_level_and_1m_order():
    r = I.resolve('long', P.Bar(0, 100, 104, 96, 100, 1, 1, 4 * MIN), 4 * MIN, 95, 105, None)
    assert r.outcome == 'none' and r.label is None
    r = I.resolve('long', P.Bar(0, 100, 106, 96, 100, 1, 1, 4 * MIN), 4 * MIN, 95, 105, None)
    assert (r.outcome, r.label, r.ambiguous) == ('target', I.ONE_LEVEL, False)
    m = mins(0, [(100, 101, 99, 100), (100, 106, 99, 105), (105, 105, 94, 95), (95, 100, 90, 100)])
    r = I.resolve('long', SIG, 4 * MIN, 95, 105, m)
    assert (r.outcome, r.label, r.ambiguous, r.unresolved) == ('target', I.RES_1M, True, False)
    assert (r.stop_first, r.target_first) == ('stop', 'target')
    r = I.resolve('short', SIG, 4 * MIN, 105, 95, m)                   # short: first touch is 106 >= stop
    assert (r.outcome, r.label) == ('stop', I.RES_1M)


def test_resolver_same_minute_missing_1m_and_gap_fall_back_to_stop():
    m = mins(0, [(100, 106, 94, 100)] + [(100, 100, 100, 100)] * 3)
    r = I.resolve('long', SIG, 4 * MIN, 95, 105, m)
    assert (r.outcome, r.label, r.unresolved) == ('stop', I.SAME_MINUTE, True)
    for partial in (None, [], m[:3]):
        r = I.resolve('long', SIG, 4 * MIN, 95, 105, partial)
        assert (r.outcome, r.label, r.unresolved) == ('stop', I.NO_1M, True)
    gap = I.resolve('long', P.Bar(0, 94, 110, 90, 100, 1, 1, 4 * MIN), 4 * MIN, 95, 105, None)
    assert (gap.outcome, gap.gap, gap.unresolved) == ('stop', True, False)
    m2 = mins(0, [(100, 101, 99, 100), (94, 106, 90, 100), (100, 100, 100, 100), (100, 100, 100, 100)])
    r = I.resolve('long', SIG, 4 * MIN, 95, 105, m2)
    assert (r.outcome, r.gap, r.label) == ('stop', True, I.RES_1M)


def test_resolver_summary_parks_above_five_percent():
    amb = I.resolve('long', SIG, 4 * MIN, 95, 105, None)
    clean = I.resolve('long', P.Bar(0, 100, 106, 96, 100, 1, 1, 4 * MIN), 4 * MIN, 95, 105, None)
    s = I.summarize([amb] * 5 + [clean] * 95, 100)
    assert s['unresolved_rate'] == 0.05 and not s['park'] and s['by_label'][I.NO_1M] == 5
    assert I.summarize([amb] * 6 + [clean] * 94, 100)['park']


def test_resolver_on_the_pit_view_minutes(world, tmp_path):
    ds = ds_of(world)
    path, _ = dirs(tmp_path)
    w = access(ds, path, pl=None).open_development('2024-02-05T00:00:00Z', '2024-03-04T00:00:00Z', lineage='root')
    t = ms('2024-02-20') + H4
    v = w.view(t)
    sig = v.bars('AAAUSDT', '4h', 1)[-1]
    assert len(v.minute_bars('AAAUSDT', sig.open_ms, sig.available_ms)) == 240
    assert v.minute_bars('XAUUSDT', sig.open_ms, sig.available_ms) == ()          # no 1m for gold here -> NO_1M
    with pytest.raises(P.PITError, match='not closed'):
        v.minute_bars('AAAUSDT', sig.available_ms, sig.available_ms + H4)


# ------------------------------------------------------------------ report envelope
def test_envelope_write_once_and_report_shape(world, tmp_path):
    ds = ds_of(world)
    env = make_env(ds, plan())
    assert env['run_digest'] == R.run_digest(env['run'])
    R.write_envelope(str(tmp_path), env)
    R.write_envelope(str(tmp_path), env)                                         # identical: no-op
    assert R.load_envelope(str(tmp_path), env['run_digest']) == env
    with pytest.raises(R.ReportError):
        R.write_envelope(str(tmp_path), dict(env, run_digest='0' * 64))
    sec = {s: {k: None for k in keys} for s, keys in R.SECTIONS.items()}
    rep = R.make_report(env, {'long': {row: sec for row in C.STRESS}})
    assert rep['report_digest'] and rep['run_digest'] == env['run_digest']
    with pytest.raises(R.ReportError, match='every stress row'):
        R.make_report(env, {'long': {'base': sec}})
    with pytest.raises(R.ReportError, match='keys must be exactly'):
        R.make_report(env, {'short': {row: dict(sec, ci={}) for row in C.STRESS}})
    assert R.sealable(make_env(ds, plan(), dirty=True)) == ['dirty working tree']


def test_runtime_never_imports_research_code():
    names = {'manifest', 'ledger', 'universe', 'pit', 'costs', 'splits', 'intrabar', 'report'}
    files = glob.glob(os.path.join(ROOT, '*.py')) + glob.glob(os.path.join(ROOT, 'newcore', '**', '*.py'), recursive=True)
    bad = []
    for f in files:
        with open(f, encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
        for n in ast.walk(tree):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                [n.module or ''] if isinstance(n, ast.ImportFrom) and not n.level else []
            bad += [(f, m) for m in mods if m.split('.')[0] in names or m.startswith('tools.research')]
    assert bad == []
