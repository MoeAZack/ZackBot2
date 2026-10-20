"""Five $2k PAPER books: equal scaling, deterministic attribution, and isolated book leverage."""
import pytest
from pathlib import Path

import engine as E
import paper_books as PB
from test_safety import mk_engine, SL, SG


def test_strategy_assignment_is_total_and_new_research_goes_to_discovery():
    assert PB.book_for('quick_bank_runner') == 'B1_FAST'
    assert PB.book_for('exp_vwaprev') == 'B2_RANGE'
    assert PB.book_for('ema_mom') == 'B3_TREND'
    assert PB.book_for('exp_structure') == 'B4_BREAKOUT'
    assert PB.book_for('future_c4_candidate') == 'B5_DISCOVERY'


def test_allocation_scales_until_account_reaches_ten_thousand():
    assert PB.allocation(4376.35, 2000) == 875.27
    assert PB.allocation(10000, 2000) == 2000
    assert PB.allocation(25000, 2000) == 2000


def test_book_mode_sizes_from_its_equal_book_not_legacy_slot_share():
    e, _ = mk_engine(CAPITAL_CAP=10000.0, PAPER_BOOKS=True, PAPER_BOOK_CAP=2000.0)
    assert e.equity() == 5000.0
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    lot = next(iter(e.state['lots'].values()))
    assert lot['book'] == 'B3_TREND' and lot['risk_usd'] == 20.0       # 2% of the scaled $1,000 book


def test_summary_keeps_pnl_and_fees_separate_by_book():
    lots = [dict(book='B1_FAST', risk_usd=10, risk_current=4), dict(book='B3_TREND', key_strategy='ema_mom', risk_usd=20),
            dict(key_strategy='future_legacy', risk_usd=99)]
    hist = [dict(book='B1_FAST', pnl=3, fees=1), dict(book='B4_BREAKOUT', strategy_key='exp_structure', pnl=-4, fees=2),
            dict(strategy_key='legacy', pnl=50, fees=5)]
    rows = {x['id']: x for x in PB.summary(10000, 2000, lots, hist)}
    assert rows['B1_FAST']['open'] == 1 and rows['B1_FAST']['open_risk'] == 4
    assert rows['B1_FAST']['net_pnl'] == 3 and rows['B1_FAST']['fees'] == 1
    assert rows['B3_TREND']['open'] == 1 and rows['B4_BREAKOUT']['net_pnl'] == -4
    assert sum(x['closed'] for x in rows.values()) == 2 and sum(x['open'] for x in rows.values()) == 2


def test_attribution_is_only_for_enabled_paper_automatic_entries():
    e, _ = mk_engine(PAPER_BOOKS=False)
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert next(iter(e.state['lots'].values())).get('book') is None
    e2, _ = mk_engine(PAPER_BOOKS=True)
    e2.live = True
    assert e2.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e2.equity())
    assert next(iter(e2.state['lots'].values())).get('book') is None


def test_add_uses_aggregate_book_leverage_cap():
    e, _ = mk_engine(PAPER_BOOKS=True, PAPER_BOOK_CAP=2000.0, MAX_LEVERAGE=1)
    e.S['SLEEVES'] = [SL]
    e.last_eq = e.equity()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    lot = next(iter(e.state['lots'].values()))
    lot['stop_dirty'] = False
    e.state['lots']['same-book-other-slot'] = dict(lot, sleeve='OTHER', qty=8, avg=100)
    assert e._add_block(lot, 3, 100).startswith('book leverage cap')


def test_new_settings_are_strictly_validated():
    E.validate_settings(dict(schema_version=E.SETTINGS_SCHEMA, PAPER_BOOKS=True, PAPER_BOOK_CAP=2000,
                             FEE_EDGE_GATE=True, FEE_EDGE_MULT=3))
    with pytest.raises(E.SchemaError):
        E.validate_settings(dict(schema_version=E.SETTINGS_SCHEMA, PAPER_BOOKS='yes'))
    with pytest.raises(E.SchemaError):
        E.validate_settings(dict(schema_version=E.SETTINGS_SCHEMA, PAPER_BOOK_CAP=50))


def test_strategies_tab_exposes_plain_five_book_tracking():
    panel = (Path(__file__).parents[1] / 'panel.html').read_text(encoding='utf-8')
    assert 'Five paper test books' in panel and 'id="bookRows"' in panel
    assert 'renderBooks();renderPresets()' in panel and 'PAPER_BOOKS' in panel and 'PAPER_BOOK_CAP' in panel
