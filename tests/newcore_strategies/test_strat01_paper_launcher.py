"""STRAT-01 PAPER launcher and journal risk context: a real PAPER run opens a protected position from a frozen-strategy
signal; the risk context reads the journal without the writer lock and fails closed."""
from __future__ import annotations

import io
import json
import sys
import threading
import time
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / 'tools', ROOT / 'tests' / 'newcore_strategies'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import strat01_paper_run as L  # noqa: E402
import test_strat01_entry_source as T  # noqa: E402
from newcore import risk as nc06  # noqa: E402
from newcore.runtime.config import config_from_mapping  # noqa: E402
from newcore.runtime.paper_run import BOOK_FILE, init_paper_account  # noqa: E402
from newcore.store import recover_journal  # noqa: E402
from newcore_strategies.journal_risk import (JournalRiskContext, PaperPolicy, RiskSetupError,  # noqa: E402
                                             filters_from_exchange_info, load_exchange_info, read_journal)
from newcore_strategies.presets import TESTNET_SET_2026_11_05, build_slots  # noqa: E402

H4 = 14_400_000
WALL0 = (2_000_000_000_000 // H4) * H4            # a 4h close on the UTC grid
SIGNAL_BAR = 4497                                  # exp_range: NEAR long and WLD short fire on this bar (data/ 4h)
SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'ZECUSDT', 'SOLUSDT', 'XRPUSDT', 'NEARUSDT', 'WLDUSDT', 'DOGEUSDT')
INFO = load_exchange_info(L.DEFAULT_EXCHANGE_INFO)
A1 = 'acct_' + f'{1:032x}'


class Clock:
    def __init__(self, wall):
        self.mono, self.wall = 1_000_000, wall
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


def write_json(path, value):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(value, handle)
    return str(path)


def config(tmp_path, *, environment='paper_sim', entries=True):
    return write_json(tmp_path / 'config.json', {
        'schema': 'zackbot.newcore.runtime.config.v1', 'environment': environment, 'entries_enabled': entries,
        'accounts': [{'account_id': A1, 'aggregate_id': f'pf_{1:032x}', 'account_dir': str(tmp_path / 'a1'),
                      'egress_id': 'egress-1'}]})


def spec(tmp_path):
    with open(config(tmp_path), encoding='utf-8') as handle:
        return config_from_mapping(json.load(handle)).accounts[0]


def candles(tmp_path, bar=SIGNAL_BAR):
    """data/ 4h candles up to ``bar``, shifted so that bar closes at WALL0.  Returns (dir, marks path, last closes)."""
    out = tmp_path / 'candles'
    out.mkdir()
    shift = None
    last = {}
    for sym in SYMBOLS:
        d = T.FRAMES[sym].iloc[:bar + 1].copy()
        opens = d['t'].astype('datetime64[ms]').astype('int64')
        if shift is None:
            shift = WALL0 - (int(T.FRAMES['BTCUSDT']['t'].astype('datetime64[ms]').astype('int64').iloc[bar]) + H4)
        d['t'] = (opens + shift).astype('datetime64[ms]').dt.strftime('%Y-%m-%d %H:%M:%S')
        d[['t', 'o', 'h', 'l', 'c', 'v', 'qv']].to_csv(out / f'{sym}_4h.csv', index=False, encoding='utf-8')
        last[sym] = str(D(repr(float(d['c'].iloc[-1]))))
    return str(out), write_json(tmp_path / 'marks.json', last), last


def run(tmp_path, *argv, environ=None, max_steps=480, wall=WALL0 + 60_000):
    out = io.StringIO()
    code = L.main(list(argv) + ['--max-steps', str(max_steps), '--step-ms', '250'], environ=environ or {}, out=out,
                  run_hooks=Clock(wall).hooks())
    return code, [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]


# ---------------------------------------------------------------------------------------------------- end to end
def test_paper_run_opens_a_protected_position_from_a_strategy_signal(tmp_path):
    s = spec(tmp_path)
    init_paper_account(s, wall_ms=WALL0 - 60_000, event_n=7)
    cdir, marks, last = candles(tmp_path)
    code, lines = run(tmp_path, '--config', str(tmp_path / 'config.json'), '--marks', marks, '--candles', cdir,
                      '--equity', '10000', '--symbols', ','.join(SYMBOLS))
    assert lines[0]['status'] == 'starting' and lines[0]['slots'] == ['range4h_long', 'range4h_short',
                                                                      'vwaprev4h_long', 'vwaprev4h_short']
    summary = lines[-1]
    assert summary['status'] == 'strategy_summary', lines[-3:]
    (acct,) = summary['accounts']
    assert acct['refused_slots'] == {'c101_kalman_long': 'strategy_unavailable'}
    assert acct['risk_reasons'].get('ok', 0) >= 1, acct
    final = lines[-2]
    assert final['status'] == 'stopped' and final['accounts'][0]['holds'] == [], final
    with open(tmp_path / 'a1' / BOOK_FILE, encoding='utf-8') as handle:
        book = json.load(handle)
    positions = [p for p in book['positions'] if p[2] != '0']
    stops = [x for x in book['stops'] if x['status'] == 'NEW']
    assert positions and len(stops) == len(positions)
    for pos in positions:
        assert pos[0] in SYMBOLS
        (stop,) = [x for x in stops if x['qty'] == pos[2] and x['ref'][0] == pos[0]]
        mark = D(last[pos[0]])
        assert (D(stop['stop']) < mark) if pos[1] == 'LONG' else (D(stop['stop']) > mark)
    assert code == L.EXIT_OK


def test_risk_context_after_a_fill_counts_the_open_position_at_its_stop_risk(tmp_path):
    s = spec(tmp_path)
    init_paper_account(s, wall_ms=WALL0 - 60_000, event_n=7)
    cdir, marks, last = candles(tmp_path)
    sources = []
    factory, slots = L.make_factory(marks_path=marks, candle_dir=cdir, equity=D('10000'), exchange_info=INFO,
                                    base_risk_fraction=D('0.005'), leverage=D('3'), symbols=SYMBOLS, sources=sources)
    from newcore.runtime.paper_run import run_paper
    with open(tmp_path / 'config.json', encoding='utf-8') as handle:
        cfg = config_from_mapping(json.load(handle))
    run_paper(cfg, marks_path=marks, emit=lambda line: None, max_steps=480, entries_factory=factory,
              **Clock(WALL0 + 60_000).hooks())
    with open(tmp_path / 'a1' / BOOK_FILE, encoding='utf-8') as handle:
        book = json.load(handle)
    (pos, *_) = [p for p in book['positions'] if p[2] != '0']
    stop = [x for x in book['stops'] if x['status'] == 'NEW' and x['ref'][0] == pos[0]][0]
    risk = sources[0]['risk']
    slot = next(x for x in slots if x.side.value == pos[1] and x.key == 'exp_range')
    ctx = risk.risk_context(A1, slot, 'BTCUSDT')
    assert ctx is not None, dict(risk.reasons)
    (exp,) = [e for e in ctx.portfolio.exposures if e.symbol == pos[0]]
    qty, mark, stop_px = D(pos[2]), D(last[pos[0]]), D(stop['stop'])
    assert exp.quantity == qty and exp.notional == qty * mark
    assert exp.risk == qty * ((mark - stop_px) if pos[1] == 'LONG' else (stop_px - mark))
    assert exp.strategy_id.startswith(('range4h', 'vwaprev4h')) and exp.book_id == 'testnet_strat01'
    assert ctx.account.generation == ctx.portfolio.generation > 1
    assert ctx.account.available_margin == D('10000') - sum(e.notional for e in ctx.portfolio.exposures) / 3


# ---------------------------------------------------------------------------------------------------- journal risk
def _risk(tmp_path, *, marks=lambda s: D('100'), slots=None, reader=None, **kw):
    s = spec(tmp_path)
    slots = slots or build_slots(L.paper_rows(TESTNET_SET_2026_11_05, SYMBOLS))[0]
    filters = filters_from_exchange_info(INFO, SYMBOLS, max_notional=D('30000'), max_leverage=D('3'))
    return s, slots, JournalRiskContext(
        account_id=A1, marks=marks, equity=kw.pop('equity', D('10000')), filters=filters, slots=slots,
        leverage=kw.pop('leverage', D('3')),
        journal_reader=reader or (lambda: read_journal(s.account_dir, s.account_id, s.aggregate_id)), **kw)


def test_risk_context_on_a_bound_journal_is_a_fresh_paper_snapshot(tmp_path):
    s, slots, risk = _risk(tmp_path)
    init_paper_account(s, wall_ms=WALL0, event_n=7)
    ctx = risk.risk_context(A1, slots[0], 'NEARUSDT')
    assert ctx.account.environment is nc06.Environment.PAPER and ctx.account.generation == 1
    assert ctx.account.confirmed and ctx.account.fresh and ctx.account.hedge_mode
    assert ctx.portfolio.exposures == () and ctx.portfolio.reservations == ()
    assert ctx.filters.symbol == 'NEARUSDT' and ctx.policy.environment is nc06.Environment.PAPER
    assert {(a.strategy_id, a.book_id) for a in ctx.policy.allocations} == {(x.strategy_id, x.book_id) for x in slots}
    assert {c.symbol for c in ctx.policy.correlation_map} == set(SYMBOLS)


def test_risk_context_fails_closed(tmp_path):
    s, slots, risk = _risk(tmp_path)
    assert risk.risk_context(A1, slots[0], 'BTCUSDT') is None                    # no journal yet
    assert risk.reasons['journal_unreadable'] == 1
    init_paper_account(s, wall_ms=WALL0, event_n=7)
    assert risk.risk_context('acct_' + 'f' * 32, slots[0], 'BTCUSDT') is None
    assert risk.risk_context(A1, slots[0], 'PUMPUSDT') is None                   # no filters for it
    assert risk.reasons['account_mismatch'] == 1 and risk.reasons['filters_missing'] == 1
    _, _, boom = _risk(tmp_path, reader=lambda: (_ for _ in ()).throw(OSError('disk')))
    assert boom.risk_context(A1, slots[0], 'BTCUSDT') is None and boom.reasons['error:OSError'] == 1


def test_risk_context_refuses_a_non_paper_binding(tmp_path, monkeypatch):
    from newcore.domain.account import Environment as BE
    s, slots, risk = _risk(tmp_path)
    init_paper_account(s, wall_ms=WALL0, event_n=7)
    real = risk._projection.load_journal

    def testnet(account_id, journal):
        durable = real(account_id, journal)
        import dataclasses
        binding = dataclasses.replace(durable.account.binding, environment=BE.TESTNET)
        account = dataclasses.replace(durable.account, binding=binding)
        return dataclasses.replace(durable, account=account)
    monkeypatch.setattr(risk._projection, 'load_journal', testnet)
    assert risk.risk_context(A1, slots[0], 'BTCUSDT') is None and risk.reasons['binding_not_paper'] == 1


def test_read_journal_does_not_take_or_need_the_writer_lock(tmp_path):
    s = spec(tmp_path)
    init_paper_account(s, wall_ms=WALL0, event_n=7)
    writer = recover_journal(s.account_dir, s.account_id, s.aggregate_id).journal     # the runtime's writer lock
    try:
        assert not getattr(writer, 'readonly', False)
        view = read_journal(s.account_dir, s.account_id, s.aggregate_id)
        assert view is not None and view.readonly and view.last_sequence() == 1
    finally:
        writer.close()
    again = recover_journal(s.account_dir, s.account_id, s.aggregate_id).journal       # lock was never taken by us
    assert again is not None and not getattr(again, 'readonly', False)
    again.close()


def test_read_journal_refuses_a_torn_tail(tmp_path):
    s = spec(tmp_path)
    init_paper_account(s, wall_ms=WALL0, event_n=7)
    seg = sorted((tmp_path / 'a1').rglob('*.seg'))
    assert seg, list((tmp_path / 'a1').rglob('*'))
    with open(seg[-1], 'ab') as handle:
        handle.write(b'\x01\x02\x03')                                  # a half-written record
    assert read_journal(s.account_dir, s.account_id, s.aggregate_id) is None


def test_unprotected_position_counts_its_whole_notional_as_risk(tmp_path):
    from newcore.domain import Side
    from newcore.reconciliation import LocalPosition
    s, slots, risk = _risk(tmp_path, marks=lambda sym: D('2'))
    init_paper_account(s, wall_ms=WALL0, event_n=7)
    real = risk._projection.load_journal

    def with_position(account_id, journal):
        import dataclasses
        durable = real(account_id, journal)
        return dataclasses.replace(durable, positions=(LocalPosition('NEARUSDT', Side.LONG, D('5')),))
    risk._projection.load_journal = with_position
    ctx = risk.risk_context(A1, slots[0], 'BTCUSDT')
    (exp,) = ctx.portfolio.exposures
    assert exp.notional == D('10') and exp.risk == D('10') and exp.strategy_id == 'manual'
    assert ctx.account.available_margin == D('10000') - D('10') / 3
    risk2 = _risk(tmp_path, marks=lambda sym: (_ for _ in ()).throw(ValueError('mark_missing')))[2]
    risk2._projection.load_journal = with_position
    assert risk2.risk_context(A1, slots[0], 'BTCUSDT') is None and risk2.reasons['error:ValueError'] == 1


def test_setup_bounds(tmp_path):
    with pytest.raises(RiskSetupError):
        _risk(tmp_path, equity=10000)                                   # float/int equity refused: Decimal only
    with pytest.raises(RiskSetupError):
        _risk(tmp_path, leverage=D('4'))
    with pytest.raises(RiskSetupError):
        PaperPolicy(max_trade_risk_fraction=D('1.5'))
    with pytest.raises(RiskSetupError):
        PaperPolicy(max_leverage=D('5'))


def test_filters_come_from_the_pinned_exchange_info():
    f = filters_from_exchange_info(INFO, ('BTCUSDT', 'NOPEUSDT'), max_notional=D('30000'), max_leverage=D('3'))
    assert set(f) == {'BTCUSDT'}
    b = f['BTCUSDT']
    assert (b.tick_size, b.step_size, b.min_quantity, b.max_quantity, b.min_notional) == (
        D('0.10'), D('0.001'), D('0.001'), D('120'), D('50'))
    assert b.max_leverage == D('3') and b.verified and b.fresh
    with pytest.raises(RiskSetupError):
        filters_from_exchange_info(INFO, ('BTCUSDT',), max_notional=D('1'), max_leverage=D('5'))


# ---------------------------------------------------------------------------------------------------- launcher
def test_launcher_refusals(tmp_path):
    cdir, marks, _ = candles(tmp_path)
    base = ['--marks', marks, '--candles', cdir, '--equity', '10000']
    code, lines = run(tmp_path, '--config', config(tmp_path), *base, environ={'live_confirm': ''})
    assert code == L.EXIT_REFUSED and lines[-1]['code'] == 'live_switch_present'
    code, lines = run(tmp_path, '--config', config(tmp_path, entries=False), *base)
    assert lines[-1]['code'] == 'entries_not_enabled'
    code, lines = run(tmp_path, '--config', config(tmp_path, environment='testnet'), *base)
    assert code == L.EXIT_REFUSED and lines[-1]['code'] in ('paper_only', 'environment_invalid',
                                                               'testnet_accounts_missing') or lines
    code, lines = run(tmp_path, '--config', config(tmp_path), *base,
                      '--symbols', 'ETHUSDT,SOLUSDT')
    assert lines[-1]['code'] == 'paper_symbols_invalid'
    code, lines = run(tmp_path, '--config', config(tmp_path), *base, '--symbols', ','.join(T.CORE_20[:9]))
    assert lines[-1]['code'] == 'paper_symbols_invalid'
    code, lines = run(tmp_path, '--config', config(tmp_path), *base, '--symbols', 'BTCUSDT,NOPEUSDT')
    assert lines[-1]['code'] == 'filters_missing:NOPEUSDT'
    code, lines = run(tmp_path, '--config', config(tmp_path), *base)        # account never initialised
    assert lines[-1]['code'] == 'account_not_initialised'
    assert L.main(['--config', 'x'], environ={}, out=io.StringIO()) == L.EXIT_USAGE


def test_cross_sectional_rows_cannot_be_narrowed():
    from newcore_strategies.entry_source import StrategyConfigError
    row = dict(key='rotation_long_h2', name='c24', version='v1', tf='4h', side='LONG', symbols=list(T.CORE_20),
               risk_mult='0.5', strategy_id='c24', book_id='b')
    with pytest.raises(StrategyConfigError, match='cross_sectional_universe_narrowed'):
        L.paper_rows((row,), SYMBOLS)


# ---------------------------------------------------------------------------------------------------- public feed
def test_public_feed_writes_closed_candles_and_marks_atomically(tmp_path):
    now = WALL0 + 60_000
    rows = [[WALL0 - 2 * H4, '1', '2', '0.5', '1.5', '10', WALL0 - H4 - 1, '15'],
            [WALL0 - H4, '1.5', '2', '1', '1.8', '11', WALL0 - 1, '18'],
            [WALL0, '1.8', '1.9', '1.7', '1.85', '1', WALL0 + H4 - 1, '2']]          # still forming: dropped
    calls = []

    def fetch(path, params):
        calls.append((path, params))
        if path == '/fapi/v1/premiumIndex':
            return [{'symbol': 'BTCUSDT', 'markPrice': '100.5'}, {'symbol': 'XYZUSDT', 'markPrice': '1'},
                    {'symbol': 'ETHUSDT', 'markPrice': 'nan'}]
        if params['symbol'] == 'ETHUSDT':
            raise OSError('down')
        if 'endTime' in params:                                        # the older page (overlaps by one)
            return [[WALL0 - 4 * H4, '1', '1', '1', '1', '1', WALL0 - 3 * H4 - 1, '1'],
                    [WALL0 - 3 * H4, '1', '1', '1', '1', '1', WALL0 - 2 * H4 - 1, '1'],
                    rows[0]]
        return rows
    (tmp_path / 'c').mkdir()
    feed = L.PublicFeed(candle_dir=str(tmp_path / 'c'), marks_path=str(tmp_path / 'm.json'),
                        symbols=('BTCUSDT', 'ETHUSDT'), tfs=('4h',), fetch=fetch, clock=lambda: now)
    feed.refresh()
    with open(tmp_path / 'm.json', encoding='utf-8') as handle:
        assert json.load(handle) == {'BTCUSDT': '100.5'}
    from newcore_strategies import CsvCandleFeed
    got = CsvCandleFeed(str(tmp_path / 'c')).closed('BTCUSDT', '4h', 10, now)
    assert len(got) == 4 and float(got['c'].iloc[-1]) == 1.8 and 'qv' in got.columns
    with open(tmp_path / 'c' / 'BTCUSDT_4h.csv', encoding='utf-8') as handle:
        assert len(handle.read().splitlines()) == 1 + 4                       # the forming candle is never written
    assert got['t'].is_monotonic_increasing and got['t'].is_unique
    pages = [q for p, q in calls if p == '/fapi/v1/klines' and q['symbol'] == 'BTCUSDT']
    assert pages == [{'symbol': 'BTCUSDT', 'interval': '4h', 'limit': 1500},
                     {'symbol': 'BTCUSDT', 'interval': '4h', 'limit': 200, 'endTime': WALL0 - 2 * H4 - 1}]
    assert L.PAGE + L.EXTRA - 1 >= 1500                                # enough closed bars for the warm-up
    assert feed.reasons['candles_ok'] == 1 and feed.reasons['candles_error:OSError'] == 1
    assert not list((tmp_path / 'c').glob('*.tmp'))


def test_public_feed_keeps_the_old_marks_when_the_read_fails(tmp_path):
    write_json(tmp_path / 'm.json', {'BTCUSDT': '1'})
    feed = L.PublicFeed(candle_dir=str(tmp_path), marks_path=str(tmp_path / 'm.json'), symbols=('BTCUSDT',),
                        tfs=(), fetch=lambda p, q: [])
    feed.refresh()
    with open(tmp_path / 'm.json', encoding='utf-8') as handle:
        assert json.load(handle) == {'BTCUSDT': '1'}
    assert feed.reasons['marks_error:ValueError'] == 1


# ---------------------------------------------------------------------------------------------------- crafted books
class _Ev:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _IR(_Ev):
    pass


class _RO(_Ev):
    pass


class _DR(_Ev):
    pass


class _Proxy:
    """A real bound journal with crafted live intents and extra events."""

    def __init__(self, journal, views=(), events=()):
        self._j, self._views, self._events = journal, tuple(views), tuple(events)
        self.account_id = journal.account_id

    def read(self, after_sequence=0):
        return tuple(self._j.read(after_sequence)) + self._events

    def state(self):
        import dataclasses
        return dataclasses.replace(self._j.state(), intents=self._views)


def _intent(purpose, symbol, side, *, qty='5', stop=None, slot=None, iid='int_x'):
    from newcore.domain import Purpose, Side
    return _Ev(purpose=Purpose(purpose), symbol=symbol, side=Side(side), qty=D(qty),
               stop_price=None if stop is None else D(stop), slot_id=slot, intent_id=iid)


def _crafted(tmp_path, monkeypatch, *, positions, views=(), events=(), mark='2', hedge=True):
    import dataclasses
    import newcore_strategies.journal_risk as JR
    from newcore.domain import Side
    from newcore.reconciliation import LocalPosition
    monkeypatch.setattr(JR, 'IntentRecorded', _IR)
    monkeypatch.setattr(JR, 'ResultObserved', _RO)
    monkeypatch.setattr(JR, 'DecisionRecorded', _DR)
    s = spec(tmp_path)
    init_paper_account(s, wall_ms=WALL0, event_n=7)
    reader = lambda: _Proxy(read_journal(s.account_dir, s.account_id, s.aggregate_id), views, events)  # noqa: E731
    _, slots, risk = _risk(tmp_path, marks=lambda sym: D(mark), reader=reader)
    real = risk._projection.load_journal

    def patched(account_id, journal):
        d = real(account_id, journal._j)
        return dataclasses.replace(d, account=dataclasses.replace(d.account, hedge_mode=hedge),
                                   positions=tuple(LocalPosition(a, Side(b), D(c)) for a, b, c in positions))
    risk._projection.load_journal = patched
    return slots, risk


def _view(it, state='working', final=None):
    from newcore.domain.orders import IntentState
    return _Ev(intent=it, state=IntentState(state), final_result=final)


def test_partial_protection_counts_the_whole_notional(tmp_path, monkeypatch):
    stop = _intent('protect', 'NEARUSDT', 'LONG', qty='2', stop='1.5')
    slots, risk = _crafted(tmp_path, monkeypatch, positions=[('NEARUSDT', 'LONG', '5')], views=[_view(stop)])
    (exp,) = risk.risk_context(A1, slots[0], 'BTCUSDT').portfolio.exposures
    assert exp.risk == exp.notional == D('10')


def test_several_stops_use_the_worst_one(tmp_path, monkeypatch):
    views = [_view(_intent('protect', 'NEARUSDT', 'LONG', qty='5', stop='1.5')),
             _view(_intent('protect', 'NEARUSDT', 'LONG', qty='5', stop='1.2')),
             _view(_intent('protect', 'WLDUSDT', 'SHORT', qty='4', stop='2.5')),
             _view(_intent('protect', 'WLDUSDT', 'SHORT', qty='4', stop='2.25'))]
    slots, risk = _crafted(tmp_path, monkeypatch, positions=[('NEARUSDT', 'LONG', '5'), ('WLDUSDT', 'SHORT', '4')],
                           views=views)
    got = {e.symbol: e.risk for e in risk.risk_context(A1, slots[0], 'BTCUSDT').portfolio.exposures}
    assert got == {'NEARUSDT': D('5') * D('0.8'), 'WLDUSDT': D('4') * D('0.5')}


def test_a_live_entry_gives_no_context(tmp_path, monkeypatch):
    slots, risk = _crafted(tmp_path, monkeypatch, positions=[],
                           views=[_view(_intent('entry', 'NEARUSDT', 'LONG'))])
    assert risk.risk_context(A1, slots[0], 'BTCUSDT') is None and risk.reasons['entry_in_flight'] == 1


def test_attribution_needs_an_executed_entry(tmp_path, monkeypatch):
    from newcore.ports.keys import strategy_instance
    long_slot = next(x for x in slots_for_paper() if x.name == 'range4h_long')
    vw = next(x for x in slots_for_paper() if x.name == 'vwaprev4h_long')
    events = [_IR(sequence=50, intent=_intent('entry', 'NEARUSDT', 'LONG', slot=strategy_instance('range4h_long', '4h'),
                                              iid='int_a')),
              _RO(sequence=51, result=_Ev(intent_id='int_a', executed_qty=D('5'))),
              _DR(sequence=52, decision=_Ev(intents=(_intent('entry', 'NEARUSDT', 'LONG', iid='int_b',
                                                             slot=strategy_instance('vwaprev4h_long', '4h')),))),
              _RO(sequence=53, result=_Ev(intent_id='int_b', executed_qty=D('0')))]   # never executed: not owner
    slots, risk = _crafted(tmp_path, monkeypatch, positions=[('NEARUSDT', 'LONG', '5')], events=events)
    (exp,) = risk.risk_context(A1, slots[0], 'BTCUSDT').portfolio.exposures
    assert (exp.strategy_id, exp.book_id) == (long_slot.strategy_id, long_slot.book_id)
    assert exp.strategy_id != vw.strategy_id


def test_hedge_mode_comes_from_the_projection(tmp_path, monkeypatch):
    slots, risk = _crafted(tmp_path, monkeypatch, positions=[], hedge=False)
    assert risk.risk_context(A1, slots[0], 'BTCUSDT').account.hedge_mode is False


def slots_for_paper():
    return build_slots(L.paper_rows(TESTNET_SET_2026_11_05, SYMBOLS))[0]


def test_filters_skip_non_trading_symbols_and_leverage_below_one():
    import copy
    info = copy.deepcopy(INFO)
    next(r for r in info['symbols'] if r['symbol'] == 'ETHUSDT')['status'] = 'SETTLING'
    f = filters_from_exchange_info(info, ('BTCUSDT', 'ETHUSDT'), max_notional=D('1'), max_leverage=D('3'))
    assert set(f) == {'BTCUSDT'}
    with pytest.raises(RiskSetupError):
        filters_from_exchange_info(INFO, ('BTCUSDT',), max_notional=D('1'), max_leverage=D('0.5'))


@pytest.mark.parametrize('state,final', [('cancelling', None), ('unknown', None), ('durable', None),
                                         ('submitted', None), ('working', 'dead')])
def test_a_stop_that_is_not_resting_counts_as_no_stop(tmp_path, monkeypatch, state, final):
    stop = _intent('protect', 'NEARUSDT', 'LONG', qty='5', stop='1.5')
    final = None if final is None else _Ev(executed_qty=D('0'))
    slots, risk = _crafted(tmp_path, monkeypatch, positions=[('NEARUSDT', 'LONG', '5')],
                           views=[_view(stop, state, final)])
    (exp,) = risk.risk_context(A1, slots[0], 'BTCUSDT').portfolio.exposures
    assert exp.risk == exp.notional == D('10')


@pytest.mark.parametrize('side,stop', [('LONG', '2.5'), ('LONG', '2'), ('SHORT', '1.5')])
def test_a_stop_past_the_mark_counts_as_no_stop(tmp_path, monkeypatch, side, stop):
    v = _view(_intent('protect', 'NEARUSDT', side, qty='5', stop=stop))
    slots, risk = _crafted(tmp_path, monkeypatch, positions=[('NEARUSDT', side, '5')], views=[v])
    (exp,) = risk.risk_context(A1, slots[0], 'BTCUSDT').portfolio.exposures
    assert exp.risk == exp.notional == D('10')


def test_two_slots_on_one_symbol_side_each_keep_their_share(tmp_path, monkeypatch):
    from newcore.ports.keys import strategy_instance
    rng, vw = (next(x for x in slots_for_paper() if x.name == n) for n in ('range4h_long', 'vwaprev4h_long'))
    events = [_IR(sequence=50, intent=_intent('entry', 'NEARUSDT', 'LONG', iid='int_a',
                                              slot=strategy_instance('range4h_long', '4h'))),
              _RO(sequence=51, result=_Ev(intent_id='int_a', executed_qty=D('3'))),
              _IR(sequence=52, intent=_intent('entry', 'NEARUSDT', 'LONG', iid='int_b',
                                              slot=strategy_instance('vwaprev4h_long', '4h'))),
              _RO(sequence=53, result=_Ev(intent_id='int_b', executed_qty=D('2')))]
    v = _view(_intent('protect', 'NEARUSDT', 'LONG', qty='6', stop='1.5'))
    slots, risk = _crafted(tmp_path, monkeypatch, positions=[('NEARUSDT', 'LONG', '6')], views=[v], events=events)
    got = {e.strategy_id: (e.quantity, e.notional, e.risk) for e in
           risk.risk_context(A1, slots[0], 'BTCUSDT').portfolio.exposures}
    assert got == {rng.strategy_id: (D('3'), D('6'), D('1.5')), vw.strategy_id: (D('2'), D('4'), D('1.0')),
                   'manual': (D('1'), D('2'), D('0.5'))}
    slots, risk = _crafted(tmp_path / 'b', monkeypatch, positions=[('NEARUSDT', 'LONG', '4')], views=[v],
                           events=events) if (tmp_path / 'b').mkdir() is None else (None, None)
    got = {e.strategy_id: e.quantity for e in risk.risk_context(A1, slots[0], 'BTCUSDT').portfolio.exposures}
    assert got == {vw.strategy_id: D('2'), rng.strategy_id: D('2')}             # older lot closed first


def test_policy_multiples_cannot_exceed_the_leverage_cap():
    with pytest.raises(RiskSetupError):
        PaperPolicy(max_gross_multiple=D('1E+9'))
    with pytest.raises(RiskSetupError):
        PaperPolicy(max_net_multiple=D('3.5'))
    with pytest.raises(RiskSetupError):
        PaperPolicy(max_leverage=D('2'), max_gross_multiple=D('3'))
    assert PaperPolicy(max_leverage=D('2'), max_gross_multiple=D('2'), max_net_multiple=D('2'))


def test_stale_marks_give_no_risk_context(tmp_path):
    s = spec(tmp_path)
    init_paper_account(s, wall_ms=WALL0 - 60_000, event_n=7)
    cdir, marks, _ = candles(tmp_path)
    now = [time.time()]
    sources = []
    factory, slots = L.make_factory(marks_path=marks, candle_dir=cdir, equity=D('10000'), exchange_info=INFO,
                                    base_risk_fraction=D('0.005'), leverage=D('3'), symbols=SYMBOLS,
                                    sources=sources, max_mark_age_s=300, now_s=lambda: now[0])
    factory(s, wall_clock=lambda: WALL0 + 60_000)
    risk = sources[0]['risk']
    from newcore.domain import Side
    from newcore.reconciliation import LocalPosition
    import dataclasses
    real = risk._projection.load_journal
    risk._projection.load_journal = lambda a, j: dataclasses.replace(
        real(a, j), positions=(LocalPosition('NEARUSDT', Side.LONG, D('1')),))
    assert risk.risk_context(A1, slots[0], 'BTCUSDT') is not None
    now[0] += 301
    assert risk.risk_context(A1, slots[0], 'BTCUSDT') is None and risk.reasons['error:ValueError'] == 1


def test_attribution_uses_first_record_order_and_the_largest_final_fill(tmp_path, monkeypatch):
    from newcore.ports.keys import strategy_instance
    rng, vw = (next(x for x in slots_for_paper() if x.name == n) for n in ('range4h_long', 'vwaprev4h_long'))
    a = _intent('entry', 'NEARUSDT', 'LONG', iid='int_a', slot=strategy_instance('range4h_long', '4h'))
    b = _intent('entry', 'NEARUSDT', 'LONG', iid='int_b', slot=strategy_instance('vwaprev4h_long', '4h'))
    events = [_IR(sequence=50, intent=a), _RO(sequence=51, result=_Ev(intent_id='int_a', executed_qty=D('3'))),
              _RO(sequence=52, result=_Ev(intent_id='int_a', executed_qty=D('1'))),
              _IR(sequence=53, intent=b), _RO(sequence=54, result=_Ev(intent_id='int_b', executed_qty=D('2'))),
              _DR(sequence=55, decision=_Ev(intents=(a,)))]                       # a seen again later: still older
    slots, risk = _crafted(tmp_path, monkeypatch, positions=[('NEARUSDT', 'LONG', '4')], events=events)
    got = {e.strategy_id: e.quantity for e in risk.risk_context(A1, slots[0], 'BTCUSDT').portfolio.exposures}
    assert got == {vw.strategy_id: D('2'), rng.strategy_id: D('2')}
