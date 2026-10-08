"""The Runner against TestnetVenue's answer shapes (nc-venue-testnet add8a2e, newcore/venue/testnet_venue.py; not
merged here), emulated by a fake over FakeVenue:
  1. a duplicate client id is UNKNOWN detail 'duplicate_client_id' (not REJECTED -4116) -> query by client id;
  2. a classic stop the venue wants on the algo service is REJECTED detail 'algo_route' (code not relied on) -> fallback;
  3. a triggered algo stop is KNOWN detail 'algo_triggered' with the CHILD order id; fills(child) is the fill truth;
     'algo_triggered_no_child' is UNKNOWN -> nothing is booked until the child and its fills show.
Plus the AccountReadsShim over a TestnetAccountReader-shaped reader."""
import dataclasses
from decimal import Decimal as D

import pytest

from newcore.domain import IntentState
from newcore.ports import keys as K
from newcore.ports.venue import OrderOutcome, OutcomeKind, ReadKind, ReadOutcome
from newcore.runner.testnet_hook import AccountReadsShim
from slice_helpers import Crash, ScriptedVenue, World, flat_bars
from test_runner_e2e import ENTRY_BAR, signals


class ShapedVenue(ScriptedVenue):
    """FakeVenue answers reshaped like TestnetVenue's."""

    def __init__(self, venue):
        super().__init__(venue)
        self.no_child_once = set()

    def _reshape(self, out):
        if out.kind is OutcomeKind.REJECTED and out.error_code == -4116:
            return OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=out.ref, observed_at_ms=out.observed_at_ms,
                                detail='duplicate_client_id')
        if out.kind is OutcomeKind.REJECTED and out.detail == 'algo_required':
            return dataclasses.replace(out, error_code=-1, detail='algo_route')    # the detail, not the code
        if out.ref.route == 'algo' and out.kind is OutcomeKind.FINAL and out.status == 'FILLED':
            if out.ref.client_id in self.no_child_once:
                self.no_child_once.discard(out.ref.client_id)
                return OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=out.ref, observed_at_ms=out.observed_at_ms,
                                    detail='algo_triggered_no_child')
            return OrderOutcome(kind=OutcomeKind.KNOWN, ref=out.ref, observed_at_ms=out.observed_at_ms,
                                status='FINISHED', exchange_order_id='child-' + out.exchange_order_id,
                                detail='algo_triggered')
        return out

    def submit_market(self, order):
        return self._reshape(super().submit_market(order))

    def submit_stop(self, order):
        return self._reshape(super().submit_stop(order))

    def query(self, ref):
        return self._reshape(super().query(ref))

    def fills(self, symbol, exchange_order_id):
        if exchange_order_id.startswith('child-'):
            r = self.inner.fills(symbol, exchange_order_id[len('child-'):])
            return dataclasses.replace(r, value=tuple(dataclasses.replace(f, exchange_order_id=exchange_order_id)
                                                      for f in r.value))
        return self.inner.fills(symbol, exchange_order_id)


def world(**kw):
    w = World(flat_bars(20, overrides=kw.pop('overrides', None)), signals(exit_=None), **kw)
    w.port = ShapedVenue(w.venue)
    w.runner = w.new_runner()
    return w


def test_1_duplicate_client_id_as_unknown_is_read_back_not_refused():
    w = world()
    w.run(ENTRY_BAR - 1)
    w.port.crash(2, 'after')                                       # the stop landed, the answer was lost
    with pytest.raises(Crash):
        w.run(ENTRY_BAR)
    stop, = [o for o in w.venue.orders_submitted() if o.order_type == 'STOP_MARKET']
    w.venue.not_found(stop.ref.client_id, 1)                       # restart query: not visible yet -> re-send
    w.restart()
    w.runner.cycle(w.close_ms(ENTRY_BAR))                          # re-send -> UNKNOWN duplicate -> query -> KNOWN
    lot, = w.runner.fold.open_lots()
    assert [p.state for p in lot.protects] == [IntentState.WORKING]
    assert len([o for o in w.venue.orders_submitted() if o.order_type == 'STOP_MARKET']) == 1


def test_2_algo_route_detail_drives_the_fallback():
    w = world()
    w.venue.refuse_classic_stops(-4120)                            # reshaped: code -1, detail 'algo_route'
    w.run(ENTRY_BAR + 1)
    lot, = w.runner.fold.open_lots()
    assert [(K.route_of(p.intent_id, p.intent.client_order_id), p.state) for p in lot.protects] == \
        [('classic', IntentState.REJECTED), ('algo', IntentState.WORKING)]


@pytest.mark.parametrize('no_child_first', [False, True])
def test_3_triggered_algo_stop_is_booked_from_the_child_fills(no_child_first):
    gap = {8: ('95', '95.2', '94.8', '95')}
    w = world(overrides=gap, strict=False)
    w.venue.refuse_classic_stops(-4120)
    w.run(ENTRY_BAR + 1)
    lot, = w.runner.fold.open_lots()
    algo = lot.protects[-1]
    if no_child_first:
        w.port.no_child_once.add(algo.intent.client_order_id)
    w.run(8)                                                       # the candle gaps through the stop
    if no_child_first:
        assert w.runner.fold.open_lots() and algo.state is not IntentState.FILLED    # nothing booked yet
        w.run(9)
    assert w.runner.fold.open_lots() == []
    t, = w.runner.trades()
    assert algo.final.exchange_order_id.startswith('child-') and algo.state is IntentState.FILLED
    assert t.exit_code == 'STOP_HIT' and t.exit_price == D('95') * D('0.9998')    # the child's fill price
    assert t.fees > 0


# ------------------------------------------------------------------------------------------------ account shim
# VERBATIM mirrors of nc-venue-testnet c872c6b newcore/venue/testnet_venue.py (Equity, FundingPayment): the branch is
# not merged here. test_shim_field_contract pins their field names to the shim's (and, once the venue package is on
# the branch, to the real classes), so a rename on either side fails a test instead of the first testnet cycle.
@dataclasses.dataclass(frozen=True)
class Equity:
    asset: str
    wallet_balance: D
    margin_balance: D
    available_balance: D
    unrealized_pnl: D


@dataclasses.dataclass(frozen=True)
class FundingPayment:
    symbol: object               # str, or None for an account-level row
    asset: str
    amount: D                    # signed: negative = paid
    at_ms: int
    tran_id: int


def test_shim_field_contract():
    from newcore.runner.testnet_hook import EQUITY_FIELDS, FUNDING_FIELDS
    names = lambda cls: tuple(f.name for f in dataclasses.fields(cls))
    assert names(Equity) == EQUITY_FIELDS and names(FundingPayment) == FUNDING_FIELDS
    try:
        from newcore.venue import testnet_venue as real
    except ImportError:
        pytest.skip('newcore.venue is not merged on this branch: the mirrors above are pinned from c872c6b')
    assert names(real.Equity) == EQUITY_FIELDS and names(real.FundingPayment) == FUNDING_FIELDS


class FakeReader:
    def __init__(self):
        self.calls = []

    def equity(self, asset='USDT'):
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=1_700_000_000_000,
                           value=(Equity('USDT', D('1000'), D('990'), D('900'), D('-10')),))

    def funding(self, *, start_ms, end_ms, symbol=None):
        self.calls.append((start_ms, end_ms, symbol))
        rows = (FundingPayment('SOLUSDT', 'USDT', D('-0.25'), 1_700_000_000_000, 1),
                FundingPayment(None, 'USDT', D('-9'), 1_700_000_000_000, 3),           # account-level row
                FundingPayment('SOLUSDT', 'USDT', D('0.10'), 1_700_000_100_000, 2))
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=end_ms, value=rows)


def test_account_reads_shim_maps_the_testnet_reader():
    s = AccountReadsShim(FakeReader())
    assert s.equity().value == (D('1000'),)                        # wallet balance = closed equity
    r = s.funding('SOLUSDT', 'LONG', 1_699_999_999_999, 1_700_000_000_000)
    assert [(x.at_ms, x.amount) for x in r.value] == [(1_700_000_000_000, D('0.25'))]   # paid > 0, window (from, to]
    assert s.reader.calls[-1] == (1_700_000_000_000, 1_700_000_000_000, 'SOLUSDT')


def test_unknown_reads_pass_through_never_zero():
    class Down(FakeReader):
        def equity(self, asset='USDT'):
            return ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=1_700_000_000_000, detail="asset_missing")
    assert AccountReadsShim(Down()).equity().kind is ReadKind.UNKNOWN
