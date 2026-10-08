"""AUD-08 fault / instrument vocabulary of zb-golden/1 (schema._validate_faults / _validate_instruments) and the legacy guard
(adapters.base.check_unsupported). Mutations that must fail here: accept a string / float / bool time, drop the explicit seed,
accept a fault outside the market, let a legacy adapter run a faulted or filtered case."""
import copy, os, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from goldenlib import schema  # noqa: E402
from goldenlib.adapters import NotExpressible  # noqa: E402
from goldenlib.adapters.base import check_unsupported  # noqa: E402

T0 = 1704067200000                       # 2024-01-01T00:00:00Z
H4 = 4 * 3600 * 1000


def _base():
    c = copy.deepcopy(schema.load(os.path.join(schema.CASES_DIR, 'G-STOP-L-01.json')))
    for a in ('legacy_engine', 'legacy_backtest'):
        c['applies_to'][a] = dict(status='not_applicable', reason='x')
    c['known_divergences'] = []
    return c


GOOD = [dict(kind='exchange_outage', from_ms=T0 + 282 * H4, to_ms=T0 + 284 * H4, seed=None),
        dict(kind='restart', at_ms=T0 + 282 * H4 + 60000, down_ms=300000, seed=None),
        dict(kind='lost_response', order='entry', nth=1, truth='filled', seed=7)]


def test_clock_ms_is_integer_utc_milliseconds():
    assert schema.clock_ms({'clock': {'start': T0}}) == T0
    assert all(type(c['clock']['start']) is int for c in schema.load_all()), 'every case carries integer UTC ms'


@pytest.mark.parametrize('bad', ['2024-01-01T00:00:00Z', '2024-01-01T02:00:00+02:00', '2024-01-01T00:00:00', str(T0),
                                 float(T0), T0 + 0.5, True, None, -1, [T0], {'ms': T0}])
def test_clock_start_must_be_integer_utc_ms(bad):
    """AUD-08 golden clock-ms: an ISO string (or any non-integer / negative value) is rejected by the schema, and clock_ms
    never parses one."""
    c = _base()
    c['clock']['start'] = bad
    with pytest.raises(schema.CaseError, match='clock.start'):
        schema.validate(c)
    with pytest.raises(schema.CaseError):
        schema.clock_ms(c)


def test_market_starts_at_the_clock_ms():
    from goldenlib import market
    c = _base()
    raw = market.build(c)
    t = next(iter(raw.values()))['t']
    assert int(t.iloc[0].value // 10 ** 6) == T0 and t.iloc[0].tzinfo is None


@pytest.mark.parametrize('f', GOOD)
def test_valid_faults(f):
    c = _base()
    c['faults'] = [f]
    schema.validate(c)


BAD = [dict(GOOD[0], from_ms=str(T0 + 282 * H4)),                       # a string time
       dict(GOOD[0], from_ms=float(T0 + 282 * H4)),                     # a float time
       dict(GOOD[0], to_ms=True),                                       # a bool
       {k: v for k, v in GOOD[0].items() if k != 'seed'},               # seed not recorded
       dict(GOOD[0], seed='7'),
       dict(GOOD[0], jitter=1),                                         # unknown key
       dict(GOOD[0], kind='outage'),                                    # unknown kind
       dict(GOOD[0], from_ms=T0 - 1),                                   # before the market
       dict(GOOD[0], to_ms=T0 + 301 * H4),                              # after it
       dict(GOOD[0], to_ms=GOOD[0]['from_ms']),                         # empty window
       dict(GOOD[1], down_ms=-1),
       dict(GOOD[2], truth='maybe'), dict(GOOD[2], order='close'), dict(GOOD[2], nth=0), dict(GOOD[2], nth=True)]


@pytest.mark.parametrize('f', BAD)
def test_invalid_faults(f):
    c = _base()
    c['faults'] = [f]
    with pytest.raises(schema.CaseError):
        schema.validate(c)


def test_instruments():
    c = _base()
    c['instruments'] = {'SOLUSDT': dict(step='5', min_qty='5', min_notional='5', tick='0.01')}
    schema.validate(c)
    for bad in ({'XRPUSDT': dict(step='5', min_qty='5', min_notional='5', tick='0.01')},
                {'SOLUSDT': dict(step='0', min_qty='5', min_notional='5', tick='0.01')},
                {'SOLUSDT': dict(step='NaN', min_qty='5', min_notional='5', tick='0.01')},
                {'SOLUSDT': dict(step='5', min_qty='5', min_notional='5')},
                {'SOLUSDT': dict(step='5', min_qty='5', min_notional='5', tick='0.01', lot='1')}, {}):
        c['instruments'] = bad
        with pytest.raises(schema.CaseError):
            schema.validate(c)


def test_legacy_adapters_refuse_faults_and_instruments():
    check_unsupported(_base())
    for f in GOOD:
        c = _base(); c['faults'] = [f]
        with pytest.raises(NotExpressible):
            check_unsupported(c)
    c = _base(); c['instruments'] = {'SOLUSDT': dict(step='5', min_qty='5', min_notional='5', tick='0.01')}
    with pytest.raises(NotExpressible):
        check_unsupported(c)
    for name in ('legacy_backtest.py', 'legacy_engine.py'):          # both adapters call the guard before running anything
        src = open(os.path.join(HERE, 'goldenlib', 'adapters', name), encoding='utf-8').read()
        assert src.index('check_unsupported(case)') < src.index('market.build(case)'), name
