"""Cowork 6063794395 verify list, the items confirmed on the S1 head with Cowork's scenarios (strengthened):

3   NOT_FOUND latch: a stop the venue LISTS is working whatever a by-id lookup says - any number of lagging NOT_FOUND
    answers never latch HOLD, never re-send a duplicate, never count an unprotected cycle.
4   G5 (rm -r journal undetected) + G1 (exit 4 leaves the position naked), together: the journal is removed AND our stop
    is gone - the run routes to the guard, which proves the lot from the venue's trades (Codex P1-1) and leaves exit 4
    with the position covered by one emergency stop, nothing else touched.
(2 - the 20k fuzz / NEW-3 - is test_fuzz_s1.py.)"""
import json
import shutil
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, IntentState
from newcore.runner import InjectedSignals, ids
from newcore.runner import app as A
from newcore.runner import config as C
from slice_helpers import H4, World, flat_bars
from test_run_cli import cfg_file, run

SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms


@pytest.mark.usefixtures('journal_kind')
@pytest.mark.parametrize('side', ('LONG', 'SHORT'))
@pytest.mark.parametrize('lags', (2, 3, 5))
def test_3_lagging_not_found_lookups_never_latch_hold_on_a_listed_stop(side, lags):
    w = World(flat_bars(20), InjectedSignals({(SYM, T0 + 6 * H4): (('enter', side),)}, stop_atr=D('2')))
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.venue.not_found(lot.live_stop.intent.client_order_id, lags)
    w.run(6 + lags + 2)
    r = w.runner
    lot, = r.fold.open_lots()
    assert lot.live_stop.state is IntentState.WORKING and r.fold.mode is EntriesMode.ACTIVE
    assert r.counters.unprotected_cycles == 0
    assert len([o for o in w.venue.open_orders().value if o.reduce and o.position_side == side]) == 1
    assert len([o for o in w.venue.orders_submitted() if o.order_type == 'STOP_MARKET']) == 1   # never re-sent


@pytest.mark.parametrize('how', ('rm_journal_dir', 'rm_segments'))
def test_4_g5_g1_a_removed_journal_and_a_gone_stop_exit_4_covered(tmp_path, how):
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '27', '--enable-candidate'])[0] == 0      # an open BTC lot
    d = tmp_path / 'nc' / C.load(cfg).account_id
    st = json.loads((d / A.STATE_FILE).read_text())
    pos = D(st['positions'][0][2])
    for o in st['orders']:
        if o['type'] == 'STOP_MARKET':
            o['status'] = 'CANCELED'                                    # our stop is gone too
    (d / A.STATE_FILE).write_text(json.dumps(st))
    if how == 'rm_journal_dir':
        shutil.rmtree(d / 'journal')
    else:
        for p in (d / 'journal').iterdir():
            p.unlink()
    for k in range(2):                                                  # exit 4, then again: idempotent
        code, out = run(['run', '--config', cfg, '--cycles', '5', '--enable-candidate'])
        assert code == A.EXIT_STORE_HOLD and 'GUARD' in out
        st = json.loads((d / A.STATE_FILE).read_text())
        em = [o for o in st['orders'] if ids.is_emergency_client_id(o['client_id']) and o['status'] == 'NEW']
        assert [D(o['qty']) for o in em] == [pos]                       # exit 4 is never naked
        assert not [o for o in st['orders'] if o['type'] == 'MARKET' and not o['reduce'] and o['status'] == 'NEW']
