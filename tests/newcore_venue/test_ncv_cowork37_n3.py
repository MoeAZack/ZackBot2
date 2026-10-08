"""Cowork #37 N3: after kill -9 / power loss the next run refuses (leftover NEWCORE order / not flat); the venue CLI's
--cleanup is the entry point that removes the stranded NEWCORE state. Fake HTTP, DUMMY keys."""
from decimal import Decimal as D

from fake_binance import FakeBinance
from test_ncv_tnet_harness import env, run  # noqa: F401  (env is a fixture)

STOP = 'zbn1o-' + 'b' * 26


def stranded(foreign=False):
    """A crash left: a NEWCORE stop resting + a NEWCORE LONG 1 (+ optionally a foreign order)."""
    fb = FakeBinance(foreign_orders=('web_manual1',) if foreign else ())
    fb._rest_classic(STOP, 'SOLUSDT', 'SELL', 'LONG', D('1'), D('150'))
    fb.pos[('SOLUSDT', 'LONG')] = D('1')
    fb.visible_pos[('SOLUSDT', 'LONG')] = D('1')
    return fb


def test_a_stranded_run_is_refused_and_names_the_cleanup_command(env):  # noqa: F811
    fb = stranded()
    rc, out = run(env, ['--probe', 'P1'], http=fb)
    assert rc == 4 and 'leftover_newcore_order' in out and 'tools/newcore_tnet.py --cleanup' in out


def test_cleanup_cancels_newcore_orders_and_lists_positions(env):  # noqa: F811
    fb = stranded(foreign=True)
    rc, out = run(env, ['--cleanup'], http=fb)
    assert rc == 8 and 'POSITIONS LEFT' in out and 'SOLUSDT LONG 1' in out
    assert fb.orders[STOP]['status'] == 'CANCELED' and fb.orders['web_manual1']['status'] == 'NEW'
    assert fb.pos[('SOLUSDT', 'LONG')] == D('1')                     # never closed without --close-positions


def test_cleanup_with_close_positions_leaves_the_account_flat(env):  # noqa: F811
    fb = stranded(foreign=True)
    rc, out = run(env, ['--cleanup', '--close-positions'], http=fb)
    assert rc == 0 and 'CLEANUP CLEAN' in out
    assert fb.flat() and fb.orders['web_manual1']['status'] == 'NEW'
    rc, out = run(env, ['--probe', 'P1', '--adopt-foreign', 'web_manual1'], http=fb)
    assert rc == 0                                                    # the next run is accepted again


def test_cleanup_keeps_an_adopted_foreign_position(env):  # noqa: F811
    fb = stranded()
    fb.pos[('SOLUSDT', 'SHORT')] = D('2')
    fb.visible_pos[('SOLUSDT', 'SHORT')] = D('2')
    rc, out = run(env, ['--cleanup', '--close-positions', '--adopt-foreign', 'SOLUSDT:SHORT:2'], http=fb)
    assert rc == 0 and fb.pos[('SOLUSDT', 'LONG')] == 0 and fb.pos[('SOLUSDT', 'SHORT')] == D('2')


def test_cleanup_dry_run_sends_nothing_and_reads_no_key(env):  # noqa: F811
    fb = stranded()
    rc, out = run(env, ['--cleanup', '--dry-run'], http=fb)
    assert rc == 0 and 'cleanup - PLAN' in out and fb.requests == []


def test_cleanup_refuses_mixing_and_close_positions_alone(env):  # noqa: F811
    assert run(env, ['--cleanup', '--probe', 'P1'], http=FakeBinance())[0] == 2
    assert run(env, ['--probe', 'P1', '--close-positions'], http=FakeBinance())[0] == 2


def test_cleanup_on_a_one_way_account_is_refused_without_sending(env):  # noqa: F811
    fb = FakeBinance(dual=False)
    rc, out = run(env, ['--cleanup', '--close-positions'], http=fb)
    assert rc == 4 and not [q for q in fb.requests if q.method in ('POST', 'DELETE')]


def test_cleanup_writes_a_cassette_that_replays_and_a_report(env):  # noqa: F811
    import importlib.util
    import io
    import json
    import os

    from ncv_support import DUMMY_KEY, DUMMY_SECRET
    fb = stranded(foreign=True)
    rc, out = run(env, ['--cleanup', '--close-positions'], http=fb)
    assert rc == 0 and 'cassette: ' in out and 'report: ' in out
    (cas,) = [os.path.join(env['cassettes'], n) for n in os.listdir(env['cassettes'])]
    reps = [os.path.join(env['reports'], n) for n in os.listdir(env['reports']) if n.endswith('.json')]
    doc = json.load(open(reps[0], encoding='utf-8'))
    assert doc['final_exchange_truth']['clean'] and doc['cassette'] == cas
    text = open(cas, encoding='utf-8').read() + open(reps[0], encoding='utf-8').read()
    assert DUMMY_KEY not in text and DUMMY_SECRET not in text
    here = os.path.dirname(os.path.abspath(__file__))
    sp = importlib.util.spec_from_file_location('rp_cleanup', os.path.join(os.path.dirname(os.path.dirname(here)),
                                                                           'tools', 'newcore_replay_cassette.py'))
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    o = io.StringIO()
    assert mod.main([cas], out=o) == 0, o.getvalue()          # every recorded teardown request replays


def test_cleanup_report_failure_is_exit_5(env, monkeypatch):  # noqa: F811
    from test_ncv_tnet_harness import tool
    mod = tool()
    import newcore.venue.tnet as T

    def leak(*a, **k):
        raise T.ReportLeak('synthetic')
    monkeypatch.setattr(mod, 'tnet_report', leak)
    rc, out = run(env, ['--cleanup', '--close-positions'], http=stranded(), mod=mod)
    assert rc == 5 and 'report not written' in out
