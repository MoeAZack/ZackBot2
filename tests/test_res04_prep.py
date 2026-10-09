"""RES-01 R4 prep: the M3 reproduction harness (tools/research/m3_repro.py), the `trend_ema_mom.v1` View evaluator
(trend_ema_mom.py), the preregistration and the gated entry point (run_r4.py). Synthetic fixtures only: no real data,
no returns of any real market, nothing run on the store."""
import csv
import hashlib
import importlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import ledger as L                                                                          # noqa: E402
import m3_repro as X                                                                        # noqa: E402
import manifest as M                                                                        # noqa: E402
import pit as P                                                                             # noqa: E402
import report as R                                                                          # noqa: E402
import run_r4 as G                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402
import trend_ema_mom as T                                                                   # noqa: E402

H4 = T.TF_MS
START = S.parse_utc('2024-01-01T00:00:00Z')
SYMS = ('AAAUSDT', 'BBBUSDT', 'CCCUSDT', 'DDDUSDT', 'EEEUSDT')
NBARS = 1200


def price(k, i):
    """Deterministic trending + oscillating synthetic path (no market data)."""
    return 100.0 * math.exp(0.0009 * i + 0.08 * math.sin(i / (5.0 + 2 * k)) + 0.02 * math.sin(i / 2.3 + k))


def write_csv(store, rel, k, lo, hi):
    p = os.path.join(store, *rel.split('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    lines = [M.HEADER]
    for i in range(lo, hi):
        o, c = round(price(k, i - 1), 4), round(price(k, i), 4)
        h = round(max(o, c) * (1 + 0.004 + 0.012 * ((i * 7 + k) % 5 == 0)), 4)
        lw = round(min(o, c) * (1 - 0.004 - 0.03 * ((i * 11 + k) % 13 == 0)), 4)
        t = S.utc(START + i * H4).replace('T', ' ').rstrip('Z')
        lines.append(f'{t},{o},{h},{lw},{c},1000')
    with open(p, 'w', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


@pytest.fixture(scope='module')
def legacy(tmp_path_factory):
    store = str(tmp_path_factory.mktemp('legacy'))
    for k, s in enumerate(SYMS):
        write_csv(store, f'data_long/{s}_4h.csv', k, 0, NBARS)
        write_csv(store, f"data/{s}_4h.csv", k, NBARS - 300, NBARS)          # overlapping second source, as legacy
    m = M.build(['data', 'data_long'], manifest_id='fx-legacy', source_class='fixture', base=store, data_root='repo',
                survivor_only=True)
    rules = {s: X.Rule(Decimal('0.0001'), Decimal('0.001'), Decimal('0.001'), Decimal('5')) for s in SYMS}
    return store, m, rules


def opened(legacy, tmp_path, *, lineage='root'):
    store, m, _ = legacy
    ds = P.Dataset(m, store, None)
    X.restrict_sources(ds, symbols=SYMS)
    lo, hi = X.span(ds, symbols=SYMS)
    (tmp_path / 'ledger').mkdir(exist_ok=True)
    acc = P.Access(ds, None, str(tmp_path / 'ledger' / 'trend_ema_mom.jsonl'), candidate_id=T.RULE_ID, author='t',
                   cairo_date='2026-10-09')
    return ds, acc.open_development(S.utc(lo), S.utc(hi), lineage=lineage), lo, hi


# ------------------------------------------------------------------ an independent M3-order reference simulator
def m3_order_reference(store, rules, costs, book=X.Book(), params=T.PRIMARY, end_bars=NBARS):
    """The M3 BookRunner order written directly over full arrays (no View): decide at close T, fill at once at the next
    open, play that bar at the next cycle. Independent structure from the harness's phase-shifted loop."""
    bars = {}
    for s in SYMS:
        rows = []
        with open(os.path.join(store, 'data_long', f'{s}_4h.csv')) as f:
            for ln in f.read().splitlines()[1:end_bars + 1]:
                t, o, h, lw, c, v = ln.split(',')
                om = M.parse_open_ms(t)
                rows.append(P.Bar(om, float(o), float(h), float(lw), float(c), 1.0, None, om + H4))
        bars[s] = rows
    D, Q = Decimal, X.dec
    wallet, lots, trades, closes = book.equity0, {}, [], {}
    day = day_start = halted = None
    n = len(bars[SYMS[0]])
    for i in range(n):
        t = START + (i + 1) * H4
        for s in sorted(lots):                       # play bar i
            lot, b = lots[s], bars[s][i]
            f_ = lot['qty'] * Q(b.close) * costs.funding_per_bar
            wallet -= f_
            lot['funding'] += f_
            hit = Q(b.open) if Q(b.open) <= lot['stop'] else (lot['stop'] if Q(b.low) <= lot['stop'] else None)
            if hit is not None:
                px = hit * (1 - costs.slip)
                fee = lot['qty'] * px * costs.taker
                gross = (px - lot['entry']) * lot['qty']
                wallet += gross - fee
                pnl = gross - lot['fees'] - fee - lot['funding']
                trades.append(dict(symbol=s, entry_ms=lot['entry_ms'], exit_ms=b.open_ms, exit_reason='exit.stop',
                                   qty=lot['qty'], r=X.RC.divide(pnl, lot['qty'] * (lot['entry'] - lot['stop']))))
                del lots[s]
        for s in SYMS:
            closes[s] = Q(bars[s][i].close)
        mtm = wallet + sum(((closes[s] - x['entry']) * x['qty'] for s, x in lots.items()), D(0))
        d = X.cairo_day(t)
        if d != day:
            day, day_start = d, mtm
        if halted != d and X.RC.subtract(X.RC.divide(mtm, day_start), 1) <= -book.daily_loss_pct:
            halted = d
        if i == n - 1:
            break
        sig = {}
        for s in SYMS:
            pre = bars[s][max(0, i + 1 - T.WINDOW):i + 1]
            sig[s] = T.signal_at_last(pre, params)
        for s in SYMS:                               # closes at the next open
            if sig[s] and sig[s][1] and s in lots:
                lot, nb = lots.pop(s), bars[s][i + 1]
                px = Q(nb.open) * (1 - costs.slip)
                fee = lot['qty'] * px * costs.taker
                gross = (px - lot['entry']) * lot['qty']
                wallet += gross - fee
                pnl = gross - lot['fees'] - fee - lot['funding']
                trades.append(dict(symbol=s, entry_ms=lot['entry_ms'], exit_ms=nb.open_ms, exit_reason='exit.exit_signal',
                                   qty=lot['qty'], r=X.RC.divide(pnl, lot['qty'] * (lot['entry'] - lot['stop']))))
        snap = wallet
        for s in SYMS:                               # entries at the next open
            if not (sig[s] and sig[s][0]) or s in lots or halted == d or len(lots) >= book.max_positions:
                continue
            dist = T.stop_distance(sig[s][2], params)
            ref = Q(bars[s][i].close)
            notional = sum((x['qty'] * (x['entry'] if x['entry_ms'] == t else closes[k]) for k, x in lots.items()), D(0))
            raw = X.SC.divide(X.SC.multiply(snap, book.risk_pct), dist)
            cap = X.SC.divide(max(X.SC.subtract(X.SC.multiply(book.max_leverage, snap), notional), D(0)),
                              X.SC.multiply(ref, X.SC.add(1, book.cap_gap_buffer)))
            q = X.q_down(min(raw, cap), rules[s].step)
            if q <= 0 or q < rules[s].min_qty or X.SC.multiply(q, ref) < rules[s].min_notional:
                continue
            nb = bars[s][i + 1]
            px = Q(nb.open) * (1 + costs.slip)
            fee = q * px * costs.taker
            wallet -= fee
            lots[s] = dict(qty=q, entry=px, stop=X.q_down(px - dist, rules[s].tick), fees=fee, funding=D(0),
                           entry_ms=nb.open_ms)
    return trades


def as_csv(trades):
    out = io.StringIO()
    w = csv.writer(out, lineterminator='\n')
    w.writerow(['symbol', 'side', 'signal_close_ms', 'entry_ms', 'exit_ms', 'qty', 'entry_price', 'exit_price',
                'stop_price', 'r', 'pnl', 'exit_reason'])
    for t in trades:
        w.writerow([t.symbol, t.side, t.signal_close_ms, t.entry_ms, t.exit_ms, t.qty, t.entry_price, t.exit_price,
                    t.stop_price, t.r, t.pnl, t.exit_reason])
    return out.getvalue()


# ------------------------------------------------------------------ M3 harness
@pytest.mark.parametrize('row', ['base', '2x'])
def test_harness_matches_an_independent_m3_order_simulator(legacy, tmp_path, row):
    store, m, rules = legacy
    ds, w, lo, hi = opened(legacy, tmp_path)
    costs = X.cost_row(row)
    rep = X.replay(w, symbols=SYMS, start_ms=lo, end_ms=hi, costs=costs, rules=rules)
    ref = m3_order_reference(store, rules, costs)
    got = sorted(((t.symbol, t.entry_ms, t.exit_ms, t.exit_reason, t.qty, t.r) for t in rep.trades))
    want = sorted(((t['symbol'], t['entry_ms'], t['exit_ms'], t['exit_reason'], t['qty'], t['r']) for t in ref))
    assert len(got) >= 8 and {t[3] for t in got} == {'exit.stop', 'exit.exit_signal'}
    assert got == want
    assert rep.decisions == (hi - lo) // H4 - 1


def test_book_limits_bind_on_the_fixture(legacy, tmp_path):
    store, m, rules = legacy
    _, w, lo, hi = opened(legacy, tmp_path)
    tight = X.Book(max_positions=1, daily_loss_pct=Decimal('0.002'))
    rep = X.replay(w, symbols=SYMS, start_ms=lo, end_ms=hi, costs=X.cost_row('base'), rules=rules, book=tight)
    assert rep.refusals.get('capacity.max_positions', 0) > 0 and rep.halts
    ref = m3_order_reference(store, rules, X.cost_row('base'), book=tight)
    assert sorted((t.symbol, t.entry_ms, t.r) for t in rep.trades) == sorted((t['symbol'], t['entry_ms'], t['r'])
                                                                              for t in ref)


def test_acceptance_compare_and_its_mutations(legacy, tmp_path):
    store, m, rules = legacy
    _, w, lo, hi = opened(legacy, tmp_path)
    rep = X.replay(w, symbols=SYMS, start_ms=lo, end_ms=hi, costs=X.cost_row('base'), rules=rules)
    text = as_csv(sorted(rep.trades, key=lambda t: (t.entry_ms, t.symbol)))
    doc = X.report(name='base', ref_text=text, rep=rep, meta={})
    assert doc['acceptance']['verdict'] == 'REPRODUCED' and doc['acceptance']['max_abs_dR'] == '0'
    assert doc['reference_summary'] == doc['repro_summary']
    rows = text.splitlines()
    drop = '\n'.join(rows[:1] + rows[2:]) + '\n'
    assert X.compare(X.parse_reference(drop), X.as_rows(rep.trades))['extra_in_repro']
    f = rows[1].split(',')
    f[5] = str(Decimal(f[5]) + Decimal('0.001'))                           # qty differs
    bad = X.compare(X.parse_reference('\n'.join([rows[0], ','.join(f)] + rows[2:]) + '\n'), X.as_rows(rep.trades))
    assert bad['verdict'] == 'NOT REPRODUCED' and bad['field_mismatches'][0]['fields'] == ['qty']
    f = rows[1].split(',')
    f[9] = str(Decimal(f[9]) + Decimal('1e-6'))                            # R beyond tolerance
    bad = X.compare(X.parse_reference('\n'.join([rows[0], ','.join(f)] + rows[2:]) + '\n'), X.as_rows(rep.trades))
    assert bad['field_mismatches'][0]['fields'] == ['r']


def test_strategy_runs_only_through_the_view_a_peeker_is_caught(legacy, tmp_path, monkeypatch):
    store, m, rules = legacy
    ds, w, lo, hi = opened(legacy, tmp_path)
    real = T.decide

    def peeking(view, **kw):
        f = ds.files('klines', SYMS[0], '4h')[0]
        future = [b for b in ds.rows(f) if b.available_ms > view.t][:1]     # bypasses the view
        return real(view, **kw) + ((('peek', future[0].close),) if future else ())
    monkeypatch.setattr(T, 'decide', peeking)
    with pytest.raises(P.PITError, match='future perturbation'):
        X.replay(w, symbols=SYMS, start_ms=lo, end_ms=hi, costs=X.cost_row('base'), rules=rules)


def test_truncating_the_future_never_changes_a_closed_trade(legacy, tmp_path):
    store, m, rules = legacy
    _, w, lo, hi = opened(legacy, tmp_path)
    full = X.replay(w, symbols=SYMS, start_ms=lo, end_ms=hi, costs=X.cost_row('base'), rules=rules, perturb=False)
    cut = hi - 120 * H4
    _, w2, _, _ = opened(legacy, tmp_path, lineage=None)                  # a window issues each decision once
    part = X.replay(w2, symbols=SYMS, start_ms=lo, end_ms=cut, costs=X.cost_row('base'), rules=rules, perturb=False)
    a = [(t.symbol, t.entry_ms, t.exit_ms, t.r) for t in full.trades if t.exit_ms < cut - H4]
    b = [(t.symbol, t.entry_ms, t.exit_ms, t.r) for t in part.trades if t.exit_ms < cut - H4]
    assert a == b and a


def test_overlapping_legacy_sources_are_refused_unless_restricted(legacy):
    store, m, _ = legacy
    ds = P.Dataset(m, store, None)
    assert len(ds.files('klines', SYMS[0], '4h')) == 2                    # data/ and data_long/ overlap
    X.restrict_sources(ds, symbols=SYMS)
    assert [f['path'] for f in ds.files('klines', SYMS[0], '4h')] == [f'data_long/{SYMS[0]}_4h.csv']
    with pytest.raises(X.ReproError, match='exactly one'):
        X.restrict_sources(P.Dataset(m, store, None), prefix='nowhere/', symbols=SYMS)


def test_cost_rows_come_from_the_r3_constants():
    b, x2 = X.cost_row('base'), X.cost_row('2x')
    assert (b.taker, b.slip, b.funding_per_bar) == (Decimal('0.0005'), Decimal('0.0002'), Decimal('0.00005'))
    assert (x2.taker, x2.slip, x2.funding_per_bar) == (Decimal('0.001'), Decimal('0.0004'), Decimal('0.00005'))
    with pytest.raises(X.ReproError):
        X.cost_row('3x')


def test_rules_snapshot_and_reference_are_pinned(tmp_path):
    p = tmp_path / 'rules.json'
    p.write_text(json.dumps({'schema': 'zackbot.exchange_rules/1', 'symbols': {}}))
    with pytest.raises(X.ReproError, match='pinned'):
        X.load_rules(str(p), [])
    assert X.load_rules(str(p), [], sha256=None) == {}
    with pytest.raises(X.ReproError, match='missing or not blob'):
        X.read_reference(str(tmp_path), 'base')                                # not a repo with the M3 commit


def test_episode_clustering_and_summary_are_deterministic():
    rows = [dict(symbol='A', entry_ms=0, exit_ms=10, exit_reason='exit.exit_signal', r=Decimal(1)),
            dict(symbol='B', entry_ms=5, exit_ms=20, exit_reason='exit.stop', r=Decimal(-1)),
            dict(symbol='A', entry_ms=20 + H4, exit_ms=30 + H4, exit_reason='exit.exit_signal', r=Decimal(2))]
    assert [len(e) for e in X.episodes(rows)] == [2, 1]
    assert X.summary(rows, resamples=200) == X.summary(rows, resamples=200)


# ------------------------------------------------------------------ strategy parity with the NEWCORE source
def _newcore_strategy(tmp_path):
    files = {}
    for rel in ('newcore/strategy/indicators.py', 'newcore/strategy/ema_mom.py'):
        p = subprocess.run(['git', '-C', ROOT, 'show', f'{X.REF_COMMIT}:{rel}'], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        if p.returncode:
            pytest.skip('M3 commit 45c22df not fetched')
        files[rel] = p.stdout
    pkg = tmp_path / 'ncref' / 'strat'
    pkg.mkdir(parents=True)
    (pkg / '__init__.py').write_text('')
    (pkg / 'indicators.py').write_text(files['newcore/strategy/indicators.py'])
    (pkg / 'ema_mom.py').write_text(files['newcore/strategy/ema_mom.py'])
    sys.path.insert(0, str(tmp_path / 'ncref'))
    try:
        return importlib.import_module('strat.ema_mom')
    finally:
        sys.path.remove(str(tmp_path / 'ncref'))


def test_signals_are_bit_identical_to_newcore_ema_mom(legacy, tmp_path):
    nc = _newcore_strategy(tmp_path)
    store, m, _ = legacy
    ds = P.Dataset(m, store, None)
    X.restrict_sources(ds, symbols=SYMS)
    for s in SYMS[:2]:
        rows = ds.rows(ds.files('klines', s, '4h')[0])
        b = nc.Bars(s, H4, [r.open_ms for r in rows], [r.open for r in rows], [r.high for r in rows],
                    [r.low for r in rows], [r.close for r in rows])
        ev = nc.evaluate(b, nc.Params(), as_of_ms=rows[-1].available_ms)
        hits = 0
        for i in range(len(rows)):
            got = T.signal_at_last(rows[:i + 1], T.PRIMARY)
            if i < ev.start:
                assert got is None
                continue
            assert (got[0], got[1], got[2]) == (ev.le[i], ev.lx[i], ev.atr[i])
            assert T.stop_distance(got[2], T.PRIMARY) == nc.stop_distance(ev.atr[i], nc.Params().stop_atr)
            hits += got[0]
        assert hits > 0


# ------------------------------------------------------------------ prereg + gate
def test_committed_prereg_validates():
    with open(os.path.join(ROOT, 'research_evidence', 'prereg', 'trend_ema_mom.v1.json'), 'rb') as f:
        raw = f.read()
    assert b'\r\n' not in raw
    pre = json.loads(raw)
    assert G.validate_prereg(pre, ROOT) == []
    plan = S.SplitPlan(pre['splits']['splits'], interval='4h', lookback_bars=240, horizon_bars=180)
    spent_end = S.parse_utc('2026-10-05T00:00:00Z')
    assert plan.access_range('holdout')[0] >= spent_end                     # holdout is forward-only
    assert all(plan.access_range(k)[1] <= spent_end for k in plan.keys() if k != 'holdout')
    assert len(G.trial_records(pre, '0' * 64, 'x')) == pre['trials']['declared']['total']


@pytest.mark.parametrize('mut, msg', [
    (lambda p: p['primary_params'].__setitem__('ema_fast', 21), 'primary_params'),
    (lambda p: p['primary_params'].pop('time_cap_bars'), 'time cap'),
    (lambda p: p['splits'].__setitem__('lookback_bars', 220), 'lookback_bars'),
    (lambda p: p['costs']['slip_cal_v1'].__setitem__('fitted', 'no'), 'slip-cal-v1'),
    (lambda p: p['universe_rule']['books'].pop('gold-commodity'), 'gold-commodity'),
    (lambda p: p['edge00'].pop('neighbours'), 'edge00'),
    (lambda p: p['neighbour_grid']['configs'].pop(), 'neighbour grid'),
])
def test_prereg_validation_refuses_drift(mut, msg):
    with open(os.path.join(ROOT, 'research_evidence', 'prereg', 'trend_ema_mom.v1.json')) as f:
        pre = json.load(f)
    mut(pre)
    assert any(msg in b for b in G.validate_prereg(pre, ROOT))


def git(repo, *a):
    return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', *a],
                          capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout.strip()


@pytest.fixture
def grepo(tmp_path):
    """A throwaway repo: research modules, the prereg, a universe stub, a fresh family ledger, two slice branches."""
    r = tmp_path / 'repo'
    for rel in R.CORE_EVAL_FILES + G.EVAL_FILES + ('research_evidence/prereg/trend_ema_mom.v1.json',):
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, rel), r / rel)
    pre = json.loads((r / 'research_evidence/prereg/trend_ema_mom.v1.json').read_text())
    (r / 'research_evidence/universe').mkdir(parents=True)
    (r / G.UNIVERSE).write_text(json.dumps({'digest': pre['data']['universe_digest'],
                                            'manifest_digest': pre['data']['manifest_digest']}))
    (r / '.gitattributes').write_text('* -text\n')
    git(tmp_path, 'init', '-q', '-b', 'master', str(r))
    led = str(r / 'research_evidence/ledger/trend_ema_mom.jsonl')
    os.makedirs(os.path.dirname(led))
    L.append(led, kind='window_spent', candidate_id=T.RULE_ID, split='development',
             window={'start': '2021-12-19T00:00:00Z', 'end': '2026-10-05T00:00:00Z'}, author='t',
             cairo_date='2026-10-09', lineage='root')
    git(r, 'add', '-A')
    git(r, 'commit', '-q', '-m', 'base')
    heads = {}
    for b in ('r2', 'r3'):
        git(r, 'checkout', '-q', '-b', b, 'master')
        (r / f'{b}.txt').write_text(b)
        git(r, 'add', '-A')
        git(r, 'commit', '-q', '-m', b)
        heads[b] = git(r, 'rev-parse', 'HEAD')
    git(r, 'checkout', '-q', 'master')
    gate = {'format': 'zb-r4-gate/1', 'prereg': 'research_evidence/prereg/trend_ema_mom.v1.json',
            'ledger': 'research_evidence/ledger/trend_ema_mom.jsonl', 'integration_ref': 'master',
            'cleared': [{'slice': 'R1+R2', 'pr': 51, 'branch': 'r2', 'head': heads['r2']},
                        {'slice': 'R3', 'pr': 52, 'branch': 'r3', 'head': heads['r3']}]}
    (r / G.GATE).write_text(json.dumps(gate))
    git(r, 'add', '-A')
    git(r, 'commit', '-q', '-m', 'gate')
    with open(r / 'research_evidence/ledger/REGISTRY.jsonl', 'rb') as f:
        gen = hashlib.sha256(f.read().splitlines(keepends=True)[0]).hexdigest()
    return str(r), gen


def test_gate_refuses_until_registered_committed_and_cleared(grepo):
    repo, gen = grepo
    kw = dict(registry_genesis=gen)
    fail = G.gate(repo, **kw)
    assert any('G3 no preregistration' in f for f in fail) and sum('not merged' in f for f in fail) == 2
    with pytest.raises(G.GateError):
        G.run_m3(repo, store=repo, rows=('base',), out_dir=repo, author='t', cairo_date='2026-10-09', gate_kw=kw)
    recs = G.register(repo, author='t', cairo_date='2026-10-09', registry_genesis=gen)
    assert [r['kind'] for r in recs].count('grid_point') == 12 and len(recs) == 18
    fail = G.gate(repo, **kw)
    assert any('G3 the registration records are not committed at HEAD' in f for f in fail)
    assert not any('G1' in f for f in fail)                          # research_evidence/ is outside the code identity
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'register prereg')
    with pytest.raises(G.GateError, match='already registered'):
        G.register(repo, author='t', cairo_date='2026-10-09', registry_genesis=gen)
    assert [f for f in G.gate(repo, **kw) if not f.startswith('G4')] == []
    git(repo, 'merge', '-q', '--no-ff', '-m', 'merge r2', 'r2')
    git(repo, 'merge', '-q', '--no-ff', '-m', 'merge r3', 'r3')
    assert G.gate(repo, **kw) == []
    with open(os.path.join(repo, 'tools/research/trend_ema_mom.py'), 'a') as f:
        f.write('# edit' + chr(10))
    assert any('G1 dirty' in f for f in G.gate(repo, **kw))
    git(repo, 'checkout', '--', 'tools/research/trend_ema_mom.py')
    recs = L.verify(os.path.join(repo, 'research_evidence/ledger/trend_ema_mom.jsonl'), registry_genesis=gen)
    assert L.n_trials(recs['trend_ema_mom']) == 18


def test_gate_closes_on_a_moved_branch_or_an_edited_prereg(grepo):
    repo, gen = grepo
    G.register(repo, author='t', cairo_date='2026-10-09', registry_genesis=gen)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'register')
    git(repo, 'merge', '-q', '--no-ff', '-m', 'm2', 'r2')
    git(repo, 'merge', '-q', '--no-ff', '-m', 'm3', 'r3')
    assert G.gate(repo, registry_genesis=gen) == []
    git(repo, 'checkout', '-q', 'r3')
    with open(os.path.join(repo, 'r3.txt'), 'a') as f:
        f.write('fix round')
    git(repo, 'commit', '-q', '-am', 'codex fix round')
    git(repo, 'checkout', '-q', 'master')
    assert any('moved' in f for f in G.gate(repo, registry_genesis=gen))
    git(repo, 'branch', '-q', '-f', 'r3', 'r3~1')                     # the fix round is cleared away again
    assert G.gate(repo, registry_genesis=gen) == []
    p = os.path.join(repo, 'research_evidence/prereg/trend_ema_mom.v1.json')
    with open(p, 'rb') as f:
        raw = f.read()
    with open(p, 'wb') as f:
        f.write(raw.replace(b'"stop_atr": "2.5"', b'"stop_atr": "2.0"'))
    git(repo, 'commit', '-q', '-am', 'retune attempt')
    fail = G.gate(repo, registry_genesis=gen)
    assert any('primary_params' in f for f in fail) and any('G3 no preregistration' in f for f in fail)


def test_pit_subcommand_and_m3_never_run_with_the_gate_closed():
    assert G.main(['pit', '--author', 't', '--cairo-date', '2026-10-09']) == 3      # this branch: not registered
    assert G.main(['m3', '--store', ROOT, '--author', 't', '--cairo-date', '2026-10-09']) == 3
