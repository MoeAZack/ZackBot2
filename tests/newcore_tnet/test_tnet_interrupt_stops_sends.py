"""Codex P1 (#13, 6064560998): an interrupt absorbed while evidence is committed must stop the suite BEFORE any later
send. (1) in the preflight cassette: no scenario runs, nothing is sent; (2) in the first scenario's cassette or
sidecar: its result is kept, the later scenarios never send; BoundedPort refuses opening sends once one is pending."""
import importlib.util
import io
import os
import signal
from decimal import Decimal as D

import pytest

from tnet_support import ACCOUNT, DIGEST, Store, World

import newcore.tnet.recording as REC
from newcore.ports import venue as P
from newcore.ports.keys import client_id_for
from newcore.tnet.seams import BoundedPort

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cli():
    sp = importlib.util.spec_from_file_location('cli_p1', os.path.join(REPO, 'tools', 'newcore_tnet_runner.py'))
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod


def interrupt_on_write(monkeypatch, nth, how):
    """The nth recording._write call (1 = preflight cassette, 2 = first scenario cassette, 3 = its sidecar) is
    interrupted: 'signal' = a real SIGINT arrives mid-write, 'raise' = a KeyboardInterrupt from the I/O call."""
    real, seen = REC._write, {'n': 0}

    def write(path, text):
        seen['n'] += 1
        if seen['n'] == nth:
            if how == 'signal':
                signal.raise_signal(signal.SIGINT)
            else:
                seen['n'] += 100                        # fire once: the retry goes through
                raise KeyboardInterrupt
        return real(path, text)
    monkeypatch.setattr(REC, '_write', write)


def run(w, tmp_path, only):
    out = io.StringIO()
    argv = ['--target', 'testnet', '--account-id', ACCOUNT, '--key-digest', DIGEST, '--run-nonce', 'p1',
            '--cassette-dir', str(tmp_path / 'c'), '--report-dir', str(tmp_path / 'r')]
    for o in only:
        argv += ['--only', o]
    rc = cli().main(argv, http=w.fb, sleep=w.sleep, local_clock=w.clock, store=Store(), out=out,
                    monotonic=w.monotonic)
    return rc, out.getvalue()


def sends(fb):
    return [q for q in fb.requests if q.method in ('POST', 'DELETE')]


@pytest.mark.parametrize('how', ['signal', 'raise'])
def test_an_interrupt_in_the_preflight_cassette_runs_no_scenario(tmp_path, monkeypatch, how):
    interrupt_on_write(monkeypatch, 1, how)
    w = World()
    rc, out = run(w, tmp_path, ['T01-long', 'T12-flat'])
    assert rc == 6, out
    assert sends(w.fb) == []                                              # nothing at all was sent
    assert os.listdir(tmp_path / 'c') == ['tnet-p1-preflight.json']       # the preflight cassette is complete
    assert [n for n in os.listdir(tmp_path / 'r') if n.endswith('.json')]


def shape(fb):
    """The sends as (method, path, symbol, side) - client ids carry the scenario id, so compare the shape only."""
    import urllib.parse
    out = []
    for q in sends(fb):
        f = dict(urllib.parse.parse_qsl(q.query))
        out.append((q.method, q.url.split('/fapi')[-1], f.get('symbol'), f.get('side')))
    return out


@pytest.mark.parametrize('nth', [2, 3], ids=['cassette', 'sidecar'])
@pytest.mark.parametrize('how', ['signal', 'raise'])
def test_an_interrupt_in_a_scenario_cassette_stops_the_suite_before_the_next_send(tmp_path, monkeypatch, how, nth):
    base = World()                                      # what T01-long alone sends, uninterrupted
    rc0, out0 = run(base, tmp_path / 'base', ['T01-long'])
    assert rc0 == 0, out0
    interrupt_on_write(monkeypatch, nth, how)
    w = World()
    rc, out = run(w, tmp_path, ['T01-long', 'T12-flat'])
    assert rc == 6, out
    assert shape(w.fb) == shape(base.fb)                # T01-long's sends and nothing after them
    assert 'T01-long' in out and 'PASS' in out          # its completed result is kept in the output
    cas = set(os.listdir(tmp_path / 'c'))
    assert {'tnet-p1-preflight.json', 'tnet-p1-t01-long.json', 'tnet-p1-t01-long.meta.json'} <= cas
    assert not any('t12' in n for n in cas) and not [n for n in cas if '.tmp' in n]
    assert [n for n in os.listdir(tmp_path / 'r') if n.endswith('.json')]
    assert w.fb.flat()


def test_bounded_port_refuses_an_opening_send_once_an_interrupt_is_pending():
    sent = []

    class Inner:
        def submit_market(self, o):
            sent.append(o)

        def submit_stop(self, o):
            sent.append(o)
    pending = {'v': False}
    port = BoundedPort(Inner(), max_orders=9, max_notional='1000', price_of=lambda s: D('100'),
                       interrupt_pending=lambda: pending['v'])
    ref = P.OrderRef(symbol='SOLUSDT', client_id=client_id_for('int_' + '3' * 32, 'classic'), route='classic')
    port.submit_market(P.MarketOrder(ref=ref, position_side='LONG', qty=D('1'), reduce=False))
    pending['v'] = True
    with pytest.raises(KeyboardInterrupt):
        port.submit_market(P.MarketOrder(ref=ref, position_side='LONG', qty=D('1'), reduce=False))
    port.submit_market(P.MarketOrder(ref=ref, position_side='LONG', qty=D('1'), reduce=True))      # a close still goes
    port.submit_stop(P.StopOrder(ref=ref, position_side='LONG', qty=D('1'), stop_price=D('99')))    # a stop too
    assert len(sent) == 3


# Codex P2 (re-review of efbdc78): exit precedence when the interrupt was absorbed by an evidence commit.

def test_a_completed_genuine_fail_stays_7_when_only_its_evidence_commit_was_interrupted():
    from newcore.tnet.driver import ScenarioResult, suite_exit_code
    done = ScenarioResult('T01-long', 'long_cycle', 'testnet', 'FAIL', error='InvariantBreach: x')
    done.evidence_interrupted = True                     # the result was complete; only its files were interrupted
    assert suite_exit_code([done]) == 7
    stopped = ScenarioResult('T01-long', 'long_cycle', 'testnet', 'FAIL', error='Interrupted')
    stopped.interrupted = True                           # the scenario ITSELF was stopped: FAIL-by-stop
    assert suite_exit_code([stopped]) == 6
    ok = ScenarioResult('T01-long', 'long_cycle', 'testnet', 'PASS')
    ok.evidence_interrupted = True
    assert suite_exit_code([ok]) == 6                    # the absorbed interrupt replaces only 0 / 1 / 2
    assert suite_exit_code([ScenarioResult('T01-long', 'long_cycle', 'testnet', 'FAIL', error='x')],
                           interrupted=True) == 7        # a Ctrl+C between scenarios never hides a FAIL


@pytest.mark.parametrize('nth', [2, 3], ids=['cassette', 'sidecar'])
def test_cli_a_genuine_fail_with_an_interrupted_cassette_exits_7_and_sends_nothing_later(tmp_path, monkeypatch, nth):
    real = REC.run_scenario

    def failing(spec, target, **kw):                     # T01-long completes, then is judged a genuine FAIL
        r = real(spec, target, **kw)
        r.verdict, r.error = 'FAIL', 'InvariantBreach: injected'
        r.assertions.append(('invariant', False, 'injected'))
        return r
    monkeypatch.setattr(REC, 'run_scenario', failing)
    interrupt_on_write(monkeypatch, nth, 'raise')
    w = World()
    rc, out = run(w, tmp_path, ['T01-long', 'T12-flat'])
    assert rc == 7, out
    assert 'INTERRUPTED' in out and not any('t12' in n for n in os.listdir(tmp_path / 'c'))
    assert w.fb.flat()


@pytest.mark.parametrize('how', ['signal', 'raise'])
def test_cli_a_refused_preflight_with_an_interrupted_cassette_exits_4(tmp_path, monkeypatch, how):
    interrupt_on_write(monkeypatch, 1, how)
    w = World()
    out = io.StringIO()
    argv = ['--target', 'testnet', '--account-id', ACCOUNT, '--key-digest', DIGEST, '--run-nonce', 'p2',
            '--cassette-dir', str(tmp_path / 'c'), '--report-dir', str(tmp_path / 'r'), '--only', 'T01-long',
            '--min-balance', '1000000000']
    rc = cli().main(argv, http=w.fb, sleep=w.sleep, local_clock=w.clock, store=Store(), out=out,
                    monotonic=w.monotonic)
    text = out.getvalue()
    assert rc == 4, text
    assert 'PREFLIGHT REFUSED' in text and 'before any scenario started' in text
    assert 'ran its teardown' not in text and sends(w.fb) == []


def test_cli_an_interrupted_preflight_cassette_says_no_scenario_ran(tmp_path, monkeypatch):
    interrupt_on_write(monkeypatch, 1, 'raise')
    w = World()
    rc, out = run(w, tmp_path, ['T01-long'])
    assert rc == 6 and 'before any scenario started' in out and 'ran its teardown' not in out, out
