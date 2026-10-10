"""`python -m newcore.run` (newcore/runner/app.py, config.py, reports.py): config refusals (PAPER / TESTNET only, no live /
mainnet value, no credentials, never the legacy %LOCALAPPDATA%\\ZackBot), the candidate gate, --once, restart from the
journal, clean shutdown, deterministic trades.csv / incidents.jsonl, the health line, the testnet factory hook, the
module entry, and the replay CLI against the research list. No network, no keys."""
import io
import json
import os
import signal
import subprocess
import sys

import pytest

from newcore.runner import app as A
from newcore.runner import config as C

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'ema_mom_long_base_trades.csv')


def toml(d):
    """A tiny TOML writer for flat sections (strings, ints, bools, lists of strings)."""
    def val(v):
        if isinstance(v, bool):
            return 'true' if v else 'false'
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, list):
            return '[' + ', '.join(val(x) for x in v) + ']'
        return json.dumps(v)
    lines = [f'{k} = {val(v)}' for k, v in d.items() if not isinstance(v, dict)]
    for k, v in d.items():
        if isinstance(v, dict):
            lines += ['', f'[{k}]'] + [f'{kk} = {val(vv)}' for kk, vv in v.items()]
    return '\n'.join(lines) + '\n'


def cfg_file(tmp_path, name='run.toml', **over):
    doc = {'mode': 'PAPER',
           'venue': {'kind': 'fake', 'data_root': ROOT, 'start': '2022-02-25 00:00:00'},
           'journal': {'dir': str(tmp_path / 'nc')},
           'strategy': {'enabled': True, 'symbols': ['BTCUSDT']},
           'cycle': {'cadence_s': 0}}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(doc.get(k), dict):
            doc[k] = dict(doc[k], **v)
        else:
            doc[k] = v
    p = tmp_path / name
    p.write_text(toml(doc), encoding='utf-8')
    return str(p)


def run(argv, stop=None):
    out = io.StringIO()
    code = A.main(argv, out=out, stop=stop or A.StopFlag())
    return code, out.getvalue()


# ------------------------------------------------------------------------------------------------ config
@pytest.mark.parametrize('bad', [
    {'mode': 'LIVE'}, {'mode': 'MAINNET'}, {'mode': 'live'}, {'mode': 'REAL'},
    {'venue': {'kind': 'testnet'}},                                          # testnet kind in PAPER
    {'venue': {'kind': 'mainnet'}},
    {'venue': {'data_root': 'C:/mainnet/data'}},                              # a value naming mainnet, anywhere
    {'strategy': {'symbols': ['BTCUSDT'], 'rule': 'trend_ema_mom.v1-live'}},
    {'account': {'api_key': 'x'}}, {'account': {'secret': 'x'}}, {'account': {'token': 'x'}},   # no credentials
    {'mode': 'TESTNET', 'venue': {'kind': 'testnet'}},                       # testnet without the factory hook
    {'book': {'max_leverage': 3.0}},                                           # a float, not a decimal string
    {'book': {'max_leverage': '50'}}, {'book': {'risk_pct': '0.5'}},
    {'surprise': 1}, {'venue': {'kind': 'fake', 'turbo': True}},              # unknown keys
])
def test_live_mainnet_and_credentials_are_refused_at_load(tmp_path, bad):
    with pytest.raises(C.ConfigError):
        C.load(cfg_file(tmp_path, **bad))


def test_the_legacy_zackbot_dir_is_refused_but_zackbotnc_is_the_default(tmp_path):
    env = {'LOCALAPPDATA': str(tmp_path / 'la')}
    doc = {'venue': {'kind': 'fake'}}
    c = C.validate(doc, environ=env)
    assert c.journal_dir == os.path.join(str(tmp_path / 'la'), 'ZackBotNC', 'data')
    with pytest.raises(C.ConfigError, match='legacy'):
        C.validate({'journal': {'dir': str(tmp_path / 'la' / 'ZackBot' / 'data')}}, environ=env)
    with pytest.raises(C.ConfigError, match='legacy'):
        C.validate({'output': {'dir': str(tmp_path / 'la' / 'ZackBot')}}, environ=env)


def test_canary_defaults_and_the_disabled_strategy():
    c = C.validate({})
    assert (c.mode, c.venue_kind, c.enabled, c.mirrored_short) == ('PAPER', 'fake', False, False)
    assert (c.max_positions, str(c.max_leverage), str(c.daily_loss_pct), str(c.kill_drawdown_pct)) == \
        (4, '3', '0.03', '0.10')
    t = C.validate({'mode': 'TESTNET', 'venue': {'factory': 'pkg.mod:make'}})
    assert (t.venue_kind, t.factory) == ('testnet', 'pkg.mod:make')


def test_a_refused_config_exits_2_before_anything_runs(tmp_path):
    code, out = run(['run', '--config', cfg_file(tmp_path, mode='MAINNET'), '--once'])
    assert code == A.EXIT_CONFIG and 'CONFIG REFUSED' in out and not (tmp_path / 'nc').exists()


# ------------------------------------------------------------------------------------------------ run
def test_once_runs_exactly_one_cycle_and_writes_everything(tmp_path):
    code, out = run(['run', '--config', cfg_file(tmp_path), '--once', '--enable-candidate'])
    assert code == 0
    health = [line for line in out.splitlines() if line.startswith('HEALTH')]
    assert len(health) == 1
    assert 'Cairo (' in health[0] and 'mode=ACTIVE hold=- positions=flat protected=yes' in health[0]
    acct = C.load(cfg_file(tmp_path)).account_id
    base = tmp_path / 'nc'
    assert (base / acct / 'journal').is_dir() and (base / acct / A.STATE_FILE).is_file()
    assert (base / 'reports' / 'trades.csv').read_text().startswith('symbol,side,lot_id')
    assert (base / 'reports' / 'incidents.jsonl').exists()


def test_the_candidate_needs_the_flag(tmp_path):
    code, out = run(['run', '--config', cfg_file(tmp_path), '--cycles', '60'])
    assert code == 0 and 'DISABLED' in out and 'strategy=off' in out
    assert (tmp_path / 'nc' / 'reports' / 'trades.csv').read_text().count('\n') == 1      # header only
    code, out = run(['run', '--config', cfg_file(tmp_path, journal={'dir': str(tmp_path / 'on')}), '--cycles', '60',
                     '--enable-candidate'])
    trades = (tmp_path / 'on' / 'reports' / 'trades.csv').read_text().splitlines()
    assert len(trades) == 2 and trades[1].startswith('BTCUSDT,LONG,')      # the 2022-03-01 entry, stopped 03-04


def test_restart_resumes_from_the_journal_with_identical_outputs(tmp_path):
    one = cfg_file(tmp_path, 'one.toml', journal={'dir': str(tmp_path / 'one')})
    two = cfg_file(tmp_path, 'two.toml', journal={'dir': str(tmp_path / 'two')})
    assert run(['run', '--config', one, '--cycles', '60', '--enable-candidate'])[0] == 0
    assert run(['run', '--config', two, '--cycles', '27', '--enable-candidate'])[0] == 0    # stop mid-trade ...
    code, out = run(['run', '--config', two, '--cycles', '33', '--enable-candidate'])       # ... restart, resume
    assert code == 0
    for f in ('trades.csv', 'incidents.jsonl'):
        assert (tmp_path / 'one' / 'reports' / f).read_bytes() == (tmp_path / 'two' / 'reports' / f).read_bytes()
    acct = C.load(one).account_id
    assert (tmp_path / 'one' / acct / A.STATE_FILE).read_bytes() == (tmp_path / 'two' / acct / A.STATE_FILE).read_bytes()
    segs = lambda d: b''.join((d / acct / 'journal' / n).read_bytes()
                              for n in sorted(os.listdir(d / acct / 'journal')))
    assert segs(tmp_path / 'one') == segs(tmp_path / 'two')                  # the journal bytes too


def test_outputs_are_deterministic(tmp_path):
    for name in ('a', 'b'):
        run(['run', '--config', cfg_file(tmp_path, f'{name}.toml', journal={'dir': str(tmp_path / name)}),
             '--cycles', '60', '--enable-candidate'])
    for f in ('trades.csv', 'incidents.jsonl'):
        assert (tmp_path / 'a' / 'reports' / f).read_bytes() == (tmp_path / 'b' / 'reports' / f).read_bytes()


class StopAfter(A.StopFlag):
    def __init__(self, n):
        super().__init__()
        self.n = n

    def is_set(self):
        self.n -= 1
        return self.n < 0 or super().is_set()


def test_clean_shutdown_finishes_the_cycle_and_writes(tmp_path):
    code, out = run(['run', '--config', cfg_file(tmp_path), '--enable-candidate'], stop=StopAfter(2))
    assert code == 0 and 'STOP: clean shutdown after the cycle' in out
    assert len([x for x in out.splitlines() if x.startswith('HEALTH')]) == 3
    assert (tmp_path / 'nc' / 'reports' / 'trades.csv').exists()


def test_the_signal_handler_sets_the_flag():
    flag = A.StopFlag()
    old = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        flag.install()
        signal.raise_signal(signal.SIGINT)
        assert flag.is_set()
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def test_health_line_reports_positions_and_protection(tmp_path):
    code, out = run(['run', '--config', cfg_file(tmp_path), '--cycles', '28', '--enable-candidate'])
    last = [x for x in out.splitlines() if x.startswith('HEALTH')][-1]
    assert 'positions=BTCUSDT:LONG:' in last and 'protected=yes' in last and 'mode=ACTIVE' in last


# ------------------------------------------------------------------------------------------------ testnet hook
def fake_factory(cfg):
    """A testnet factory for the hook test: FakeVenue + data_long, no network."""
    from newcore.adapters import CsvBarSource, FakeVenue
    bars = CsvBarSource.from_data_long(ROOT, list(cfg.symbols), cfg.tf)
    venue = FakeVenue({s: bars.all_bars(s) for s in cfg.symbols}, 14_400_000)
    return {'venue': venue, 'bars': bars, 'account_reads': venue}


def test_testnet_mode_uses_the_factory_hook_and_a_testnet_binding(tmp_path):
    p = cfg_file(tmp_path, mode='TESTNET', venue={'kind': 'testnet', 'factory': 'test_run_cli:fake_factory',
                                                    'data_root': ROOT, 'start': ''},
                 cycle={'cadence_s': 0, 'delay_s': 0})
    code, out = run(['run', '--config', p, '--once'])
    assert code == 0 and 'RUN mode=TESTNET venue=testnet' in out and 'HEALTH' in out


# ------------------------------------------------------------------------------------------------ entry + replay
def test_python_dash_m_entry(tmp_path):
    env = dict(os.environ, PYTHONPATH=ROOT)
    ok = subprocess.run([sys.executable, '-m', 'newcore.run', 'run', '--config', cfg_file(tmp_path), '--once'],
                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert ok.returncode == 0 and 'HEALTH' in ok.stdout, ok.stderr
    bad = subprocess.run([sys.executable, '-m', 'newcore.run', '--config', cfg_file(tmp_path, 'm.toml', mode='LIVE')],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert bad.returncode == A.EXIT_CONFIG and 'CONFIG REFUSED' in bad.stdout


@pytest.mark.slow
def test_replay_cli_reproduces_the_research_match(tmp_path):
    p = cfg_file(tmp_path, venue={'kind': 'replay', 'data_root': ROOT, 'start': ''}, book={'cap_gap_buffer': '0'})
    code, out = run(['replay', '--config', p, '--symbols', 'BTCUSDT', '--compare', FIXTURE, '--enable-candidate'])
    assert code == 0, out
    assert 'COMPARE TOTAL 31/31' in out
    assert (tmp_path / 'nc' / 'reports' / 'replay-BTCUSDT' / 'trades.csv').read_text().count('\n') == 32
