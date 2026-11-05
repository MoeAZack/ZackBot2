"""STRAT-01 testnet launcher and the testnet mode of the journal risk context (no network: fake fetch and runtime)."""
from __future__ import annotations

import dataclasses
import io
import json
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / 'tools', ROOT / 'tests' / 'newcore_strategies'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import strat01_testnet_run as TN  # noqa: E402
import test_strat01_paper_launcher as PL  # noqa: E402
from newcore import risk as nc06  # noqa: E402
from newcore.domain.account import Environment as BE  # noqa: E402
from newcore_strategies.journal_risk import JournalRiskContext, RiskSetupError  # noqa: E402

A1 = PL.A1
H4 = PL.H4
SYMBOLS = PL.SYMBOLS


def tconfig(tmp_path, *, environment='testnet', entries=True):
    return PL.write_json(tmp_path / 'tconfig.json', {
        'schema': 'zackbot.newcore.runtime.config.v1', 'environment': environment, 'entries_enabled': entries,
        'accounts': [{'account_id': A1, 'aggregate_id': f'pf_{1:032x}', 'account_dir': str(tmp_path / 'a1'),
                      'egress_id': 'testnet-1'}]})


def fake_fetch(calls, *, info=True, marks=True):
    def fetch(path, params):
        calls.append((path, params))
        if path == '/fapi/v1/exchangeInfo':
            if not info:
                raise OSError('down')
            return PL.INFO
        if path == '/fapi/v1/premiumIndex':
            return [{'symbol': s, 'markPrice': '100'} for s in SYMBOLS] if marks else []
        return [[PL.WALL0 - H4, '1', '2', '0.5', '1.5', '10', PL.WALL0 - 1, '15']]
    return fetch


def args(tmp_path, *extra, equity='1000'):
    (tmp_path / 'candles').mkdir(exist_ok=True)
    return ['--config', str(tmp_path / 'tconfig.json'), '--account-file', str(tmp_path / 'acct.json'),
            '--marks', str(tmp_path / 'marks.json'), '--candles', str(tmp_path / 'candles'), '--equity', equity,
            '--symbols', ','.join(SYMBOLS), *extra]


def run(tmp_path, *extra, environ=None, fetch=None, run_testnet=None, equity='1000'):
    out = io.StringIO()
    code = TN.main(args(tmp_path, *extra, equity=equity), environ=environ or {}, out=out,
                   fetch=fetch or fake_fetch([]), run_testnet=run_testnet)
    return code, [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]


# ---------------------------------------------------------------------------------------------------- refusals
def test_testnet_launcher_refusals(tmp_path):
    tconfig(tmp_path)
    never = lambda *a, **k: (_ for _ in ()).throw(AssertionError('runtime must not start'))  # noqa: E731
    assert run(tmp_path, environ={'ZACKBOT_LIVE': '1'}, run_testnet=never)[1][-1]['code'] == 'live_switch_present'
    assert run(tmp_path, '--feed-origin', 'https://fapi.binance.com',
               run_testnet=never)[1][-1]['code'] == 'feed_origin_not_testnet'
    assert run(tmp_path, equity='2000.01', run_testnet=never)[1][-1]['code'] == 'equity_above_testnet_cap'
    tconfig(tmp_path, environment='paper_sim')
    assert run(tmp_path, run_testnet=never)[1][-1]['code'] == 'testnet_only'
    tconfig(tmp_path, entries=False)
    assert run(tmp_path, run_testnet=never)[1][-1]['code'] == 'entries_not_enabled'
    tconfig(tmp_path)
    assert run(tmp_path, fetch=fake_fetch([], info=False), run_testnet=never)[1][-1]['code'] == \
        'exchange_info_unavailable'
    assert run(tmp_path, fetch=fake_fetch([], marks=False), run_testnet=never)[1][-1]['code'] == 'marks_unavailable'
    assert run(tmp_path, '--symbols', 'ETHUSDT', run_testnet=never)[1][-1]['code'] == 'paper_symbols_invalid'


def test_feed_reads_only_the_testnet_origin(tmp_path):
    tconfig(tmp_path)
    urls = []
    import urllib.request

    def opener(req, timeout):
        urls.append(req.full_url)
        raise OSError('offline')
    orig = urllib.request.urlopen
    urllib.request.urlopen = opener
    try:
        out = io.StringIO()
        TN.main(args(tmp_path, '--feed-origin', 'https://testnet.binancefuture.com'), environ={}, out=out,
                run_testnet=lambda *a, **k: None)
    finally:
        urllib.request.urlopen = orig
    assert urls and all(u.startswith('https://testnet.binancefuture.com/fapi/v1/') for u in urls)
    assert json.loads(out.getvalue().splitlines()[-1])['code'] == 'exchange_info_unavailable'


# ---------------------------------------------------------------------------------------------------- happy path
def test_testnet_launcher_hands_a_testnet_factory_to_run_testnet(tmp_path):
    tconfig(tmp_path)
    PL.init_paper_account(PL.spec(tmp_path), wall_ms=PL.WALL0, event_n=7)      # a journal (SIM-bound) to view
    calls, seen = [], {}

    def fake_run_testnet(config, account_file, *, symbols, emit, entries_factory, environ, **kw):
        spec = config.accounts[0]
        view = lambda: PL.read_journal(spec.account_dir, spec.account_id, spec.aggregate_id)  # noqa: E731
        source = entries_factory(spec, wall_clock=lambda: PL.WALL0 + 60_000, journal=view)
        seen.update(symbols=symbols, account_file=account_file, source=source)
        return {'accounts': [{'holds': []}]}
    code, lines = run(tmp_path, fetch=fake_fetch(calls), run_testnet=fake_run_testnet)
    assert code == TN.EXIT_OK, lines
    assert lines[0]['status'] == 'starting' and lines[0]['environment'] == 'testnet'
    assert seen['symbols'] == SYMBOLS and seen['account_file'].endswith('acct.json')
    assert {p for p, _ in calls} == {'/fapi/v1/exchangeInfo', '/fapi/v1/premiumIndex', '/fapi/v1/klines'}
    (acct,) = lines[-1]['accounts']
    assert acct['refused_slots'] == {'c101_kalman_long': 'strategy_unavailable'}
    risk = seen['source']._risk
    assert risk.environment is nc06.Environment.TESTNET
    # the journal here carries a SIM binding: the testnet risk context refuses it
    slot = seen['source'].slots[0]
    assert risk.risk_context(A1, slot, 'BTCUSDT') is None and risk.reasons['binding_not_testnet'] == 1


# ---------------------------------------------------------------------------------------------------- risk context
def _testnet_risk(tmp_path, monkeypatch, *, binding=BE.TESTNET, equity=D('1000')):
    s, slots, risk = PL._risk(tmp_path, environment='testnet', equity=equity)
    PL.init_paper_account(s, wall_ms=PL.WALL0, event_n=7)
    real = risk._projection.load_journal

    def patched(account_id, journal):
        d = real(account_id, journal)
        b = dataclasses.replace(d.account.binding, environment=binding)
        return dataclasses.replace(d, account=dataclasses.replace(d.account, binding=b))
    monkeypatch.setattr(risk._projection, 'load_journal', patched)
    return slots, risk


def test_testnet_risk_context_is_a_testnet_snapshot(tmp_path, monkeypatch):
    slots, risk = _testnet_risk(tmp_path, monkeypatch)
    ctx = risk.risk_context(A1, slots[0], 'BTCUSDT')
    assert ctx.account.environment is ctx.policy.environment is nc06.Environment.TESTNET
    assert ctx.account.equity == ctx.policy.capital_cap == D('1000')


@pytest.mark.parametrize('binding', [BE.SIM, BE.MAINNET])
def test_testnet_risk_context_refuses_other_bindings(tmp_path, monkeypatch, binding):
    slots, risk = _testnet_risk(tmp_path, monkeypatch, binding=binding)
    assert risk.risk_context(A1, slots[0], 'BTCUSDT') is None and risk.reasons['binding_not_testnet'] == 1


def test_paper_risk_context_refuses_a_testnet_binding(tmp_path, monkeypatch):
    s, slots, risk = PL._risk(tmp_path)
    PL.init_paper_account(s, wall_ms=PL.WALL0, event_n=7)
    real = risk._projection.load_journal
    monkeypatch.setattr(risk._projection, 'load_journal', lambda a, j: dataclasses.replace(
        real(a, j), account=dataclasses.replace(real(a, j).account, binding=dataclasses.replace(
            real(a, j).account.binding, environment=BE.TESTNET))))
    assert risk.risk_context(A1, slots[0], 'BTCUSDT') is None and risk.reasons['binding_not_paper'] == 1


def test_testnet_capital_and_environment_bounds(tmp_path):
    with pytest.raises(RiskSetupError, match='equity_above_testnet_cap'):
        PL._risk(tmp_path, environment='testnet', equity=D('2000.01'))
    assert PL._risk(tmp_path, environment='testnet', equity=D('2000'))[2].equity == D('2000')
    with pytest.raises(RiskSetupError, match='environment_invalid'):
        PL._risk(tmp_path, environment='mainnet')
    assert isinstance(PL._risk(tmp_path, equity=D('5000'))[2], JournalRiskContext)          # paper: no $2k cap
