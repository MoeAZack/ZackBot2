"""REC-02 wired into the runner (docs/newcore/reconcile/RUNNER_WIRING.md). Skipped until the S1 agent applies the
wiring patch (RunnerConfig gains `rec02`); then these run as they are.

  W1 a clean long / short run with rec02 on journals exactly what reconcile v0 journals (no extra event, no HOLD)
  W2 a vanished stop is restored in the next cycle; nothing held
  W3 a manual close is an owner HOLD with ONE incident (deduplicated) naming the evidence; no new entry is taken
  W4 a positionRisk lag right after the fill settles (PENDING, check-only tick) where reconcile v0 HOLDs
  W5 needs_tick: a lost entry answer asks for check-only cycles; a settled clean account does not
  W6 the first pass after a restart is a STARTUP reconciliation
  W7 config: [reconcile] rec02 and [cycle] tick_s parse, default off / 30 s, refuse non-bool / negative
"""
import dataclasses
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, ReasonCode
from newcore.runner import runner as RUN

if 'rec02' not in {f.name for f in dataclasses.fields(RUN.RunnerConfig)}:
    pytest.skip('the REC-02 runner wiring patch is not applied yet', allow_module_level=True)

from newcore.ports import header_of                                                  # noqa: E402
from newcore.reconcile import Outcome, Trigger                                       # noqa: E402
from rec_helpers import ENTRY_BAR, SYM, fault_world, signals                         # noqa: E402
from slice_helpers import World, flat_bars                                           # noqa: E402

EXIT_BAR = 10


def wired(w, on=True):
    w.config = dataclasses.replace(w.config, rec02=on)
    w.runner = w.new_runner()
    return w


def digests(w):
    return [header_of(e).digest for e in w.journal.read()]


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_W1_clean_run_journals_exactly_what_reconcile_v0_journals(side):
    ref = World(flat_bars(20), signals(side, exit_=EXIT_BAR))
    ref.run(14)
    w = wired(World(flat_bars(20), signals(side, exit_=EXIT_BAR)))
    w.run(14)
    assert digests(w) == digests(ref)
    assert w.runner.fold.mode is EntriesMode.ACTIVE and w.runner.counters.unprotected_cycles == 0
    assert w.runner.last_verdict.outcome is Outcome.FLAT and w.runner.incidents == []


def test_W2_vanished_stop_is_restored_without_a_hold():
    w = wired(fault_world(flat_bars(20), signals()))
    w.run(ENTRY_BAR)
    lot, = w.runner.fold.open_lots()
    w.fault.vanish_stop(lot.live_stop.intent.client_order_id)
    w.run(ENTRY_BAR + 1)
    lot, = w.runner.fold.open_lots()
    assert lot.live_stop is not None and w.runner.fold.mode is EntriesMode.ACTIVE
    assert w.runner.last_verdict.outcome is Outcome.PROTECTED


def test_W3_manual_close_is_one_owner_hold_and_blocks_new_risk():
    more = {(SYM, flat_bars(1)[0].open_ms + 14 * 14_400_000): (('enter', 'LONG'),)}
    w = wired(fault_world(flat_bars(20), signals(more=more)))
    w.run(ENTRY_BAR + 1)
    lot, = w.runner.fold.open_lots()
    w.fault.manual_close(SYM, 'LONG', lot.qty)
    w.run(ENTRY_BAR + 4)
    f = w.runner.fold
    assert f.mode is EntriesMode.HOLD and ReasonCode.OWNERSHIP_UNTRACKED_POSITION in f.mode_reasons
    # the runner keeps Q2 off until NC-01 A1: the manual close is an owner item naming the venue trade, raised once
    texts = [t for _, t in w.runner.incidents if 'R03' in t]
    assert len(texts) == 1 and 'unexplained_deficit' in texts[0] and 'order:880001' in texts[0]
    assert 'actions=confirm_external_close,flatten' in texts[0]
    w.run(14)
    assert not [d for d in f.decisions.values() if str(d.action) == 'enter' and d.at_ms > w.close_ms(ENTRY_BAR)]


def test_W4_read_lag_after_the_fill_settles_where_reconcile_v0_holds():
    v0 = fault_world(flat_bars(20), signals())
    v0.run(ENTRY_BAR - 1)
    v0.fault.hide_position_reads(SYM, 'LONG', 1)
    v0.run(ENTRY_BAR)
    assert v0.runner.fold.mode is EntriesMode.HOLD                        # v0: the lag is a mismatch

    w = wired(fault_world(flat_bars(20), signals()))
    w.run(ENTRY_BAR - 1)
    w.fault.hide_position_reads(SYM, 'LONG', 2)
    w.run(ENTRY_BAR)
    assert w.runner.last_verdict.outcome is Outcome.PENDING and w.runner.fold.mode is EntriesMode.ACTIVE
    assert w.runner.needs_tick()
    w.runner.tick(w.venue.now_ms)                                         # the check-only cycle between candles
    assert w.runner.last_verdict.outcome is Outcome.PROTECTED and w.runner.fold.mode is EntriesMode.ACTIVE
    assert not w.runner.needs_tick()


def test_W5_needs_tick_follows_unknown_answers():
    w = wired(World(flat_bars(20), signals()))
    w.run(ENTRY_BAR - 1)
    assert not w.runner.needs_tick()
    w.venue.lose_next_market_answer('not_filled')
    w.run(ENTRY_BAR)
    assert w.runner.fold.mode is EntriesMode.HOLD and w.runner.needs_tick()


def test_W6_first_pass_after_a_restart_is_a_startup_reconciliation():
    w = wired(World(flat_bars(20), signals()))
    w.run(ENTRY_BAR)
    w.restart()
    seen, orig = [], w.runner.rec02

    def rec02(trigger):
        seen.append(trigger)
        return orig(trigger)
    w.runner.rec02 = rec02
    w.run(ENTRY_BAR + 2)                                                  # venue and runner advance together
    assert seen == [Trigger.STARTUP, Trigger.CYCLE, Trigger.CYCLE, Trigger.CYCLE]
    assert w.runner.last_verdict.outcome is Outcome.PROTECTED


def test_W7_config_parses_rec02_and_tick_s():
    from newcore.runner import config as C
    base = {'mode': 'PAPER'}
    c = C.validate(base)
    assert (c.rec02, c.tick_s) == (False, 30.0)
    c = C.validate({**base, 'reconcile': {'rec02': True}, 'cycle': {'tick_s': 5}})
    assert (c.rec02, c.tick_s) == (True, 5.0)
    for bad in ({'reconcile': {'rec02': 'yes'}}, {'cycle': {'tick_s': -1}}, {'reconcile': {'other': 1}}):
        with pytest.raises(C.ConfigError):
            C.validate({**base, **bad})
