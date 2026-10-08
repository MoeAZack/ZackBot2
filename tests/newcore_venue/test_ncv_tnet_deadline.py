"""Venue harness deadline (Codex P1 pattern, nc-tnet01 e76d99c, audited here): ONE absolute run deadline bounds every
wait (probe polls, spec waits: never a sleep past it) and every opening send. Teardown always runs."""
import json
from decimal import Decimal as D

import pytest

from fake_binance import FakeBinance
from test_ncv_tnet_harness import EXAMPLE, env, run  # noqa: F401  (env is a fixture)

from newcore.ports import venue as P
from newcore.ports.keys import client_id_for
from newcore.venue.tnet_seams import DeadlineExceeded, DeadlinePort, RunDeadline


class Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.mark.parametrize('v', [0, -1, 7201, True, '10', None])
def test_run_deadline_is_bounded(v):
    with pytest.raises(ValueError):
        RunDeadline(v, Clock())


def test_bounded_sleep_never_sleeps_past_the_deadline():
    c = Clock()
    d = RunDeadline(10, c)
    slept = []

    def sleep(s):
        slept.append(s)
        c.t += s
    b = d.bounded(sleep)
    b(4)
    b(6)                                                     # exactly to the deadline: allowed
    with pytest.raises(DeadlineExceeded, match='would end past the run deadline'):
        b(0.001)
    assert slept == [4, 6] and not d.expired()
    c.t += 0.5
    assert d.expired() and d.remaining() < 0


def ref(n):
    return P.OrderRef(symbol='SOLUSDT', client_id=client_id_for('int_' + f'{n:032x}', 'classic'), route='classic')


def test_deadline_port_refuses_only_opening_orders_past_the_deadline():
    sent = []

    class V:
        def submit_market(self, o):
            sent.append(('m', o.reduce))
            return 'ok'

        def submit_stop(self, o):
            sent.append(('s',))
            return 'ok'

        def cancel(self, r):
            sent.append(('c',))
            return 'ok'
    c = Clock()
    port = DeadlinePort(V(), RunDeadline(5, c))
    assert port.submit_market(P.MarketOrder(ref=ref(1), position_side='LONG', qty=D('1'), reduce=False)) == 'ok'
    c.t = 6
    with pytest.raises(DeadlineExceeded, match='not sent'):
        port.submit_market(P.MarketOrder(ref=ref(2), position_side='LONG', qty=D('1'), reduce=False))
    port.submit_market(P.MarketOrder(ref=ref(3), position_side='LONG', qty=D('1'), reduce=True))
    port.submit_stop(P.StopOrder(ref=ref(4), position_side='LONG', qty=D('1'), stop_price=D('99')))
    port.cancel(ref(4))
    assert sent == [('m', False), ('m', True), ('s',), ('c',)]


@pytest.mark.parametrize('v', ['0', '-5', '7201'])
def test_cli_refuses_an_unbounded_deadline(env, v):  # noqa: F811
    rc, out = run(env, ['--probe', 'P1', '--deadline-s', v], http=FakeBinance())
    assert rc == 2 and '--deadline-s must be in (0, 7200]' in out


def test_p2_polling_stops_at_the_deadline_and_the_teardown_runs(env):  # noqa: F811
    fb = FakeBinance()
    fb.position_lag = 10 ** 9                                # entries never show: P2 keeps polling the position
    rc, out = run(env, ['--probe', 'P2', '--p2-samples', '3', '--deadline-s', '1'], http=fb)
    assert rc in (6, 8) and 'DEADLINE: a 0.2 s wait would end past the run deadline' in out
    assert 'CLEANUP' in out                                  # the teardown ran (an unseeable position cannot be closed)


def test_a_deadline_abort_with_read_lag_is_flattened_and_never_reported_clean_on_a_stale_view(env):  # noqa: F811
    """Deadline mid-P2 with a lagging position read: the teardown closes the position once it shows; the fake then
    serves the stale pre-close view, so the verdict stays conservative (residue, exit 8: the owner checks the UI) while
    the account is in fact flat. Never CLEAN on a view that still shows exposure."""
    fb = FakeBinance(position_lag_reads=6)
    rc, out = run(env, ['--probe', 'P2', '--p2-samples', '3', '--deadline-s', '1'], http=fb)
    assert 'final executed 1' in out and fb.flat()
    assert rc == 8 and 'CLEANUP NOT CLEAN' in out


def test_a_spec_wait_past_the_deadline_is_not_slept_and_nothing_opens_after_it(env, tmp_path):  # noqa: F811
    spec = json.load(open(EXAMPLE, encoding='utf-8'))
    spec['faults'] = []
    spec['expect']['outcomes'] = {}
    spec['steps'].insert(1, {'id': 'nap', 'op': 'wait', 'seconds': 600})                # entry, then a long wait
    spec['steps'].insert(2, {'id': 'again', 'op': 'market', 'symbol': 'SOLUSDT', 'side': 'LONG', 'reduce': False,
                             'qty': 'min'})
    p = tmp_path / 's.json'
    p.write_text(json.dumps(spec), encoding='utf-8')
    fb = FakeBinance()
    rc, out = run(env, ['--scenario', str(p), '--deadline-s', '30'], http=fb)
    assert rc == 6 and 'DEADLINE: a 600 s wait would end past the run deadline' in out
    opens = [q for q in fb.requests if q.method == 'POST' and 'side=BUY' in q.query and 'positionSide=LONG' in q.query]
    assert len(opens) == 1                                   # the entry; 'again' never ran
    assert 'CLEANUP CLEAN' in out and fb.flat() and not fb.open_cids()


# ---------------------------------------------------------------------------------------------- cleanup confirmation
class LagVenue:
    """positions() shows the LONG only from read number reveal_at on; a close flattens it at once."""

    def __init__(self, reveal_at):
        self.reads, self.reveal_at, self.qty, self.closes = 0, reveal_at, D('1'), []

    def positions(self, symbol=None):
        self.reads += 1
        q = self.qty if self.reads >= self.reveal_at else D(0)
        return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=1_759_917_600_000,
                             value=(P.VenuePosition(symbol='SOLUSDT', side='LONG', qty=q, entry_price=D('100')),))

    def open_orders(self, symbol=None):
        return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=1_759_917_600_000, value=())

    def submit_market(self, order):
        self.closes.append(order.qty)
        self.qty = D(0)
        return P.OrderOutcome(kind=P.OutcomeKind.FINAL, ref=order.ref, observed_at_ms=1_759_917_600_000,
                              status='FILLED', exchange_order_id='1', executed_qty=order.qty, avg_price=D('100'))


def test_one_flat_read_is_not_enough_when_confirmation_is_asked():
    from newcore.venue.tnet import tnet_cleanup
    slept = []
    v = LagVenue(reveal_at=2)
    res = tnet_cleanup(v, ['SOLUSDT'], run_id='r1', confirm_reads=2, settle_s=2.0, sleep=slept.append)
    assert res.clean and v.closes == [D('1')] and slept == [2.0, 2.0]
    assert any('NOT confirmed 2 s later' in n for n in res.notes)
    legacy = tnet_cleanup(LagVenue(reveal_at=2), ['SOLUSDT'], run_id='r2')            # confirm_reads=1: one read
    assert legacy.clean and legacy.attempts == 1


@pytest.mark.parametrize('kw', [{'confirm_reads': 0}, {'confirm_reads': 6}, {'confirm_reads': True},
                                {'confirm_reads': 2}])
def test_confirm_reads_is_validated(kw):
    from newcore.venue.tnet import tnet_cleanup
    with pytest.raises(ValueError):
        tnet_cleanup(LagVenue(1), ['SOLUSDT'], run_id='r3', **kw)


@pytest.mark.parametrize('v', ['-1', '10.5'])
def test_cli_refuses_an_unbounded_settle(env, v):  # noqa: F811
    rc, out = run(env, ['--probe', 'P1', '--settle-s', v], http=FakeBinance())
    assert rc == 2 and '--settle-s must be in 0..10' in out
