"""STOP-COST-SMOKE (Codex 6245287466): a read-only per-close check of the cohort's cost evidence - fills booked without a
matching cost record, an exit price that is not exchange truth, every fees_actual_partial reason - rolled up into the
comparison's evidence gaps. It never changes a row, a rank or a trading decision."""
import os
import paper_books as PB
import true_books as TB
from test_true_books import C, tb_engine, row, close_row

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K = sorted(PB.BOOKS[2][2])[0]


def close(i, **kw):
    return dict(dict(id=f'r{i}', book='B3_TREND', cohort_id='C', strategy_key=K, symbol='SOLUSDT', exit_reason='stop',
                     closed=f'2026-10-18T0{i}:00:00+00:00', pnl_gross=-10.0, fees=0.1, fees_actual=0.09, fees_actual_partial=False,
                     r=-1.0, fills_booked=2, cost_refs=['c1-1-1', 'c1-1-2']), **kw)


def test_a_clean_exact_cost_close_has_no_gap():
    g = TB.cost_gaps([close(1)])
    assert g == dict(closes=1, with_gaps=0, by_reason={}, by_book={}, by_strategy={}, rows=[], truncated=False)


def test_missing_commission_and_unconfirmed_price_are_each_reported():
    rows = [close(1, fees_actual=None, fees_actual_partial=['fill_commission_missing']),
            close(2, fees_actual=None, exit_px_unconfirmed=1, fills_booked=2, cost_refs=['c1-1-1'],
                  fees_actual_partial=['exit_px_unconfirmed', 'fills_without_cost_record'])]
    g = TB.cost_gaps(rows)
    assert g['closes'] == 2 and g['with_gaps'] == 2
    assert g['by_reason'] == {'exit_px_unconfirmed': 1, 'fill_commission_missing': 1, 'fills_cost_refs_mismatch': 1,
                              'fills_without_cost_record': 1}
    assert [r['reasons'] for r in g['rows']] == [['fill_commission_missing'],
                                                 ['exit_px_unconfirmed', 'fills_cost_refs_mismatch', 'fills_without_cost_record']]
    assert g['by_book'] == {'B3_TREND': 2} and g['by_strategy'] == {f'B3_TREND|{K}': 2}


def test_multiple_fills_need_one_cost_ref_each_and_a_bad_count_never_matches():
    ok = close(1, fills_booked=4, cost_refs=['a', 'b', 'c', 'd'])
    more = close(2, fills_booked=3, cost_refs=['a', 'b', 'c', 'd'])          # more records than fills: not proven either
    bad = close(3, fills_booked='x', cost_refs=[])
    g = TB.cost_gaps([ok, more, bad])
    assert g['with_gaps'] == 2 and [r['id'] for r in g['rows']] == ['r2', 'r3']
    assert all(r['reasons'] == ['fills_cost_refs_mismatch'] for r in g['rows'])
    assert TB.cost_gaps([close(4, fills_booked=None, cost_refs=None)])['with_gaps'] == 0   # nothing booked, nothing claimed


def test_a_duplicate_revision_counts_once_as_its_latest_revision():
    early = close(1, fees_actual=None, fees_actual_partial=['fill_records_pending'], cost_rev=1)
    late = close(1, cost_rev=2)                                              # the commission arrived: proven
    for rows in ([early, late], [late, early]):
        g = TB.cost_gaps(rows)
        assert g['closes'] == 1 and g['with_gaps'] == 0
    g = TB.cost_gaps([late, dict(early, cost_rev=3)])                       # a later restatement that lost it: a gap
    assert g['closes'] == 1 and g['by_reason'] == {'fill_records_pending': 1}


def test_compare_rolls_the_gaps_into_rows_without_changing_any_rank():
    rows = [close(i, id=f'g{i}', closed=f'2026-10-18T{i:02d}:00:00+00:00', pnl_gross=10.0, r=0.5) for i in range(20)]
    base = TB.compare(C('C', rows), rows)
    rows2 = rows + [dict(rows[0], cost_rev=9, fees_actual_partial=['fill_commission_missing'], fees_actual=None)]
    out = TB.compare(C('C', rows2), rows2)
    f = next(x for x in out['families'] if x['id'] == 'B3_TREND'); s = next(x for x in out['strategies'] if x['key'] == K)
    assert f['cost_gaps'] == 1 and s['cost_gaps'] == 1
    assert out['cost_evidence']['with_gaps'] == 1 and out['cost_evidence']['closes'] == 20
    assert all(x['cost_gaps'] == 0 for x in out['families'] if x['id'] != 'B3_TREND')
    strip = lambda o: [{k: v for k, v in x.items() if k != 'cost_gaps'} for x in o['families'] + o['strategies']]
    assert strip(TB.compare(C('C', rows), rows)) == strip(base)               # the check itself changes nothing
    assert TB.cost_gaps(rows) == TB.cost_gaps(rows) and rows[0].get('cost_rev') is None   # pure: inputs untouched


def test_the_listing_is_bounded_but_the_counts_cover_every_close():
    rows = [close(i, id=f'm{i}', fees_actual=None, fees_actual_partial=['fill_records_lost']) for i in range(TB.COST_GAP_ROWS + 7)]
    g = TB.cost_gaps(rows)
    assert g['with_gaps'] == TB.COST_GAP_ROWS + 7 and len(g['rows']) == TB.COST_GAP_ROWS and g['truncated']


def test_the_engine_route_carries_the_cost_evidence_and_the_card_shows_it():
    e, _ = tb_engine()
    r = row(e, 'B3_TREND', 20.0, id='t1', strategy='ema_mom'); close_row(e, r)
    out = e.true_books_compare()
    assert out['on'] and out['cost_evidence']['closes'] == 1 and 'rows' in out['cost_evidence']
    assert set(out['cost_evidence']) == {'closes', 'with_gaps', 'by_reason', 'rows', 'truncated'}
    html = open(os.path.join(ROOT, 'panel.html'), encoding='utf-8').read()
    assert 'ce=c.cost_evidence||{}' in html and 'closes with cost-evidence gaps' in html and 'cost evidence complete' in html


def test_each_signal_is_read_on_its_own():
    g = TB.cost_gaps([close(1, exit_px_unconfirmed=1),                      # not finalized yet: the flag alone counts
                      close(2, fees_actual=None, fees_actual_partial=True)])  # a non-list partial is still a gap
    assert [r['reasons'] for r in g['rows']] == [['exit_px_unconfirmed'], ['fees_actual_partial']]


def test_a_strategy_counts_only_its_own_gaps():
    k2 = sorted(PB.BOOKS[2][2])[1]
    rows = [close(1, fees_actual=None, fees_actual_partial=['fill_records_lost']), close(2, id='ok', strategy_key=k2)]
    out = TB.compare(C('C', rows), rows)
    s = {x['key']: x['cost_gaps'] for x in out['strategies'] if x['book'] == 'B3_TREND'}
    assert s[K] == 1 and s[k2] == 0 and next(x for x in out['families'] if x['id'] == 'B3_TREND')['cost_gaps'] == 1
