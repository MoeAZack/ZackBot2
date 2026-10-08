"""nc-s1-slice facd4b6 (F3 degraded fallback, guard known-exposure): the Runner reads a current mark through
`mark_price(symbol)` (value[0] = the Decimal mark). On the testnet target the harness provides it from
TestnetAccountReader (MarkedReads); any degraded protection the Runner used is surfaced per scenario."""
import json
from decimal import Decimal as D

from fake_binance import FakeBinance, ok

from newcore.ports import venue as P
from newcore.tnet.driver import run_scenario
from newcore.tnet.rspec import bundled
from newcore.tnet.targets import FakeTarget

from tnet_support import World


class WithMark(FakeBinance):
    def _get_fapi_v1_premiumIndex(self, q):
        return ok({'symbol': q['symbol'], 'markPrice': '221.5', 'indexPrice': '221.4', 'estimatedSettlePrice': '0',
                   'lastFundingRate': '0', 'interestRate': '0', 'nextFundingTime': self.now + 1, 'time': self.now})


def test_the_testnet_target_gives_the_runner_a_decimal_mark():
    w = World()
    w.fb = WithMark()
    w.fb.now = w.t
    t = w.target()
    r = t.reads.mark_price('SOLUSDT')
    assert r.kind is P.ReadKind.OK and r.value == (D('221.5'),)
    assert t.reads.equity().kind is P.ReadKind.OK                       # the shim's reads still pass through


def test_an_unreadable_mark_stays_typed():
    w = World()                                                          # the fake has no premiumIndex endpoint
    r = w.target().reads.mark_price('SOLUSDT')
    assert r.kind is not P.ReadKind.OK


def test_the_runner_reads_the_mark_through_the_target(monkeypatch):
    w = World()
    w.fb = WithMark()
    w.fb.now = w.t
    from newcore.runner import runner as RR
    seen = []
    real = RR.Runner.cycle

    def cycle(self, now_ms, *, decide=True):
        seen.append(self._current_mark('SOLUSDT'))
        return real(self, now_ms, decide=decide)
    monkeypatch.setattr(RR.Runner, 'cycle', cycle)
    s = next(x for x in bundled() if x['id'] == 'T10-floor')
    r = run_scenario(s, w.target(), run_nonce='mk', monotonic=w.monotonic)
    assert r.verdict == 'PASS' and seen and all(m == D('221.5') for m in seen)


def test_degraded_protection_is_surfaced_in_the_result_and_the_cli_line(monkeypatch):
    from newcore.runner import runner as RR
    real = RR.Runner.cycle

    def cycle(self, now_ms, *, decide=True):
        out = real(self, now_ms, decide=decide)
        self.degraded[('SOLUSDT', 'LONG')] = 'fallback_stop'
        return out
    monkeypatch.setattr(RR.Runner, 'cycle', cycle)
    s = next(x for x in bundled() if x['id'] == 'T10-floor')
    r = run_scenario(s, FakeTarget(), run_nonce='dg')
    assert r.degraded == {'SOLUSDT:LONG': 'fallback_stop'} and r.as_dict()['degraded'] == r.degraded
    import io
    import importlib.util
    import os
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sp = importlib.util.spec_from_file_location('cli_dg', os.path.join(repo, 'tools', 'newcore_tnet_runner.py'))
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    out = io.StringIO()
    mod._print(r, out, False)
    assert 'degraded=SOLUSDT:LONG:fallback_stop' in out.getvalue()
    assert json.dumps(r.as_dict())
