"""run_breadth_candidate.py --regime-refuse (research-only pre-entry refusal of counter-regime entries).

Synthetic only (the temp candle cache + temp git repo fixtures of test_breadth_candidate_runner / test_vwaprev_candidate_runner);
no real cache, store or data folder is read, no network, no app settings or credentials."""
import importlib.util
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_breadth_candidate as B                                                           # noqa: E402
import test_breadth_candidate_runner as BT_T                                                # noqa: E402
import test_vwaprev_candidate_runner as VT                                                  # noqa: E402

PRE_CHANGE = 'b52dd5e'          # master before the --regime-refuse option
H4 = 4 * 3_600_000
KEY = 'failed_breakout_scalp'


@pytest.fixture
def env(tmp_path):
    cache = VT.write_cache(str(tmp_path / 'candles'), VT.frames())
    c15 = BT_T.write15(str(tmp_path / 'c15'),
                       {s: BT_T.tape15(i + 7, 100.0 * (i + 1)) for i, s in enumerate(['BTCUSDT'] + VT.SYMS)})
    repo = VT.git_repo(str(tmp_path / 'repo'))
    return dict(cache=cache, c15=c15, repo=repo, tmp=tmp_path)


def go(mod, env, out, **kw):
    kw = dict(dict(cache=env['c15'], regime_cache=env['cache'], window=VT.WINDOW, symbols=VT.SYMS, repo=env['repo'],
                   key=KEY, side='long'), **kw)
    return mod.run(out=str(env['tmp'] / out), **kw)


def _strip(path):
    d = json.load(open(path))
    d.pop('runner_sha256')       # the runner file itself changed: its digest must differ
    d.pop('packet_sha256')       # covers runner_sha256
    return json.dumps(d, sort_keys=True, indent=1, allow_nan=False).encode()


def _old_runner(tmp):
    try:
        src = subprocess.run(['git', '-C', ROOT, 'show', f'{PRE_CHANGE}:tools/research/run_breadth_candidate.py'],
                             capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip(f'pre-change runner {PRE_CHANGE} not in this clone')
    p = tmp / 'rbc_pre_change.py'
    p.write_bytes(src)
    spec = importlib.util.spec_from_file_location('rbc_pre_change', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------------------------------------------ (1) default / none = the pre-change runner, byte for byte
@pytest.mark.parametrize('key,side,c15', [(KEY, 'long', True), (KEY, 'short', True), ('exp_range', 'long', False)])
def test_none_byte_identical_to_pre_change_runner(env, key, side, c15):
    old = _old_runner(env['tmp'])
    kw = dict(key=key, side=side) if c15 else dict(key=key, side=side, cache=env['cache'], regime_cache=None)
    a = go(old, env, 'old.json', **kw)
    b = go(B, env, 'new_default.json', **kw)
    c = go(B, env, 'new_none.json', regime_refuse='none', **kw)
    assert a['trades']
    ref = _strip(env['tmp'] / 'old.json')
    assert _strip(env['tmp'] / 'new_default.json') == ref and _strip(env['tmp'] / 'new_none.json') == ref
    assert 'regime_refuse' not in b and 'config' not in c


# ------------------------------------------------------------------ fake book for the mask unit tests
class FakeBook:
    def __init__(self, t_ms, sigs):
        self.t = pd.Series(pd.to_datetime(np.asarray(t_ms, dtype=np.int64), unit='ms'))
        self._sig = {('k', s, 'None'): g for s, g in sigs.items()}

    def raw_sig(self, key, sym, params=None):
        return self._sig[(key, sym, str(params))]


def sig(n, le=(), se=()):
    g = {c: np.zeros(n, bool) for c in ('le', 'se', 'lx', 'sx')}
    g['le'][list(le)] = True
    g['se'][list(se)] = True
    return g


# ------------------------------------------------------------------ (2) causality: only a CLOSED 4h candle decides
def test_regime_uses_only_closed_4h_candle():
    t4 = np.array([0, H4, 2 * H4], dtype=np.int64)
    c4 = np.array([10.0, 9.0, 11.0])          # candle@4h closes BELOW, the in-progress candle@8h closes ABOVE
    e4 = np.array([10.0, 10.0, 10.0])
    reg = (t4, c4, e4)
    q = H4 // 4                                 # 1h bars
    t = np.arange(8, 14) * q                    # 8h..13h: fills at 9h,10h,11h,12h,13h
    # every bar signals long; the fill of bar i is bar i + 1
    bk = FakeBook(t, {'X': sig(len(t), le=range(len(t)))})
    assert B.fill_regimes(bk, reg) == ['btc_below_ema200'] * 3 + ['btc_above_ema200'] * 2 + ['n/a']
    r = B.mask_regime_refuse(bk, 'k', 'long', ['X'], reg, int(t[0]), int(t[-1]) + q)
    le = bk.raw_sig('k', 'X')['le']
    # fills at 9h/10h/11h (candle@8h still in progress, it would say ABOVE) use candle@4h -> BELOW -> refused;
    # the fill at exactly 12h sees candle@8h closed -> ABOVE -> kept; the last bar has no fill -> n/a -> kept
    assert le.tolist() == [False, False, False, True, True, True]
    assert r['refused_signals'] == 3 and r['refused_by_symbol'] == {'X': 3}
    # an off-by-one that read the in-progress candle would keep bars 0..2
    assert B.regime_at(reg, 10 * q) == 'btc_below_ema200' and B.regime_at(reg, 12 * q) == 'btc_above_ema200'


# ------------------------------------------------------------------ (3) counter refuses only counter-regime entries
def test_mask_refuses_only_counter_regime():
    t4 = np.array([0, H4, 2 * H4, 3 * H4], dtype=np.int64)
    reg = (t4, np.array([9.0, 11.0, 9.0, 11.0]), np.full(4, 10.0))   # below, above, below, above
    t = np.arange(4, 17) * (H4 // 4)                                     # fills 5h..16h
    n = len(t)
    bk = FakeBook(t, {'X': sig(n, le=range(n), se=range(n))})
    rg = B.fill_regimes(bk, reg)
    lx0 = bk.raw_sig('k', 'X')['lx'].copy()
    B.mask_regime_refuse(bk, 'k', 'long', ['X'], reg, int(t[0]), int(t[-1]) + 1)
    g = bk.raw_sig('k', 'X')
    for i, lab in enumerate(rg):
        assert g['le'][i] == (lab != 'btc_below_ema200')
        assert g['se'][i] == (lab != 'btc_above_ema200')
    assert (g['lx'] == lx0).all()
    assert {'btc_below_ema200', 'btc_above_ema200'} <= set(rg)


def test_counter_run_refuses_and_substitutes(env):
    a = go(B, env, 'a.json')
    b = go(B, env, 'b.json', regime_refuse='counter')
    assert b['config'] == {'regime_refuse': 'counter'} and b['regime_refuse']['mode'] == 'counter'
    assert b['raw_entry_signals'] == a['raw_entry_signals']          # unmasked count, as in arm none
    assert b['regime_refuse']['refused_signals'] > 0
    assert any(t['regime'] == 'btc_below_ema200' for t in a['trades'])
    assert all(t['regime'] != 'btc_below_ema200' for t in b['trades'])
    s = go(B, env, 's.json', side='short', regime_refuse='counter')
    assert s['trades'] and all(t['regime'] != 'btc_above_ema200' for t in s['trades'])


def test_substitute_later_signal_enters(env, monkeypatch):
    """Arm none holds the trade of a refused signal; in counter the later (in-regime) signal on the same symbol enters."""
    b4 = VT.R.slice_frame(VT.R.read_cache(env['cache'], 'BTCUSDT')[0], VT.WARM_LO, VT.HI)
    reg = B.btc_regime_frame(b4)
    orig = B.BT.Book.raw_sig
    pick = {}

    def raw_sig(self, key, sym, params=None):          # the book's own cache semantics (the mask writes into it)
        k = (key, sym, str(params))
        if k in self._sig and self._sig[k].get('_crafted'):
            return self._sig[k]
        g = dict(orig(self, key, sym, params))
        g['_crafted'] = True
        if not pick:
            t = B._ms(self.t)
            lab = [B.regime_at(reg, int(t[i + 1])) for i in range(len(t) - 1)]
            i1 = next(i for i in range(len(lab) - 3) if t[i] >= VT.LO and lab[i] == 'btc_below_ema200'
                      and lab[i + 3] == 'btc_above_ema200')
            pick.update(i1=i1, i2=i1 + 3, t=t)
        n = len(g['le'])
        le = np.zeros(n, bool)
        if sym == 'ETHUSDT':
            le[[pick['i1'], pick['i2']]] = True
        g['le'], g['se'] = le, np.zeros(n, bool)
        g['lx'], g['sx'] = np.zeros(n, bool), np.zeros(n, bool)       # no signal exits: hold to time exit / stop
        if 'atr' in g:
            g['atr'] = np.asarray(g['atr'], float) * 20.0            # a wide stop: the first trade outlives the 2nd signal
        self._sig[k] = g
        return g

    monkeypatch.setattr(B.BT.Book, 'raw_sig', raw_sig)
    a = go(B, env, 'a.json', mgmt_mode='custom', custom=dict(max_bars=20))
    b = go(B, env, 'b.json', mgmt_mode='custom', custom=dict(max_bars=20), regime_refuse='counter')
    ta = [(t['symbol'], t['t_in']) for t in a['trades']]
    tb = [(t['symbol'], t['t_in']) for t in b['trades']]
    t1, t2 = (B._utc(pick['t'][pick[k] + 1]) for k in ('i1', 'i2'))
    assert ta == [('ETHUSDT', t1)]                 # arm none: the later signal is skipped (position still open)
    assert a['trades'][0]['t_out'] > t2
    assert tb == [('ETHUSDT', t2)]                 # counter: refused first signal, the substitute enters
    assert b['regime_refuse']['refused_signals'] == 1 and b['raw_entry_signals'] == a['raw_entry_signals'] == 2


def test_bad_mode_refused(env):
    with pytest.raises(B.RunnerError, match='regime-refuse'):
        go(B, env, 'x.json', regime_refuse='with')


# ------------------------------------------------------------------ (4) n/a never refuses
def test_na_never_refuses():
    t4 = np.array([10 * H4, 11 * H4], dtype=np.int64)
    reg_nan = (t4, np.array([5.0, 50.0]), np.array([np.nan, np.nan]))       # EMA not formed
    reg_late = (t4, np.array([5.0, 5.0]), np.array([10.0, 10.0]))          # no candle closed before the fills
    t = np.arange(0, 8) * (H4 // 4)
    for reg in (reg_nan, reg_late):
        bk = FakeBook(t, {'X': sig(len(t), le=range(len(t)), se=range(len(t)))})
        assert set(B.fill_regimes(bk, reg)) == {'n/a'}
        r = B.mask_regime_refuse(bk, 'k', 'long', ['X'], reg, 0, int(t[-1]) + 1)
        g = bk.raw_sig('k', 'X')
        assert r['refused_signals'] == 0 and g['le'].all() and g['se'].all()
