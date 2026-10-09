"""Codex review of facd4b6 (#13), the P1s on the emergency path - repros first:

P1-2  a terminal refusal of the emergency stop other than -2021 (-2010, -4131, -1013 ...) left the exposure naked: the
      close escalation lived only in the would-trigger branch. Now, after duplicate / algo routing, EVERY terminal
      refusal or non-confirmation takes the deterministic reduce-only emergency close; UNKNOWN stays query / retry.
P1-3  that close is checked against the central hard-HOLD table (hard_hold_permits(CLOSE, PLACE,
      emergency_close=True), NC-01 modes 4de860e / NC-02a hold 922d599) before it is submitted.
P1-1  the guard protected a position because an OLD own entry of that side was FINAL at the venue, without proving any
      of it is still held. Now the surviving net of our own orders is established from the venue's trades of that
      side since the entry (own entry / add fills minus own exits); any trade we cannot attribute, or no trades read,
      -> touch nothing, HOLD + an ambiguity report; and at most the proven residual is protected."""
import json
from decimal import Decimal as D

import pytest

from newcore.runner import InjectedSignals, ids
from newcore.runner import app as A
from newcore.runner import config as C
from newcore.runner.reports import health_line
from slice_helpers import H4, World, flat_bars
from test_run_cli import cfg_file, run

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def position(w, side):
    return next(p.qty for p in w.venue.positions().value if p.side == side)


def hard_hold_with_gap(side):
    w = World(flat_bars(20), InjectedSignals({(SYM, T0 + 6 * H4): (('enter', side),)}, stop_atr=D('2')),
              strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)  # the lot's stop is gone ...
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')                     # ... and the store is down
    return w


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('code', (-2010, -4131, -1013, -2021))
def test_p1_2_every_terminal_emergency_stop_refusal_escalates_to_the_reduce_only_close(side, code):
    w = hard_hold_with_gap(side)
    w.port.refuse('stop', code)                                    # every stop attempt refused with this code
    w.run(7)
    assert position(w, side) == 0                                  # never left naked
    assert f'degraded={SYM}:{side}:emergency_close' in health_line(w.runner)


@pytest.mark.parametrize('side', SIDES)
def test_p1_2_an_unknown_answer_is_retried_not_escalated(side):
    w = hard_hold_with_gap(side)
    w.port.lose('stop')                                            # the answer is lost once (never reached the venue)
    w.run(7)
    assert position(w, side) == D('5')                             # no close on an UNKNOWN ...
    assert not [o for o in w.venue.orders_submitted() if o.order_type == 'MARKET' and o.reduce]
    w.run(8)                                                       # ... the retry places the stop
    cover = sum((o.qty for o in w.venue.open_orders().value if o.reduce and o.position_side == side), D(0))
    assert cover >= position(w, side) == D('5')


@pytest.mark.parametrize('side', SIDES)
def test_p1_3_the_emergency_close_asks_the_central_hard_hold_table(side, monkeypatch):
    from newcore.runner import runner as RUN
    seen = []

    def deny(purpose, op, *, emergency_close=False):
        seen.append((str(purpose), str(op), emergency_close))
        from newcore.domain.modes import Permission
        return Permission.FORBIDDEN if (str(purpose), emergency_close) == ('close', True) else \
            RUN_ORIG(purpose, op, emergency_close=emergency_close)
    RUN_ORIG = RUN.hard_hold_permits
    monkeypatch.setattr(RUN, 'hard_hold_permits', deny)
    w = hard_hold_with_gap(side)
    w.port.refuse('stop', -2010)
    w.run(7)
    assert ('close', 'place', True) in seen                        # asked before submitting
    assert position(w, side) == D('5')                             # forbidden -> not sent (an incident instead)
    assert any('forbidden' in t for _, t in w.runner.incidents)


# ------------------------------------------------------------------------------------------------------- P1-1
def _damage(d):
    seg = sorted(p for p in (d / 'journal').iterdir() if p.name.endswith('.seg'))[-1]
    raw = bytearray(seg.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    seg.write_bytes(bytes(raw))


def _emergency(d):
    st = json.loads((d / A.STATE_FILE).read_text())
    return [o for o in st['orders'] if ids.is_emergency_client_id(o['client_id']) and o['status'] == 'NEW']


def test_p1_1_an_old_flattened_own_entry_never_proves_a_later_foreign_position(tmp_path):
    """Our lot was CLOSED (its stop filled / its close done: our entry and exits net to zero); later a foreign same-side
    position appears. The guard must not protect it from the old entry's id: touch nothing, an ambiguity report."""
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '60', '--enable-candidate'])[0] == 0      # one closed BTC trade
    d = tmp_path / 'nc' / C.load(cfg).account_id
    st = json.loads((d / A.STATE_FILE).read_text())
    assert all(D(p[2]) == 0 for p in st['positions'])
    st['positions'] = [['BTCUSDT', 'LONG', '0.5', '40000']]           # a manual position, not ours
    st['fills'].append(['999999', 'manual-eoid', 'BTCUSDT', 'LONG', '0.5', '40000', '0', 'USDT', '0', False,
                        st['fills'][-1][10] + 1])
    (d / A.STATE_FILE).write_text(json.dumps(st))
    _damage(d)
    code, out = run(['run', '--config', cfg, '--cycles', '5', '--enable-candidate'])
    assert code == A.EXIT_STORE_HOLD and 'GUARD' in out
    assert _emergency(d) == [] and 'ambiguous' in out


def test_p1_1_own_exits_are_attributed_and_only_the_surviving_net_is_protected(tmp_path):
    """Our lot's own stop (a lineage PROTECT child, found by its deterministic id) filled HALF the lot after the entry,
    then the rest of it is gone: the trades are all ours, the surviving net is entry - that fill, and exactly that is
    protected (not ambiguous, not the whole entry)."""
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '27', '--enable-candidate'])[0] == 0      # an open BTC lot
    d = tmp_path / 'nc' / C.load(cfg).account_id
    st = json.loads((d / A.STATE_FILE).read_text())
    stop, = [o for o in st['orders'] if o['type'] == 'STOP_MARKET' and o['status'] == 'NEW']
    lot = D(st['positions'][0][2])
    half = lot / 2
    stop.update(status='CANCELED', executed=str(half), avg=stop['stop'])          # partly filled, then cancelled
    st['fills'].append(['999998', stop['eoid'], 'BTCUSDT', 'LONG', str(half), stop['stop'], '0', 'USDT', '0', False,
                        st['fills'][-1][10] + 1])
    st['positions'][0][2] = str(lot - half)
    (d / A.STATE_FILE).write_text(json.dumps(st))
    _damage(d)
    code, out = run(['run', '--config', cfg, '--cycles', '5', '--enable-candidate'])
    assert code == A.EXIT_STORE_HOLD and 'ambiguous' not in out and 'surviving net' in out
    assert [D(o['qty']) for o in _emergency(d)] == [lot - half]


def test_p1_1_the_guard_protects_at_most_the_proven_residual(tmp_path):
    """Our open lot on top of a foreign position opened before it: our trades since the entry prove only our lot's
    quantity, so only that residual is protected (the older foreign part is never touched)."""
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '27', '--enable-candidate'])[0] == 0      # an open BTC lot
    d = tmp_path / 'nc' / C.load(cfg).account_id
    st = json.loads((d / A.STATE_FILE).read_text())
    ours = D(st['positions'][0][2]) if st['positions'][0][1] == 'LONG' else None
    for o in st['orders']:
        if o['type'] == 'STOP_MARKET':
            o['status'] = 'CANCELED'                                    # our stop is gone
    st['positions'][0][2] = str(ours + D('0.5'))                        # + a foreign (manual) position on the same
    st['fills'].insert(0, ['999999', 'manual-eoid', 'BTCUSDT', 'LONG', '0.5', '40000', '0', 'USDT', '0', False,
                           st['fills'][0][10] - 1])                     # side, opened BEFORE our entry
    (d / A.STATE_FILE).write_text(json.dumps(st))
    _damage(d)
    code, out = run(['run', '--config', cfg, '--cycles', '5', '--enable-candidate'])
    assert code == A.EXIT_STORE_HOLD
    em = _emergency(d)
    assert [D(o['qty']) for o in em] == [ours]


def test_p1_1_unreadable_trades_touch_nothing(tmp_path, monkeypatch):
    """The provenance cannot be correlated (the venue's trades read is not OK): HOLD + an ambiguity report, no stop."""
    from newcore.adapters.fake_venue import FakeVenue
    from newcore.ports.venue import ReadKind, ReadOutcome
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '27', '--enable-candidate'])[0] == 0
    d = tmp_path / 'nc' / C.load(cfg).account_id
    st = json.loads((d / A.STATE_FILE).read_text())
    for o in st['orders']:
        if o['type'] == 'STOP_MARKET':
            o['status'] = 'CANCELED'
    (d / A.STATE_FILE).write_text(json.dumps(st))
    _damage(d)
    monkeypatch.setattr(FakeVenue, 'trades', lambda self, symbol, side, from_ms: ReadOutcome(
        kind=ReadKind.UNKNOWN, observed_at_ms=self.now_ms, detail='timeout'))
    code, out = run(['run', '--config', cfg, '--cycles', '5', '--enable-candidate'])
    assert code == A.EXIT_STORE_HOLD and 'ambiguous' in out and 'trades not proven' in out
    assert _emergency(d) == []
