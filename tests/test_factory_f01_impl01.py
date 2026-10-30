"""FACTORY-F01-IMPL-01 (Codex ruling 6513808658): the default-OFF, testnet-only C-F01 canary runtime.

Pins: the flag is OFF by default and an enabled C-F01 is then refused (review 6514109966 #2), other slots unchanged; ON is
refused outside PAPER / testnet or with a malformed config; each OQ1 bound (30 days,
8 closed trades, 4.0 R measured drawdown) and a pending-cost close stop NEW C-F01 entries while open lots stay protected and
managed (OQ8); the preflight is read-only and fails each C-check; extracts are immutable, sha256-addressed and carry no
ids; raw qty / stop are the exact captured pre-quantization values; scoring starts at the first engine-confirmed fully
closed bar after the start; C-F02 is never enabled; the pinned functions equal the contract plus pin_amendment.v1.json.
Synthetic data only (tests/test_safety.FakeX)."""
import copy
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_factory_cand01 import F01, F02, SG, cycle4, lots, rot, slot   # noqa: E402,F401  (fixtures + helpers)
import engine as E                                                     # noqa: E402
import f01_canary as FC                                                # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, 'contracts', 'factory_f01_impl_01', 'cohort_manifest.v1.json')
AMEND = os.path.join(ROOT, 'contracts', 'factory_f01_impl_01', 'pin_amendment.v1.json')
HEAD, TREE = 'a' * 40, 'b' * 40
CD = [os.path.join(tempfile.gettempdir(), 'zb_f01_unused')]          # per-test cohort dir (autouse fixture below)
OFF = 'F01 canary flag missing, malformed or OFF - C-F01 trades only inside the bounded canary (fail closed)'
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def iso(dt): return dt.isoformat(timespec='seconds')


def cfg(start=NOW - timedelta(days=1), auth='6599999999', cohort_dir=..., head=HEAD, tree=TREE):
    return dict(on=True, start_utc=iso(start) if isinstance(start, datetime) else start, authorization_comment_id=auth,
                cohort_dir=CD[0] if cohort_dir is ... else cohort_dir, impl_head=head, impl_tree=tree)


@pytest.fixture(autouse=True)
def _cohort_dir(tmp_path):
    CD[0] = str(tmp_path / 'cohort')
    yield


@pytest.fixture
def f01(rot):
    """C-F01 alone switched on (C-F02 stays OFF), PAPER on the testnet base URL."""
    e, tmp = rot
    slot(e, F01)['enabled'] = True
    e.trade.base = E.TESTNET
    return e, tmp


@pytest.fixture
def measured(monkeypatch):
    """row_view / measured read the synthetic rows' _st / _pnl (status, net)."""
    monkeypatch.setattr(FC.AC, 'row_view', lambda r: dict(status=r.get('_st', 'measured'), pnl=r.get('_pnl'), why=[]))
    monkeypatch.setattr(FC.AC, 'measured', lambda r: dict(gross=r.get('_pnl'), fees=0.1, funding=0.0, net=r.get('_pnl')))


def row(i, r, opened=NOW - timedelta(hours=20), st='measured'):
    return dict(id=f'{F01}|BTCUSDT|LONG|{i}', sleeve=F01, symbol='BTCUSDT', side='LONG', opened=iso(opened),
                closed=iso(opened + timedelta(hours=1 + i / 10)), risk_usd=10.0, _pnl=None if r is None else r * 10.0, _st=st)


def now_at(monkeypatch, dt):
    monkeypatch.setattr(E, 'now_utc', lambda: dt)


# ------------------------------------------------------------------ default OFF
@pytest.mark.parametrize('flag', [None, {}, {'on': False}, {'on': 'true'}, {'on': 1}, 'on'])
def test_flag_off_or_malformed_refuses_an_enabled_c_f01_only(f01, flag):
    e, _ = f01
    assert FC.CANARY_KEY not in E.GLOBAL_DEFAULTS and FC.CANARY_KEY not in e.S      # absent by default
    if flag is not None: e.S[FC.CANARY_KEY] = flag
    assert FC.config(e.S) is None and e.f01_canary_block(slot(e, F01)) == OFF
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    assert not lots(e, F01) and e.missed[-1]['reason'] == OFF and slot(e, F02)['enabled'] is False
    for sl in e.S['SLEEVES']:                                                      # every other slot: unchanged
        if sl['id'] != F01: assert e.f01_canary_block(sl) is None and e._rules_block(sl, 'BTCUSDT', 'LONG') is None


def test_the_gate_never_concerns_other_slots(f01):
    e, _ = f01
    e.S[FC.CANARY_KEY] = cfg(start=NOW + timedelta(days=9))                         # would refuse C-F01
    for sl in e.S['SLEEVES']:
        if sl['id'] != F01: assert e.f01_canary_block(sl) is None


# ------------------------------------------------------------------ testnet-only guard
def test_on_in_live_mode_is_refused(f01, monkeypatch):
    e, _ = f01
    now_at(monkeypatch, NOW)
    e.S[FC.CANARY_KEY] = cfg()
    assert e.f01_canary_block(slot(e, F01)) is None
    e.live = True
    assert e.f01_canary_block(slot(e, F01)) == 'F01 canary is PAPER/testnet only - refused in live mode'
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    assert not lots(e, F01)


@pytest.mark.parametrize('base', [E.MAINNET, None, 'https://example.invalid'])
def test_on_without_a_confirmed_testnet_is_refused(f01, monkeypatch, base):
    e, _ = f01
    now_at(monkeypatch, NOW)
    e.S[FC.CANARY_KEY] = cfg()
    e.trade.base = base
    want = 'F01 canary: environment not confirmed as Binance Futures testnet - refused'
    assert e.f01_canary_block(slot(e, F01)) == want
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    assert not lots(e, F01) and e.missed[-1]['reason'] == want


@pytest.mark.parametrize('kw,part', [
    (dict(start='2026-10-31T00:00:00'), 'no frozen tz-aware start'), (dict(start='garbage'), 'no frozen tz-aware start'),
    (dict(auth=None), 'positive numeric'), (dict(auth='abc'), 'positive numeric'), (dict(auth=0), 'positive numeric'),
    (dict(auth=-5), 'positive numeric'), (dict(auth=True), 'positive numeric'), (dict(auth='0123'), 'positive numeric'),
    (dict(auth='12 3'), 'positive numeric'), (dict(auth=6.5e9), 'positive numeric'),
    (dict(cohort_dir=None), 'cohort_dir'), (dict(cohort_dir='rel/dir'), 'cohort_dir'),
    (dict(cohort_dir=os.path.join(ROOT, 'evidence')), 'cohort_dir'),
    (dict(head='abc1234'), 'impl_head'), (dict(head=None), 'impl_head'), (dict(tree='B' * 40), 'impl_tree'),
    (dict(start=NOW + timedelta(hours=1)), 'before the frozen start')])
def test_on_with_a_malformed_or_future_config_fails_closed(f01, monkeypatch, kw, part):
    e, _ = f01
    now_at(monkeypatch, NOW)
    e.S[FC.CANARY_KEY] = cfg(**kw)
    assert part in e.f01_canary_block(slot(e, F01))


@pytest.mark.parametrize('auth', ['6514109966', 6514109966])
def test_a_well_formed_config_has_no_problems(auth):
    assert FC.cfg_problems(cfg(auth=auth)) == [] and FC.auth_id_ok(auth)


def test_a_bound_check_error_fails_closed(f01, monkeypatch):
    e, _ = f01
    e.S[FC.CANARY_KEY] = cfg()
    monkeypatch.setattr(FC, 'bound_status', lambda *a, **k: 1 / 0)
    assert 'bound check failed (ZeroDivisionError)' in e.f01_canary_block(slot(e, F01))


# ------------------------------------------------------------------ bounds (OQ1) + rollback semantics (OQ8)
def test_inside_the_bounds_c_f01_trades_normally(f01, monkeypatch, measured):
    e, _ = f01
    now_at(monkeypatch, datetime.now(timezone.utc))
    e.S[FC.CANARY_KEY] = cfg(start=datetime.now(timezone.utc) - timedelta(days=1))
    e.history += [row(i, 0.5) for i in range(7)]
    assert e.f01_canary_block(slot(e, F01)) is None
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    assert lots(e, F01, 'BTCUSDT')


def _open_then_bound(e, monkeypatch, apply_bound):
    """An open C-F01 lot, then a bound is reached: no new entry, the open lot keeps its stop and is still managed."""
    real_now = datetime.now(timezone.utc)
    now_at(monkeypatch, real_now)
    e.S[FC.CANARY_KEY] = cfg(start=real_now - timedelta(days=1))
    cycle4(e, {f'{F01}|BTCUSDT': SG})
    (l,) = lots(e, F01, 'BTCUSDT')
    apply_bound(e, real_now)
    why = e.f01_canary_block(slot(e, F01))
    assert why
    eth = dict(SG, close=50.0, atr=1.0)
    cycle4(e, {f'{F01}|BTCUSDT': dict(SG, le=False), f'{F01}|ETHUSDT': eth})       # hold BTC, new ETH signal
    assert not lots(e, F01, 'ETHUSDT') and e.missed[-1]['reason'] == why            # refused with the exact bound text
    assert lots(e, F01, 'BTCUSDT') == [l] and l['stop_id'] in e.trade.stops          # never force-closed, still protected
    cycle4(e, {f'{F01}|BTCUSDT': dict(SG, le=False, lx=True)})                       # normal signal management continues
    assert not lots(e, F01, 'BTCUSDT')
    assert slot(e, F01)['enabled'] is True and slot(e, F02)['enabled'] is False      # settings untouched (R1 is Integration's)
    return why


def test_30_calendar_days_stop_new_entries_only(f01, monkeypatch):
    e, _ = f01
    def bound(e, now): e.S[FC.CANARY_KEY]['start_utc'] = iso(now - timedelta(days=30))
    assert 'S6' not in _open_then_bound(e, monkeypatch, bound)
    assert '30 calendar days' in e.f01_canary_block(slot(e, F01))


def test_8_closed_trades_stop_new_entries_only(f01, monkeypatch, measured):
    e, _ = f01
    def bound(e, now): e.history += [row(i, 0.5, opened=now - timedelta(hours=20)) for i in range(8)]
    assert '8 closed C-F01 trades' in _open_then_bound(e, monkeypatch, bound)


def test_4r_measured_drawdown_stops_new_entries_only(f01, monkeypatch, measured):
    e, _ = f01
    def bound(e, now): e.history += [row(i, r, opened=now - timedelta(hours=20)) for i, r in enumerate((1.0, -2.0, -3.0))]
    assert 'measured net drawdown 5.00 R' in _open_then_bound(e, monkeypatch, bound)


@pytest.mark.parametrize('rs,stop', [((1.0, -2.0, -1.9), None), ((1.0, -2.0, -2.0), 'S5'), ((-4.0,), 'S5'),
                                     ((2.0, -1.0, 3.0, -3.9), None), ((2.0, -1.0, 3.0, -4.0), 'S5')])
def test_drawdown_is_from_the_canary_peak_including_the_start(measured, rs, stop):
    st = FC.bound_status(cfg(), [row(i, r) for i, r in enumerate(rs)], now=NOW)
    assert (st['stop'] or {}).get('id') == stop and (st['block'] is None) == (stop is None)


def test_trade_and_day_bounds_and_pre_canary_rows(measured):
    rows = [row(i, 0.1) for i in range(7)]
    assert FC.bound_status(cfg(), rows, now=NOW)['block'] is None
    pre = row(9, -9.0, opened=NOW - timedelta(days=3))                               # opened before the start: never counted
    st = FC.bound_status(cfg(), rows + [pre, dict(row(10, 0.1), sleeve=F02)], now=NOW)
    assert st['block'] is None and st['closed'] == 7 and st['drawdown_r'] == 0.0
    assert FC.bound_status(cfg(), rows + [row(8, 0.1)], now=NOW)['stop']['id'] == 'S6'
    st = FC.bound_status(cfg(start=NOW - timedelta(days=30)), [], now=NOW)
    assert st['stop']['id'] == 'S6' and '30 calendar days' in st['block']
    assert FC.bound_status(cfg(start=NOW - timedelta(days=30) + timedelta(seconds=1)), [], now=NOW)['block'] is None


def test_pending_costs_fail_closed_from_the_row_and_the_durable_index(measured):
    st = FC.bound_status(cfg(), [row(0, 1.0), row(1, None, st='pending')], now=NOW)
    assert st['stop'] is None and 'costs pending' in st['block'] and st['pending'] == ['BTCUSDT']
    booked = FC.bound_status(cfg(), [row(0, 1.0, st='booked')], now=NOW)         # not measured -> not counted as measured
    assert 'costs pending' in booked['block']
    idx = {'gone': dict(sleeve=F01, symbol='ETHUSDT', closed=iso(NOW - timedelta(hours=2)))}
    assert 'costs pending' in FC.bound_status(cfg(), [], idx, now=NOW)['block']     # evicted row: the index holds it
    idx = {'a': dict(sleeve=F02, symbol='ETHUSDT', closed=iso(NOW)),
           'b': dict(sleeve=F01, symbol='ETHUSDT', closed=iso(NOW - timedelta(days=5)))}   # other slot / pre-canary
    assert FC.bound_status(cfg(), [], idx, now=NOW)['block'] is None
    idx = {'c': dict(sleeve=F01, symbol='ETHUSDT', closed='??')}                    # unreadable close -> fail closed
    assert 'costs pending' in FC.bound_status(cfg(), [], idx, now=NOW)['block']


def test_counting_needs_usable_unique_ids_and_both_times_after_start(measured):
    rows = [row(i, 0.1) for i in range(7)]
    cohort = [copy.deepcopy(r) for r in rows] + [row(7, 0.1)]          # the store repeats history + one evicted row
    st = FC.bound_status(cfg(), rows, now=NOW, extra=cohort)
    assert st['closed'] == 8 and st['stop']['id'] == 'S6'             # duplicates join by id; the evicted row counts
    for bad_id in (None, '', '  ', 7):
        st = FC.bound_status(cfg(), rows, now=NOW, extra=[dict(row(8, 0.1), id=bad_id)])
        assert 'without a usable id' in st['block'] and st['stop'] is None       # never skipped, never collides
        st = FC.bound_status(cfg(), [dict(row(8, 0.1), id=bad_id)] + rows, now=NOW)
        assert 'without a usable id' in st['block']
    assert 'without a usable id' in FC.bound_status(cfg(), [dict(row(1, 0.1), closed='??')], now=NOW)['block']
    early = dict(row(1, -9.0), closed=iso(NOW - timedelta(days=2)), opened=iso(NOW - timedelta(days=1)))   # closed < start
    st = FC.bound_status(cfg(), [early], now=NOW)
    assert st['closed'] == 0 and st['block'] is None
    assert FC.bound_status(cfg(), [dict(row(1, 0.1), sleeve='ema_mom', id=None)], now=NOW)['block'] is None   # other slots


def test_manual_entries_never_consult_the_canary(f01, monkeypatch):
    e, _ = f01
    e.S[FC.CANARY_KEY] = cfg(start=NOW + timedelta(days=9))
    assert e._rules_block(slot(e, F01), 'BTCUSDT', 'LONG', manual=True) is None
    assert 'before the frozen start' in e._rules_block(slot(e, F01), 'BTCUSDT', 'LONG')


# ------------------------------------------------------------------ scoring start (OQ2 / review #3)
def test_the_bar_spanning_the_start_is_excluded_and_the_next_closed_bar_is_first_scored():
    c = cfg(start=datetime(2026, 11, 1, 1, 30, tzinfo=timezone.utc))
    bar = lambda h: f'2026-11-01 {h:02d}:00:00'
    assert not FC.is_scored(bar(0), c, '2026-11-01T04:00:05+00:00')               # 00:00-04:00 spans 01:30
    assert not FC.is_scored('2026-10-31 20:00:00', c, '2026-11-01T00:00:05+00:00')  # warm-up
    assert not FC.is_scored(bar(4), c, '2026-11-01T07:59:59+00:00')               # not confirmed closed yet
    assert FC.is_scored(bar(4), c, '2026-11-01T08:00:05+00:00')                   # first scored bar
    assert FC.is_scored(bar(8), c, '2026-11-01T12:00:05+00:00')
    c0 = cfg(start=datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc))
    assert FC.is_scored(bar(4), c0, '2026-11-01T08:00:00+00:00') and not FC.is_scored(bar(0), c0, '2026-11-01T08:00:00+00:00')
    assert not FC.is_scored(bar(4), c, None) and not FC.is_scored(None, c, '2026-11-01T08:00:05+00:00')
    rec = FC.decision_record('BTCUSDT', dict(SG, time=bar(0)), 'taken', None, '2026-11-01T04:00:05+00:00', start_cfg=c)
    assert rec['scored'] is False


# ------------------------------------------------------------------ preflight (C1-C10)
@pytest.fixture
def build(monkeypatch):
    """The running checkout as read-only git would report it (head, tree, clean)."""
    cur = [(HEAD, TREE, True)]
    monkeypatch.setattr(FC, 'running_build', lambda root=None: cur[0])
    return cur


def _ready(e):
    e.S.update(ENTRY_ORDER='market', MAX_LEVERAGE=10)
    e.S[FC.CANARY_KEY] = cfg()
    pins = {k: v for k, v in json.load(open(MANIFEST, encoding='utf-8'))['pins_expected'].items()}
    return pins


def test_preflight_passes_on_a_candidate_flat_testnet_book_and_is_read_only(f01, build):
    e, _ = f01
    pins = _ready(e)
    e.state['lots']['x'] = dict(symbol='ETHUSDT', sleeve='ema_mom', side='LONG', stop_id='S123')   # another slot (C1b)
    S0, st0 = copy.deepcopy(e.S), copy.deepcopy(e.state)
    pf = e.f01_canary_preflight(pins_expected=pins)
    assert pf['pass'], {k: v for k, v in pf['checks'].items() if not v['ok']}
    assert e.S == S0 and e.state == st0                                             # nothing switched or written
    assert pf['checks']['C1b']['other_open'] == [dict(symbol='ETHUSDT', slot='ema_mom', side='LONG')]
    assert pf['checks']['C8']['AI_FILTER'] == e.S.get('AI_FILTER') and 'FEE_EDGE_GATE' in pf['checks']['C8']
    assert pf['checks']['C9']['pins_checked'] == 34 and pf['recorded']['c_f02_enabled'] is False
    assert FC.id_fields(pf) == [] and 'S123' not in json.dumps(pf)


@pytest.mark.parametrize('check,mutate', [
    ('C1', lambda e: e.state['lots'].update(x=dict(symbol='BTCUSDT', sleeve=F01, side='LONG'))),
    ('C1', lambda e: e.state['lots'].update(x=dict(symbol='BTCUSDT', sleeve=F02, side='LONG'))),
    ('C1', lambda e: e.state.setdefault('unconfirmed_entries', {}).update({f'UE|{F01}|BTCUSDT|LONG': dict(symbol='BTCUSDT')})),
    ('C1', lambda e: e.state.setdefault('resting_entries', {}).update(r=dict(sleeve=F01, symbol='BTCUSDT'))),
    ('C1', lambda e: e.state.setdefault('pending_entries', {}).update(p=dict(sleeve=F01, symbol='BTCUSDT'))),
    ('C1', lambda e: setattr(e, '_deferred', [dict(sl=slot(e, F01), sym='BTCUSDT')])),
    ('C2', lambda e: slot(e, F02).update(enabled=True)),
    ('C3', lambda e: e.state.setdefault('candidate_disabled', {}).update({F01: dict(why='drift')})),
    ('C4', lambda e: e.state.update(candidate_cost_pending={'r': dict(sleeve=F01, symbol='BTCUSDT', closed='x')})),
    ('C5', lambda e: slot(e, F01).update(risk=0.02)),
    ('C5', lambda e: slot(e, F01).update(share=0.2)),                        # share is not in candidate_drift: the card is
    ('C5', lambda e: slot(e, F01).update(share=None)),
    ('C6', lambda e: slot(e, F01).update(kelly=dict(min_trades=0)) or e.state.setdefault('hist', {}).update({F01: [1.0, -1.0]})),
    ('C7', lambda e: e.S.update(CTRL_BIAS='bearish')),
    ('C8', lambda e: e.S.update(ENTRY_ORDER='maker')),
    ('C8', lambda e: e.S.update(MAX_LEVERAGE=5)),
    ('C10', lambda e: e.S[FC.CANARY_KEY].update(authorization_comment_id=None)),
    ('C10', lambda e: e.S[FC.CANARY_KEY].update(authorization_comment_id='abc')),
    ('C10', lambda e: e.S[FC.CANARY_KEY].update(authorization_comment_id=0)),
    ('C10', lambda e: e.S[FC.CANARY_KEY].update(cohort_dir=None)),
    ('C10', lambda e: e.S[FC.CANARY_KEY].update(cohort_dir='relative/dir')),
    ('C10', lambda e: e.S[FC.CANARY_KEY].update(cohort_dir=os.path.join(ROOT, 'x'))),
    ('C10', lambda e: e.S[FC.CANARY_KEY].update(start_utc='2026-10-31T00:00:00')),
    ('C10', lambda e: e.S[FC.CANARY_KEY].update(on=False)),
    ('ENV', lambda e: setattr(e, 'live', True)),
    ('ENV', lambda e: setattr(e.trade, 'base', E.MAINNET)),
])
def test_preflight_fails_each_check(f01, build, check, mutate):
    e, _ = f01
    pins = _ready(e)
    mutate(e)
    pf = e.f01_canary_preflight(pins_expected=pins)
    assert not pf['pass'] and not pf['checks'][check]['ok']


def test_preflight_c9_compares_the_running_build_with_the_frozen_head_tree_and_pins(f01, build):
    e, _ = f01
    pins = _ready(e)
    assert e.f01_canary_preflight(pins_expected=pins)['checks']['C9']['ok']
    assert not e.f01_canary_preflight()['checks']['C9']['ok']                               # no pins
    bad = dict(pins, **{'engine.py:Engine.cycle': '0' * 64})
    c9 = e.f01_canary_preflight(pins_expected=bad)['checks']['C9']
    assert not c9['ok'] and c9['pins_mismatch'] == ['engine.py:Engine.cycle']
    for cur in [('c' * 40, TREE, True), (HEAD, 'c' * 40, True), (HEAD, TREE, False), (None, None, False)]:
        build[0] = cur
        assert not e.f01_canary_preflight(pins_expected=pins)['checks']['C9']['ok']
    build[0] = ('abc1234', TREE, True)                                                      # an arbitrary short head
    e.S[FC.CANARY_KEY]['impl_head'] = 'abc1234'
    pf = e.f01_canary_preflight(pins_expected=pins)
    assert not pf['checks']['C9']['ok'] and not pf['checks']['C10']['ok'] and not pf['pass']


def test_running_build_reads_this_checkout_read_only():
    head, tree, clean = FC.running_build(ROOT)
    assert head is None or (FC.HEX40.match(head) and FC.HEX40.match(tree) and clean in (True, False))
    assert FC.running_build(os.path.join(tempfile.gettempdir(), 'zb_not_a_repo_dir_xyz')) == (None, None, False)


def test_pinned_functions_equal_the_contract_plus_the_amendment_only():
    pins = {k: v for k, v in json.load(open(MANIFEST, encoding='utf-8'))['pins_expected'].items() if not k.startswith('_')}
    am = json.load(open(AMEND, encoding='utf-8'))
    assert len(pins) == 34 and set(am['amended']) == {'engine.py:Engine.open_lot', 'engine.py:Engine._create_lot'}
    got = FC.function_pins(pins)
    assert {k for k in pins if got[k] != pins[k]} == set()                       # the running build == the amended pins
    for k, ch in am['amended'].items(): assert pins[k] == ch['to'] != ch['from']


def test_the_raw_sizing_capture_is_observe_only():
    src = open(os.path.join(ROOT, 'engine.py'), encoding='utf-8').read()
    assert src.count("sizing_raw") == 1                                         # written once in _create_lot, never read
    assert src.count("qty_raw=qty_raw") == 1 and src.count("plan.get('qty_raw')") == 1


def test_frozen_card_matches_the_engine_card():
    (card,) = [c for c in E.candidate_sleeves() if c['id'] == F01]
    assert FC.card_mismatch(card) == [] and FC.FROZEN_CARD['symbols'] == list(E.CAND_FIXED18)
    assert FC.card_mismatch(dict(card, mgmt=None)) == [] and FC.card_mismatch(dict(card, share=0.17)) == ['share']


# ------------------------------------------------------------------ extracts
def _all_extracts(d):
    out = []
    for root, _dirs, files in os.walk(d):
        for n in files:
            with open(os.path.join(root, n), 'rb') as f: out.append((n, f.read()))
    return out


def test_runtime_extracts_are_hashed_immutable_and_carry_no_ids(f01, monkeypatch, tmp_path):
    e, _ = f01
    real_now = datetime.now(timezone.utc)
    now_at(monkeypatch, real_now)
    cd = str(tmp_path / 'cohort')
    e.S[FC.CANARY_KEY] = cfg(start=real_now - timedelta(days=1), cohort_dir=cd)
    sg = dict(SG, time=iso(real_now - timedelta(hours=4)).replace('+00:00', ''))
    cycle4(e, {f'{F01}|BTCUSDT': sg})
    (l,) = lots(e, F01, 'BTCUSDT')
    e.S['SYMBOLS_ON']['ETHUSDT'] = False                                                     # a refusal on ETH
    cycle4(e, {f'{F01}|BTCUSDT': dict(sg, le=False), f'{F01}|ETHUSDT': dict(sg, close=50.0)})
    e.close_lot(next(k for k, x in e.state['lots'].items() if x is l), 'exit_signal')
    w = FC.ExtractWriter(cd)
    dec, tr = sorted(w.records('decision'), key=lambda d: d['symbol']), w.records('trade')
    assert [(d['symbol'], d['outcome']) for d in dec] == [('BTCUSDT', 'taken'), ('ETHUSDT', 'not_taken')]
    assert dec[1]['reason'] == 'coin switched off'                  # exact refusal text
    assert dec[0]['scored'] is True and dec[0]['cap'] == dict(family_open=1, family_cap=4, slot_held=1)
    (ent,) = [r for r in tr if r['kind'] == 'entry']
    raw = l['sizing_raw']
    assert ent['stop_sent'] == l['stop'] and ent['qty_sent'] == l['q0'] and ent['stop_atr'] == 2.5   # exact values sent
    assert ent['qty_raw'] == raw['qty_raw'] and ent['stop_raw'] == raw['stop_raw'] == l['e0'] - raw['stop_dist']
    assert ent['qty_raw'] >= ent['qty_sent'] and E.Engine._rd(ent['stop_raw'], e.rules['BTCUSDT']['tick']) == l['stop']
    (trd,) = [r for r in tr if r['kind'] == 'trade']
    assert trd['row_key_sha256'] == FC.row_key_sha256(e.history[-1]['id']) and trd['exit_why'] == 'exit_signal'
    blob = b''.join(b for _n, b in _all_extracts(cd))
    for v in {l['stop_id'], e.history[-1]['id']} | {str(x) for x in e.trade.stops}:
        assert str(v).encode() not in blob                                                       # no exchange / row ids
    for n, b in _all_extracts(cd):
        assert n == FC.sha256(b) + '.json' and FC.id_fields(json.loads(b)) == []


def test_extract_writes_are_stable_and_write_once(tmp_path):
    w = FC.ExtractWriter(str(tmp_path / 'c'))
    rec = dict(kind='decision', symbol='BTCUSDT', decision_bar='2026-11-01 00:00:00', atr=float('nan'), le=True)
    h = w.write('decision', rec)
    assert h == w.write('decision', copy.deepcopy(rec)) == FC.sha256(FC.canonical(rec))            # idempotent, stable
    assert FC.canonical(rec) == FC.canonical(dict(reversed(list(rec.items()))))                 # key order free
    assert b'{"float":"nan"}' in FC.canonical(rec)
    p = tmp_path / 'c' / 'extracts' / 'decision' / f'{h}.json'
    p.write_bytes(b'{"tampered":1}')
    with pytest.raises(ValueError): w.write('decision', rec)
    with pytest.raises(ValueError): w.records('decision')


@pytest.mark.parametrize('bad', [dict(orderId=1), dict(x=dict(clientOrderId='a')), dict(fills=[dict(tradeId=2)]),
                                 dict(stop_id='s'), dict(cid='c'), dict(API_KEY='k'), dict(id='C-F01|X|1')])
def test_extract_refuses_id_fields(tmp_path, bad):
    with pytest.raises(ValueError, match='id fields'): FC.ExtractWriter(str(tmp_path)).write('trade', bad)


def test_extract_refuses_a_cohort_dir_inside_the_repo_and_none():
    with pytest.raises(ValueError): FC.ExtractWriter(os.path.join(ROOT, 'evidence'))
    with pytest.raises(ValueError): FC.ExtractWriter(None)


def test_extracts_are_disabled_without_a_cohort_dir_or_off_testnet(f01, monkeypatch, tmp_path):
    e, _ = f01
    e.S[FC.CANARY_KEY] = cfg(cohort_dir=None)
    assert e._f01_extract('decision', lambda: dict(kind='decision')) is None                    # cohort_dir None
    e.S[FC.CANARY_KEY]['cohort_dir'] = str(tmp_path / 'c')
    e.live = True
    assert e._f01_extract('decision', lambda: dict(kind='decision')) is None and not (tmp_path / 'c').exists()
    e.live = False
    assert e._f01_extract('decision', lambda: dict(orderId=1)) is None                           # refused, never raises


def test_finalize_builds_the_contract_files_and_manifest_once(tmp_path, measured):
    w = FC.ExtractWriter(str(tmp_path / 'c'))
    c = cfg()
    w.write('decision', FC.decision_record('BTCUSDT', dict(SG, time='2026-10-31 04:00:00'), 'taken', None, 't', None, 1, 1, c))
    r = row(0, 1.5)
    w.write('trade', FC.trade_record(dict(r, _st='pending', _pnl=None)))
    w.write('trade', FC.trade_record(r))
    w.write('preflight', dict(kind='preflight', recorded=dict(at_utc='2026-10-31T00:00:00+00:00'), checks={}))
    hs = w.finalize(str(tmp_path / 'out'))
    assert set(hs) == {'preflight.json', 'decisions.csv', 'trades.csv'}
    man = (tmp_path / 'out' / 'MANIFEST.sha256').read_text().splitlines()
    for line in man:
        h, n = line.split('  ')
        assert FC.sha256((tmp_path / 'out' / n).read_bytes()) == h
    trades = (tmp_path / 'out' / 'trades.csv').read_text().splitlines()
    assert trades[0].split(',') == list(FC.TRADE_COLUMNS) and len(trades) == 2 and ',measured' in trades[1]   # measured wins
    assert (tmp_path / 'out' / 'decisions.csv').read_text().splitlines()[0].split(',') == list(FC.DECISION_COLUMNS)
    with pytest.raises(FileExistsError): w.finalize(str(tmp_path / 'out'))


# ------------------------------------------------------------------ C-F02 never enabled
def test_c_f02_is_never_enabled_by_any_canary_path(f01, monkeypatch, tmp_path, measured):
    e, _ = f01
    e.S[FC.CANARY_KEY] = cfg(cohort_dir=str(tmp_path / 'c'))
    sleeves0 = copy.deepcopy(e.S['SLEEVES'])
    e.f01_canary_block(slot(e, F01)); e.f01_canary_preflight()
    cycle4(e, {f'{F01}|BTCUSDT': SG, f'{F02}|ETHUSDT': SG})
    e.history += [row(i, -2.0) for i in range(3)]
    e.f01_canary_block(slot(e, F01))
    assert e.S['SLEEVES'] == sleeves0 and slot(e, F02)['enabled'] is False and not lots(e, F02)
    assert e.f01_canary_block(slot(e, F02)) is None and set(FC.config(e.S)) == {'on', 'start_utc', 'authorization_comment_id', 'cohort_dir', 'impl_head', 'impl_tree'}


def test_cohort_pack_is_not_activated():
    m = json.load(open(MANIFEST, encoding='utf-8'))
    assert m['status'] == 'NOT ACTIVATED' and m['authorization'] is None and m['authorization_comment_id'] is None
    assert m['flag']['settings_key'] == FC.CANARY_KEY and m['bounds']['duration_days'] == FC.DURATION_DAYS
    assert m['bounds']['max_closed_trades'] == FC.MAX_CLOSED_TRADES and m['bounds']['drawdown_stop_r'] == FC.DRAWDOWN_STOP_R
    md = open(os.path.join(os.path.dirname(MANIFEST), 'COHORT_PACK.md'), encoding='utf-8').read()
    assert 'NOT ACTIVATED' in md and '`authorization: null`' in md
