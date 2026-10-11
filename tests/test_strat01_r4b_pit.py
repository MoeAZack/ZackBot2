"""STRAT-01 R4b-PIT (Codex 6108364741): the real `run_r4.py pit` runner (tools/research/pit_run.py + pit_eval.py).

Synthetic / portable fixtures only: a generated Binance-vision-shaped archive store, a fixture static list and a
throwaway family ledger. No real development or holdout data is read and no real-market outcome exists anywhere here.
Covers: fail-closed gate first, point-in-time tape (sandboxed + perturbed), 180-bar cap / purge, costs + funding rows,
INCOMPLETE exclusion, baselines + deterministic seeds, atomic progress / sealed finals, interrupted-run refusal and
resume, identity mismatch refusal, tier separation, no holdout path, and two fresh-process replay determinism.
"""
import glob
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import costs as C                                                                           # noqa: E402
import ledger as L                                                                          # noqa: E402
import manifest as M                                                                        # noqa: E402
import pit as P                                                                             # noqa: E402
import pit_eval as PE                                                                       # noqa: E402
import pit_run as PR                                                                        # noqa: E402
import progress as PG                                                                       # noqa: E402
import run_r4 as G                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402
import static_universe as SU                                                                # noqa: E402
import trend_ema_mom as T                                                                   # noqa: E402

TF, HOUR, DAY = T.TF_MS, 3_600_000, 86_400_000
START = S.parse_utc('2024-01-01T00:00:00Z')
SYMS = ('BTCUSDT', 'ETHUSDT', 'ZECUSDT')
MONTHS = [(2024, m) for m in range(1, 7)]
FIX_SPLITS = [{'name': 'train', 'start': '2024-01-01T00:00:00Z', 'end': '2024-03-31T00:00:00Z'},
              {'name': 'walk_forward', 'fold': 0, 'start': '2024-03-31T00:00:00Z', 'end': '2024-06-29T00:00:00Z'},
              {'name': 'holdout', 'start': '2024-07-01T00:00:00Z', 'end': '2024-10-01T00:00:00Z'}]
DROP = ('ZECUSDT', S.parse_utc('2024-02-20T08:00:00Z'))   # one missing 4h bar (gap) on the CORE-20-only symbol


# ------------------------------------------------------------------ the synthetic archive store
def price(k, x):
    """Deterministic trending + oscillating path in bar units x (no market data)."""
    return 100.0 * (k + 1) * math.exp(0.0009 * x + 0.08 * math.sin(x / (5.0 + 2 * k)) + 0.02 * math.sin(x / 2.3 + k))


def kline(k, t, step, deep=False):
    x0, x1 = (t - START) / TF, (t + step - START) / TF
    o, c = round(price(k, x0), 4), round(price(k, x1), 4)
    h = round(max(o, c) * 1.004, 4)
    lo = round(min(o, c) * (0.96 if deep else 0.996), 4)
    return f'{t},{o},{h},{lo},{c},10,{t + step - 1},{1000.0 + (t // step) % 7},5,4,1,0'


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


def month_range(y, m):
    a = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return a, int(datetime(y + m // 12, m % 12 + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def build_store(store):
    for k, sym in enumerate(SYMS):
        for y, m in MONTHS:
            a, b = month_range(y, m)
            tag = f'{y}-{m:02d}'
            put(store, f'um/monthly/klines/{sym}/4h/{sym}-4h-{tag}.zip',
                [kline(k, t, TF, deep=((t - START) // TF) % 37 == 11) for t in range(a, b, TF)
                 if (sym, t) != DROP])
            put(store, f'um/monthly/markPriceKlines/{sym}/1h/{sym}-1h-{tag}.zip',
                [kline(k, t, HOUR) for t in range(a, b, HOUR)])
            put(store, f'um/monthly/fundingRate/{sym}/{sym}-fundingRate-{tag}.zip',
                ['calc_time,funding_interval_hours,last_funding_rate'] +
                [f'{t + 3},8,{0.0001 * (1 + k)}' for t in range(a, b, 8 * HOUR)])
    return M.build(['um/monthly'], manifest_id='fx-strat01', source_class='archive-verified', base=store,
                   data_root='binance_um', survivor_only=False, loader=M.ARCHIVE_LOADER)


def fixture_static():
    s = [{'symbol': x, 'rank': i + 1} for i, x in enumerate(SYMS)]
    return {'format': SU.FORMAT, 'label': SU.LABEL, 'owner_approved': True, 'symbols': s,
            'digest': hashlib.sha256(json.dumps(SYMS).encode()).hexdigest()}


FIX_RULES = {s: {'tick': 0.0001, 'step': 0.001, 'min_qty': 0.001, 'min_notional': 5.0} for s in SYMS}


def fixture_env(store, ledger, *, resamples=200, perms=200, b0_draws=20):
    man = M.load(os.path.join(store, 'manifest.json'))
    plan = S.SplitPlan(FIX_SPLITS, interval='4h', lookback_bars=240, horizon_bars=180)
    run_config = {'configs': [c['name'] for c in PR.configs()], 'stress_rows': list(C.STRESS), 'slip_c': 0.05,
                  'seed': PR.SEED, 'resamples': resamples, 'perms': perms, 'b0_draws': b0_draws,
                  'dev_keys': PR.dev_keys(plan), 'plan_digest': plan.digest, 'window': T.WINDOW, 'tape': PE.FORMAT,
                  'n_trials': 18, 'book': dict(PR.BOOK), 'rules': FIX_RULES}
    static = fixture_static()
    identity = {'commit': '0' * 40, 'evaluator': PR.sha({'code': PR.code_digest(ROOT), 'config': run_config}),
                'manifest': man['digest'], 'universe': static['digest'], 'classification': None, 'prereg': None}
    return {'repo': ROOT, 'manifest': man, 'store': store, 'static': static,
            'tiers': {'CORE-10': list(SYMS[:2]), 'CORE-20': list(SYMS)}, 'plan': plan, 'ledger': ledger,
            'evaluator': PR.EVALUATOR, 'author': 't', 'cairo_date': '2026-10-11', 'run_config': run_config,
            'identity': identity, 'n_trials': 18, 'cost_labels': ['SLIP-C-UNFITTED'], 'rules': FIX_RULES}


def make_store(store):
    m = build_store(store)
    with open(os.path.join(store, 'manifest.json'), 'w', newline='\n') as f:
        json.dump(m, f)


def child(store, out, ledger):
    """One complete fixture run in THIS (fresh) process: both tiers, clock off (byte-comparable finals)."""
    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    for p in PR.run_tiers(fixture_env(store, ledger), out, clock=None):
        print(p)


# ------------------------------------------------------------------ the end-to-end fixture: two fresh processes
@pytest.fixture(scope='module')
def e2e(tmp_path_factory):
    base = tmp_path_factory.mktemp('r4b')
    store = str(base / 'store')
    make_store(store)
    procs, outs = [], []
    for n in (1, 2):
        out, led = str(base / f'out{n}'), str(base / f'ledger{n}' / 'trend_ema_mom.jsonl')
        code = (f'import sys; sys.path.insert(0, {os.path.join(ROOT, "tests")!r}); '
                f'import test_strat01_r4b_pit as F; F.child({store!r}, {out!r}, {led!r})')
        procs.append(subprocess.Popen([sys.executable, '-c', code], cwd=ROOT, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, stdin=subprocess.DEVNULL))
        outs.append((out, led))
    for p in procs:
        o, e = p.communicate(timeout=1800)
        assert p.returncode == 0, e.decode(errors='replace')[-3000:]
    return store, outs


def tier_files(out, tier):
    d = os.path.join(out, tier)
    get = lambda pat: sorted(glob.glob(os.path.join(d, pat)))
    return get('final-*.json'), get('progress-*.json'), get('summary-*.json'), get('detail-*.json')


def test_two_fresh_process_replay_is_byte_identical(e2e):
    _, outs = e2e
    for tier in PR.RUN_TIERS:
        a, b = (tier_files(o, tier) for o, _ in outs)
        assert all(len(x) == 1 for x in a) and all(len(x) == 1 for x in b)
        for fa, fb in zip(a, b):
            with open(fa[0], 'rb') as x, open(fb[0], 'rb') as y:
                assert x.read() == y.read(), os.path.basename(fa[0])
        fin = PG.read(a[0][0])
        assert fin['state'] == 'sealed' and fin['digest'] == PG.doc_digest(fin)


def test_tiers_are_separate_sealed_finals_with_one_identity(e2e):
    _, outs = e2e
    out, _ = outs[0]
    f10, f20 = (PG.read(tier_files(out, t)[0][0]) for t in PR.RUN_TIERS)
    assert f10['identity'] == f20['identity'] and f10['run'] != f20['run']
    for fin, tier in ((f10, 'CORE-10'), (f20, 'CORE-20')):
        names = [s['name'] for s in fin['stages']]
        assert names == ['pit.open', 'pit.development', 'pit.walk_forward']
        assert all(s['status'] == 'done' for s in fin['stages'])
        assert [a['path'] for a in fin['artifacts']] == sorted(os.path.basename(p) for p in tier_files(out, tier)[2:]
                                                                for p in p)
    s10, s20 = (json.load(open(tier_files(out, t)[2][0])) for t in PR.RUN_TIERS)
    assert s10['run_config_digest'] == s20['run_config_digest'] and s10['symbol_count'] == 2 and s20['symbol_count'] == 3


def test_summary_is_labelled_aggregate_only_and_mechanical(e2e):
    _, outs = e2e
    for tier in PR.RUN_TIERS:
        path = tier_files(outs[0][0], tier)[2][0]
        raw = open(path, 'rb').read()
        s = json.loads(raw)
        assert s['label'] == SU.LABEL and SU.LABEL in s['labels'] and 'DEVELOPMENT-ONLY' in s['labels']
        assert s['development_only'] is True and s['verdict'] == 'PARK'
        assert set(s['edge00']) == G.EDGE_ROWS
        assert set(s['flags']) <= {'DEV-NEGATIVE'}
        for sym in SYMS:                                     # no symbol names / rankings / raw trades disclosed
            assert sym.encode() not in raw
        assert b'"trades": [' not in raw and b'neighbours_mean_r' not in raw
        for key, blk in s['splits'].items():
            assert blk['primary']['label'] == SU.LABEL and blk['uncapped_development_only']['label'] == SU.LABEL
        assert s['aggregates']['development']['trades'] > 0                 # the synthetic path does trade
        assert set(s['aggregates']['development']['stress_mean_r']) == set(C.STRESS)


def test_ledger_records_development_access_and_attestations_only(e2e):
    _, outs = e2e
    led = outs[0][1]
    recs = L._check_state(led)['trend_ema_mom']                         # records + chain (no git history here)
    assert recs and all(r['kind'] == 'data_access' for r in recs)
    assert {r['split'] for r in recs} == {'train', 'walk_forward'}          # never holdout, never calibration
    att = [r for r in recs if r['detail'].get('purpose') == 'evaluation attestation']
    assert len(att) == 4 and all(r['detail']['attestation']['perturbed'] for r in att)
    assert all(SU.LABEL in r['detail']['labels'] for r in recs)


def test_capped_trades_never_cross_their_split_and_incomplete_is_excluded(e2e):
    _, outs = e2e
    out = outs[0][0]
    plan = S.SplitPlan(FIX_SPLITS, interval='4h', lookback_bars=240, horizon_bars=180)
    for tier in PR.RUN_TIERS:
        det = json.load(open(tier_files(out, tier)[3][0]))
        for key, sp in det['splits'].items():
            first, last = plan.decision_range(key)
            for tr in sp['configs'][PR.PRIMARY_NAME]['trades']:
                assert first <= tr['entry_ms'] <= last and tr['exit_ms'] - tr['entry_ms'] <= 180 * TF
                assert plan.split_of_episode(tr['entry_ms'], tr['exit_ms']) == key
            unc = sp['configs'][PR.DEV_ONLY]
            assert all(i['reason'].startswith('incomplete.') for i in unc['incomplete'])


# ------------------------------------------------------------------ evaluator parity (no sandbox, no data)
def test_shared_period_signals_are_bit_identical_to_trend_ema_mom():
    bars = [P.Bar(START + i * TF, price(0, i - 1), price(0, i) * 1.004, price(0, i) * 0.99, price(0, i), 1, 1,
                  START + (i + 1) * TF) for i in range(700)]
    for n in (200, 230, 260, 400, 700):
        got = PE.signals(bars[:n])
        for (name, p), g in zip(PE.SIGNAL_CONFIGS, got):
            assert g == T.signal_at_last(bars[:n], p), (name, n)
    assert [n for n, _ in PE.SIGNAL_CONFIGS] == ['primary'] + [n for n, _ in T.neighbours()]


# ------------------------------------------------------------------ accounting on hand-built tapes (pure)
T0 = S.parse_utc('2024-01-01T00:00:00Z')
RULE = {'tick': 0.01, 'step': 0.001, 'min_qty': 0.001, 'min_notional': 5.0}


def mk_tape(closes, *, sym='AAAUSDT', le=(), lx=(), d='2.0', lows=None, highs=None, opens=None, drop=(),
            fund_drop=(), mark_none=(), regime='btc_up', n_last=True):
    """Bar i closes at T0 + (i + 1) x 4h (open = previous close unless `opens` says otherwise); funding every 8h
    (calc_time + 3 ms) with a mark; `n` = the next bar's open (the last bar's `n` is the given/next value)."""
    out = []
    op = lambda i: (opens or {}).get(i, closes[i - 1] if i else closes[0])
    for i, c in enumerate(closes):
        t = T0 + (i + 1) * TF
        o = op(i)
        lo = (lows or {}).get(i, min(o, c) - 0.25)
        h = (highs or {}).get(i, max(o, c) + 0.25)
        f = [[ft + 3, 0.0001, 8, None if ft in mark_none else o] for ft in range(T0, t + 1, 8 * HOUR)
             if t - TF < ft + 3 <= t and ft not in fund_drop]
        g = regime(i) if callable(regime) else regime
        if i in drop:
            out.append({'t': t, 'r': [{'s': sym, 'f': f}] if f else [], 'g': g})
            continue
        nxt = op(i + 1) if i + 1 < len(closes) else ((opens or {}).get(i + 1, c) if n_last else None)
        rec = {'s': sym, 'b': [o, h, lo, c], 'f': f, 'w': '1' * 13, 'x': ('1' if i in lx else '0') * 13,
               'e': {str(k): d for k in range(13)} if i in le else {}, 'd': d, 'n': nxt}
        out.append({'t': t, 'r': [rec], 'g': g})
    return out


def merge(*tapes):
    out = []
    for cyc in zip(*tapes):
        out.append({'t': cyc[0]['t'], 'r': [r for c in cyc for r in c['r']], 'g': cyc[0]['g']})
    return out


def t_of(i):
    return T0 + (i + 1) * TF


CFG = {c['name']: c for c in PR.configs()}


def acct(tape, cfg=PR.PRIMARY_NAME, first=None, last=None, hi=None, rows=('base',), rules=None):
    syms = sorted({r['s'] for o in tape for r in o['r']})
    return PR.account(tape, CFG[cfg], symbols=syms, first=first if first is not None else t_of(20),
                      last=last if last is not None else t_of(len(tape) - 2), hi=hi or tape[-1]['t'], slip_c=0.05,
                      rules=rules or {s: RULE for s in syms}, rows=rows)


def unit(tape, cfg=PR.PRIMARY_NAME, first=None, last=None):
    return PR.account_unit(tape, CFG[cfg], first=first if first is not None else t_of(20),
                           last=last if last is not None else t_of(len(tape) - 2), hi=tape[-1]['t'], slip_c=0.05)


def test_unit_diagnostic_net_r_is_the_registered_cost_model():
    closes = [100.0 + 0.1 * i for i in range(60)]
    a = unit(mk_tape(closes, le={25}, lx={30}))
    (tr,) = a['counted']
    assert (tr['entry_ms'], tr['exit_ms'], tr['exit_reason'], tr['bars']) == (t_of(25), t_of(30), 'exit.signal', 5)
    sym = PR.read_tape(mk_tape(closes, le={25}, lx={30}))[1]['AAAUSDT']
    for name, scen in C.STRESS.items():
        ef = C.fill_price(closes[25], 'buy', sym.bps(t_of(25), scen.vol, 0.05), scen)
        xf = C.fill_price(closes[30], 'sell', sym.bps(t_of(30), scen.vol, 0.05), scen)
        ev = sym.events(t_of(25), t_of(30))
        assert len(ev) == 3                                              # 8h cadence: 104h (entry counts), 112h, 120h
        want = (xf - ef - C.fee(ef, 'taker', C.CRYPTO, scen) - C.fee(xf, 'taker', C.CRYPTO, scen)
                - C.funding_cost('long', 1.0, ev, scen, bars_held=5, entry_notional=ef)) / 2.0
        assert tr['r'][name] == pytest.approx(want, abs=1e-12), name


def test_the_book_sizes_by_risk_floors_to_step_and_books_the_wallet():
    closes = [100.0 + 0.1 * i for i in range(60)]
    a = acct(mk_tape(closes, le={25}, lx={30}), rows=tuple(C.STRESS))
    (tr,) = a['counted']
    x = PR.read_tape(mk_tape(closes))[1]['AAAUSDT']
    fill = C.fill_price(closes[25], 'buy', x.bps(t_of(25), C.TR_SMA14, 0.05), C.STRESS['base'], tick=0.01)
    stop = math.floor((fill - 2.0) / 0.01 + 1e-9) * 0.01
    q = math.floor(min(10_000 * 0.01 / (fill - stop), 3 * 10_000 / (closes[25] * 1.10)) / 0.001 + 1e-9) * 0.001
    assert tr['qty'] == pytest.approx(q) and tr['entry_price'] == fill
    assert tr['stop_price'] == pytest.approx(math.floor((fill - 2.0) / 0.01 + 1e-9) * 0.01)
    base = a['rows']['base']
    assert base['wallet_end'] == pytest.approx(10_000 + tr['pnl'])
    assert base['net_pct_equity'] == pytest.approx(tr['pnl'] / 100.0)
    assert tr['r']['base'] == pytest.approx(tr['pnl'] / (tr['qty'] * (fill - tr['stop_price'])))
    assert a['rows']['fees_slip_x2']['counted'][0]['pnl'] < tr['pnl']          # stress rows run their own book
    assert set(a['rows']) == set(C.STRESS)


def test_book_limits_max_lots_leverage_and_minimums():
    closes = [100.0 + 0.05 * i for i in range(60)]
    five = merge(*(mk_tape(closes, sym=f'S{k}USDT', le={25}, lx={40}) for k in range(5)))
    a = acct(five)
    assert len(a['counted']) == 4 and a['refusals'] == {'capacity.max_positions': 1}
    assert [t['symbol'] for t in a['counted']] == ['S0USDT', 'S1USDT', 'S2USDT', 'S3USDT']    # frozen rank order
    tight = acct(mk_tape(closes, le={25}, lx={40}, d='0.01'))                      # tiny stop: leverage-capped
    (tr,) = tight['counted']
    assert tr['qty'] * closes[25] * 1.10 <= 3 * 10_000 + 1e-6 and tr['qty'] < 10_000 * 0.01 / 0.01
    big = acct(mk_tape(closes, le={25}, lx={40}), rules={'AAAUSDT': dict(RULE, min_notional=1e9)})
    assert big['counted'] == [] and big['refusals'] == {'execution.size_min': 1}


@pytest.mark.parametrize('tick', [0.01, 0.5, 1.0, 3.0, 7.0])
def test_initial_stop_risk_never_exceeds_one_percent_on_coarse_ticks(tick):
    """Codex 6109370176: qty is sized from the tick-rounded fill minus the tick-floored stop, never from the raw
    distance (flooring the stop widens the risk)."""
    rule = dict(RULE, tick=tick, step=0.0001)
    for d in ('0.3', '1.7', '2.0', '4.9', '9.99'):
        closes = [100.0 + 0.137 * i for i in range(60)]
        a = acct(mk_tape(closes, le={25}, lx={40}, d=d), rules={'AAAUSDT': rule}, rows=tuple(C.STRESS))
        for row, x in a['rows'].items():
            for tr in x['counted']:
                risk = tr['qty'] * (tr['entry_price'] - tr['stop_price'])
                assert risk <= 0.01 * tr['snapshot'] * (1 + 1e-9), (tick, d, row)
                assert tr['entry_price'] - tr['stop_price'] >= float(d) - 1e-9
                bigger = tr['qty'] + rule['step']                           # floored: one more step would breach
                lev_room = 3 * tr['snapshot'] / (closes[25] * 1.10)
                assert bigger * (tr['entry_price'] - tr['stop_price']) > 0.01 * tr['snapshot'] or bigger > lev_room


def test_the_cairo_day_mtm_halt_refuses_that_days_entries():
    closes = [100.0] * 32 + [98.0] * 40                                   # bar 32 gaps through a 0.5 stop: ~-4%
    a = acct(merge(mk_tape(closes, sym='AAAUSDT', le={25}, d='0.5', opens={32: 98.0}),
                   mk_tape([100.0] * 72, sym='BBBUSDT', le={32})))
    (tr,) = a['counted']
    assert tr['symbol'] == 'AAAUSDT' and tr['gap'] and tr['pnl'] < -300
    assert a['refusals'] == {'filter.halt': 1}                           # BBB's same-Cairo-day signal is refused


def test_the_180_bar_cap_exits_at_the_next_bar_open_never_a_close():
    closes = [100.0 + 0.01 * i for i in range(260)]
    opens = {206: 150.0}                                                 # the cap bar gaps open far from the close
    a = acct(mk_tape(closes, le={25}, opens=opens))
    (tr,) = a['counted']
    assert tr['exit_reason'] == 'exit.time_cap' and tr['exit_ms'] - tr['entry_ms'] == 180 * TF and tr['bars'] == 180
    x = PR.read_tape(mk_tape(closes, opens=opens))[1]['AAAUSDT']
    assert tr['exit_price'] == C.fill_price(150.0, 'sell', x.bps(tr['exit_ms'], C.TR_SMA14, 0.05),
                                            C.STRESS['base'], tick=0.01)
    u = acct(mk_tape(closes, le={25}), cfg=PR.DEV_ONLY)
    assert not u['counted'] and [i['reason'] for i in u['incomplete']] == ['incomplete.split_end']
    assert u['excluded_symbols'] == ['AAAUSDT']


def test_a_cap_due_at_the_split_end_reads_the_attested_next_open_or_is_incomplete():
    closes = [100.0 + 0.01 * i for i in range(206)]                      # entry bar 26 -> cap at hi exactly
    hi = t_of(205)
    a = acct(mk_tape(closes, le={25}, opens={206: 123.0}), hi=hi, last=t_of(25))
    (tr,) = a['counted']
    assert tr['exit_ms'] == hi and tr['exit_reason'] == 'exit.time_cap'
    x = PR.read_tape(mk_tape(closes))[1]['AAAUSDT']
    assert tr['exit_price'] == C.fill_price(123.0, 'sell', x.bps(hi, C.TR_SMA14, 0.05), C.STRESS['base'], tick=0.01)
    b = acct(mk_tape(closes, le={25}, n_last=False), hi=hi, last=t_of(25))
    assert b['counted'] == [] and [i['reason'] for i in b['incomplete']] == ['incomplete.split_end']


def test_stops_fill_at_the_level_or_at_a_gapping_open_and_mfe_is_pre_stop():
    closes = [100.0] * 40
    a = acct(mk_tape(closes, le={25}, lows={28: 97.0}, highs={28: 130.0}))
    (tr,) = a['counted']
    assert tr['exit_reason'] == 'exit.stop' and not tr['gap'] and tr['exit_ms'] == t_of(28)    # stop bar CLOSE
    risk = tr['entry_price'] - tr['stop_price']
    assert tr['mfe_r'] == pytest.approx((100.25 - tr['entry_price']) / risk)   # earlier highs + the stop bar's open,
    assert tr['mfe_r'] < (130.0 - tr['entry_price']) / risk                      # never the stop bar's own high
    u = unit(mk_tape(closes, le={25}, lows={28: 97.0}, highs={28: 130.0}))
    assert u['counted'][0]['mfe_r'] < 1.0
    closes2 = [100.0] * 27 + [96.0] * 13                                 # bar 28 opens at 96, below the stop
    b = acct(mk_tape(closes2, le={25}, lows={27: 99.5}))
    (tr2,) = b['counted']
    assert tr2['exit_reason'] == 'exit.stop' and tr2['gap'] and tr2['r']['base'] < -1.0


def _has_event(i):
    """The bar closing at t_of(i) holds a funding event (calc time + 3 ms inside [open, close))."""
    return any(t_of(i) - TF <= ft + 3 < t_of(i) for ft in range(T0, t_of(i) + 1, 8 * HOUR))


@pytest.mark.parametrize('row', ['base', 'funding_x2'])
def test_a_non_gap_stop_pays_the_stop_bars_funding_and_a_gap_stop_does_not(row):
    """Cowork 6109240642: a non-gap stop exits at the stop-bar close (PR #61 convention, entry <= T < exit_time),
    so an event inside the stop bar is charged; the fill stays at the stop level. A gap stop exits at the open."""
    closes = [100.0] * 60
    fund = {}
    for k in range(27, 36):
        a = acct(mk_tape(closes, le={25}, lows={k: 97.0}), rows=(row,))
        (tr,) = a['rows'][row]['counted']
        assert tr['exit_reason'] == 'exit.stop' and not tr['gap'] and tr['exit_ms'] == t_of(k)
        n = sum(1 for i in range(26, k + 1) if _has_event(i))         # events over entry bar .. stop bar inclusive
        fund[k] = (tr['funding'], n)
        assert tr['exit_price'] == pytest.approx(tr['stop_price'], abs=1.0)   # the stop level (+ slippage), not a close
    per = {f / n for f, n in fund.values() if n}
    assert len({round(v, 9) for v in per}) == 1                         # funding = events x one per-event amount
    for k in range(28, 36):
        stepped = fund[k][0] > fund[k - 1][0] + 1e-12
        assert stepped == _has_event(k), k                              # steps up exactly on bars holding an event
    gapk = next(k for k in range(28, 36) if _has_event(k))
    g = acct(mk_tape(closes[:gapk] + [96.0] * (60 - gapk), le={25}, opens={gapk: 96.0}), rows=(row,))
    (tg,) = g['rows'][row]['counted']
    assert tg['gap'] and tg['exit_ms'] == t_of(gapk) - TF and tg['funding'] == pytest.approx(fund[gapk - 1][0])
    u = unit(mk_tape(closes, le={25}, lows={gapk: 97.0}))
    v = unit(mk_tape(closes, le={25}, lows={gapk - 1: 97.0}))
    assert u['counted'][0]['exit_ms'] == t_of(gapk)
    assert u['counted'][0]['r'][row] < v['counted'][0]['r'][row]        # the per-symbol diagnostic pays the event too


def test_entries_only_inside_the_decision_range():
    closes = [100.0 + 0.1 * i for i in range(60)]
    a = acct(mk_tape(closes, le={25, 45}, lx={30, 50}), first=t_of(26), last=t_of(44))
    assert a['counted'] == []
    b = acct(mk_tape(closes, le={25, 45}, lx={30, 50}), first=t_of(25), last=t_of(45))
    assert [t['entry_ms'] for t in b['counted']] == [t_of(25), t_of(45)]


@pytest.mark.parametrize('kw,reason', [({'drop': {28}}, 'incomplete.gap'),
                                       ({'fund_drop': {T0 + 13 * 8 * HOUR}}, 'incomplete.funding'),
                                       ({'mark_none': {T0 + 14 * 8 * HOUR}}, 'incomplete.funding_mark')])
def test_incomplete_symbols_are_excluded_and_the_book_is_rerun_without_them(kw, reason):
    closes = [100.0 + 0.1 * i for i in range(80)]
    bad = mk_tape(closes, sym='AAAUSDT', le={25, 60}, lx={32, 66}, **kw)
    good = mk_tape(closes, sym='BBBUSDT', le={25}, lx={32})
    a = acct(merge(bad, good))
    assert [i['reason'] for i in a['incomplete']] == [reason]
    assert a['excluded_symbols'] == ['AAAUSDT'] and [t['symbol'] for t in a['counted']] == ['BBBUSDT']
    assert a['excluded_trades'] >= 1
    alone = acct(good)
    assert a['counted'] == alone['counted']                              # the re-run book never saw the excluded symbol
    m = PR.metrics(PR._acc(a), rows=('base',))
    assert m['trades'] == 1 and m['incomplete'] == 1 and m['excluded_symbol_count'] == 1


def test_a_gap_while_flat_drops_the_pending_entry_as_a_refusal():
    closes = [100.0 + 0.1 * i for i in range(60)]
    a = acct(mk_tape(closes, le={25}, drop={26}))
    assert a['counted'] == [] and a['refusals'] == {'entry_no_next_open': 1} and not a['incomplete']


def test_baselines_b1_weekly_and_b2_momentum_rules():
    closes = [100.0 + 0.1 * i for i in range(200)]
    b1 = acct(mk_tape(closes), cfg='B1', first=t_of(20), last=t_of(150), rows=tuple(C.STRESS))
    assert not b1['book'] and b1['counted']
    assert all(tr['exit_reason'] == 'exit.time_cap' and tr['bars'] == 42 for tr in b1['counted'])
    assert all(datetime.fromtimestamp(tr['entry_ms'] / 1000, tz=timezone.utc).weekday() == 0
               and tr['entry_ms'] % DAY == 0 for tr in b1['counted'])
    b2 = acct(mk_tape(closes + [closes[-1] + 0.1 * i for i in range(1, 200)]), cfg='B2', first=t_of(20),
              last=t_of(150))
    assert b2['book'] and b2['counted'] and b2['counted'][0]['entry_ms'] == t_of(20)
    down = acct(mk_tape([100.0 - 0.1 * i for i in range(200)]), cfg='B2', first=t_of(20), last=t_of(150))
    assert down['counted'] == []


def test_b0_draws_match_the_registered_regime_label_and_are_seeded():
    closes = [100.0 + 2 * math.sin(i / 7) + 0.02 * i for i in range(400)]
    lab = lambda i: None if i == 140 else ('btc_up' if (i // 30) % 2 == 0 else 'btc_down')
    tape = mk_tape(closes, le={60, 140, 220}, lx={70, 160, 240}, regime=lab)
    pool = PR.b0_pool(tape, first=t_of(20), last=t_of(220))
    assert set(pool) == {('AAAUSDT', 'btc_up'), ('AAAUSDT', 'btc_down')}
    for (_, g), ts in pool.items():
        assert all(lab((t - T0) // TF - 1) == g for t in ts)
    prim = acct(tape, first=t_of(20), last=t_of(220), hi=tape[-1]['t'])
    kw = dict(first=t_of(20), last=t_of(220), hi=tape[-1]['t'], slip_c=0.05, draws=50)
    a, b = PR.b0_pairs(tape, prim, seed_key='k', **kw), PR.b0_pairs(tape, prim, seed_key='k', **kw)
    c = PR.b0_pairs(tape, prim, seed_key='j', **kw)
    assert a == b and a['diffs'] != c['diffs'] and a['unmatched_trades'] == 1          # the bar-140 trade: no label
    assert a['episodes'] == 2


def test_the_tape_regime_label_is_btc_close_vs_its_ema200():
    bars = [P.Bar(START + i * TF, 1.0, 1.0, 1.0, 100.0 + (i if i < 300 else 600 - i), 1, 1, START + (i + 1) * TF)
            for i in range(400)]

    class V:
        def __init__(self, n):
            self.t, self.n = bars[n - 1].available_ms, n

        def members(self):
            return frozenset({'BTCUSDT'})

        def bars(self, s, tf, n):
            return tuple(bars[:self.n][-n:])

        def funding(self, s, since):
            return ()

        def next_open(self, s, tf):
            return None
    for n, want in ((150, None), (280, 'btc_up'), (400, 'btc_down')):
        out = PE.Tape().decide(V(n))
        assert out['g'] == want, n


def test_view_next_open_serves_only_the_open_and_survives_perturbation(tmp_path):
    store = str(tmp_path / 's')
    make_store(store)
    man = M.load(os.path.join(store, 'manifest.json'))
    ds = PR.StaticDataset(man, store, fixture_static(), list(SYMS))
    plan = S.SplitPlan(FIX_SPLITS, interval='4h', lookback_bars=240, horizon_bars=180)
    led = str(tmp_path / 'ledger' / 'trend_ema_mom.jsonl')
    os.makedirs(os.path.dirname(led))
    acc = P.Access(ds, plan, led, candidate_id=T.RULE_ID, author='t', cairo_date='2026-10-11')
    w = acc.open('train', lineage='root')
    v = w.view(w.hi)
    k = SYMS.index('BTCUSDT')
    assert v.next_open('BTCUSDT', '4h') == float(kline(k, w.hi, TF).split(',')[1])     # the bar opening at hi
    assert v.bars('BTCUSDT', '4h', 1)[-1].available_ms == w.hi                           # never served as a bar
    saved = P._perturb_future(ds, w.hi)
    try:
        assert v.next_open('BTCUSDT', '4h') == float(kline(k, w.hi, TF).split(',')[1])  # the open is known at hi
    finally:
        for p, rows in saved.items():
            ds._cache[p][:] = rows
    assert v.next_open(DROP[0], '4h') is not None
    assert w.view(DROP[1]).next_open(DROP[0], '4h') is None                # no bar opens there (gap): None
    assert 'next_open' in P.VIEW_CALLS


def test_statistics_are_seeded_and_holm_is_step_down():
    d = [0.3, -0.1, 0.2, 0.25, -0.05, 0.4]
    p = PR.sign_flip_p(d, perms=2000, seed=PR.SEED)
    assert p == PR.sign_flip_p(d, perms=2000, seed=PR.SEED) and 0 < p <= 1
    assert PR.sign_flip_p([], perms=10, seed=1) is None
    adj = PR.holm({'B0': 0.01, 'B1': 0.02, 'B2': None}, 18)
    assert adj == {'B0': pytest.approx(0.18), 'B1': pytest.approx(0.34), 'B2': None}
    rows = [{'symbol': 'A', 'entry_ms': i * TF, 'exit_ms': i * TF + TF, 'exit_reason': 'exit.signal', 'pnl': 10.0 * i,
             'r': {'base': (-1) ** i * 0.5 + 0.1}} for i in range(0, 40, 2)]
    assert PR.bootstrap(rows, seed=1, resamples=300) == PR.bootstrap(rows, seed=1, resamples=300)
    mc = PR.mc_drawdown(rows, seed=1, resamples=300)
    assert mc == PR.mc_drawdown(rows, seed=1, resamples=300) and mc['p5'] <= mc['p50'] <= mc['p95']


# ------------------------------------------------------------------ no holdout path
def prereg_plan():
    sp = json.loads(G._load(ROOT, json.loads(G._load(ROOT, G.GATE))['prereg']))['splits']
    return S.SplitPlan(sp['splits'], interval=sp['interval'], lookback_bars=sp['lookback_bars'],
                       horizon_bars=sp['horizon_bars'])


def test_the_registered_plan_opens_development_splits_only():
    plan = prereg_plan()
    keys = PR.dev_keys(plan)
    assert keys == ['train'] + [f'walk_forward[{i}]' for i in range(5)]
    h0 = PR.holdout_start(plan)
    assert h0 == S.parse_utc('2026-10-12T00:00:00Z')
    for k in keys:
        lo, hi = PR.dev_window(plan, k)
        assert hi <= h0 and not plan.overlaps_holdout(lo, hi)
    for k in ('holdout', 'calibration'):
        with pytest.raises(PR.PitRunError, match='development splits only'):
            PR.dev_window(plan, k)


def test_a_holdout_key_is_refused_before_any_ledger_access(tmp_path, monkeypatch):
    store = str(tmp_path / 's')
    make_store(store)
    led = str(tmp_path / 'ledger' / 'trend_ema_mom.jsonl')
    env = fixture_env(store, led)
    for key in ('holdout', 'calibration'):
        monkeypatch.setattr(PR, 'dev_keys', lambda plan, k=key: [k])
        with pytest.raises((PR.PitRunError, S.SplitError)):
            PR.evaluate_tier(env, 'CORE-10', None)
    assert not os.path.exists(led)                                       # nothing opened, nothing recorded


def test_the_cli_has_no_split_or_holdout_option():
    with pytest.raises(SystemExit):
        G.main(['pit', '--store', ROOT, '--author', 't', '--cairo-date', '2026-10-11', '--split', 'holdout'])
    src = open(os.path.join(ROOT, 'tools', 'research', 'pit_run.py'), encoding='utf-8').read()
    assert "'holdout'" in src and 'envelope' not in src.split('def evaluate_tier')[1].split('def _counted')[0]


def test_the_gate_runs_first_and_nothing_is_read_when_closed(monkeypatch):
    called = []
    monkeypatch.setattr(G, 'gate', lambda *a, **kw: ['G1 dirty working tree'])
    monkeypatch.setattr(PR, 'real_env', lambda *a, **kw: called.append(1))
    monkeypatch.setattr(PR, 'run_tiers', lambda *a, **kw: called.append(2))
    assert G.main(['pit', '--store', ROOT, '--author', 't', '--cairo-date', '2026-10-11']) == 3
    for fail in (['G3 no preregistration record'], ['G4 R1+R2+R3 (#51,52) 0156426: not merged into origin/master']):
        monkeypatch.setattr(G, 'gate', lambda *a, f=fail, **kw: f)
        with pytest.raises(G.GateError):
            G.run_pit(ROOT, store=ROOT, out_dir='x', author='t', cairo_date='2026-10-11')
    assert called == []


# ------------------------------------------------------------------ orchestration (stubbed tier evaluation; fast)
def stub_env(**over):
    run_config = {'configs': ['x'], 'stress_rows': ['base'], 'seed': 1, 'resamples': 10, **over}
    ident = {'commit': '1' * 40, 'evaluator': PR.sha(run_config), 'manifest': '2' * 64, 'universe': '3' * 64,
             'classification': None, 'prereg': None}
    return {'tiers': {'CORE-10': ['A'], 'CORE-20': ['A', 'B']}, 'run_config': run_config, 'identity': ident}


@pytest.fixture
def stub(monkeypatch):
    seen = []

    def ev(env, tier, prog):
        seen.append(tier)
        prog.stage_start('pit.development')
        prog.stage_done('pit.development')
        if env.get('boom') == tier:
            raise KeyboardInterrupt
        if env.get('fail') == tier:
            raise P.PITError('integrity')
        return {'format': PR.SUMMARY_FORMAT, 'tier': tier, 'label': SU.LABEL}, {'tier': tier}
    monkeypatch.setattr(PR, 'evaluate_tier', ev)
    return seen


def test_core10_is_sealed_before_core20_and_finals_are_separate(tmp_path, stub):
    out = str(tmp_path)
    finals = PR.run_tiers(stub_env(), out, clock=None)
    assert stub == ['CORE-10', 'CORE-20'] and [os.path.basename(os.path.dirname(f)) for f in finals] == list(PR.RUN_TIERS)
    for f in finals:
        assert PG.read(f)['state'] == 'sealed'
    with pytest.raises(PR.PitRunError, match='already sealed'):
        PR.run_tiers(stub_env(), out, clock=None)
    assert PR.run_tiers(stub_env(), out, resume=True, clock=None) == finals and stub == ['CORE-10', 'CORE-20']


def test_core20_never_starts_without_a_sealed_core10(tmp_path, stub):
    env = dict(stub_env(), fail='CORE-10')
    with pytest.raises(P.PITError):
        PR.run_tiers(env, str(tmp_path), clock=None)
    assert stub == ['CORE-10'] and not os.path.exists(tmp_path / 'CORE-20')
    (fin,) = glob.glob(str(tmp_path / 'CORE-10' / 'final-*.json'))
    assert PG.read(fin)['state'] == 'aborted' and PG.read(fin)['integrity'] == [{'code': 'PITError'}]
    with pytest.raises(PR.PitRunError, match='aborted'):
        PR.run_tiers(stub_env(), str(tmp_path), resume=True, clock=None)


def test_interrupted_run_refuses_without_resume_and_resumes_under_the_same_identity(tmp_path, stub):
    out = str(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        PR.run_tiers(dict(stub_env(), boom='CORE-20'), out, clock=None)
    assert glob.glob(str(tmp_path / 'CORE-10' / 'final-*.json'))
    assert glob.glob(str(tmp_path / 'CORE-20' / 'progress-*.json')) and not glob.glob(str(tmp_path / 'CORE-20' / 'final-*'))
    with pytest.raises(PR.PitRunError, match='already sealed'):
        PR.run_tiers(stub_env(), out, clock=None)
    with pytest.raises(PR.PitRunError, match='another run identity'):
        PR.run_tiers(stub_env(resamples=11), out, resume=True, clock=None)           # mismatched identity
    finals = PR.run_tiers(stub_env(), out, resume=True, clock=None)
    assert stub == ['CORE-10', 'CORE-20', 'CORE-20'] and all(PG.read(f)['state'] == 'sealed' for f in finals)
    clean = str(tmp_path / 'clean')
    PR.run_tiers(stub_env(), clean, clock=None)
    for tier in PR.RUN_TIERS:
        (a,), (b,) = (glob.glob(os.path.join(d, tier, 'summary-*.json')) for d in (out, clean))
        assert open(a, 'rb').read() == open(b, 'rb').read()


def test_an_interrupted_core10_refuses_a_plain_rerun(tmp_path, stub):
    with pytest.raises(KeyboardInterrupt):
        PR.run_tiers(dict(stub_env(), boom='CORE-10'), str(tmp_path), clock=None)
    with pytest.raises(PR.PitRunError, match='interrupted run'):
        PR.run_tiers(stub_env(), str(tmp_path), clock=None)
    assert not os.path.exists(tmp_path / 'CORE-20')


def test_a_tampered_sealed_artifact_is_refused_on_resume(tmp_path, stub):
    PR.run_tiers(stub_env(), str(tmp_path), clock=None)
    (s,) = glob.glob(str(tmp_path / 'CORE-10' / 'summary-*.json'))
    with open(s, 'ab') as f:
        f.write(b' ')
    with pytest.raises(PR.PitRunError, match='no longer matches'):
        PR.run_tiers(stub_env(), str(tmp_path), resume=True, clock=None)


def test_artifacts_are_written_atomically(tmp_path, stub, monkeypatch):
    real = os.replace

    def boom(a, b):
        if os.path.basename(b).startswith('summary-'):
            raise OSError('disk full')
        return real(a, b)
    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(OSError):
        PR.run_tiers(stub_env(), str(tmp_path), clock=None)
    d = tmp_path / 'CORE-10'
    assert not glob.glob(str(d / 'summary-*')) and not glob.glob(str(d / '.summary-*'))
    (fin,) = glob.glob(str(d / 'final-*.json'))
    assert PG.read(fin)['state'] == 'aborted'


def test_static_dataset_serves_a_rank_prefix_with_the_label_only(tmp_path):
    store = str(tmp_path / 's')
    make_store(store)
    man = M.load(os.path.join(store, 'manifest.json'))
    ds = PR.StaticDataset(man, store, fixture_static(), SYMS[:2])
    assert ds.labels == (SU.LABEL,) and ds.members(START) == frozenset(SYMS[:2]) and ds.books == ('crypto',)
    assert not ds.files('klines', 'ZECUSDT', '4h')
    with pytest.raises(PR.PitRunError, match='rank prefix'):
        PR.StaticDataset(man, store, fixture_static(), ['ETHUSDT'])
    with pytest.raises(PR.PitRunError, match='owner-approved'):
        PR.StaticDataset(man, store, dict(fixture_static(), owner_approved=False), SYMS[:1])
    with pytest.raises((PR.PitRunError, M.ManifestError), match='repro'):
        PR.StaticDataset(dict(man, source_class='repro-only'), store, fixture_static(), SYMS[:1])


def test_registered_configurations_and_constants():
    names = [c['name'] for c in PR.configs()]
    pre = json.loads(G._load(ROOT, json.loads(G._load(ROOT, G.GATE))['prereg']))
    assert names[:2] == [pre['variants']['primary']['name'], pre['variants']['secondary'][0]['name']]
    assert names[2:14] == pre['neighbour_grid']['configs'] and names[14:] == ['B1', 'B2']
    assert PR.CAP == pre['primary_params']['time_cap_bars'] == pre['splits']['horizon_bars'] == 180
    assert 'seed 20261009' in pre['baselines']['B0'] and PR.SEED == 20261009
    assert '10,000' in pre['baselines']['p_value'] and PR.PERMS == PR.RESAMPLES == 10_000 and PR.B0_DRAWS == 1000
    assert pre['costs']['stress_rows'] == list(C.STRESS) and pre['costs']['slip_cal_v1']['c']['crypto'] == 0.05
    assert PR.RUN_TIERS == ('CORE-10', 'CORE-20')


def test_the_real_environment_derives_from_committed_files_only(tmp_path):
    """Manifest metadata + gate files only: the store path does not exist and no data row is read."""
    env = PR.real_env(ROOT, store=str(tmp_path / 'no-store'), author='t', cairo_date='2026-10-11', gate_path=G.GATE)
    u = json.loads(G._load(ROOT, PR.json.loads(G._load(ROOT, G.GATE))['universe']['path']))
    assert env['tiers'] == {'CORE-10': u['core_10'], 'CORE-20': u['core_20']} and len(env['tiers']['CORE-20']) == 20
    assert env['n_trials'] == 18 and env['run_config']['dev_keys'] == ['train'] + [f'walk_forward[{i}]' for i in range(5)]
    assert env['run_config']['slip_c'] == 0.05 and env['run_config']['resamples'] == PR.RESAMPLES
    assert env['identity']['universe'] == u['digest'] and env['identity']['classification'] is None
    assert 'SLIP-C-UNFITTED' in env['cost_labels'] and not os.path.exists(tmp_path / 'no-store')
    PG.check_identity(env['identity'])
    rules = env['rules']                                                 # the pinned M3 rules snapshot, sha-checked
    assert env['run_config']['rules'] == rules and env['run_config']['book'] == PR.BOOK
    assert set(rules) <= set(u['core_20']) and all(set(r) == {'tick', 'step', 'min_qty', 'min_notional'}
                                                    for r in rules.values())
    missing = sorted(set(u['core_20']) - set(rules))                    # fail closed: such a tier refuses to run
    if missing:
        with pytest.raises(PR.PitRunError, match='no pinned exchange rule'):
            PR.evaluate_tier(env, 'CORE-20', None)
