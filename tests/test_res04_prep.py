"""RES-01 R4-0 prep: the M3 reproduction harness (tools/research/m3_repro.py) running the M3 book evaluator
(m3_eval.py) only through the accepted R3 sandboxed runner, the `trend_ema_mom.v1` signal code, the preregistration
and the gated entry point (run_r4.py). Synthetic fixtures only: no real data, no returns of any real market, nothing
run on the store, the holdout never opened."""
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
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import costs as C                                                                           # noqa: E402
import ledger as L                                                                          # noqa: E402
import m3_eval as E                                                                         # noqa: E402
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
NBARS = 1000
RULE = ("E.Rule(D('0.0001'), D('0.001'), D('0.001'), D('5'))")
FIX_EVAL = f'''from decimal import Decimal as D

import m3_eval as E

SYMS = {SYMS!r}
RULES = {{s: {RULE} for s in SYMS}}
_B = E.Machine(symbols=SYMS, rules=RULES, costs=E.COSTS['base'])
_X2 = E.Machine(symbols=SYMS, rules=RULES, costs=E.COSTS['2x'])
_T = E.Machine(symbols=SYMS, rules=RULES, costs=E.COSTS['base'],
               book=E.Book(max_positions=1, daily_loss_pct=D('0.002')))


def base(view):
    return _B.decide(view)


def x2(view):
    return _X2.decide(view)


def tight(view):
    return _T.decide(view)


summary = E.summary
'''
FUNCS = {'base': 'base', '2x': 'x2'}


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
    m = M.build(['data_long'], manifest_id='fx-m3-repro', source_class='repro-only', base=store, data_root='repo',
                survivor_only=True)
    rules = {s: X.Rule(Decimal('0.0001'), Decimal('0.001'), Decimal('0.001'), Decimal('5')) for s in SYMS}
    ev = os.path.join(str(tmp_path_factory.mktemp('ev')), 'm3_fixture_eval.py')
    with open(ev, 'w', newline='\n') as f:
        f.write(FIX_EVAL)
    return store, m, rules, ev


def opened(legacy, tmp_path, *, lineage='root', manifest=None):
    store, m, _, _ = legacy
    ds = P.Dataset(manifest or m, store, None)
    lo, hi = X.span(ds, symbols=SYMS)
    (tmp_path / 'ledger').mkdir(exist_ok=True)
    acc = P.Access(ds, None, str(tmp_path / 'ledger' / 'trend_ema_mom.jsonl'), candidate_id=T.RULE_ID, author='t',
                   cairo_date='2026-10-10')
    return ds, acc.open_development(S.utc(lo), S.utc(hi), lineage=lineage), lo, hi


@pytest.fixture(scope='module')
def runs(legacy, tmp_path_factory):
    """One attested, perturbed sandbox run per fixture function (shared: each run is two fresh processes)."""
    out = {}
    for name, fn in (('base', 'base'), ('2x', 'x2'), ('tight', 'tight')):
        tmp = tmp_path_factory.mktemp(f'run_{name}')
        _, w, lo, hi = opened(legacy, tmp)
        out[name] = (X.replay(w, start_ms=lo, end_ms=hi, function=fn, evaluator=legacy[3]), tmp, lo, hi)
    return out


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
        w.writerow([t[k] for k in ('symbol', 'side', 'signal_close_ms', 'entry_ms', 'exit_ms', 'qty', 'entry_price',
                                   'exit_price', 'stop_price', 'r', 'pnl', 'exit_reason')])
    return out.getvalue()


def key_of(trades):
    return sorted((t['symbol'], t['entry_ms'], t['exit_ms'], t['exit_reason'], Decimal(t['qty']), Decimal(t['r']))
                  for t in trades)


# ------------------------------------------------------------------ M3 harness through the sandboxed runner
@pytest.mark.parametrize('row', ['base', '2x'])
def test_harness_matches_an_independent_m3_order_simulator(legacy, runs, row):
    store, m, rules, _ = legacy
    ev, _, lo, hi = runs[row]
    ref = m3_order_reference(store, rules, X.cost_row(row))
    got = key_of(ev.results['trades'])
    assert len(got) >= 8 and {t[3] for t in got} == {'exit.stop', 'exit.exit_signal'}
    assert got == sorted((t['symbol'], t['entry_ms'], t['exit_ms'], t['exit_reason'], t['qty'], t['r']) for t in ref)
    assert ev.results['cycles'] == len(X.schedule(lo, hi)) == (hi - lo) // H4


def test_book_limits_bind_on_the_fixture(legacy, runs):
    store, m, rules, _ = legacy
    ev = runs['tight'][0]
    assert ev.results['refusals'].get('capacity.max_positions', 0) > 0 and ev.results['halts']
    ref = m3_order_reference(store, rules, X.cost_row('base'), book=X.Book(max_positions=1,
                                                                         daily_loss_pct=Decimal('0.002')))
    assert sorted((t['symbol'], t['entry_ms'], Decimal(t['r'])) for t in ev.results['trades']) == \
        sorted((t['symbol'], t['entry_ms'], t['r']) for t in ref)


def test_m3_runs_only_in_the_sandbox_with_a_perturbed_attestation(legacy, runs):
    ev, tmp, lo, hi = runs['base']
    att = ev.attestation
    assert att['perturbed'] is True and att['isolation'] == P.ISOLATION_ID and att['runner'] == P.RUNNER_ID
    assert att['evaluator']['function'] == 'base' and att['evaluator']['summary'] == 'summary'
    assert att['schedule'] == R.schedule_of(X.schedule(lo, hi))
    assert {c['path'] for c in att['code']} >= {'tools/research/m3_eval.py', 'tools/research/trend_ema_mom.py'}
    rec = L._check_state(str(tmp / 'ledger' / 'trend_ema_mom.jsonl'))['trend_ema_mom'][-1]
    assert rec['detail']['evaluation_attestation'] == ev.attestation_digest
    assert len(ev) == ev.results['cycles'] and ev.results['trades'] == sorted(
        (t for o in ev for t in o['trades']), key=lambda t: (t['entry_ms'], t['symbol']))
    assert att['decisions_digest']                                    # View.enter capabilities issued at fills
    assert not hasattr(X, 'restrict_sources') and not hasattr(X, 'LEGACY_DIGEST')
    assert 'decide' not in vars(X) and 'P.evaluate(window, evaluator' in open(X.__file__).read()


def test_acceptance_compare_and_its_mutations(runs):
    ev = runs['base'][0]
    trades = ev.results['trades']
    text = as_csv(trades)
    doc = X.report(name='base', ref_text=text, ev=ev, meta={})
    assert doc['acceptance']['verdict'] == 'REPRODUCED' and Decimal(doc['acceptance']['max_abs_dR']) == 0
    assert doc['reference_summary'] == doc['repro_summary']
    assert doc['attestation']['digest'] == ev.attestation_digest and doc['attestation']['perturbed'] is True
    rows = text.splitlines()
    drop = '\n'.join(rows[:1] + rows[2:]) + '\n'
    assert X.compare(X.parse_reference(drop), X.as_rows(trades))['extra_in_repro']
    f = rows[1].split(',')
    f[5] = str(Decimal(f[5]) + Decimal('0.001'))                           # qty differs
    bad = X.compare(X.parse_reference('\n'.join([rows[0], ','.join(f)] + rows[2:]) + '\n'), X.as_rows(trades))
    assert bad['verdict'] == 'NOT REPRODUCED' and bad['field_mismatches'][0]['fields'] == ['qty']
    f = rows[1].split(',')
    f[9] = str(Decimal(f[9]) + Decimal('1e-6'))                            # R beyond tolerance
    bad = X.compare(X.parse_reference('\n'.join([rows[0], ','.join(f)] + rows[2:]) + '\n'), X.as_rows(trades))
    assert bad['field_mismatches'][0]['fields'] == ['r']


def test_legacy_flat_funding_adapter_cannot_enter_primary_results(legacy, tmp_path):
    """R4-0 item 4: the flat per-bar funding adapter is reproduction-only. The harness refuses a promotion-eligible
    dataset, and the sandboxed evaluator itself refuses without the REPRO-ONLY label."""
    store, m, _, ev = legacy
    prim = M.build(['data_long'], manifest_id='fx-primary', source_class='fixture', base=store, data_root='repo',
                   survivor_only=True)
    _, w, lo, hi = opened(legacy, tmp_path, manifest=prim)
    with pytest.raises(X.ReproError, match='reproduction-only'):
        X.replay(w, start_ms=lo, end_ms=lo + 3 * H4, function='base', evaluator=ev)
    with pytest.raises(P.PITError, match='REPRO-ONLY'):
        P.evaluate(w, ev, 'base', X.schedule(lo, lo + 3 * H4), summary='summary')
    assert C.STRESS['base'].funding_mode == 'actual'                      # the primary R3 row is actual funding
    assert E.COSTS['base'] == X.cost_row('base') and E.COSTS['2x'] == X.cost_row('2x')


def test_repro_only_manifest_opens_only_development_windows(legacy, tmp_path):
    store, m, _, _ = legacy
    ds = P.Dataset(m, store, None)
    assert P.REPRO_ONLY in ds.labels and P.SURVIVOR_ONLY in ds.labels
    plan = S.SplitPlan([{'name': 'calibration', 'start': '2024-01-01T00:00:00Z', 'end': '2024-02-05T00:00:00Z'},
                        {'name': 'train', 'start': '2024-02-05T00:00:00Z', 'end': '2024-03-04T00:00:00Z'},
                        {'name': 'holdout', 'start': '2024-03-04T00:00:00Z', 'end': '2024-04-01T00:00:00Z'}],
                       interval='4h', lookback_bars=20, horizon_bars=5)
    acc = P.Access(ds, plan, str(tmp_path / 'f.jsonl'), candidate_id=T.RULE_ID, author='t', cairo_date='2026-10-10')
    with pytest.raises(P.PITError, match='reproduction-only'):
        acc.open('train', lineage='root')


def test_truncating_the_future_never_changes_a_closed_trade(legacy, runs, tmp_path):
    full = runs['base'][0].results['trades']
    _, w, lo, hi = opened(legacy, tmp_path, lineage='root')
    cut = hi - 120 * H4
    part = X.replay(w, start_ms=lo, end_ms=cut, function='base', evaluator=legacy[3], perturb=False)
    a = [(t['symbol'], t['entry_ms'], t['exit_ms'], t['r']) for t in full if t['exit_ms'] < cut - H4]
    b = [(t['symbol'], t['entry_ms'], t['exit_ms'], t['r']) for t in part.results['trades'] if t['exit_ms'] < cut - H4]
    assert a == b and a


def test_overlapping_sources_are_refused_by_the_dataset_and_the_repro_manifest_is_pinned(legacy, tmp_path):
    store, m, _, _ = legacy
    write_csv(store, f'data/{SYMS[0]}_4h.csv', 0, NBARS - 300, NBARS)          # an overlapping second source
    try:
        both = M.build(['data', 'data_long'], manifest_id='fx-overlap', source_class='repro-only', base=store,
                       data_root='repo', survivor_only=True)
        with pytest.raises(P.OverlapError):
            P.Dataset(both, store, None)
    finally:
        shutil.rmtree(os.path.join(store, 'data'))
    man = M.load(os.path.join(ROOT, X.REPRO_MANIFEST))
    assert (man['manifest_id'], man['digest'], man['source_class']) == (X.REPRO_ID, X.REPRO_DIGEST, M.REPRO_ONLY)
    assert sorted(f['symbol'] for f in man['files']) == sorted(X.CORE8)
    assert all(f['path'].startswith('data_long/4h/') for f in man['files'])
    X.require_repro(P.Dataset(man, ROOT, None))                                # zero overlaps, repro-only
    with pytest.raises(P.PITError):
        P.Dataset(man, ROOT, {'format': 'x'}, books=['crypto'])               # never with a universe


def test_cost_rows_rules_and_embedded_constants_are_pinned(tmp_path):
    b, x2 = X.cost_row('base'), X.cost_row('2x')
    assert (b.taker, b.slip, b.funding_per_bar) == (Decimal('0.0005'), Decimal('0.0002'), Decimal('0.00005'))
    assert (x2.taker, x2.slip, x2.funding_per_bar) == (Decimal('0.001'), Decimal('0.0004'), Decimal('0.00005'))
    with pytest.raises(X.ReproError):
        X.cost_row('3x')
    X.check_embedded(ROOT)                                                     # m3_eval rules == pinned snapshot
    p = tmp_path / 'rules.json'
    p.write_text(json.dumps({'schema': 'zackbot.exchange_rules/1', 'symbols': {}}))
    with pytest.raises(X.ReproError, match='pinned'):
        X.load_rules(str(p), [])
    assert X.load_rules(str(p), [], sha256=None) == {}
    with pytest.raises(X.ReproError, match='missing or not blob'):
        X.read_reference(str(tmp_path), 'base')                                # not a repo with the M3 commit


def test_cairo_day_table_matches_zoneinfo_hour_by_hour():
    z = ZoneInfo('Africa/Cairo')
    t = E.CAIRO_OFFSETS[0][0]
    while t < E.CAIRO_END_MS:
        assert E.cairo_day(t) == datetime.fromtimestamp(t // 1000, tz=timezone.utc).astimezone(z).date().isoformat()
        t += 3_600_000
    for bad in (E.CAIRO_OFFSETS[0][0] - 1, E.CAIRO_END_MS):
        with pytest.raises(ValueError):
            E.cairo_day(bad)


def test_m3_reports_are_written_atomically(tmp_path, monkeypatch):
    p = str(tmp_path / 'm3_repro_base.json')
    X.atomic_write_json(p, {'a': 1})
    assert json.load(open(p)) == {'a': 1}

    def boom(src, dst):
        raise OSError('interrupted')
    monkeypatch.setattr(X.os, 'replace', boom)
    with pytest.raises(OSError):
        X.atomic_write_json(p, {'a': 2})
    with pytest.raises(OSError):
        X.atomic_write_json(str(tmp_path / 'new.json'), {'b': 1})
    monkeypatch.undo()
    assert json.load(open(p)) == {'a': 1}                                      # old content intact
    assert sorted(os.listdir(tmp_path)) == ['m3_repro_base.json']              # no partial file, no temp left


def test_warmup_known_answer():
    """R4-0 item 3: no signal before the configuration's start index; the first evaluable bar is exactly
    max(warmup, 7d, 30d bars) = 220 for the primary; a gap restarts the count (never forward-filled)."""
    assert T.PRIMARY.start() == 220 and T.PRIMARY.bars_of(7) == 42 and T.PRIMARY.bars_of(30) == 180
    assert T.max_lookback_bars([T.PRIMARY] + [p for _, p in T.neighbours()]) == 240
    bars = [P.Bar(START + i * H4, price(0, i - 1), price(0, i) * 1.01, price(0, i) * 0.99, price(0, i), 1.0, None,
                  START + (i + 1) * H4) for i in range(600)]
    assert T.signal_at_last(bars[:220], T.PRIMARY) is None                     # index 219
    assert T.signal_at_last(bars[:221], T.PRIMARY) is not None                 # index 220: first evaluable
    gap = bars[:300] + [b._replace(open_ms=b.open_ms + H4, available_ms=b.available_ms + H4) for b in bars[300:]]
    tail = T.contiguous_tail(gap[:300 + 219])
    assert len(tail) == 219 and T.signal_at_last(tail, T.PRIMARY) is None      # 219 bars after the gap: none
    tail = T.contiguous_tail(gap[:300 + 221])
    assert len(tail) == 221 and T.signal_at_last(tail, T.PRIMARY) is not None


# ------------------------------------------------------------------ F1: gaps while exposed (Cowork 6095679832)
def holed(legacy, tmp_path, sym, drop):
    """A copy of the fixture store with bar indices `drop` removed from `sym` (a data hole), as a repro manifest."""
    store, _, _, _ = legacy
    new = str(tmp_path / 'holed')
    shutil.copytree(store, new)
    p = os.path.join(new, 'data_long', f'{sym}_4h.csv')
    lines = open(p).read().splitlines()
    keep = [ln for i, ln in enumerate(lines) if i == 0 or (i - 1) not in set(drop)]
    with open(p, 'w', newline=chr(10)) as f:
        f.write(chr(10).join(keep) + chr(10))
    m = M.build(['data_long'], manifest_id='fx-holed', source_class='repro-only', base=new, data_root='repo',
                survivor_only=True)
    return new, m


def idx(ms):
    return (ms - START) // H4


def test_an_open_lot_meeting_a_gap_fails_closed(legacy, runs, tmp_path):
    """Cowork F1 probe: a lot open across a data hole must not be carried silently (missed stop / funding)."""
    tr = next(t for t in runs['base'][0].results['trades'] if t['symbol'] != SYMS[0]
              and idx(t['exit_ms']) - idx(t['entry_ms']) >= 6)
    e = idx(tr['entry_ms'])
    store, m = holed(legacy, tmp_path, tr['symbol'], range(e + 2, e + 5))
    ds = P.Dataset(m, store, None)
    lo, hi = X.span(ds, symbols=SYMS)
    acc = P.Access(ds, None, str(tmp_path / 'trend_ema_mom.jsonl'), candidate_id=T.RULE_ID, author='t',
                   cairo_date='2026-10-10')
    w = acc.open_development(S.utc(lo), S.utc(hi), lineage='root')
    with pytest.raises(P.PITError, match='GapUnderPositionError'):
        X.replay(w, start_ms=lo, end_ms=START + (e + 10) * H4, function='base', evaluator=legacy[3], perturb=False)
    cov = X.coverage(ds, lo, hi, symbols=SYMS, read_rows=True)
    assert cov['series'][tr['symbol']]['missing'] == 3 and cov['total_missing'] == 3 and not cov['zero_missing']
    assert cov['series'][tr['symbol']]['first_missing'][0] == S.utc(START + (e + 2) * H4)
    assert X.coverage(ds, lo, hi, symbols=SYMS)['series'][tr['symbol']]['missing'] == 3    # metadata deficit


def test_a_gap_while_flat_only_restarts_warm_up(legacy, runs, tmp_path):
    """Warm-up reset on a gap stays allowed while flat: no error, and no entry for that symbol until its contiguous
    history reaches the start index again."""
    sym = SYMS[1]
    busy = [(idx(t['entry_ms']) - 1, idx(t['exit_ms']) + 1) for t in runs['base'][0].results['trades']
            if t['symbol'] == sym]
    g = next(i for i in range(450, NBARS - 260) if all(not (a <= j <= b) for a, b in busy for j in range(i - 2, i + 5)))
    store, m = holed(legacy, tmp_path, sym, range(g, g + 3))
    ds = P.Dataset(m, store, None)
    lo, hi = X.span(ds, symbols=SYMS)
    acc = P.Access(ds, None, str(tmp_path / 'trend_ema_mom.jsonl'), candidate_id=T.RULE_ID, author='t',
                   cairo_date='2026-10-10')
    w = acc.open_development(S.utc(lo), S.utc(hi), lineage='root')
    ev = X.replay(w, start_ms=lo, end_ms=hi, function='base', evaluator=legacy[3], perturb=False)
    after = [t for t in ev.results['trades'] if t['symbol'] == sym and idx(t['entry_ms']) >= g]
    assert all(idx(t['signal_close_ms']) - 1 >= g + 3 + T.PRIMARY.start() for t in after)


def test_coverage_proves_zero_missing_bars_or_names_the_holes(legacy):
    store, m, _, _ = legacy
    ds = P.Dataset(m, store, None)
    lo, hi = X.span(ds, symbols=SYMS)
    for read in (False, True):
        cov = X.coverage(ds, lo, hi, symbols=SYMS, read_rows=read)
        assert cov['zero_missing'] and cov['expected_per_series'] == NBARS == (hi - lo) // H4
        assert cov['mode'] == ('bytes' if read else 'metadata')
    assert X.coverage(ds, lo, hi + H4, symbols=SYMS)['total_missing'] == len(SYMS)       # window beyond the data
    with pytest.raises(X.ReproError):
        X.coverage(ds, lo + 1, hi, symbols=SYMS)


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
    store, m, _, _ = legacy
    ds = P.Dataset(m, store, None)
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
def load_prereg():
    with open(os.path.join(ROOT, 'research_evidence', 'prereg', 'trend_ema_mom.v1.json'), 'rb') as f:
        return f.read()


def test_committed_prereg_validates():
    raw = load_prereg()
    assert b'\r\n' not in raw
    pre = json.loads(raw)
    assert G.validate_prereg(pre, ROOT) == []
    plan = S.SplitPlan(pre['splits']['splits'], interval='4h', lookback_bars=240, horizon_bars=180)
    spent_end = S.parse_utc('2026-10-05T00:00:00Z')
    assert plan.access_range('holdout')[0] >= spent_end                     # holdout is forward-only
    assert all(plan.access_range(k)[1] <= spent_end for k in plan.keys() if k != 'holdout')
    recs = G.trial_records(pre, '0' * 64, 'x')
    assert len(recs) == pre['trials']['declared']['total'] == 18
    assert [(r['detail']['role'], r['detail'].get('book'), r['detail'].get('config')) for r in recs[:3]] == [
        ('preregistration', 'crypto', G.PRIMARY_NAME), ('development_only_variant', 'crypto', G.DEV_ONLY),
        ('neighbour', 'crypto', 'ema_fast-5')]
    assert not any(r['detail'].get('book') == 'gold' for r in recs)              # gold: own future budget
    assert pre['primary_params']['time_cap_bars'] == 180 == pre['splits']['horizon_bars']
    assert pre['variants']['primary']['promotable'] is True
    assert pre['variants']['secondary'][0]['time_cap_bars'] is None
    assert pre['variants']['secondary'][0]['promotable'] is False
    assert set(pre['universe_rule']['books']) == {'crypto'}
    assert pre['data']['classification']['digest'] == 'PENDING'
    assert 'UNREGISTERED' in pre['status']


@pytest.mark.parametrize('mut, msg', [
    (lambda p: p['primary_params'].__setitem__('ema_fast', 21), 'primary_params'),
    (lambda p: p['primary_params'].pop('time_cap_bars'), 'finite time cap'),
    (lambda p: p['primary_params'].__setitem__('time_cap_bars', 120), 'finite time cap'),
    (lambda p: p['variants']['primary'].__setitem__('time_cap_bars', None), 'variants.primary'),
    (lambda p: p['variants']['secondary'][0].__setitem__('promotable', True), 'variants.secondary'),
    (lambda p: p['splits'].__setitem__('horizon_bars', 240), 'purge'),
    (lambda p: p['variants'].__setitem__('selection', 'best of the two on walk-forward'), 'adaptive'),
    (lambda p: p['splits'].__setitem__('lookback_bars', 220), 'lookback_bars'),
    (lambda p: p['costs']['slip_cal_v1'].__setitem__('fitted', 'no'), 'slip-cal-v1'),
    (lambda p: p['universe_rule']['books'].__setitem__('gold-commodity', 'x'), 'only book'),
    (lambda p: p['costs']['rows'].__setitem__('gold', 'x'), 'crypto row only'),
    (lambda p: p['universe_rule']['deferred_pilot']['gold-tokenized'].__setitem__('cost_row', 'gold-spot'),
     'separate cost rows'),
    (lambda p: p['verdict_rules']['REJECT']['retained_gates'].pop('drawdown'), 'retain'),
    (lambda p: p['trials']['declared'].__setitem__('total', 19), '18'),
    (lambda p: p['trials']['declared'].__setitem__('variant', 3), 'crypto family only'),
    (lambda p: p['m3_reproduction']['manifest'].__setitem__('digest', '0' * 64), 'legacy-m3-repro-v1'),
    (lambda p: p['edge00'].pop('neighbours'), 'edge00'),
    (lambda p: p['neighbour_grid']['configs'].pop(), 'neighbour grid'),
])
def test_prereg_validation_refuses_drift(mut, msg):
    pre = json.loads(load_prereg())
    mut(pre)
    assert any(msg in b for b in G.validate_prereg(pre, ROOT))


def git(repo, *a):
    return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', *a],
                          capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout.strip()


@pytest.fixture
def grepo(tmp_path):
    """A throwaway repo: research modules, the prereg, a universe stub, a fresh family ledger, a foundation commit
    pinned on master's line and a follow-up branch pinned by exact SHA."""
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
    git(r, 'commit', '-q', '-m', 'r1-r3 merged')
    foundation = git(r, 'rev-parse', 'HEAD')
    git(r, 'checkout', '-q', '-b', 'overlap', 'master')
    (r / 'overlap.txt').write_text('overlap')
    git(r, 'add', '-A')
    git(r, 'commit', '-q', '-m', 'overlap follow-up')
    follow = git(r, 'rev-parse', 'HEAD')
    git(r, 'checkout', '-q', '-b', 'r4', 'overlap')
    gate = {'format': G.GATE_FORMAT, 'prereg': 'research_evidence/prereg/trend_ema_mom.v1.json',
            'ledger': 'research_evidence/ledger/trend_ema_mom.jsonl', 'integration_ref': 'master',
            'pins': [{'slice': 'R1+R2+R3', 'prs': [51, 52], 'head': foundation},
                     {'slice': 'overlap', 'prs': [56], 'branch': 'overlap', 'head': follow, 'after': foundation}],
            'classification': {'id': 'instrument-classes-v2', 'digest': 'PENDING'}}
    (r / G.GATE).write_text(json.dumps(gate))
    git(r, 'add', '-A')
    git(r, 'commit', '-q', '-m', 'gate')
    with open(r / 'research_evidence/ledger/REGISTRY.jsonl', 'rb') as f:
        gen = hashlib.sha256(f.read().splitlines(keepends=True)[0]).hexdigest()
    return str(r), gen


def bind_classification(repo, digest):
    p = os.path.join(repo, G.GATE)
    g = json.load(open(p))
    g['classification']['digest'] = digest
    with open(p, 'w') as f:
        json.dump(g, f)
    git(repo, 'commit', '-q', '-am', 'bind classification')


def test_gate_refuses_until_registered_committed_merged_and_classified(grepo):
    repo, gen = grepo
    kw = dict(registry_genesis=gen)
    fail = G.gate(repo, **kw)
    assert any('G3 no preregistration' in f for f in fail)
    assert [f for f in fail if f.startswith('G4')] == [f for f in fail if 'overlap' in f and 'not merged' in f]
    assert any(f.startswith('G5') and 'PENDING' in f for f in fail)
    with pytest.raises(G.GateError):
        G.run_m3(repo, store=repo, rows=('base',), out_dir=repo, author='t', cairo_date='2026-10-10', gate_kw=kw)
    recs = G.register(repo, author='t', cairo_date='2026-10-10', registry_genesis=gen)
    assert [r['kind'] for r in recs].count('grid_point') == 12 and len(recs) == 18
    fail = G.gate(repo, **kw)
    assert any('G3 the registration records are not committed at HEAD' in f for f in fail)
    assert not any('G1' in f for f in fail)                          # research_evidence/ is outside the code identity
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'register prereg')
    with pytest.raises(G.GateError, match='already registered'):
        G.register(repo, author='t', cairo_date='2026-10-10', registry_genesis=gen)
    assert sorted(f[:2] for f in G.gate(repo, **kw)) == ['G4', 'G5']
    git(repo, 'checkout', '-q', 'master')
    git(repo, 'merge', '-q', '--no-ff', '-m', 'merge overlap', 'overlap')
    git(repo, 'checkout', '-q', 'r4')
    git(repo, 'merge', '-q', '--no-ff', '-m', 'merge master', 'master')
    assert [f[:2] for f in G.gate(repo, **kw)] == ['G5']               # classification PENDING keeps it closed
    bind_classification(repo, 'c' * 64)
    assert G.gate(repo, **kw) == []
    with open(os.path.join(repo, 'tools/research/trend_ema_mom.py'), 'a') as f:
        f.write('# edit' + chr(10))
    assert any('G1 dirty' in f for f in G.gate(repo, **kw))
    git(repo, 'checkout', '--', 'tools/research/trend_ema_mom.py')
    recs = L.verify(os.path.join(repo, 'research_evidence/ledger/trend_ema_mom.jsonl'), registry_genesis=gen)
    assert L.n_trials(recs['trend_ema_mom']) == 18


def test_gate_closes_on_a_moved_pin_bad_ancestry_or_an_edited_prereg(grepo):
    repo, gen = grepo
    G.register(repo, author='t', cairo_date='2026-10-10', registry_genesis=gen)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'register')
    git(repo, 'checkout', '-q', 'master')
    git(repo, 'merge', '-q', '--no-ff', '-m', 'm', 'overlap')
    git(repo, 'checkout', '-q', 'r4')
    git(repo, 'merge', '-q', '--no-ff', '-m', 'mm', 'master')
    bind_classification(repo, 'c' * 64)
    assert G.gate(repo, registry_genesis=gen) == []
    git(repo, 'checkout', '-q', 'overlap')
    with open(os.path.join(repo, 'overlap.txt'), 'a') as f:
        f.write('fix round')
    git(repo, 'commit', '-q', '-am', 'codex fix round')
    git(repo, 'checkout', '-q', 'r4')
    assert any('moved' in f for f in G.gate(repo, registry_genesis=gen))
    git(repo, 'branch', '-q', '-f', 'overlap', 'overlap~1')            # the fix round is cleared away again
    assert G.gate(repo, registry_genesis=gen) == []
    g = json.load(open(os.path.join(repo, G.GATE)))
    g['pins'][1]['after'] = g['pins'][1]['head']                      # a pin that does not descend from its base
    g['pins'][0]['head'] = g['pins'][0]['head'][:7]                   # an abbreviated pin
    assert any('descend' in f for f in G.pin_failures(repo, dict(g, pins=[dict(g['pins'][1],
                                                                                after=git(repo, 'rev-parse', 'r4'))])))
    assert any('full 40-hex' in f for f in G.pin_failures(repo, g))
    p = os.path.join(repo, 'research_evidence/prereg/trend_ema_mom.v1.json')
    with open(p, 'rb') as f:
        raw = f.read()
    with open(p, 'wb') as f:
        f.write(raw.replace(b'"stop_atr": "2.5"', b'"stop_atr": "2.0"'))
    git(repo, 'commit', '-q', '-am', 'retune attempt')
    fail = G.gate(repo, registry_genesis=gen)
    assert any('primary_params' in f for f in fail) and any('G3 no preregistration' in f for f in fail)


def test_pit_subcommand_and_m3_never_run_with_the_gate_closed():
    assert G.main(['pit', '--author', 't', '--cairo-date', '2026-10-10']) == 3      # this branch: not registered
    assert G.main(['m3', '--store', ROOT, '--author', 't', '--cairo-date', '2026-10-10']) == 3
    g = json.loads(G._load(ROOT, G.GATE))
    assert g['classification']['digest'] == 'PENDING'
    assert [p['head'] for p in g['pins']] == ['01564264431650a9fde53f27623928bb84702914',
                                              '33ee82f70039a30dd6bcd9cda6b3816156131638']
    assert g['pins'][1]['integrated'] == 'a47815b76350dc77d195637b00de3c2607126d0c'
    assert 'branch' not in g['pins'][1]


def test_a_rebase_merged_pin_is_checked_by_its_integrated_commit(grepo):
    """Codex 6095813913: the reviewed source head was rebase-merged; G4 binds both identities, requires identical
    trees and checks ancestry on the integrated commit only."""
    repo, gen = grepo
    g = json.load(open(os.path.join(repo, G.GATE)))
    src = g['pins'][1]['head']
    git(repo, 'checkout', '-q', 'master')
    git(repo, 'cherry-pick', '-x', src)                                    # a rewritten commit with the same tree
    integ = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '-q', 'r4')
    git(repo, 'merge', '-q', '--no-ff', '-m', 'merge master', 'master')
    assert integ != src and git(repo, 'rev-parse', f'{integ}^{{tree}}') == git(repo, 'rev-parse', f'{src}^{{tree}}')
    pin = {'slice': 'overlap', 'prs': [56], 'head': src, 'integrated': integ, 'after': g['pins'][0]['head']}
    git(repo, 'branch', '-q', '-D', 'overlap')                       # the source branch may be gone after merging
    assert G.pin_failures(repo, dict(g, pins=[g['pins'][0], pin])) == []
    other = git(repo, 'rev-parse', 'master~1')                       # a master commit with a different tree
    bad = G.pin_failures(repo, dict(g, pins=[dict(pin, integrated=other)]))
    assert any('tree differs' in f for f in bad)
    assert any('full 40-hex' in f for f in G.pin_failures(repo, dict(g, pins=[dict(pin, integrated=integ[:7])])))
    git(repo, 'checkout', '-q', '-b', 'side', 'master~1')
    git(repo, 'cherry-pick', '-x', src)
    stray = git(repo, 'rev-parse', 'HEAD')                           # same tree, but never merged into master
    git(repo, 'checkout', '-q', 'r4')
    assert any('not merged into' in f for f in G.pin_failures(repo, dict(g, pins=[dict(pin, integrated=stray)])))
