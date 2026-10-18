"""TRUE-BOOK comparison view (Codex 6240118383 #4, draft 6240649800): families = the five books, strategies = every mapped
strategy, measured on the clean cohort. Incomplete rows stay visible with reasons and are never ranked; ranking needs
min_n closes; hindsight excursions come from the trade-audit close records and a missing one is never zero."""
import json, os, time
from decimal import Decimal
import pytest

import engine as E
import paper_books as PB
import true_books as TB
from test_safety import mk_engine
from test_true_books import FOK, C, tb_engine, row, close_row, T_SL, R_SL

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rows_for(book, key, n, gross=10.0, **kw):
    return [dict(dict(id=f'{key}{i}', book=book, cohort_id='C', strategy_key=key, pnl_gross=gross, fees=1.0, fees_actual=0.5,
                      r=gross / 20, notional=100.0, hours=2.0, exit_reason='tp', closed=f'2026-10-18T0{i % 10}:00:00+00:00'), **kw)
            for i in range(n)]


def fam(out, k):
    return next(f for f in out['families'] if f['id'] == k)


def strat(out, k):
    return next(s for s in out['strategies'] if s['key'] == k)


def test_every_book_and_every_mapped_strategy_has_a_row():
    out = TB.compare(C('C', []), [])
    assert [f['id'] for f in out['families']] == list(PB.IDS)
    assert {s['key'] for s in out['strategies']} == {k for _b, _n, ks in PB.BOOKS for k in ks}
    assert all(s['closed'] == 0 and not s['rankable'] and s['note'] == 'insufficient n (0/20)' for s in out['strategies'])


def test_strategy_stats_use_actual_fees_and_funding_and_rank_only_with_enough_closes():
    k1, k2 = sorted(PB.BOOKS[2][2])[:2]
    rows = rows_for('B3_TREND', k1, 20) + rows_for('B3_TREND', k2, 5, gross=-4.0, funding_actual=-0.5)
    out = TB.compare(C('C', rows), rows)
    a, b = strat(out, k1), strat(out, k2)
    assert a['closed'] == 20 and a['net'] == 190.0 and a['expectancy'] == 9.5 and a['fees'] == 10.0 and a['win_rate'] == 1.0
    assert a['avg_r'] == 0.5 and a['net_r'] == 10.0 and a['turnover'] == 1.0 and a['avg_hours'] == 2.0 and a['exits'] == {'tp': 20}
    assert a['rankable'] and a['rank'] == 1
    assert b['net'] == -25.0 and b['funding'] == -2.5 and b['pf'] == 0.0 and not b['rankable'] and b['rank'] is None
    assert b['note'] == 'insufficient n (5/20)'
    f = fam(out, 'B3_TREND')
    assert f['closed'] == 25 and f['net'] == 165.0 and f['rankable'] and f['rank'] == 1 and f['nav'] == 2165.0


def test_an_incomplete_book_or_close_is_shown_but_never_ranked():
    k = sorted(PB.BOOKS[0][2])[0]
    rows = rows_for('B1_FAST', k, 20)
    rows[3] = dict(rows[3], fees_actual=None, fees_actual_partial=['fill_records_lost'])
    out = TB.compare(C('C', rows), rows)
    s, f = strat(out, k), fam(out, 'B1_FAST')
    assert s['closed'] == 20 and s['incomplete_closes'] == 1 and not s['complete'] and not s['rankable'] and s['rank'] is None
    assert not f['rankable'] and 'fees_actual_missing' in f['incomplete']


def test_excursions_are_summarised_and_a_missing_audit_is_counted_never_zero():
    k = sorted(PB.BOOKS[1][2])[0]
    rows = rows_for('B2_RANGE', k, 3)
    aud = {f'{k}0': dict(mfe_r=2.0, mae_r=-0.5, giveback_r=1.0), f'{k}1': dict(mfe_r=1.0, mae_r=-1.5, giveback_r=0.0)}
    s = strat(TB.compare(C('C', rows), rows, aud), k)
    assert s['mfe_r'] == dict(mean=1.5, median=1.5, n=2) and s['mae_r'] == dict(mean=-1.0, median=-1.0, n=2)
    assert s['giveback_r']['mean'] == 0.5 and s['audit_missing'] == 1


def test_rejections_count_per_family_and_strategy():
    k = sorted(PB.BOOKS[3][2])[0]
    out = TB.compare(C('C', []), [], rejections=[('B4_BREAKOUT', k, 'book_full'), ('B4_BREAKOUT', k, 'book_full'),
                                                 ('B4_BREAKOUT', k, 'regime')])
    assert strat(out, k)['rejections'] == {'book_full': 2, 'regime': 1}
    assert fam(out, 'B4_BREAKOUT')['rejections'] == {'book_full': 2, 'regime': 1} and fam(out, 'B1_FAST')['rejections'] == {}


def test_engine_compare_joins_the_cohort_rows_audit_records_and_rejections():
    e, _ = tb_engine()
    r = row(e, 'B3_TREND', 20.0, id='t1', strategy='ema_mom', notional=500.0)
    close_row(e, r)
    rec = dict(kind='trade_audit', id='t1', metrics=dict(mfe=dict(r=1.8), mae=dict(r=-0.4)), giveback_r=0.3)
    with open(e.F['audit'], 'a', encoding='utf-8') as f:
        f.write(json.dumps(dict(kind='funnel', id='x')) + '\n' + json.dumps(rec) + '\n')
    e.missed.append(dict(logged='2099-01-01T00:00:00+00:00', sleeve='T', reason='book B3_TREND full (4/4 lots)', symbol='BTCUSDT'))
    e.missed.append(dict(logged='2000-01-01T00:00:00+00:00', sleeve='T', reason='book B3_TREND full (4/4 lots)'))   # older cohort
    e.missed.append(dict(logged='2099-01-01T00:00:00+00:00', sleeve='T', reason='spread', kind='warning'))           # a warning
    out = e.true_books_compare()
    assert out['on'] and out['cohort']['id'] == e.state['cohort']['id'] and out['cohort']['unowned_activity'] == 0
    s = strat(out, 'ema_mom')
    assert s['closed'] == 1 and s['mfe_r']['mean'] == 1.8 and s['giveback_r']['mean'] == 0.3 and s['audit_missing'] == 0
    assert s['rejections'] == {'book_full': 1} and s['turnover'] == 0.25
    with open(e.F['audit'], 'a', encoding='utf-8') as f:       # incremental: a later record is picked up
        f.write(json.dumps(dict(rec, giveback_r=0.9)) + '\n')
    assert strat(e.true_books_compare(), 'ema_mom')['giveback_r']['mean'] == 0.9


def test_engine_compare_surfaces_unowned_activity_and_never_raises():
    e, _ = tb_engine()
    e.state['lots']['m'] = dict(symbol='ETHUSDT', side='LONG', avg=100.0, qty=1.0, stop=98.0, manual=True, fees_actual=0.0)
    out = e.true_books_compare()
    assert out['cohort']['unowned_activity'] >= 1 and not any(f['rankable'] for f in out['families'])
    e.true_books = None                                          # anything broken: an error doc, never an exception
    assert 'error' in e.true_books_compare()


def test_engine_compare_with_true_books_off_is_marked_off():
    e, _ = mk_engine(PAPER_BOOKS=True)
    out = e.true_books_compare()
    assert out.get('on') is False


def test_the_route_and_strategies_tab_card_are_wired():
    app = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
    html = open(os.path.join(ROOT, 'panel.html'), encoding='utf-8').read()
    assert "if p == '/api/true_books/compare': return self._json(APP.engine.true_books_compare())" in app
    assert 'id="tbCmpCard"' in html and 'id="tbCmp"' in html and "api('/api/true_books/compare')" in html
    assert 'renderBooks();renderPresets();renderTbCmp();' in html
    assert 'never ranked or promoted' in html and "Date.now()-TBCT>60000" in html


def test_the_audit_reader_waits_for_a_whole_line_and_follows_a_rotation():
    e, _ = tb_engine()
    close_row(e, row(e, 'B3_TREND', 20.0, id='t1', strategy='ema_mom'))
    close_row(e, row(e, 'B3_TREND', 20.0, id='t2', strategy='ema_mom'))
    rec = lambda i, g: json.dumps(dict(kind='trade_audit', id=i, giveback_r=g))
    with open(e.F['audit'], 'a', encoding='utf-8') as f: f.write(rec('t1', 0.5))           # still being written
    assert strat(e.true_books_compare(), 'ema_mom')['audit_missing'] == 2
    with open(e.F['audit'], 'a', encoding='utf-8') as f: f.write('\n' + json.dumps(dict(kind='funnel', pad='x' * 400)) + '\n')
    assert strat(e.true_books_compare(), 'ema_mom')['audit_missing'] == 1
    os.replace(e.F['audit'], e.F['audit'] + '.1')                                          # rotation: a new, shorter file
    with open(e.F['audit'], 'w', encoding='utf-8') as f: f.write(rec('t2', 1.5) + '\n')
    s = strat(e.true_books_compare(), 'ema_mom')
    assert s['audit_missing'] == 0 and s['giveback_r']['mean'] == 1.0


def test_a_close_without_r_cannot_rank_and_a_keyless_close_is_counted():
    k = sorted(PB.BOOKS[2][2])[0]
    rows = rows_for('B3_TREND', k, 20)
    rows[0] = dict(rows[0], r=None)
    rows.append(dict(rows_for('B3_TREND', 'x', 1)[0], id='nokey', strategy_key=None))
    out = TB.compare(C('C', rows), rows)
    s = strat(out, k)
    assert s['closed'] == 20 and s['incomplete_closes'] == 1 and not s['rankable'] and s['rank'] is None
    assert strat(out, '?')['closed'] == 1 and not strat(out, '?')['mapped']


def test_avg_hold_ignores_closes_without_hours():
    k = sorted(PB.BOOKS[1][2])[0]
    rows = rows_for('B2_RANGE', k, 2)
    rows[1] = dict(rows[1], hours=None)
    assert strat(TB.compare(C('C', rows), rows), k)['avg_hours'] == 2.0


def test_a_rotation_to_a_larger_file_is_still_detected():
    e, _ = tb_engine()
    close_row(e, row(e, 'B3_TREND', 20.0, id='t1', strategy='ema_mom'))
    close_row(e, row(e, 'B3_TREND', 20.0, id='t2', strategy='ema_mom'))
    rec = lambda i: json.dumps(dict(kind='trade_audit', id=i, giveback_r=1.0)) + '\n'
    with open(e.F['audit'], 'a', encoding='utf-8') as f: f.write(rec('t1'))
    assert strat(e.true_books_compare(), 'ema_mom')['audit_missing'] == 1
    os.replace(e.F['audit'], e.F['audit'] + '.1')
    time.sleep(0.01)
    with open(e.F['audit'], 'w', encoding='utf-8') as f: f.write(rec('t2') + json.dumps(dict(kind='funnel', pad='y' * 5000)) + '\n')   # t2 lies before the old offset
    assert strat(e.true_books_compare(), 'ema_mom')['audit_missing'] == 0


def test_concurrent_reads_never_double_the_offset():
    import threading
    e, _ = tb_engine()
    with open(e.F['audit'], 'a', encoding='utf-8') as f:
        for i in range(2000): f.write(json.dumps(dict(kind='trade_audit', id=f'z{i}', giveback_r=0.1)) + '\n')
    ts = [threading.Thread(target=e._tb_excursions) for _ in range(4)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert e._tb_exc['off'][e.F['audit']] == os.path.getsize(e.F['audit']) and len(e._tb_exc['ids']) == 2000


def test_a_row_losing_after_costs_is_never_ranked_even_with_positive_r():
    k = sorted(PB.BOOKS[2][2])[0]
    rows = rows_for('B3_TREND', k, 20, fees_actual=12.0)          # +0.5R each, but -2.0 net after actual fees
    out = TB.compare(C('C', rows), rows)
    s, f = strat(out, k), fam(out, 'B3_TREND')
    assert s['avg_r'] == 0.5 and s['net'] == -40.0 and not s['rankable'] and s['rank'] is None
    assert s['note'] == 'not positive after costs' and s['closed'] == 20
    assert f['avg_r'] == 0.5 and f['net'] == -40.0 and not f['rankable'] and f['rank'] is None
    rows = rows_for('B3_TREND', k, 20, r=-0.1)                     # net positive, R not: unranked too
    assert not strat(TB.compare(C('C', rows), rows), k)['rankable']


def test_equal_avg_r_ranks_the_larger_after_cost_net_first():
    k3, k2 = sorted(PB.BOOKS[2][2])[0], sorted(PB.BOOKS[1][2])[0]
    rows = rows_for('B2_RANGE', k2, 20, r=0.5) + rows_for('B3_TREND', k3, 20, gross=20.0, r=0.5)   # larger net in the later book
    out = TB.compare(C('C', rows), rows)
    assert strat(out, k3)['rank'] == 1 and strat(out, k2)['rank'] == 2
    assert fam(out, 'B3_TREND')['rank'] == 1 and fam(out, 'B2_RANGE')['rank'] == 2
