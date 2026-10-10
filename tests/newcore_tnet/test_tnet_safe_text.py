"""Cowork 6068833115: the report's cleanup closes carry the executed quantity as text; an exception object there
(never expected) must not put its raw text into the report - type + ref only (newcore.venue.safe_text)."""
import json
from decimal import Decimal as D

from newcore.tnet.driver import _executed_text, run_scenario
from newcore.tnet.rspec import bundled
from newcore.tnet.targets import FakeTarget

SECRET = ('https://testnet.binancefuture.com/fapi/v1/order?signature=' + 'f' * 64 + ' X-MBX-APIKEY: ' + 'K' * 64)
FRAGMENTS = ('signature=', 'X-MBX-APIKEY', 'K' * 12, 'f' * 12, 'binancefuture')


class Boom(Exception):
    pass


def test_executed_text_is_typed():
    assert _executed_text(None) is None and _executed_text(D('0.5')) == '0.5' and _executed_text(3) == '3'
    t = _executed_text(Boom(SECRET))
    assert t.startswith('Boom (detail withheld, ref ') and not any(f in t for f in FRAGMENTS)
    assert _executed_text(SECRET) == '<str>' and _executed_text(True) == '<bool>'


def test_a_secret_shaped_exception_in_a_cleanup_close_never_reaches_the_report(monkeypatch):
    real = FakeTarget.cleanup

    def cleanup(self, *a, **kw):
        res = real(self, *a, **kw)
        res.closes.append(('SOLUSDT', 'LONG', D('1'), 'zbn1o-' + 'a' * 26, 'unknown', Boom(SECRET)))
        return res
    monkeypatch.setattr(FakeTarget, 'cleanup', cleanup)
    spec = next(s for s in bundled() if s['id'] == 'T01-long')
    r = run_scenario(spec, FakeTarget(), run_nonce='safe1')
    text = json.dumps(r.as_dict(), default=str)
    assert r.cleanup['closes'][-1][5].startswith('Boom (detail withheld, ref ')
    assert not any(f in text for f in FRAGMENTS)
