"""TRUE-BOOK-03 (#147, PAPER/testnet): funding paid on a symbol held both ways cannot be assigned to either lot, so with
TRUE_BOOKS on an automatic entry is refused while the symbol carries exposure on the other side - open lot, armed trailing
entry, resting maker or unconfirmed entry - in any book. Existing lots are untouched; the shadow still records the signal."""
import copy, json, time

import engine as E
import trade_audit as TA
from test_safety import SG
from test_true_books import tb_engine, fake_lot, T_SL, T2_SL, R_SL

OPP = 'true_book/opposite_side_funding_ambiguity'
SG_S = dict(SG, le=False, se=True)


def _working(sym='BTCUSDT', side='SHORT', sleeve='X'):
    return dict(symbol=sym, side=side, sleeve=sleeve, qty=1.0, plan=dict(qty=1.0, px=100.0, stop_dist=2.0))


def test_opposite_lot_in_the_same_book_refuses_the_entry():
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B3_TREND', sym='BTCUSDT', side='SHORT', stop=102.0)   # another B3 strategy is short
    assert not e.open_lot(T2_SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert e.last_skip == OPP


def test_opposite_lot_in_another_book_refuses_the_entry():
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B2_RANGE', sym='BTCUSDT', side='LONG')
    assert e.entry_block(T_SL, 'BTCUSDT', 'SHORT', sg=SG_S) == OPP


def test_manual_opposite_lot_also_refuses_an_automatic_entry():
    e, _ = tb_engine()
    e.state['lots']['m'] = fake_lot(e, None, sym='BTCUSDT', side='SHORT', stop=102.0, manual=True, sleeve='MAN')
    assert e.entry_block(T_SL, 'BTCUSDT', 'LONG', sg=SG) == OPP


def test_every_kind_of_working_opposite_entry_refuses():
    for fam in ('pending_entries', 'resting_entries', 'unconfirmed_entries'):
        e, _ = tb_engine()
        e.state.setdefault(fam, {})['w'] = _working()
        assert e.entry_block(T_SL, 'BTCUSDT', 'LONG', sg=SG) == OPP, fam


def test_the_triggering_trailing_entry_is_not_its_own_opposite():
    e, _ = tb_engine()
    e.state['pending_entries']['T|BTCUSDT'] = _working(side='SHORT', sleeve='T')
    assert e.true_book_block(T_SL, None, 'T|BTCUSDT', 'BTCUSDT', 'LONG') is None
    assert e.true_book_block(T_SL, None, None, 'BTCUSDT', 'LONG') == OPP


def test_same_side_and_other_symbols_are_unchanged():
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B2_RANGE', sym='BTCUSDT', side='LONG')
    e.state['lots']['y'] = fake_lot(e, 'B2_RANGE', sym='ETHUSDT', side='SHORT', stop=102.0)
    assert e.entry_block(T_SL, 'BTCUSDT', 'LONG', sg=SG) is None
    assert e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip


def test_existing_opposite_lots_are_left_exactly_as_they_were():
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B2_RANGE', sym='BTCUSDT', side='SHORT', stop=102.0)
    before = copy.deepcopy(e.state['lots'])
    assert not e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert e.state['lots'] == before                                  # nothing closed, resized or re-stopped


def test_gate_is_true_books_only():
    e, _ = tb_engine()
    e.S['TRUE_BOOKS'] = False
    e.state['lots']['x'] = fake_lot(e, 'B2_RANGE', sym='BTCUSDT', side='SHORT', stop=102.0)
    assert e.entry_block(T_SL, 'BTCUSDT', 'LONG', sg=SG) != OPP
    e2, _ = tb_engine()
    e2.live = True                                                    # never a live / mainnet path
    assert e2._tb_on() is False and e2.true_book_block(T_SL, None, None, 'BTCUSDT', 'LONG') is None


def test_reason_has_a_stable_audit_code():
    assert TA.reason_code(OPP) == ('true_book', 'opposite_side_funding_ambiguity')


def test_rejected_entry_is_still_a_shadow_candidate(tmp_path):
    e, _ = tb_engine(STRATEGY_SHADOW=True)
    e.state['lots']['x'] = fake_lot(e, 'B2_RANGE', sym='BTCUSDT', side='SHORT', stop=102.0)
    assert not e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    e._shadow_signal(dict(T_SL, tf='4h'), 'BTCUSDT', 'LONG', dict(SG, time='2026-10-04T00:00:00+00:00'), 'skipped', e.last_skip)
    sb = e._shadow_book()
    assert sb is not None
    recs = [json.loads(l) for l in open(sb.path, encoding='utf-8') if l.strip()]
    cand = [r for r in recs if r['kind'] == 'candidate']
    assert cand and cand[-1]['live_decision'] == dict(status='skipped', reason=OPP) and cand[-1]['side'] == 'LONG'


def test_funding_holders_stay_one_sided_under_overlapping_signals():
    e, _ = tb_engine()
    e.state['lots']['x'] = fake_lot(e, 'B2_RANGE', sym='BTCUSDT', side='SHORT', stop=102.0)
    for sl in (T_SL, T2_SL, R_SL):                                    # three strategies want the long at once
        e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
    t = int(time.time() * 1000) + 1
    sides = {s for _, s, _ in e._funding_holders('BTCUSDT', t)}
    assert sides == {'SHORT'}                                         # attribution never becomes ambiguous


def test_untracked_opposite_exchange_position_refuses():
    e, _ = tb_engine()
    e.untracked = {'BTCUSDT|SHORT': 1.0}
    assert e.true_book_block(T_SL, None, None, 'BTCUSDT', 'LONG') == OPP
    e.untracked = {'BTCUSDT|LONG': 1.0}
    assert e.true_book_block(T_SL, None, None, 'BTCUSDT', 'LONG') is None


def test_trailing_trigger_goes_through_the_gate_with_its_own_key_skipped():
    e, _ = tb_engine()
    e.state['pending_entries']['T|BTCUSDT'] = _working(side='LONG', sleeve='T')
    e.state['lots']['x'] = fake_lot(e, 'B2_RANGE', sym='BTCUSDT', side='SHORT', stop=102.0)
    assert e.entry_block(T_SL, 'BTCUSDT', 'LONG', sg=SG, skip_pending='T|BTCUSDT') == OPP


def test_maker_remainder_fallback_is_blocked_by_an_opposite_side():
    import inspect
    src = inspect.getsource(E.Engine)
    i = src.index("blk = (self.entry_block(sl, rec['symbol'], rec['side'], manual=True)")
    assert "self._tb_opposite(rec['symbol'], rec['side'])" in src[i:i + 600]   # the remainder is a new-entry leg
    e, _ = tb_engine()
    e.state['lots']['m'] = fake_lot(e, None, sym='BTCUSDT', side='SHORT', stop=102.0, manual=True, sleeve='MAN')
    assert e._tb_on() and e._tb_opposite('BTCUSDT', 'LONG')


def test_manual_lot_stays_byte_identical_and_manual_entries_are_never_gated():
    e, _ = tb_engine()
    e.state['lots']['m'] = fake_lot(e, None, sym='BTCUSDT', side='SHORT', stop=102.0, manual=True, sleeve='MAN')
    before = copy.deepcopy(e.state['lots']['m'])
    assert not e.open_lot(T_SL, 'BTCUSDT', 'LONG', SG, None, e.equity()) and e.last_skip == OPP
    assert e.state['lots']['m'] == before                             # the manual trade is never altered
    e.state['lots'].clear()
    e.state['lots']['x'] = fake_lot(e, 'B2_RANGE', sym='BTCUSDT', side='SHORT', stop=102.0)
    assert e.entry_block(None, 'BTCUSDT', 'LONG', manual=True) != OPP   # a hand trade is the owner's call, never this gate


def test_untracked_position_seen_once_already_refuses():
    e, _ = tb_engine()
    e.state['over_seen'] = {'BTCUSDT|SHORT': 1}                       # first reconcile pass, not alerted yet
    assert e.true_book_block(T_SL, None, None, 'BTCUSDT', 'LONG') == OPP
