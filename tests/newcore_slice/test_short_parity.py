"""M3 short-side mechanical parity (newcore/runner/mirror.py, parity.py) on windows of real data: the long rule on the
original series and the mirrored short rule on the mirrored series, through the full Runner with a FileJournal, must
trade the same candles with the same exit codes, quantities and (at zero costs) exactly equal R; plus a crash / restart
sample on the short side. The full core-8 table is docs/newcore/slice/M3_short_parity.md."""
import os
from decimal import Decimal as D

import pytest

from newcore.runner import parity as P
from newcore.runner.mirror import mirror_bars, pivot_of
from slice_helpers import Crash, H4

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
N = 1500


@pytest.fixture(scope='module')
def data():
    orig, _ = P.candles(ROOT, ('BTCUSDT', 'SOLUSDT'))
    return {s: b[:N] for s, b in orig.items()}


def test_the_mirror_is_an_exact_involution(data):
    b = data['BTCUSDT']
    m = mirror_bars(b)
    p = pivot_of(b)
    assert all(x.low >= p > 0 for x in m)
    assert mirror_bars(m, pivot=p) == b                            # mirroring twice gives the series back
    assert all((x.high - x.low) == (y.high - y.low) for x, y in zip(b, m))   # ranges (ATR input) unchanged


@pytest.mark.parametrize('sym', ['BTCUSDT', 'SOLUSDT'])
def test_long_and_mirrored_short_trade_identically_at_zero_cost(tmp_path, data, sym):
    b = data[sym]
    t0 = b[0].open_ms
    lr = P.run_single(ROOT, sym, b, 'LONG', 'zero', str(tmp_path / 'l'))
    sr = P.run_single(ROOT, sym, mirror_bars(b), 'SHORT', 'zero', str(tmp_path / 's'))
    res = P.pair(P.trade_rows(lr, t0), P.trade_rows(sr, t0))
    assert res['longs'] == res['shorts'] == res['paired'] == res['same_exit_qty'] == res['r_equal'] >= 2
    assert D(res['max_abs_dr']) == 0 and lr.counters.unprotected_cycles == sr.counters.unprotected_cycles == 0
    assert {t.side for t in sr.trades()} == {'SHORT'}


def test_costs_break_exact_r_parity_only_by_the_price_level(tmp_path, data):
    """With proportional costs the trades still match candle for candle; only R differs (cost on a different level)."""
    b = data['BTCUSDT']
    t0 = b[0].open_ms
    lr = P.run_single(ROOT, 'BTCUSDT', b, 'LONG', 'base', str(tmp_path / 'l'))
    sr = P.run_single(ROOT, 'BTCUSDT', mirror_bars(b), 'SHORT', 'base', str(tmp_path / 's'))
    res = P.pair(P.trade_rows(lr, t0), P.trade_rows(sr, t0))
    assert res['longs'] == res['shorts'] == res['same_exit_qty']
    assert D(res['max_abs_dr']) < D('0.2')


@pytest.mark.slow
@pytest.mark.parametrize('sym,code', [('BTCUSDT', 'STOP_HIT'), ('SOLUSDT', 'SIGNAL_EXIT')])
def test_short_side_crash_restart_sample_matches_the_uninterrupted_run(tmp_path, data, sym, code):
    """Crash before / after each of the first 8 venue effects of the mirrored short trades (entry, stop, cancel, close),
    restart from the FileJournal, finish: identical trades; and a forced stop replace. (The gapped short stop is the
    synthetic crash matrix's stopped-SHORT points, test_crash_safety.py.)"""
    from slice_helpers import ScriptedVenue
    from newcore.adapters import CsvBarSource, FakeVenue, load_rules
    from newcore.runner import EmaMomSignals, Runner, RunnerConfig, SizingPolicy
    from newcore.store import create_journal, recover_journal
    from newcore.strategy import Params


    m = mirror_bars(data[sym])
    src = CsvBarSource({sym: m}, H4)
    rules = load_rules(os.path.join(ROOT, 'data', 'exchange_rules_testnet.json'), [sym])

    def world(d):
        venue = FakeVenue({sym: m}, H4, costs=P.COSTS['zero'], equity=D('10000'))
        port = ScriptedVenue(venue)
        cfg = RunnerConfig(account=P.sim_account(), portfolio_id=P.PF, symbols=(sym,), tf_ms=H4, timeframe='4h',
                           rules=rules, sizing=SizingPolicy(D('0.01'), P.NO_CAP), sides=('SHORT',))
        sig = EmaMomSignals(H4, '4h', params=Params(enable_short=True))

        def make(j):
            return Runner(cfg, journal=j, venue=port, bars=src, signals=sig, account_reads=venue)
        return venue, port, make, create_journal(d, P.ACCT, P.PF)

    def run(d, crash=None, replace_at=None):
        venue, port, make, j = world(d)
        if crash:
            port.crash(*crash)
        r = make(j)
        for i in range(N - 1):
            t = m[i].close_ms
            venue.advance_to(t)
            if replace_at is not None and i == replace_at:                # an external cancel forces a stop replace
                for lot in r.fold.open_lots():
                    if lot.live_stop is not None:
                        venue.external_cancel(lot.live_stop.intent.client_order_id)
            try:
                r.cycle(t, decide=i < N - 2)
            except Crash:
                r.journal.close()
                j = recover_journal(d, P.ACCT, P.PF).journal
                r = make(j)
                r.cycle(t, decide=i < N - 2)
        return r, port

    ref, port = run(str(tmp_path / 'ref'), replace_at=None)
    want = [(t.entry_ms, t.exit_ms, t.exit_code, t.r) for t in ref.trades()]
    first_entry = (want[0][0] - m[0].open_ms) // H4
    effects = len(port.effects)
    assert effects >= 6 and code in {w[2] for w in want}
    for n in range(1, min(effects, 8) + 1):                        # the first trades' entry / stop / cancel / close
        for when in ('before', 'after'):
            r, _ = run(str(tmp_path / f'c{n}{when}'), crash=(n, when))
            got = [(t.entry_ms, t.exit_ms, t.exit_code, t.r) for t in r.trades()]
            if not (when == 'before' and port.effects[n - 1] == 'market_entry'):
                assert got == want, (n, when)                      # (a crash before an entry send loses that entry)
            assert r.counters.unprotected_cycles == 0
    rr, rport = run(str(tmp_path / 'replace'), replace_at=first_entry + 1)
    assert [(t.entry_ms, t.exit_ms, t.exit_code) for t in rr.trades()] == [w[:3] for w in want]
    assert rport.effects.count('stop') == port.effects.count('stop') + 1     # exactly one restored stop


def test_parity_cli_parses_and_renders_a_report(tmp_path):
    import json
    with pytest.raises(SystemExit) as e:
        P.main(['--help'])
    assert e.value.code == 0
    part = {'kind': 'single', 'costs': 'zero', 'symbols': {'BTCUSDT': P.pair([], [])}}
    part['symbols']['BTCUSDT']['unprotected'] = (0, 0)
    (tmp_path / 'p.json').write_text(json.dumps(part), encoding='utf-8')
    assert P.main(['report', '--parts', str(tmp_path / 'p.json'), '--out', str(tmp_path / 'r.md')]) == 0
    assert '| BTCUSDT | 0 | 0 |' in (tmp_path / 'r.md').read_text(encoding='utf-8')
