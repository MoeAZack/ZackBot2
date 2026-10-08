"""TNET-01 harness hooks on the runner (nc-tnet01 requests H1 / H2 / H4; H3 comes with the REC-02 wiring).

H1  the entry's planned stop distance is journaled (the ENTER decision's 'dist <d>' token, exact Decimal text): a
    restarted process protects a fill with it even when the klines / signal can no longer re-derive it.
H2  `raw_qty`: a TESTNET-only, unsized entry quantity (so the venue's own min-qty / notional refusal is exercised);
    refused for any other binding and, in the run config, outside [tnet] + TESTNET + venue.kind testnet.
H4  the carrying stop's route (classic | algo) per open lot in the Runner summary and the health line."""
import dataclasses
from decimal import Decimal as D

import pytest

from newcore.domain import (Account, AccountBinding, Action, BindingConfirmation, BindingState, Environment, Purpose,
                            Venue, confirmation_phrase)
from newcore.runner import NoSignals, Runner, ids
from newcore.runner import config as C
from newcore.runner.reports import health_line
from newcore.runner.runner import journaled_distance
from mg_helpers import MgWorld, path, signals as mg_signals
from slice_helpers import ACCOUNT_ID, KEY_DIGEST, Crash, World, flat_bars
from test_fold_add import _world

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'


def _testnet_account(confirmed_at_ms):
    binding = AccountBinding(venue=Venue.BINANCE_USDM, environment=Environment.TESTNET, settlement_asset='USDT',
                             key_digest=KEY_DIGEST, exchange_uid=None)
    conf = BindingConfirmation(account_id=ACCOUNT_ID, old_key_digest=None, new_key_digest=KEY_DIGEST,
                               typed_phrase=confirmation_phrase(ACCOUNT_ID, KEY_DIGEST), confirmed_at_ms=confirmed_at_ms)
    return Account(account_id=ACCOUNT_ID, label='tnet', hedge_mode=True, binding=binding,
                   binding_state=BindingState.CONFIRMED, proposed_binding=None, confirmation=conf)


# ------------------------------------------------------------------------------------------------------------- H1
def test_h1_the_enter_decision_journals_the_exact_planned_stop_distance():
    w = _world()
    r = w.runner
    d, = [x for x in r.fold.decisions.values() if x.action is Action.ENTER]
    entry, = [iv for iv in r.fold.intents.values() if iv.purpose is Purpose.ENTRY]
    assert journaled_distance(d) == r._dist[entry.intent_id] and journaled_distance(d) > 0
    assert journaled_distance(None) is None


@pytest.mark.parametrize('side', ('LONG', 'SHORT'))
def test_h1_a_restart_protects_a_fill_from_the_journal_without_the_klines(side):
    """Crash right after the entry is booked (before its stop is decided); the restarted process has NO signal source
    able to re-derive the distance (NoSignals): it still places the stop at the planned level."""
    ref = World(flat_bars(20), mg_signals(side))
    ref.run(6)
    want = ref.runner.fold.open_lots()[0].protects[0].intent.stop_price
    w = World(flat_bars(20), mg_signals(side))
    w.run(4)
    w.journal.fail_writes(1, after=5, error=Crash)                  # entry: decision, intent, sent, result, closed
    t = w.close_ms(5)
    w.venue.advance_to(t)
    with pytest.raises(Crash):
        w.runner.cycle(t)
    w.signals = NoSignals(tf_label='4h')
    r = w.restart()
    lot, = r.fold.open_lots()
    assert not lot.protects
    r.cycle(t)
    lot, = r.fold.open_lots()
    assert lot.protects[0].intent.stop_price == want and r.counters.unprotected_cycles == 0


# ------------------------------------------------------------------------------------------------------------- H2
def test_h2_raw_qty_is_refused_for_a_non_testnet_binding():
    w = World(flat_bars(20), mg_signals('LONG'))
    cfg = dataclasses.replace(w.config, raw_qty=D('0.001'))
    with pytest.raises(ValueError, match='TESTNET-only'):
        Runner(cfg, journal=w.journal, venue=w.port, bars=w.bars, signals=w.signals, account_reads=w.venue)
    cfg = dataclasses.replace(w.config, account=_testnet_account(w.t0 - 1), raw_qty=D('0'))
    with pytest.raises(ValueError, match='positive'):
        Runner(cfg, journal=w.journal, venue=w.port, bars=w.bars, signals=w.signals, account_reads=w.venue)


def test_h2_raw_qty_sends_the_entry_unsized_on_a_testnet_binding():
    w = World(flat_bars(20), mg_signals('LONG'))
    w.config = dataclasses.replace(w.config, account=_testnet_account(w.t0 - 1), raw_qty=D('0.00042'))
    w.runner = w.new_runner()
    w.run(6)
    r = w.runner
    entry, = [iv for iv in r.fold.intents.values() if iv.purpose is Purpose.ENTRY]
    d = r.fold.decisions[entry.intent.decision_id]
    assert entry.intent.qty == D('0.00042') and 'raw_qty 0.00042' in d.detail
    assert journaled_distance(d) is not None                         # H1 token kept


def _cfg(tmp_path, **doc):
    return C.validate({'journal': {'dir': str(tmp_path / 'j')}, **doc}, environ={})


def test_h2_the_tnet_section_is_testnet_only(tmp_path):
    tn = {'mode': 'TESTNET', 'venue': {'kind': 'testnet', 'factory': 'pkg.mod:make'}}
    ok = _cfg(tmp_path, tnet={'enabled': True, 'raw_qty': '0.001'}, **tn)
    assert ok.tnet_enabled and ok.tnet_raw_qty == D('0.001')
    assert _cfg(tmp_path).tnet_enabled is False and _cfg(tmp_path).tnet_raw_qty is None
    for bad in (dict(tnet={'enabled': True}),                                   # PAPER / fake
                dict(tnet={'enabled': True}, venue={'kind': 'replay'}),
                dict(tnet={'raw_qty': '0.001'}, **tn),                          # raw qty without the flag
                dict(tnet={'enabled': 'yes'}, **tn),
                dict(tnet={'enabled': True, 'raw_qty': '-1'}, **tn),
                dict(tnet={'enabled': True, 'size': '1'}, **tn)):
        with pytest.raises(C.ConfigError):
            _cfg(tmp_path, **bad)


# ------------------------------------------------------------------------------------------------------------- H4
def test_h4_summary_and_health_line_name_the_stop_route():
    w = World(flat_bars(20), mg_signals('LONG'))
    w.run(3)
    assert w.runner.summary().stop_routes == '-' and health_line(w.runner).endswith('stops=-')
    w.run(6)
    assert w.runner.summary().stop_routes == f'{SYM}:LONG:classic'
    assert health_line(w.runner).endswith(f'stops={SYM}:LONG:classic')


@pytest.mark.parametrize('side', ('LONG', 'SHORT'))
def test_h4_a_managed_lot_on_the_algo_route_reports_algo(side):
    w = MgWorld(path(24, {}, side), mg_signals(side))
    w.venue.refuse_classic_stops(-4120)
    w.run(6)
    assert w.runner.stop_routes() == ((SYM, side, 'algo'),)
    assert f'stops={SYM}:{side}:algo' in health_line(w.runner)
    assert ids.marker_decision_id('mg fallback', 'int_' + '0' * 32).startswith('dec_')
