"""RT-02 PAPER run command: init-paper, run, the paper book round trip and the refusals.  No network, no credentials.
All text I/O is utf-8."""
from __future__ import annotations

import io
import json
import threading
import time
from decimal import Decimal

import pytest

from newcore.domain import BindingChanged
from newcore.domain.account import BindingState, Environment
from newcore.ports import venue as P
from newcore.runtime.__main__ import EXIT_HELD, EXIT_OK, EXIT_REFUSED, EXIT_USAGE, main
from newcore.runtime.paper import STATE_SCHEMA, PaperVenue
from newcore.runtime.paper_run import BOOK_FILE, PAPER_KEY_DIGEST, FileMarks, PaperRunError
from newcore.store import recover_journal

D = Decimal


def acct(n):
    return f'acct_{n:032x}'


def write_json(path, value):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(value, handle)
    return str(path)


def config_file(tmp_path, *, environment='paper_sim', n=1, **extra):
    row = {'schema': 'zackbot.newcore.runtime.config.v1', 'environment': environment,
           'accounts': [{'account_id': acct(i), 'aggregate_id': f'pf_{i:032x}', 'account_dir': str(tmp_path / f'a{i}'),
                         'egress_id': f'egress-{i}'} for i in range(1, n + 1)]}
    row.update(extra)
    return write_json(tmp_path / 'config.json', row)


class FakeTime:
    """Clocks the run loop advances through ``sleep``; runner threads still get a little real time."""

    def __init__(self):
        self.mono, self.wall = 1_000_000, 2_000_000_000_000
        self.lock = threading.Lock()

    def mono_ms(self):
        with self.lock:
            return self.mono

    def wall_ms(self):
        with self.lock:
            return self.wall

    def sleep(self, seconds):
        with self.lock:
            self.mono += int(seconds * 1000)
            self.wall += int(seconds * 1000)
        time.sleep(0.003)

    def hooks(self):
        return {'sleep': self.sleep, 'monotonic_clock': self.mono_ms, 'wall_clock': self.wall_ms}


def lines(out):
    return [json.loads(x) for x in out.getvalue().splitlines()]


# ------------------------------------------------------------------------------------------------ init-paper
def test_init_paper_journals_a_confirmed_sim_binding_once(tmp_path):
    cfg = config_file(tmp_path)
    out = io.StringIO()
    assert main(['init-paper', '--config', cfg], environ={}, out=out) == EXIT_OK
    assert lines(out)[-1]['accounts'][0]['result'] == 'bound'
    journal = recover_journal(str(tmp_path / 'a1'), acct(1), f'pf_{1:032x}').journal
    try:
        (ev,) = [e for e in journal.read() if isinstance(e, BindingChanged)]
    finally:
        journal.close()
    assert ev.to_state is BindingState.CONFIRMED and ev.binding.environment is Environment.SIM
    assert ev.binding.key_digest == PAPER_KEY_DIGEST == ev.confirmation.new_key_digest
    out = io.StringIO()
    assert main(['init-paper', '--config', cfg], environ={}, out=out) == EXIT_REFUSED
    assert lines(out)[-1]['accounts'][0]['result'] == 'already_bound'


def test_init_paper_refuses_a_testnet_config(tmp_path):
    out = io.StringIO()
    assert main(['init-paper', '--config', config_file(tmp_path, environment='testnet')], environ={},
                out=out) == EXIT_REFUSED
    assert lines(out) == [{'status': 'refused', 'code': 'paper_only'}]
    assert not (tmp_path / 'a1').exists()


# ------------------------------------------------------------------------------------------------ run
def test_run_proves_a_flat_paper_account_and_saves_the_book(tmp_path):
    cfg = config_file(tmp_path)
    marks = write_json(tmp_path / 'marks.json', {'BTCUSDT': '100'})
    assert main(['init-paper', '--config', cfg], environ={}, out=io.StringIO()) == EXIT_OK
    out, clock = io.StringIO(), FakeTime()
    code = main(['run', '--config', cfg, '--marks', marks, '--step-ms', '250', '--max-steps', '60',
                 '--status-every', '10'], environ={}, out=out, run_hooks=clock.hooks())
    rows = lines(out)
    assert code == EXIT_OK, rows[-1]
    assert rows[0]['status'] == 'booted' and rows[-1]['status'] == 'stopped' and rows[-1]['reason'] == 'max_steps'
    (account,) = rows[-1]['accounts']
    assert account['holds'] == [] and account['plan'] == 'flat_confirmed' and account['restore_proven']
    assert rows[-1]['scheduler'] == 'stopped' and rows[-1]['entries_enabled'] is False
    with open(tmp_path / 'a1' / BOOK_FILE, encoding='utf-8') as handle:
        assert json.load(handle)['schema'] == STATE_SCHEMA


def test_run_without_init_is_refused(tmp_path):
    cfg = config_file(tmp_path)
    marks = write_json(tmp_path / 'marks.json', {'BTCUSDT': '100'})
    out = io.StringIO()
    assert main(['run', '--config', cfg, '--marks', marks, '--max-steps', '5'], environ={}, out=out,
                run_hooks=FakeTime().hooks()) == EXIT_REFUSED
    assert lines(out) == [{'status': 'refused', 'code': 'account_not_initialised'}]
    assert not (tmp_path / 'a1').exists()


def test_cli_entry_disable_file_latches_before_boot_and_cannot_be_cleared_by_removing_it(tmp_path):
    cfg = config_file(tmp_path, entries_enabled=True)
    assert main(['init-paper', '--config', cfg], environ={}, out=io.StringIO()) == EXIT_OK
    marks = write_json(tmp_path/'marks.json', {'BTCUSDT': '100'})
    latch = tmp_path/'DISABLE_ENTRIES'
    latch.write_text('disable\n', encoding='utf-8')
    class Source:
        calls = 0
        def candidates(self, account_id, now_mono):
            self.calls += 1
            return ()
    source, clock, out = Source(), FakeTime(), io.StringIO()
    def sleep(seconds):
        latch.unlink(missing_ok=True)
        clock.sleep(seconds)
    hooks = {**clock.hooks(), 'sleep': sleep,
             'entries_factory': lambda spec, **kwargs: source}
    result = main(['run', '--config', cfg, '--marks', marks, '--entry-disable-file', str(latch),
        '--max-steps', '60', '--status-every', '5'], environ={}, out=out, run_hooks=hooks)
    rows = lines(out)
    assert result == EXIT_OK and source.calls == 0
    assert all(row['entries_enabled'] is False for row in rows)
    assert any(row['status'] == 'running' for row in rows)
    assert rows[-1]['accounts'][0]['plan'] == 'flat_confirmed'


def test_run_with_a_journal_but_no_binding_holds_the_account(tmp_path):
    from newcore.store import create_journal
    cfg = config_file(tmp_path)
    marks = write_json(tmp_path / 'marks.json', {'BTCUSDT': '100'})
    create_journal(str(tmp_path / 'a1'), acct(1), f'pf_{1:032x}').close()
    out = io.StringIO()
    code = main(['run', '--config', cfg, '--marks', marks, '--max-steps', '5'], environ={}, out=out,
                run_hooks=FakeTime().hooks())
    assert code == EXIT_HELD
    assert lines(out)[-1]['accounts'][0]['holds']                  # no confirmed binding: never trades


def test_run_stops_on_the_stop_file(tmp_path):
    cfg = config_file(tmp_path)
    marks = write_json(tmp_path / 'marks.json', {'BTCUSDT': '100'})
    main(['init-paper', '--config', cfg], environ={}, out=io.StringIO())
    stop = tmp_path / 'STOP'
    stop.write_text('stop', encoding='utf-8')
    out = io.StringIO()
    main(['run', '--config', cfg, '--marks', marks, '--stop-file', str(stop)], environ={}, out=out,
         run_hooks=FakeTime().hooks())
    assert lines(out)[-1]['reason'] == 'stop_file' and lines(out)[-1]['step'] == 0


@pytest.mark.parametrize('environment,extra,code', [
    ('testnet', {}, 'testnet_run_unavailable'),
    ('paper_sim', {'entries_enabled': True}, 'entries_source_missing'),
])
def test_run_refusals(tmp_path, environment, extra, code):
    cfg = config_file(tmp_path, environment=environment, **extra)
    marks = write_json(tmp_path / 'marks.json', {'BTCUSDT': '100'})
    out = io.StringIO()
    assert main(['run', '--config', cfg, '--marks', marks, '--max-steps', '1'], environ={}, out=out) == EXIT_REFUSED
    assert lines(out) == [{'status': 'refused', 'code': code}]
    assert not (tmp_path / 'a1').exists()


@pytest.mark.parametrize('variable', ['LIVE_CONFIRM', 'zackbot_mainnet', 'ZACKBOT_LIVE'])
def test_every_command_refuses_a_live_switch(tmp_path, variable):
    cfg = config_file(tmp_path)
    for argv in (['init-paper', '--config', cfg], ['run', '--config', cfg, '--marks', cfg]):
        out = io.StringIO()
        assert main(argv, environ={variable: ''}, out=out) == EXIT_REFUSED
        assert lines(out)[0]['code'] == 'live_switch_present'
    assert not (tmp_path / 'a1').exists()


def test_run_usage_errors(tmp_path):
    assert main(['run', '--config', 'x'], environ={}, out=io.StringIO()) == EXIT_USAGE      # --marks missing
    cfg = config_file(tmp_path)
    marks = write_json(tmp_path / 'marks.json', {'BTCUSDT': '100'})
    assert main(['run', '--config', cfg, '--marks', marks, '--status-every', '0'], environ={},
                out=io.StringIO()) == EXIT_USAGE
    out = io.StringIO()
    assert main(['run', '--config', cfg, '--marks', marks, '--step-ms', '1'], environ={}, out=out) == EXIT_REFUSED
    assert lines(out)[-1]['code'] == 'step_ms_invalid'


# ------------------------------------------------------------------------------------------------ marks
@pytest.mark.parametrize('value', [{}, {'BTCUSDT': 100}, {'BTCUSDT': '-1'}, {'BTCUSDT': '0'}, {'BTCUSDT': 'NaN'},
                                   {'BTCUSDT': 'Infinity'}, {'btc usdt': '1'}, ['BTCUSDT'], 'x'])
def test_bad_marks_are_refused(tmp_path, value):
    path = write_json(tmp_path / 'marks.json', value)
    with pytest.raises(PaperRunError) as ex:
        FileMarks(path)
    assert ex.value.code in ('marks_invalid', 'marks_empty')


def test_marks_are_reread_when_the_file_changes(tmp_path):
    path = write_json(tmp_path / 'marks.json', {'BTCUSDT': '100'})
    marks = FileMarks(path)
    write_json(tmp_path / 'marks.json', {'BTCUSDT': '94.5', 'ETHUSDT': '3000'})
    marks.refresh()
    assert marks('BTCUSDT') == D('94.5') and marks.symbols == ('BTCUSDT', 'ETHUSDT')
    with pytest.raises(ValueError):
        marks('SOLUSDT')


def test_missing_marks_file_is_refused(tmp_path):
    with pytest.raises(PaperRunError) as ex:
        FileMarks(str(tmp_path / 'nope.json'))
    assert ex.value.code == 'marks_unreadable'


# ------------------------------------------------------------------------------------------------ paper book
def _busy_venue():
    price = {'v': D('100')}
    venue = PaperVenue(clock=lambda: 2_000_000_000_000, marks=lambda s: price['v'])
    ref = P.OrderRef(symbol='BTCUSDT', client_id='zbn1e-1', route='classic')
    venue.submit_market(P.MarketOrder(ref=ref, position_side='LONG', qty=D('3'), reduce=False))
    venue.submit_market(P.MarketOrder(ref=P.OrderRef(symbol='BTCUSDT', client_id='zbn1c-9', route='classic'),
                                      position_side='LONG', qty=D('9'), reduce=True))          # rejected
    stop = P.OrderRef(symbol='BTCUSDT', client_id='zbn1p-1', route='algo')
    venue.submit_stop(P.StopOrder(ref=stop, position_side='LONG', qty=D('1'), stop_price=D('95')))
    stop2 = P.OrderRef(symbol='BTCUSDT', client_id='zbn1p-2', route='algo')
    venue.submit_stop(P.StopOrder(ref=stop2, position_side='LONG', qty=D('2'), stop_price=D('90')))
    price['v'] = D('94')
    venue.tick()                                                    # first stop fills, second keeps resting
    return venue, ref, stop, stop2


def test_paper_book_round_trips(tmp_path):
    venue, ref, stop, stop2 = _busy_venue()
    raw = json.loads(json.dumps(venue.export_state()))
    again = PaperVenue(clock=lambda: 2_000_000_000_001, marks=lambda s: D('94'))
    again.load_state(raw)
    assert again.export_state() == venue.export_state()
    assert again.positions().value == venue.positions().value
    assert again.open_orders().value == venue.open_orders().value
    fired = again.query(stop)
    assert (fired.kind, fired.status, fired.detail) == (P.OutcomeKind.KNOWN, 'FINISHED', 'algo_triggered')
    assert again.query(ref).executed_qty == D('3')
    eid = fired.exchange_order_id                                   # the child market order
    assert eid == venue.query(stop).exchange_order_id
    (fill,) = again.fills('BTCUSDT', eid).value
    assert (fill.qty, fill.price) == (D('1'), D('94'))              # filled at the mark, not the stop price
    assert again.fills('BTCUSDT', eid).value == venue.fills('BTCUSDT', eid).value
    # ids keep counting after a restart: no reused exchange order or trade id
    out = again.submit_market(P.MarketOrder(ref=P.OrderRef(symbol='BTCUSDT', client_id='zbn1e-2', route='classic'),
                                            position_side='LONG', qty=D('1'), reduce=False))
    assert int(out.exchange_order_id) > max(int(s['eid']) for s in raw['stops'])
    # a client id used before the restart is still a duplicate
    dup = again.submit_market(P.MarketOrder(ref=ref, position_side='LONG', qty=D('1'), reduce=False))
    assert dup.error_code == -4116


def test_paper_book_refuses_a_bad_or_second_load():
    venue, *_ = _busy_venue()
    raw = venue.export_state()
    with pytest.raises(ValueError, match='paper_state_not_empty'):
        venue.load_state(raw)
    for broken in ({**raw, 'schema': 'x'}, {**raw, 'positions': [['BTCUSDT', 'LONG', 3]]},
                   {**raw, 'positions': [['BTCUSDT', 'BOTH', '3']]}, {**raw, 'positions': [['BTCUSDT', 'LONG', '-1']]},
                   {**raw, 'next_order': '5'}):
        with pytest.raises(Exception):
            PaperVenue(clock=lambda: 1, marks=lambda s: D('1')).load_state(broken)


def test_run_refuses_a_corrupt_book(tmp_path):
    cfg = config_file(tmp_path)
    marks = write_json(tmp_path / 'marks.json', {'BTCUSDT': '100'})
    main(['init-paper', '--config', cfg], environ={}, out=io.StringIO())
    (tmp_path / 'a1' / BOOK_FILE).write_text('{not json', encoding='utf-8')
    out = io.StringIO()
    assert main(['run', '--config', cfg, '--marks', marks, '--max-steps', '1'], environ={}, out=out,
                run_hooks=FakeTime().hooks()) == EXIT_REFUSED
    assert lines(out) == [{'status': 'refused', 'code': 'paper_book_invalid'}]


# ------------------------------------------------------------------------------------------------ entries hook
def test_run_with_an_entries_factory_opens_a_protected_paper_position(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_rt02_journaling import Keyed                         # one keyed EntryCandidate, then nothing
    from newcore.runtime.config import config_from_mapping
    from newcore.runtime.paper_run import init_paper_account, run_paper

    cfg = config_file(tmp_path, entries_enabled=True)
    with open(cfg, encoding='utf-8') as handle:
        config = config_from_mapping(json.load(handle))
    init_paper_account(config.accounts[0], wall_ms=2_000_000_000_000 - 60_000, event_n=7)
    marks = write_json(tmp_path / 'marks.json', {'BTCUSDT': '100'})
    made = []

    def factory(spec, *, wall_clock):
        made.append(spec.account_id)
        return Keyed()

    rows = []
    final = run_paper(config, marks_path=marks, emit=rows.append, max_steps=480, status_every=50,   # cycle: 60 s
                      entries_factory=factory, **FakeTime().hooks())
    assert made == [acct(1)] and final['entries_enabled'] is True
    (account,) = final['accounts']
    assert account['holds'] == [] and account['plan'] == 'protected_confirmed', final
    with open(tmp_path / 'a1' / BOOK_FILE, encoding='utf-8') as handle:
        book = json.load(handle)
    (position,) = [p for p in book['positions'] if p[2] != '0']
    (stop,) = [s for s in book['stops'] if s['status'] == 'NEW']
    assert position[1] == 'LONG' and stop['qty'] == position[2]


def test_a_non_callable_entries_factory_is_refused(tmp_path):
    from newcore.runtime.config import config_from_mapping
    from newcore.runtime.paper_run import run_paper
    cfg = config_file(tmp_path, entries_enabled=True)
    with open(cfg, encoding='utf-8') as handle:
        config = config_from_mapping(json.load(handle))
    (tmp_path / 'a1').mkdir()
    marks = write_json(tmp_path / 'marks.json', {'BTCUSDT': '100'})
    with pytest.raises(PaperRunError, match='entries_factory_invalid'):
        run_paper(config, marks_path=marks, emit=lambda line: None, max_steps=1, entries_factory='x')


def test_a_factory_that_takes_journal_reads_the_live_journal_read_only(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_rt02_journaling import Keyed
    from newcore.domain import BindingChanged
    from newcore.runtime.config import config_from_mapping
    from newcore.runtime.paper_run import JournalView, init_paper_account, run_paper

    cfg = config_file(tmp_path, entries_enabled=True)
    with open(cfg, encoding='utf-8') as handle:
        config = config_from_mapping(json.load(handle))
    init_paper_account(config.accounts[0], wall_ms=2_000_000_000_000 - 60_000, event_n=7)
    marks = write_json(tmp_path / 'marks.json', {'BTCUSDT': '100'})
    seen = []

    class Reading(Keyed):
        def __init__(self, journal):
            super().__init__()
            self.journal = journal

        def candidates(self, account_id, now_mono):
            view = self.journal()
            seen.append((type(view), view.account_id, view.last_sequence(),
                         any(isinstance(e, BindingChanged) for e in view.read()), len(view.state().intents)))
            return super().candidates(account_id, now_mono)

    def factory(spec, *, wall_clock, journal):
        assert journal() is None                                   # not booted yet
        return Reading(journal)

    final = run_paper(config, marks_path=marks, emit=lambda line: None, max_steps=960, entries_factory=factory,
                      **FakeTime().hooks())
    assert final['accounts'][0]['plan'] == 'protected_confirmed'
    assert seen and seen[0][:2] == (JournalView, acct(1)) and seen[0][3] is True
    assert len(seen) >= 2 and seen[-1][2] > seen[0][2]             # the view follows the live journal
    view = seen[0][0]
    assert not hasattr(view, 'append') and not hasattr(view, 'close')
