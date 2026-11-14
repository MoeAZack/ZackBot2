"""RT-01 regression tests for each defect found by the adversarial review of 22fd123."""
from __future__ import annotations

import dataclasses
import io
import json
import sys
import threading
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rt01_fixtures import D, Evidence, World, account_view, acct  # noqa: E402

import newcore.runtime.runtime as rt_module  # noqa: E402
from newcore.account_context import OpenOrdersRequest, Operation, PositionsRequest, VenueWork  # noqa: E402
from newcore.domain import Action  # noqa: E402
from newcore.domain.orders import Side  # noqa: E402
from newcore.management import ProtectionStatus  # noqa: E402
from newcore.ports.venue import MarketOrder, OrderRef  # noqa: E402
from newcore.reconciliation import VenuePosition  # noqa: E402
from newcore.runtime import (AccountSpec, HoldCode, RuntimeBootError, RuntimeConfig, RuntimeConfigError,  # noqa: E402
                             RuntimeEnvironment)
from newcore.runtime.__main__ import EXIT_OK, EXIT_REFUSED, main  # noqa: E402
from newcore.store import JournalLocked, recover_journal  # noqa: E402

from test_rt01_runtime import Lots, _evaluation  # noqa: E402


@pytest.fixture
def world(tmp_path):
    made = []

    def make(n=1, **kwargs):
        w = World(tmp_path, n, **kwargs)
        made.append(w)
        return w

    yield make
    for w in made:
        try:
            w.shutdown()
        except Exception:
            pass


def _relock_free(w):
    for spec in w.specs:
        again = recover_journal(spec.account_dir, spec.account_id, spec.aggregate_id)
        assert again.journal is not None
        again.journal.close()


# R1: a context with leftover work is refused before any journal or venue is touched.
def test_context_with_prequeued_order_is_refused_before_anything_is_sent(world):
    w = world()
    ports = w.ports()
    order = MarketOrder(ref=OrderRef(symbol='BTCUSDT', client_id='zbn1o-pre', route='classic'),
                        position_side='LONG', qty=D('1'), reduce=True)
    ports[acct(1)].context.submit(VenueWork(work_id='pre', action=Action.CLOSE, operation=Operation.SUBMIT_MARKET,
                                            payload=order, enqueued_at_ms=w.mono(), deadline_ms=w.mono() + 10_000))
    with pytest.raises(RuntimeBootError, match='account_context_not_fresh'):
        rt_module.boot(w.config, ports, boot_id='b', monotonic_clock=w.mono, wall_clock=w.wall)
    assert w.venues[acct(1)].calls == []
    _relock_free(w)


# R2: reused work ids can never fold an old generation into a fresh proof.
class FixedIds(Evidence):
    def read_work(self, account_id, generation, now_mono, deadline_mono):
        return (VenueWork(work_id='pos', action=Action.RECONCILE, operation=Operation.POSITIONS,
                          payload=PositionsRequest(), enqueued_at_ms=now_mono, deadline_ms=deadline_mono),
                VenueWork(work_id='oo', action=Action.RECONCILE, operation=Operation.OPEN_ORDERS,
                          payload=OpenOrdersRequest(symbol='BTCUSDT'), enqueued_at_ms=now_mono,
                          deadline_ms=deadline_mono))


def test_reused_evidence_ids_hold_instead_of_folding_old_results(world):
    w = world(refresh_period_ms=1_000, proof_max_age_ms=12_000)
    w.evidence[acct(1)] = FixedIds(w.truths[acct(1)])
    rt = w.boot()
    boot_proof = account_view(rt.snapshot(), acct(1)).proof_mono
    snap = w.settle(lambda s: 'evidence_work_invalid' in account_view(s, acct(1)).holds, advance_ms=500)
    view = account_view(snap, acct(1))
    assert view.proof_mono in (None, boot_proof) and not snap.scheduler.ready


def test_guards_hold_when_the_scheduler_latched_reconcile(world):
    w = world()
    rt = w.boot()
    w.settle(lambda s: s.scheduler.ready)
    rt._scheduler_reconcile = lambda account_id: True
    w.settle(lambda s: 'worker_reconcile_before_restart' in account_view(s, acct(1)).holds, advance_ms=1_000)


# R3: an NC-07 HOLD for stale or mismatched facts never lets the account be ready.
@pytest.mark.parametrize('change', [
    lambda e: dataclasses.replace(e, protection=dataclasses.replace(e.protection, fresh=False)),
    lambda e: dataclasses.replace(e, lot_generation=6)])
def test_stale_or_mismatched_management_facts_keep_the_account_not_ready(world, change):
    w = world()
    w.extra_ports[acct(1)] = {'management': Lots((change(_evaluation(protection_status=ProtectionStatus.MISSING)),))}
    w.boot()
    snap = w.settle(lambda s: any(e.code.startswith('not_ready:') for e in s.events))
    for _ in range(30):
        snap = w.runtime.step()
        assert not snap.scheduler.ready
    assert not any(e.code.startswith('why_held:') for e in snap.events)


# R4/R5: a boot failure after recovery never leaks a journal lock, and NC-04 errors become a hold.
def test_invalid_boot_id_is_refused_before_any_journal_is_opened(world):
    w = world()
    with pytest.raises(RuntimeBootError, match='boot_id_invalid'):
        rt_module.boot(w.config, w.ports(), boot_id='', monotonic_clock=w.mono, wall_clock=w.wall)
    _relock_free(w)


def test_scheduler_construction_failure_releases_journal_locks(world, monkeypatch):
    w = world()

    def broken(*args, **kwargs):
        raise RuntimeError('scheduler')

    monkeypatch.setattr(rt_module, 'Scheduler', broken)
    with pytest.raises(RuntimeError, match='scheduler'):
        w.boot()
    w.runtime = None
    _relock_free(w)


def test_nc04_exception_during_boot_is_a_per_account_hold(world):
    w = world(2)
    ports = w.ports()

    def explode():
        raise RuntimeError('nc04 internal failure')

    ports[acct(1)].context.run_next = explode
    rt = rt_module.boot(w.config, ports, boot_id='b', monotonic_clock=w.mono, wall_clock=w.wall)
    w.runtime = rt
    assert account_view(rt.snapshot(), acct(1)).holds == ('boot_error',)
    assert account_view(rt.snapshot(), acct(2)).restore_proven


def test_unexpected_hook_exception_becomes_a_runtime_hold(world):
    w = world()
    rt = w.boot()
    w.settle(lambda s: s.scheduler.ready)

    def explode():
        raise RuntimeError('snapshot')

    rt._accounts[acct(1)].ports.context.snapshot = explode
    w.settle(lambda s: 'hook_error' in account_view(s, acct(1)).holds, advance_ms=1_000)


# R6: the restore mark and a concurrent hold cannot interleave.
def test_restore_mark_is_atomic_with_the_hold_check(world):
    w = world()
    w.truths[acct(1)].venue_positions = (VenuePosition('BTCUSDT', Side.LONG, D('1')),)
    rt = w.boot()
    row = rt._accounts[acct(1)]
    row.holds = set()  # a later proof cleared the boot reconcile hold
    row.restore_mark_due = True
    seen = []
    real = rt._scheduler.mark_restore_proven

    def mark(account_id):
        # A runner trying to hold now must wait for the owner to finish the mark.
        got = row.lock.acquire(blocking=False)
        seen.append(got)
        if got:
            row.lock.release()
        real(account_id)

    rt._scheduler.mark_restore_proven = mark
    rt.step()
    assert seen == [False] and row.restore_proven


# R7: CLI live-switch presence is case-insensitive, an empty value counts, and parse failures are refusals.
def _cfg(tmp_path, text):
    path = tmp_path / 'c.json'
    path.write_text(text, encoding='utf-8')
    return str(path)


GOOD = json.dumps({'schema': 'zackbot.newcore.runtime.config.v1', 'environment': 'testnet',
                   'accounts': [{'account_id': 'acct_' + '1' * 32, 'aggregate_id': 'pf_' + '1' * 32,
                                 'account_dir': '/tmp/none', 'egress_id': 'e1'}]})


@pytest.mark.parametrize('environ', [{'LIVE_CONFIRM': ''}, {'live_confirm': '1'}, {'Zackbot_Mainnet': '0'}])
def test_cli_refuses_any_live_switch_presence(tmp_path, environ):
    out = io.StringIO()
    assert main(['check', '--config', _cfg(tmp_path, GOOD)], environ=environ, out=out) == EXIT_REFUSED


def test_cli_turns_pathological_json_into_a_refusal(tmp_path):
    out = io.StringIO()
    assert main(['check', '--config', _cfg(tmp_path, '[' * 100_000)], environ={}, out=out) == EXIT_REFUSED
    assert json.loads(out.getvalue())['code'] == 'config_unreadable'


def test_cli_never_probes_a_unc_path(tmp_path, monkeypatch):
    import newcore.runtime.__main__ as cli
    monkeypatch.setattr(cli.os.path, 'isdir', lambda p: (_ for _ in ()).throw(AssertionError('probed')))
    text = GOOD.replace('/tmp/none', '//server/share/a1')
    out = io.StringIO()
    assert main(['check', '--config', _cfg(tmp_path, text)], environ={}, out=out) == EXIT_OK
    assert json.loads(out.getvalue())['plan']['accounts'][0]['account_dir_exists'] is None


# Plausible items closed as well.
def test_account_dir_aliases_are_duplicates():
    def spec(n, d):
        return AccountSpec(account_id=f'acct_{n:032x}', aggregate_id=f'pf_{n:032x}', account_dir=d, egress_id='e')

    with pytest.raises(RuntimeConfigError, match='duplicate_account_dir'):
        RuntimeConfig(environment=RuntimeEnvironment.TESTNET, accounts=(spec(1, '/x/a'), spec(2, '/x/./b/../a')))


def test_stop_grace_must_leave_time_to_drain_protection():
    spec = AccountSpec(account_id='acct_' + '1' * 32, aggregate_id='pf_' + '1' * 32, account_dir='/a',
                       egress_id='e')
    with pytest.raises(RuntimeConfigError, match='stop_grace_ms_invalid'):
        RuntimeConfig(environment=RuntimeEnvironment.TESTNET, accounts=(spec,), stop_grace_ms=0)
