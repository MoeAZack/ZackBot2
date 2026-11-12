import importlib

import pandas as pd
import pytest
import strategies as S


@pytest.fixture(autouse=True)
def restore_registry():
    old = S.STRATEGIES.copy()
    yield
    S.STRATEGIES.clear()
    S.STRATEGIES.update(old)


def module():
    import h5_hourly_long as h5
    return importlib.reload(h5)


def frame():
    d = pd.DataFrame(dict(o=[10.] * 26, h=[12.] * 26, l=[9.5] * 26,
                          c=[11.5] * 26, v=[10.] * 26, atr=[1.5] * 26,
                          e50=[11.] * 26, e200=[10.] * 26))
    d.loc[24, 'v'] = 20.
    return d


def test_import_is_default_off_and_research_safe():
    before = S.STRATEGIES.copy()
    h = module()
    assert S.STRATEGIES == before
    assert h.RESEARCH['research_only'] is True
    assert h.RESEARCH['fn'] is h.signal


@pytest.mark.parametrize('mode,base', [
    ('live', 'https://testnet.binancefuture.com'), ('PAPER', 'https://testnet.binancefuture.com'),
    ('paper', 'https://fapi.binance.com'), ('paper', 'http://testnet.binancefuture.com'),
    ('paper', 'https://testnet.binancefuture.com:443'),
    ('paper', 'https://testnet.binancefuture.com.evil.example'),
    ('paper', 'https://testnet.binancefuture.com@evil.example'),
    ('paper', 'https://testnet.binancefuture.com/path'),
    ('paper', 'https://testnet.binancefuture.com?x=1'),
    ('paper', 'https://testnet.binancefuture.com/'),
])
def test_refusal_never_mutates_registry(mode, base):
    h = module()
    before = S.STRATEGIES.copy()
    with pytest.raises(ValueError):
        h.register_for_paper(mode, base)
    assert S.STRATEGIES == before


@pytest.mark.parametrize('base', ['https://testnet.binancefuture.com', 'https://demo-fapi.binance.com'])
def test_explicit_registration_collision_and_management(base):
    h = module()
    S.STRATEGIES[h.KEY] = foreign = {'fn': object()}
    with pytest.raises(ValueError):
        h.register_for_paper('paper', base)
    assert S.STRATEGIES[h.KEY] is foreign
    del S.STRATEGIES[h.KEY]
    entry = h.register_for_paper('paper', base)
    assert h.register_for_paper('paper', base) is entry
    assert entry['research_only'] is False and h.RESEARCH['research_only'] is True
    assert entry['sides'] == 'long' and entry['tf'] == '1h' and entry['params'] == {}
    assert entry['mgmt'] == dict(stop_atr=3, tp_r=4, max_bars=48, be_r=0, trail_atr=0)
    # No optional future-dependent stop mover, including runner/giveback or PROT.
    assert not ({'runner', 'prot', 'ttp', 'pyramid', 'dca'} & entry['mgmt'].keys())


def test_boundaries_and_long_only():
    h = module()
    d = frame()
    result = h.signal(d, {}, {})
    assert result['le'].iloc[24] and not result['le'].iloc[23]
    assert not any(result[k].any() for k in ('se', 'lx', 'sx'))
    for column, value in [('atr', .499), ('v', 19.999), ('e50', 10.), ('c', 10.), ('h', 13.)]:
        changed = d.copy(); changed.loc[24, column] = value
        assert not h.signal(changed, {}, {})['le'].iloc[24], column
    # 3 ATR is included, as is a close exactly at the upper 20% boundary.
    d.loc[24, 'atr'] = .5
    assert h.signal(d, {}, {})['le'].iloc[24]
    d.loc[24, 'h'] = 12.
    assert h.signal(d, {}, {})['le'].iloc[24]


def test_prefix_causality_and_prior_volume_excludes_current():
    h = module(); d = frame()
    whole = h.signal(d, {}, {})
    for n in range(1, len(d) + 1):
        prefix = h.signal(d.iloc[:n], {}, {})
        for key in whole:
            pd.testing.assert_series_equal(prefix[key], whole[key].iloc[:n])
    d.loc[25, 'v'] = 1000000
    assert h.signal(d, {}, {})['le'].iloc[24]

