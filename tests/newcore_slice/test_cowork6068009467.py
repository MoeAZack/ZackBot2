"""Cowork acceptance suite (PR #37, issuecomment 6068009467) on the S1 head, every evidence fault class, both sides:
raise / unknown / empty / trunc (one half-qty row) / stale / mismatch must read as UNKNOWN (never zero, never a widening);
dup / duplast (repeated rows, a page overlap) are de-duplicated by trade id.

h4  hard-HOLD sizing: (a) a FINAL's executed quantity is the venue's own order record - an empty / stale fills read never
    zeroes it; (b) duplicated rows never double-count; (c) unproven trades never prove our emergency fills zero;
    (d) unknown fills never widen to the venue total.
h3  the guard: a truncated / stale / mismatched / empty trades page is UNKNOWN (ambiguous), never 'proven'; repeated rows
    are de-duplicated.
h5  a malformed FINAL answer (FILLED with executed 0 / half / double) is UNKNOWN + an incident, never an exception out
    of cycle(); the venue's truth is booked and protected on the next query.
h6  a fee is booked only from proven rows: otherwise the trade is PENDING with an incident (never a zero fee).
h7  the structured state reports the effective mode: portfolio().entries_mode / hold_kind are HOLD /
    durability_unavailable in a hard HOLD (fold.mode is the journal's durable view only)."""
import dataclasses
import json
from decimal import Decimal as D

import pytest

from evidence_faults import FAULTS, RECOVERABLE, UNPROVEN, faulty
from newcore.domain import EntriesMode, HoldKind, Purpose
from newcore.ports.venue import OrderOutcome, OutcomeKind, ReadKind, ReadOutcome
from newcore.runner import InjectedSignals, ids
from slice_helpers import H4, ScriptedVenue, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def at(bar):
    return T0 + (bar + 1) * H4


def position(w, side):
    return sum((p.qty for p in w.venue.positions().value if p.side == side), D(0))


def stops(w, side):
    return [o for o in w.venue.open_orders().value if o.reduce and o.position_side == side
            and o.order_type == 'STOP_MARKET']


def new_orders(w, n):
    return list(w.venue.orders_submitted()[n:])


# ------------------------------------------------------------------------------------------------------- h4
def resting_entry_hard_hold(side, fill, *, final):
    """Our entry rests; the store goes down; `fill` of it executes (race), plus a foreign add of 2. final=True: the
    drain cancel lands (the entry is FINAL with `fill` executed); else it keeps resting (KNOWN, partly filled)."""
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.venue.rest_next_entries(1)
    w.run(5)
    entry = next(iv for iv in w.runner.fold.intents.values() if iv.purpose is Purpose.ENTRY)
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')
    w.venue.fill_resting(entry.intent.client_order_id, fill)
    w.venue.inject_position(SYM, side, D('2'), D('100'))
    if not final:
        w.port.lose('cancel')
    return w


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('fault', ('ok',) + FAULTS)
def test_h4a_a_final_entry_is_owned_by_its_executed_quantity_whatever_the_fills_read(side, fault):
    w = resting_entry_hard_hold(side, D('2'), final=True)
    w.venue.fills = faulty(w.venue.fills, fault)
    n = len(w.venue.orders_submitted())
    for b in (6, 7, 8):
        w.run(b)
    sent = new_orders(w, n)
    stop_qty = [o.qty for o in sent if o.order_type == 'STOP_MARKET']
    if fault in UNPROVEN:                                                # fills disagree with the executed qty:
        assert stop_qty == []                                            # UNKNOWN, nothing sized (6068372233 #3)
        assert any('fills disagree with executed qty' in t for _, t in w.runner.incidents)
    else:
        assert stop_qty == [D('2')]                                      # exactly ours: never 0, never 4 / 7
    assert not [o for o in sent if o.order_type == 'MARKET'] and position(w, side) == D('4')


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('fault', ('ok',) + FAULTS)
def test_h4bd_a_working_entry_is_never_sized_from_its_fills_alone(side, fault):
    w = resting_entry_hard_hold(side, D('3'), final=False)
    w.venue.fills = faulty(w.venue.fills, fault)
    n = len(w.venue.orders_submitted())
    for b in (6, 7, 8):
        w.port.lose('cancel')
        w.run(b)
    sent = new_orders(w, n)
    stop_qty = sum((o.qty for o in sent if o.order_type == 'STOP_MARKET'), D(0))
    assert not [o for o in sent if o.order_type == 'MARKET']             # never a close (foreign 2 is there)
    # a WORKING order's fills cannot be checked against an executed quantity (the port carries it on FINAL only):
    # nothing is sized from fills alone (6068372233 #3) - UNKNOWN and loud whatever the read, until it is FINAL
    assert stop_qty == 0
    assert any("working order's fills cannot be checked" in t for _, t in w.runner.incidents)
    assert position(w, side) == D('5')


def ours_emergency_closed(side):
    w = World(flat_bars(30), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.venue.inject_position(SYM, side, D('2'), D('100'))
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')
    w.port.refuse('stop', -2010)
    w.run(7)
    assert position(w, side) == D('2')                                   # our 5 closed, the foreign 2 left
    return w


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('fault', ('ok',) + FAULTS)
@pytest.mark.parametrize('restart', (False, True))
def test_h4c_unproven_trades_never_make_our_emergency_fills_zero(side, fault, restart):
    w = ours_emergency_closed(side)
    if restart:
        w.restart(hard_hold='test: store still down at boot')
    w.venue.trades = faulty(w.venue.trades, fault)
    w.venue.fills = faulty(w.venue.fills, fault)
    n = len(w.venue.orders_submitted())
    for b in (8, 9, 10):
        w.run(b)
        assert new_orders(w, n) == [] and position(w, side) == D('2')   # zero action on the foreign 2


# ------------------------------------------------------------------------------------------------------- h3
def guard_case(tmp_path, fault, monkeypatch):
    """Our open lot, our stop gone, damaged journal: the guard proves the lot from the trades page (bent by `fault`)."""
    from newcore.adapters.fake_venue import FakeVenue
    from newcore.runner import app as A
    from newcore.runner import config as C
    from test_run_cli import cfg_file, run
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '27', '--enable-candidate'])[0] == 0
    d = tmp_path / 'nc' / C.load(cfg).account_id
    st = json.loads((d / A.STATE_FILE).read_text())
    ours = D(st['positions'][0][2])
    for o in st['orders']:
        if o['type'] == 'STOP_MARKET':
            o['status'] = 'CANCELED'
    (d / A.STATE_FILE).write_text(json.dumps(st))
    seg = sorted(p for p in (d / 'journal').iterdir() if p.name.endswith('.seg'))[-1]
    raw = bytearray(seg.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    seg.write_bytes(bytes(raw))
    real = FakeVenue.trades
    monkeypatch.setattr(FakeVenue, 'trades', lambda self, *a, **k: faulty(lambda *x: real(self, *x), fault)(*a))
    code, out = run(['run', '--config', cfg, '--cycles', '5', '--enable-candidate'])
    st = json.loads((d / A.STATE_FILE).read_text())
    em = [D(o['qty']) for o in st['orders'] if ids.is_emergency_client_id(o['client_id']) and o['status'] == 'NEW']
    return code, out, em, ours


@pytest.mark.parametrize('fault', ('ok',) + FAULTS)
def test_h3_the_guard_proves_only_from_a_complete_deduplicated_page(tmp_path, fault, monkeypatch):
    from newcore.runner import app as A
    code, out, em, ours = guard_case(tmp_path, fault, monkeypatch)
    assert code == A.EXIT_STORE_HOLD
    if fault in UNPROVEN:
        assert em == [] and 'ambiguous' in out and 'proven ours' not in out   # never 'proven' from a short page
    else:
        assert em == [ours]                                              # ok / dup / duplast: exactly our lot


# ------------------------------------------------------------------------------------------------------- h5
class Bent(ScriptedVenue):
    how = None

    def submit_market(self, order):
        out = super().submit_market(order)
        if self.how and not order.reduce and out.kind is OutcomeKind.FINAL:
            q = {'zero': D(0), 'half': out.executed_qty / 2, 'double': out.executed_qty * 2}[self.how]
            out = OrderOutcome(kind=OutcomeKind.FINAL, ref=out.ref, observed_at_ms=out.observed_at_ms, status='FILLED',
                               exchange_order_id=out.exchange_order_id, executed_qty=q,
                               avg_price=out.avg_price if q > 0 else None)
        return out


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('how', ('zero', 'half', 'double'))
def test_h5_a_malformed_final_answer_is_unknown_never_an_exception(side, how):
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.port = Bent(w.venue)
    w.port.how = how
    w.runner = w.new_runner()
    w.run(8)                                                             # no exception out of cycle()
    r = w.runner
    assert any('malformed venue answer' in t for _, t in r.incidents)
    lot, = r.fold.open_lots()                                            # the venue's truth, booked by query
    assert lot.qty == position(w, side) == D('5') and sum((o.qty for o in stops(w, side)), D(0)) == D('5')


# ------------------------------------------------------------------------------------------------------- h6
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('fault', ('ok',) + FAULTS)
def test_h6_a_fee_is_booked_only_from_proven_rows(side, fault):
    sig = InjectedSignals({(SYM, at(5)): (('enter', side),), (SYM, at(7)): (('close', side),)}, stop_atr=D('2'))
    w = World(flat_bars(20), sig, strict=False)
    w.run(9)
    truth = w.runner.trades()
    assert len(truth) == 1 and truth[0].fees > 0
    w.venue.fills = faulty(w.venue.fills, fault)
    got = w.runner.trades()
    if fault in UNPROVEN:
        assert got == [] and any('not booked' in t for _, t in w.runner.incidents)   # pending, never a zero fee
    else:
        assert got == truth                                              # dup / duplast: never a double fee


# ------------------------------------------------------------------------------------------------------- h7
@pytest.mark.parametrize('side', SIDES)
def test_h7_the_structured_state_reports_the_effective_hold(side):
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.journal.fail_writes(10 ** 9)
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    w.port.refuse('stop', -2010)
    w.port.refuse('reduce', -2010)
    w.run(8)
    r = w.runner
    pf = r.portfolio()
    assert r.mode is EntriesMode.HOLD and r.hold is HoldKind.DURABILITY_UNAVAILABLE
    assert pf.entries_mode is EntriesMode.HOLD and pf.hold_kind is HoldKind.DURABILITY_UNAVAILABLE
    assert r.fold.mode is EntriesMode.ACTIVE                             # the journal's durable view (documented)


# ---------------------------------------------------- 6068372233 #2 / 6069221337 #4: completeness, #5: fee
def unmarked(read):
    """The same rows without the adapter's completeness evidence (an adapter / page that does not prove its end)."""
    def call(*a, **k):
        r = read(*a, **k)
        if r.kind is not ReadKind.OK:
            return r
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=r.observed_at_ms, value=r.value)
    return call


@pytest.mark.parametrize('side', SIDES)
def test_a_read_without_proven_completeness_is_unknown(side):
    """No row-count heuristic (a 40-row page passes any limit): only the adapter's completeness evidence makes a read
    complete; without it nothing is sized from it."""
    w = resting_entry_hard_hold(side, D('2'), final=True)
    w.venue.fills = unmarked(w.venue.fills)
    n = len(w.venue.orders_submitted())
    for b in (6, 7):
        w.run(b)
    assert not [o for o in new_orders(w, n) if o.order_type == 'STOP_MARKET']
    assert any('no completeness evidence' in t for _, t in w.runner.incidents)


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('fault', UNPROVEN)
def test_a_projection_fee_is_pending_never_zero_and_never_a_breach(side, fault):
    """_fee never returns ZERO for unproven rows: the projection is PENDING (an incident once), and a strict run does
    not breach on it."""
    from newcore.runner.fill_evidence import EvidencePending
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=True)
    w.run(6)
    w.venue.fills = faulty(w.venue.fills, fault)
    with pytest.raises(EvidencePending):
        w.runner.portfolio()
    w.run(9)                                                             # strict: no InvariantBreach
    assert sum(1 for _, t in w.runner.incidents if t.startswith('projection pending')) >= 1


def test_the_adapters_completeness_marker_lifts_the_full_page_rule():
    """TestnetVenue (nc-venue-testnet 5f0c959) pages fills / userTrades to a PROVEN end and says so in the OK read's
    detail ('complete pages=P dups=N'): a long complete answer is accepted; without the marker a full page is UNKNOWN."""
    from newcore.ports.venue import ReadKind, ReadOutcome, VenueFill
    from newcore.runner.fill_evidence import rows_of
    n = 1005
    rows = tuple(VenueFill(trade_id=str(i), exchange_order_id='7', symbol=SYM, position_side='LONG', qty=D('1'),
                           price=D('100'), fee=D('0.01'), fee_asset='USDT', realized_pnl=D('0'), maker=False,
                           at_ms=T0 + i) for i in range(n))
    marked = ReadOutcome(kind=ReadKind.OK, observed_at_ms=T0 + 10 ** 6, value=rows, detail='complete pages=2 dups=0')
    plain = ReadOutcome(kind=ReadKind.OK, observed_at_ms=T0 + 10 ** 6, value=rows)
    got, why = rows_of(marked, symbol=SYM, side='LONG', expect={'7': D(n)})
    assert why is None and len(got) == n
    got, why = rows_of(plain, symbol=SYM, side='LONG')
    assert got is None and 'no completeness evidence' in why
    forged = ReadOutcome(kind=ReadKind.OK, observed_at_ms=T0 + 10 ** 6, value=rows, detail='complete-ish')
    assert rows_of(forged, symbol=SYM, side='LONG')[0] is None             # only the exact marker counts



# ------------------------------------------------------------------------------- Cowork 6069221337 #1 / #2 / #5
def earlier_emergency_exit(side, *, restart):
    """Lot 5 of ours; an emergency close of 2 sent by an EARLIER process (never in this process's ledger) left own 3;
    a foreign add of 3 -> the venue holds 6; then the store is down and our stop is gone."""
    from newcore.ports.venue import MarketOrder, OrderRef
    w = World(flat_bars(30), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    cid = ids.emergency_stop_client_id(w.runner.acct, SYM, side, D('2'), 'close')
    out = w.venue.submit_market(MarketOrder(ref=OrderRef(symbol=SYM, client_id=cid), position_side=side, qty=D('2'),
                                            reduce=True))
    assert out.kind is OutcomeKind.FINAL and position(w, side) == D('3')
    w.emergency_eoid = out.exchange_order_id
    w.venue.inject_position(SYM, side, D('3'), D('100'))
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    w.journal.fail_writes(10 ** 9)
    if restart:
        w.restart(hard_hold='test: store down at boot')
    else:
        w.runner.store_unavailable('test: ENOSPC')
    return w


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('fault', ('ok',) + FAULTS)
@pytest.mark.parametrize('restart', (False, True))
@pytest.mark.parametrize('code', (None, -2010))
def test_emgexit_unproven_trades_never_size_from_the_whole_lot(side, fault, restart, code):
    w = earlier_emergency_exit(side, restart=restart)
    w.venue.trades = faulty(w.venue.trades, fault)
    if code is not None:
        w.port.refuse('stop', code)                                      # a refused stop escalates to the close
    n = len(w.venue.orders_submitted())
    for b in (7, 8, 9):
        w.run(b)
    sent = new_orders(w, n)
    r = w.runner
    assert r.mode is EntriesMode.HOLD
    if fault in UNPROVEN:                                                # ownership UNKNOWN: nothing new, loud
        assert sent == [] and position(w, side) == D('6')
        assert any('UNREADABLE' in t for _, t in r.incidents)
    elif code is None:                                                   # proven: exactly our 3
        assert [o.qty for o in sent if o.order_type == 'STOP_MARKET'] == [D('3')]
    else:
        assert [o.qty for o in sent if o.order_type == 'MARKET'] == [D('3')] and position(w, side) == D('3')


def conflicting(read, how, eoid):
    """h8 A1b: a trade id of OUR emergency exit reused by another order (a conflicting repeat); A1c: our emergency
    exit's exchange order id reused by another trade (its rows no longer add up to it)."""
    def call(*a, **k):
        r = read(*a, **k)
        if r.kind is not ReadKind.OK or not r.value:
            return r
        f = next((x for x in r.value if x.exchange_order_id == eoid), r.value[-1])
        extra = (dataclasses.replace(f, exchange_order_id='424242', qty=f.qty + 1) if how == 'tradeid'
                 else dataclasses.replace(f, trade_id=f.trade_id + '9', qty=D('1')))
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=r.observed_at_ms, value=r.value + (extra,), detail=r.detail)
    return call


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('how', ('tradeid', 'eoid'))
def test_a1_reused_ids_never_widen(side, how):
    w = earlier_emergency_exit(side, restart=False)
    w.venue.trades = conflicting(w.venue.trades, how, w.emergency_eoid)
    n = len(w.venue.orders_submitted())
    for b in (7, 8):
        w.run(b)
    sent = new_orders(w, n)
    assert sum((o.qty for o in sent if o.order_type == 'STOP_MARKET'), D(0)) <= D('3')   # never 5 on own 3
    assert not [o for o in sent if o.order_type == 'MARKET'] and position(w, side) == D('6')
    assert w.runner.mode is EntriesMode.HOLD and any('UNREADABLE' in t for _, t in w.runner.incidents)


@pytest.mark.parametrize('side', SIDES)
def test_a3f_the_venue_goes_flat_while_ownership_is_unknown(side):
    """Store back with the trades unreadable (ownership UNKNOWN), then the venue side reads flat: the lot takes the
    external-flat path (owner item, suspended, nothing sent), never left open and silent."""
    w = earlier_emergency_exit(side, restart=False)
    w.run(7)
    w.restart()                                                          # writable again
    w.venue.trades = faulty(w.venue.trades, 'unknown')
    w.run(8)
    w.venue._positions.pop((SYM, side), None)                            # flat at the venue
    n = len(w.venue.orders_submitted())
    w.run(10)
    r = w.runner
    lot, = r.fold.open_lots()
    assert r.journal.find_decision(ids.marker_decision_id('external_close', lot.lot_id)) is not None
    assert r.mode is EntriesMode.HOLD and new_orders(w, n) == []
