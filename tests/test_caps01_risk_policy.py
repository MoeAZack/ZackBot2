"""CAPS-01 (moe, 16 Nov 2026): a trading engine whose risk caps are missing or looser than the owner policy - open risk
2.5 % (enforced, never just a warning), daily entry halt 3 %, drawdown stop 10 % - opens nothing new: no entry, no add,
manual included. Exits, stops and the management of open trades keep running. The code defaults equal the policy."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import engine as E
from test_safety import mk_engine, SL, SG, _pyr                          # noqa: E402  the shared fake-Binance harness


def test_policy_values_and_code_defaults_match_the_owner_policy():
    assert E.RISK_CAP_POLICY == dict(open_risk_pct=2.5, daily_loss_halt=0.03, peak_dd_flatten=0.10)
    assert E.GLOBAL_DEFAULTS['DAILY_LOSS_HALT'] == 0.03 and E.GLOBAL_DEFAULTS['PEAK_DD_FLATTEN'] == 0.10
    assert E.RISK_RULE_DEFAULTS['open_risk_cap'] == dict(mode='enforce', pct=2.5)


def test_defaults_are_compliant_and_an_entry_opens():
    e, _ = mk_engine()
    assert e.risk_caps_problems() == [] and e.risk_caps_block() is None
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip


@pytest.mark.parametrize('setting, word', [
    (dict(DAILY_LOSS_HALT=0.08), 'daily loss halt'),                      # the old code default
    (dict(DAILY_LOSS_HALT=0.0), 'daily loss halt'),                       # switched off
    (dict(DAILY_LOSS_HALT=None), 'daily loss halt'),                      # missing / unreadable
    (dict(DAILY_LOSS_HALT=float('nan')), 'daily loss halt'),
    (dict(DAILY_LOSS_HALT=0.0301), 'daily loss halt'),                    # just looser
    (dict(PEAK_DD_FLATTEN=0.0), 'drawdown stop'),                         # the old code default (off)
    (dict(PEAK_DD_FLATTEN=0.2), 'drawdown stop'),
    (dict(PEAK_DD_FLATTEN='x'), 'drawdown stop'),
    (dict(PEAK_DD_FLATTEN=0.1001), 'drawdown stop'),
    (dict(RISK_RULES={'open_risk_cap': {'mode': 'warn', 'pct': 2.5}}), 'open-risk cap'),     # warns only
    (dict(RISK_RULES={'open_risk_cap': {'mode': 'off', 'pct': 2.5}}), 'open-risk cap'),
    (dict(RISK_RULES={'open_risk_cap': {'mode': 'enforce', 'pct': 15.0}}), 'open-risk cap'),  # the old code default pct
    (dict(RISK_RULES={'open_risk_cap': {'mode': 'enforce', 'pct': 2.51}}), 'open-risk cap'),
    (dict(RISK_RULES={'open_risk_cap': {'mode': 'enforce', 'pct': 0}}), 'open-risk cap'),
    (dict(RISK_RULES={'open_risk_cap': {'mode': 'enforce', 'pct': None}}), 'open-risk cap'),
    (dict(RISK_RULES={'open_risk_cap': {'mode': 'bogus', 'pct': 2.5}}), 'open-risk cap'),   # unknown mode -> warn -> refused
])
def test_a_looser_or_missing_cap_refuses_new_entries_with_a_named_reason(setting, word):
    e, _ = mk_engine()
    e.S.update(setting)
    probs = e.risk_caps_problems()
    assert len(probs) == 1 and probs[0].startswith(word), probs
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()) and not e.state['lots']
    assert e.last_skip.startswith('risk caps looser than policy - no new entries: ') and word in e.last_skip


def test_tighter_caps_are_allowed():
    e, _ = mk_engine(DAILY_LOSS_HALT=0.02, PEAK_DD_FLATTEN=0.05, RISK_RULES={'open_risk_cap': {'mode': 'enforce', 'pct': 1.5}})
    assert e.risk_caps_problems() == []
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip


def test_the_exact_policy_values_are_allowed():
    e, _ = mk_engine(DAILY_LOSS_HALT=0.03, PEAK_DD_FLATTEN=0.10, RISK_RULES={'open_risk_cap': {'mode': 'enforce', 'pct': 2.5}})
    assert e.risk_caps_problems() == []


def test_every_problem_is_named_together():
    e, _ = mk_engine(DAILY_LOSS_HALT=0.08, PEAK_DD_FLATTEN=0.0, RISK_RULES={'open_risk_cap': {'mode': 'warn', 'pct': 15.0}})
    assert [p.split(' ')[0] for p in e.risk_caps_problems()] == ['daily', 'drawdown', 'open-risk']


def test_a_manual_entry_is_refused_too():
    e, _ = mk_engine(PEAK_DD_FLATTEN=0.0)
    r = e.entry_block(None, 'BTCUSDT', 'LONG', manual=True, size=dict(notional=100.0, risk=1.0))
    assert r and r.startswith('risk caps looser than policy')


def test_an_add_is_refused_but_the_open_trade_keeps_its_stop_and_exit():
    e, _ = mk_engine(); sl, lot = _pyr(e)                                   # opened while compliant
    e.S['DAILY_LOSS_HALT'] = 0.5                                            # then the settings were loosened
    e.trade.mark['BTCUSDT'] = 106.0; e.manage(e.trade.marks())
    assert lot['adds'] == 0 and lot['add_blocked'].startswith('risk caps looser than policy')
    assert lot['stop_id'] and lot['stop_id'] in e.trade.stops                # protection untouched
    k = next(iter(e.state['lots']))
    assert e.close_lot(k, 'manual close') is not False and not e.state['lots']   # exits never consult the caps


def test_open_risk_cap_enforces_by_default():
    """The open-risk cap blocks (not warns) by default: 1 % per trade -> the third concurrent trade (3 %) is refused."""
    sl = dict(SL, max_pos=5)
    e, _ = mk_engine(UNIVERSE=['BTCUSDT', 'ETHUSDT', 'SOLUSDT'])
    e.trade.mark['SOLUSDT'] = 20.0
    e.rules.setdefault('SOLUSDT', dict(e.rules['ETHUSDT']))
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    assert e.open_lot(sl, 'ETHUSDT', 'LONG', dict(SG, close=50.0, atr=1.0), None, e.equity()), e.last_skip
    assert not e.open_lot(sl, 'SOLUSDT', 'LONG', dict(SG, close=20.0, atr=0.4), None, e.equity())
    assert 'open_risk_cap' in e.last_skip


def test_the_alert_is_keyed_and_resolves_when_fixed():
    e, _ = mk_engine()
    e.S['PEAK_DD_FLATTEN'] = 0.0
    assert e.risk_caps_block()
    assert (e.health.get('incidents', {}).get('risk-caps') or {}).get('open')
    e.S['PEAK_DD_FLATTEN'] = 0.10
    assert e.risk_caps_block() is None
    assert not (e.health.get('incidents', {}).get('risk-caps') or {}).get('open')


def test_a_non_compliant_start_says_so_at_once():
    sent = []
    e, _ = mk_engine(PEAK_DD_FLATTEN=0.0)
    e.notify = sent.append
    e.connect()
    assert any(m.startswith('⛔ risk caps looser than policy') for m in sent), sent


# ---------------------------------------------------------------- the one exemption: offline parity replay (moe, 16 Nov)
def test_the_replay_flag_never_exempts_an_engine_holding_the_real_binance_client():
    e, _ = mk_engine(PEAK_DD_FLATTEN=0.0)
    e.trade = E._REAL_FUTURES('', '', E.TESTNET)                            # the real client class (never called here)
    e.offline_replay = True
    assert e.risk_caps_problems() and e.risk_caps_block()


def test_the_replay_flag_must_be_exactly_true():
    e, _ = mk_engine(PEAK_DD_FLATTEN=0.0)
    for v in (1, 'true', 'yes', [1]):
        e.offline_replay = v
        assert e.risk_caps_problems(), v


def test_a_fake_client_alone_is_not_exempt():
    e, _ = mk_engine(PEAK_DD_FLATTEN=0.0)                                    # the test fake, no flag
    assert e.risk_caps_problems()


def test_only_the_replay_harness_sets_the_flag_and_no_setting_can():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    users = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ('.git', 'tests', '__pycache__', 'node_modules')]
        for f in fn:
            if f.endswith('.py') and 'offline_replay' in open(os.path.join(dp, f), encoding='utf-8', errors='replace').read():
                users.append(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, '/'))
    assert sorted(users) == ['engine.py', 'replay.py'], users
    src = open(os.path.join(root, 'engine.py'), encoding='utf-8').read()
    assert src.count('offline_replay') == 2                                   # docstring + the one read; never from self.S
    assert "S.get('offline_replay')" not in src and "S['offline_replay']" not in src


def test_the_replay_harness_runs_exempt_with_the_pre_policy_caps():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'replay.py'), encoding='utf-8').read()
    assert 'eng.offline_replay = True' in src and 'E.Futures = Fake' in src
