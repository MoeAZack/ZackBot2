"""Cowork's re-run of its original scripts (PR #37, issuecomment 6062740390), the items still open there - repros first:

4b/289  the 20k fuzz: an entry filled at the venue and the lazy store lost its last 1-3 events (every record of the
        entry with 3): the restart sat in HOLD, unowned and naked. Now: before reconciling, a venue position the journal
        cannot explain on a traded side is checked against the deterministic client ids of the strategy's own recent
        ENTER signals; the venue's order proves it ours -> the decision, intent, send and result are recorded and the
        lot is owned and protected (lost 1-3, crash after the entry or after its stop, same candle or later).
6       a crash after the entry with 2 lost events ended in HOLD (the reconciliation ran before the recovery): now the
        recovery runs first, the account stays ACTIVE.
NEW-3   a hard-HOLD emergency stop outlived the flat position until a clean restart: once the venue shows that side
        flat, it is cancelled (exchange cleanup; protection is never cancelled while exposed).
GUARD   a deleted journal (rm -r journal/, or every segment removed) with exposure / our orders on the venue booted a
        normal fresh run: now it is a store HOLD and routes to the guard (exit 4)."""
import json
import shutil
from decimal import Decimal as D

import pytest

from newcore.adapters import MemoryJournal
from newcore.domain import EntriesMode, IntentState
from newcore.runner import InjectedSignals, NoSignals, ids
from newcore.runner import app as A
from newcore.runner import config as C
from slice_helpers import ACCOUNT_ID, H4, PORTFOLIO_ID, Crash, World, flat_bars
from test_run_cli import cfg_file, run

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def sig(side, bar=5):
    return InjectedSignals({(SYM, T0 + (bar + 1) * H4): (('enter', side),)}, stop_atr=D('2'))


def position(w, side):
    return next(p.qty for p in w.venue.positions().value if p.side == side)


def covered(w, side):
    return sum((o.qty for o in w.venue.open_orders().value if o.reduce and o.position_side == side), D(0))


def lose_tail(side, effect, lost, signals=None):
    w = World(flat_bars(20), sig(side), strict=False)
    w.run(4)
    w.port.crash(effect, 'after')                                  # 1 = the entry, 2 = its stop
    t5 = w.close_ms(5)
    w.venue.advance_to(t5)
    with pytest.raises(Crash):
        w.runner.cycle(t5)
    w.port.disarm()
    keep = w.journal.read()[:-lost]
    w.journal = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    for e in keep:
        w.journal.append(e)
    if signals is not None:
        w.signals = signals
    w.runner = w.new_runner()
    return w, t5


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('effect', (1, 2))
@pytest.mark.parametrize('lost', (1, 2, 3))
@pytest.mark.parametrize('later', (False, True))
def test_289_lazy_store_tail_loss_after_a_filled_entry_is_owned_and_protected(side, effect, lost, later):
    w, t5 = lose_tail(side, effect, lost)
    if not later:
        w.runner.cycle(t5)
    w.run(9)
    r = w.runner
    assert position(w, side) == D('5')
    lot, = r.fold.open_lots()
    assert lot.qty == D('5') and lot.live_stop is not None and lot.live_stop.state is IntentState.WORKING
    assert covered(w, side) == D('5')                              # one stop, never two
    assert r.counters.unprotected_cycles == 0


@pytest.mark.parametrize('side', SIDES)
def test_item6_a_recovered_entry_does_not_put_the_account_in_hold(side):
    w, t5 = lose_tail(side, 1, 2)
    w.runner.cycle(t5)
    w.run(7)
    assert w.runner.fold.mode is EntriesMode.ACTIVE and w.runner.fold.open_lots()


@pytest.mark.parametrize('side', SIDES)
def test_contract_an_unprovable_position_is_a_surfaced_hold(side):
    """When nothing proves the position ours (every record lost AND the strategy cannot re-derive its signal), the
    runner never goes ACTIVE over it: an untracked-position item and a durable HOLD."""
    w, t5 = lose_tail(side, 1, 3, signals=NoSignals(tf_label='4h'))
    w.run(7)
    r = w.runner
    assert r.fold.mode is EntriesMode.HOLD and not r.fold.open_lots() and position(w, side) == D('5')
    assert any(k == 'position' for k, _ in r.last_rec.items)


@pytest.mark.parametrize('side', SIDES)
def test_new3_a_hard_hold_emergency_stop_is_cancelled_once_the_side_is_flat(side):
    w = World(flat_bars(20), sig(side))
    w.run(6)
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')
    w.venue.inject_position(SYM, side, D('3'), D('100'))
    w.run(7)
    assert [o for o in w.venue.open_orders().value if ids.is_emergency_client_id(o.ref.client_id)]
    w.venue._positions.pop((SYM, side), None)                      # flattened by hand while the store is down
    w.run(8)
    assert not [o for o in w.venue.open_orders().value if ids.is_emergency_client_id(o.ref.client_id)]


@pytest.mark.parametrize('how', ('rm_journal_dir', 'rm_segments'))
def test_guard_a_deleted_journal_with_venue_exposure_routes_to_the_guard(tmp_path, how):
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '27', '--enable-candidate'])[0] == 0      # an open BTC lot
    d = tmp_path / 'nc' / C.load(cfg).account_id
    st = json.loads((d / A.STATE_FILE).read_text())
    assert any(float(p[2]) > 0 for p in st['positions'])
    if how == 'rm_journal_dir':
        shutil.rmtree(d / 'journal')
    else:
        for p in (d / 'journal').iterdir():
            p.unlink()
    code, out = run(['run', '--config', cfg, '--cycles', '5', '--enable-candidate'])
    assert code == A.EXIT_STORE_HOLD and 'GUARD' in out
