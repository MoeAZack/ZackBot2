"""tools/research/collect_klines_research.py - bounded public-kline research collector. Fake transport only: no network."""
import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import collect_klines_research as C                                                        # noqa: E402
import market_data as MD                                                                    # noqa: E402
import splits as SP                                                                         # noqa: E402

WINDOW = '2025-03-03T00:00:00Z/2025-03-24T00:00:00Z'
LO, HI = (SP.parse_utc(x) for x in WINDOW.split('/'))
STEP = C.INTERVALS['15m']


class Fake:
    """Serves 15m klines for any time (also after END, to prove the bounds hold); records every request."""
    def __init__(self, holes=(), fail_on=None):
        self.calls, self.holes, self.fail_on = [], set(holes), fail_on
        self.used_weight = None

    def get(self, path, p):
        self.calls.append(dict(p, path=path))
        if self.fail_on and p['symbol'] == self.fail_on:
            raise MD.Banned(120, 'HTTP 418')
        out, t = [], p['startTime'] - p['startTime'] % STEP
        while len(out) < p['limit'] and t <= p['endTime'] + 5 * STEP:           # misbehaving server: runs past endTime
            if t not in self.holes:
                out.append([t, '1', '2', '0.5', '1.5', '10', t + STEP - 1, '15', 1, '5', '7', '0'])
            t += STEP
        return out


def go(tmp_path, fake, syms=('BTCUSDT', 'ETHUSDT'), **kw):
    return C.collect(symbols=list(syms), interval='15m', window=WINDOW, out=str(tmp_path / 'cache'),
                     manifest=str(tmp_path / 'm.json'), transport=fake, sleep=lambda s: None, **kw)


def test_bounds_and_end_exclusive(tmp_path):
    f = Fake()
    m = go(tmp_path, f)
    assert all(c['endTime'] == HI - 1 and c['path'] == '/fapi/v1/klines' for c in f.calls)
    flo = LO - 220 * STEP
    for s in ('BTCUSDT', 'ETHUSDT'):
        r = m['files'][s]
        assert r['status'] == 'complete' and r['rows'] == (HI - flo) // STEP and r['last'] == C._utc(HI - STEP)
        assert r['dropped_at_or_after_end'] > 0                           # the fake leaked; nothing kept
    assert m['fetch']['start'] == C._utc(flo) and m['window']['end'] == '2025-03-24T00:00:00Z'
    assert json.load(open(tmp_path / 'm.json')) == json.loads(json.dumps(m))
    with pytest.raises(C.CollectError, match='never overwritten'):
        go(tmp_path, Fake())


def test_holdout_and_repo_refused(tmp_path):
    with pytest.raises(C.CollectError, match='sealed|holdout'):
        C.collect(symbols=['BTCUSDT'], interval='15m', window='2026-09-01T00:00:00Z/2026-09-28T04:00:00Z',
                  out=str(tmp_path), manifest=None, transport=Fake(), sleep=lambda s: None)
    with pytest.raises(C.CollectError, match='inside the git repo'):
        C.collect(symbols=['BTCUSDT'], interval='15m', window=WINDOW, out=os.path.join(ROOT, 'xx_cache'),
                  manifest=None, transport=Fake(), sleep=lambda s: None)
    assert not os.path.exists(os.path.join(ROOT, 'xx_cache'))


def test_gap_incomplete_never_filled(tmp_path):
    hole = LO + 10 * STEP
    m = go(tmp_path, Fake(holes={hole, hole + STEP}))
    r = m['files']['ETHUSDT']
    assert r['status'] == 'INCOMPLETE' and r['missing'] == 2 and r['missing_runs'][0][2] == 2
    t = np.loadtxt(tmp_path / 'cache' / 'ETHUSDT_15m.csv', delimiter=',', skiprows=1)[:, 0]
    assert hole not in t and 'ETHUSDT' in m['incomplete']


def test_ban_stops_and_marks_incomplete(tmp_path):
    m = go(tmp_path, Fake(fail_on='BTCUSDT'), syms=('BTCUSDT', 'ETHUSDT'))
    assert m['stopped'] and m['incomplete'] == ['BTCUSDT', 'ETHUSDT']
    assert not os.path.exists(tmp_path / 'cache' / 'ETHUSDT_15m.csv')
