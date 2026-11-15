"""LEADFWD-01 tests. RESEARCH ONLY. Unit tests of the merge / coverage / parity / open-position / fetch helpers, plus one
integration test that runs every frozen lead on public sample data (data_long/1h, renamed onto universe symbols) at two
boundaries and checks that the later row reproduces the earlier one exactly (prefix invariance = no look-ahead, append-only).
  python -m pytest -q research/lead_fwd/test_lead_fwd.py
"""
import os, sys, json, re, hashlib, subprocess, types
import numpy as np, pandas as pd, pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import run_fwd as RF, fetch_fwd as FF

H = 3600000
T0 = 1767225600000          # 2026-01-01 00:00 UTC


def frame(t0, n, px=1.0, skip=()):
    t = [t0 + i * H for i in range(n) if i not in skip]
    return pd.DataFrame(dict(t=t, o=px, h=px + 1, l=px - 1, c=px, v=10.0, qv=100.0))


# ------------------------------------------------------------------ freeze
def test_freeze_matches_committed_sha_and_cases():
    fz = RF.load_freeze(check=True)
    assert fz['t_fwd_ms'] == int(pd.Timestamp(fz['t_fwd_utc'], tz='UTC').value // 10 ** 6)
    assert fz['t_fwd_ms'] % 86400000 == 0
    assert len(fz['universe']['fresh134']) == 134 and len(fz['universe']['extra31']) == 31
    assert not set(fz['universe']['fresh134']) & set(fz['universe']['extra31'])
    orig9 = {'ADAUSDT', 'AVAXUSDT', 'BNBUSDT', 'BTCUSDT', 'DOGEUSDT', 'ETHUSDT', 'LINKUSDT', 'SOLUSDT', 'XRPUSDT'}
    assert not orig9 & set(fz['universe']['fresh134'] + fz['universe']['extra31'])
    assert set(fz['leads']) == {'l24_breakaway_day_4h', 's26_weekly_close_breakdown_4h', 's36_max_effect_4h', 'l40_dual_momentum_4h',
                                's40_relative_weakness_4h', 's51_momentum_accel_4h'}
    for k, L in fz['leads'].items():
        c = json.load(open(os.path.join(ROOT, L['cases_file']), encoding='utf-8'))
        case = c['cases'][k]
        assert case['management'] == L['management'] and case['params'] == L['params'] and case['side'] == L['side']
        assert c['sources'][case['source']]['commit'] == L['commit']
    assert fz['forward_fetch']['start_ms'] < pd.Timestamp('2026-10-04 04:00', tz='UTC').value // 10 ** 6   # overlap with the base


def test_tampered_freeze_is_refused(tmp_path, monkeypatch):
    p = tmp_path / 'freeze.sha256'; p.write_text('0' * 64 + '\n', encoding='utf-8')
    monkeypatch.setattr(RF, 'FREEZE_SHA_FILE', str(p))
    with pytest.raises(SystemExit): RF.load_freeze(check=True)


# ------------------------------------------------------------------ merge / coverage
def test_merge_ok_with_identical_overlap():
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 60)
    B = T0 + 150 * H
    m, info = RF.merge_coin(base, fwd, B)
    assert info['status'] == 'OK' and info['overlap_bars'] == 10
    assert len(m) == 150 and (np.diff(m.t) == H).all() and m.t.iloc[-1] == B - H


def test_merge_mismatch_names_the_bar():
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 60)
    fwd.loc[3, 'c'] = 1.5
    m, info = RF.merge_coin(base, fwd, T0 + 150 * H)
    assert m is None and info['status'] == 'MISMATCH' and info['column'] == 'c' and info['t_ms'] == T0 + 93 * H


@pytest.mark.parametrize('col', RF.COLS)
def test_merge_mismatch_any_column(col):
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 60)
    fwd.loc[0, col] = fwd.loc[0, col] + 1e-9
    assert RF.merge_coin(base, fwd, T0 + 150 * H)[1]['status'] == 'MISMATCH'


def test_merge_cuts_at_first_forward_gap():
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 60, skip=(30,))
    m, info = RF.merge_coin(base, fwd, T0 + 150 * H)
    assert info['status'] == 'CUT' and info['cut_after_ms'] == T0 + 119 * H and m.t.iloc[-1] == T0 + 119 * H
    assert (np.diff(m.t) == H).all()


def test_merge_cuts_when_forward_starts_after_a_gap():
    base = frame(T0, 100); fwd = frame(T0 + 101 * H, 40)
    m, info = RF.merge_coin(base, fwd, T0 + 141 * H)
    assert info['status'] == 'CUT' and m.t.iloc[-1] == T0 + 99 * H


def test_merge_cuts_early_end_and_no_fwd():
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 40)
    m, info = RF.merge_coin(base, fwd, T0 + 150 * H)
    assert info['status'] == 'CUT' and info['reason'] == 'forward data ends before the boundary'
    m, info = RF.merge_coin(base, frame(T0 + 90 * H, 59), T0 + 150 * H)     # one bar short of B-1h
    assert info['status'] == 'CUT' and info['cut_after_ms'] == T0 + 148 * H
    m, info = RF.merge_coin(base, None, T0 + 150 * H)
    assert info['status'] == 'NO_FWD' and len(m) == 100


def test_merge_drops_bars_at_or_after_the_boundary():
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 80)
    B = T0 + 150 * H
    m, info = RF.merge_coin(base, fwd, B)
    assert info['status'] == 'OK' and m.t.max() == B - H


def test_read_1h_rejects_unsorted_and_duplicates(tmp_path):
    for bad in ([T0, T0 + H, T0 + H], [T0 + H, T0]):
        p = tmp_path / 'X_1h.csv'
        pd.DataFrame(dict(t=bad, o=1, h=1, l=1, c=1, v=1, qv=1)).to_csv(p, index=False, encoding='utf-8')
        with pytest.raises(ValueError): RF.read_1h(str(p))


def test_read_write_round_trip_is_exact(tmp_path):
    d = frame(T0, 5); d['c'] = [0.1, 1 / 3, 12345.678901234, 1e-8, 2.5]
    p = str(tmp_path / 'X_1h.csv'); RF.write_1h(d, p)
    back = RF.read_1h(p)
    assert (back.t.values == d.t.values).all() and (back.c.values == d.c.values).all()


def test_btc_incomplete_blocks_the_row(tmp_path):
    base = tmp_path / 'b'; fwd = tmp_path / 'f'; base.mkdir(); fwd.mkdir()
    frame(T0, 100).to_csv(base / 'BTCUSDT_1h.csv', index=False, encoding='utf-8')
    frame(T0 + 90 * H, 40).to_csv(fwd / 'BTCUSDT_1h.csv', index=False, encoding='utf-8')
    cov, probs = RF.build_data({'BTCUSDT': str(base / 'BTCUSDT_1h.csv')}, {'BTCUSDT': str(fwd / 'BTCUSDT_1h.csv')}, T0 + 150 * H, str(tmp_path / 'w'))
    assert probs and 'BTCUSDT incomplete' in probs[0]


def test_cut_coin_makes_the_row_incomplete_but_btc_ok(tmp_path):
    base = tmp_path / 'b'; fwd = tmp_path / 'f'; base.mkdir(); fwd.mkdir()
    for s_ in ('BTCUSDT', 'AAVEUSDT'):
        frame(T0, 100).to_csv(base / f'{s_}_1h.csv', index=False, encoding='utf-8')
    frame(T0 + 90 * H, 60).to_csv(fwd / 'BTCUSDT_1h.csv', index=False, encoding='utf-8')
    frame(T0 + 90 * H, 60, skip=(40,)).to_csv(fwd / 'AAVEUSDT_1h.csv', index=False, encoding='utf-8')
    paths = lambda d: {s_: str(d / f'{s_}_1h.csv') for s_ in ('BTCUSDT', 'AAVEUSDT')}
    cov, probs = RF.build_data(paths(base), paths(fwd), T0 + 150 * H, str(tmp_path / 'w'))
    assert cov['BTCUSDT']['status'] == 'OK' and cov['AAVEUSDT']['status'] == 'CUT'
    assert probs == [f'AAVEUSDT: missing prices (CUT, last contiguous bar {T0 + 129 * H})']


def test_parity_reference_is_the_latest_complete_row(tmp_path):
    for B, st in ((1, 'COMPLETE'), (2, 'INCOMPLETE'), (3, 'NONPARITY')):
        (tmp_path / str(B)).mkdir()
        json.dump(dict(boundary_ms=B, status=st), open(tmp_path / str(B) / 'row.json', 'w', encoding='utf-8'))
    assert RF.latest_row(str(tmp_path), 4)['boundary_ms'] == 1
    assert RF.latest_row(str(tmp_path), 1) is None


# ------------------------------------------------------------------ fetch helpers
def test_fetch_keeps_closed_bars_only():
    end = T0 + 3 * H
    k = [[T0 + i * H, '1', '2', '0', '1', '5', T0 + (i + 1) * H - 1, '7'] for i in range(4)]
    rows = FF.closed_rows(k, end)
    assert [r[0] for r in rows] == [T0, T0 + H, T0 + 2 * H]
    k[2][6] = end          # a bar whose close time reaches the boundary is not closed yet
    assert [r[0] for r in FF.closed_rows(k, end)] == [T0, T0 + H]


def test_boundary_is_the_last_utc_midnight():
    assert FF.boundary(T0 + 5 * H) == T0 and FF.boundary(T0) == T0 and FF.boundary(T0 - 1) == T0 - 86400000


# ------------------------------------------------------------------ parity / flags / open positions
def _row(closed, sigs):
    return dict(leads={'x': dict(primary=dict(closed=closed), signals=dict(rows=sigs))})


def test_parity_detects_changed_missing_trades_and_signals():
    t1 = dict(sym='A', t_in='2026-01-02 04:00:00', R15=1.0); t2 = dict(sym='B', t_in='2026-01-03 04:00:00', R15=-1.0)
    s1 = dict(sym='A', decision_bar_open_utc='2026-01-02 00:00:00', side='long', eligible=True)
    prev = _row([t1], [s1])
    assert RF.parity(prev, _row([t1, t2], [s1])) == []
    assert RF.parity(prev, _row([dict(t1, R15=0.9)], [s1]))
    assert RF.parity(prev, _row([t2], [s1]))
    assert RF.parity(prev, _row([t1], []))
    assert RF.parity(prev, _row([t1], [dict(s1, eligible=False)]))


def test_review_flags():
    fz = RF.load_freeze(check=False)
    mk = lambda n, m, m23=0.0: {'primary': {'stats': dict(n=n, meanR15=m, meanR23=m23)}}
    f = RF.review_flags(fz, {'a': mk(30, 0.1, 0.0), 'b': mk(30, 0.1, -0.01), 'c': mk(12, -0.31), 'd': mk(9, -2.0), 'e': mk(29, -0.5)})
    assert f == ['a: FORWARD_CONSISTENT at 30 closes', 'b: FORWARD_KILLED at 30 closes', 'c: EARLY_WARNING mean R15 -0.31 at 12 closes',
                 'e: EARLY_WARNING mean R15 -0.5 at 29 closes']


def test_open_positions_from_fill_log():
    T = pd.to_datetime(pd.Series([T0 + i * 4 * H for i in range(6)]), unit='ms').values
    T = pd.to_datetime(T)
    c = np.array([10, 10, 11, 12, 11, np.nan])
    book = types.SimpleNamespace(arr={'A': dict(c=c), 'B': dict(c=np.array([5.0] * 6))})
    log = [dict(kind='entry', sym='A', pos_i=1, side=1, ref=10.0, qty=4.0, stop_dist=2.0, risk=8.0, i=1),
           dict(kind='entry', sym='B', pos_i=0, side=-1, ref=5.0, qty=1.0, stop_dist=1.0, risk=8.0, i=0),
           dict(kind='exit', sym='B', pos_i=0, side=-1, ref=4.0, qty=1.0, stop_dist=1.0, risk=8.0, i=2)]
    tr = pd.DataFrame(dict(sym=['B'], i_in=[0]))
    o = RF.open_positions(tr, log, book, T, dict(tp_r=3.0, max_bars=60))
    assert len(o) == 1 and o[0]['sym'] == 'A' and o[0]['mark'] == 11.0 and o[0]['stale'] is True
    assert o[0]['initial_stop'] == 8.0 and o[0]['target'] == 16.0 and o[0]['unrealized_R_before_costs'] == 0.5


def test_every_text_open_names_utf8():
    for f in ('run_fwd.py', 'fetch_fwd.py', 'test_lead_fwd.py'):
        src = open(os.path.join(HERE, f), encoding='utf-8').read()
        for m in re.finditer(r'(?<![\w.])open\(([^\n]*)', src):
            arg = m.group(1)
            assert "encoding='utf-8'" in arg or "'rb'" in arg, f'{f}: {m.group(0)}'


def test_no_network_or_account_code_in_the_runner():
    src = open(os.path.join(HERE, 'run_fwd.py'), encoding='utf-8').read()
    for bad in ('urllib', 'requests', 'binance_client', 'api_key', 'LIVE_CONFIRM', 'engine'):
        assert bad not in src


# ------------------------------------------------------------------ integration: all six leads, two boundaries
MAP = {'BTCUSDT': 'BTCUSDT', 'ETHUSDT': 'AAVEUSDT', 'SOLUSDT': 'ARBUSDT', 'XRPUSDT': '1000SHIBUSDT', 'DOGEUSDT': 'ATOMUSDT',
       'LINKUSDT': 'CRVUSDT', 'AVAXUSDT': 'OPUSDT', 'BNBUSDT': 'TRXUSDT'}


def _fixture(tmp, Bs):
    sha = lambda p: hashlib.sha256(open(p, 'rb').read()).hexdigest()
    fz = RF.load_freeze(check=False)
    base = tmp / 'base'; base.mkdir()
    cut = pd.Timestamp('2026-01-01').value // 10 ** 6
    for src, dst in MAP.items():
        d = pd.read_csv(os.path.join(ROOT, 'data_long', '1h', f'{src}_1h.csv'))
        d['qv'] = d.v * d.c * 1000
        d['t'] = pd.to_datetime(d.t).values.astype('datetime64[ms]').astype('int64')
        d = d[d.t >= pd.Timestamp('2024-06-01').value // 10 ** 6]
        d[d.t < cut].to_csv(base / f'{dst}_1h.csv', index=False, encoding='utf-8')
        for B in Bs:
            fd = tmp / 'fwd' / str(B); fd.mkdir(parents=True, exist_ok=True)
            d[(d.t >= cut - 7 * 24 * H) & (d.t < B)].to_csv(fd / f'{dst}_1h.csv', index=False, encoding='utf-8')
    for B in Bs:
        fd = tmp / 'fwd' / str(B)
        json.dump(dict(boundary_ms=B, freeze_sha256=sha(RF.FREEZE), start_ms=fz['forward_fetch']['start_ms'],
                       symbols={s: dict(sha256=sha(fd / f'{s}_1h.csv')) for s in MAP.values()}),
                  open(fd / 'fetch_manifest.json', 'w', encoding='utf-8'))
    return base


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, 'data_long', '1h', 'BTCUSDT_1h.csv')), reason='sample data missing')
def test_two_boundaries_replay_exactly_and_never_rewrite(tmp_path):
    B1 = pd.Timestamp('2026-03-15').value // 10 ** 6; B2 = pd.Timestamp('2026-10-04').value // 10 ** 6
    base = _fixture(tmp_path, [B1, B2])
    run = lambda B: subprocess.run([sys.executable, os.path.join(HERE, 'run_fwd.py'), str(tmp_path / 'fwd'), str(tmp_path / 'rows'),
                                    '--boundary-ms', str(B), '--fresh', str(base), '--skip-base-verify', '--orders', '2',
                                    '--t-fwd', '2026-01-01 00:00'], capture_output=True, text=True, encoding='utf-8')
    for B in (B1, B2):
        r = run(B); assert r.returncode == 0, r.stderr[-3000:]
    r1 = json.load(open(tmp_path / 'rows' / 'test' / str(B1) / 'row.json', encoding='utf-8'))
    r2 = json.load(open(tmp_path / 'rows' / 'test' / str(B2) / 'row.json', encoding='utf-8'))
    assert r1['status'] == r2['status'] == 'COMPLETE' and r2['parity_with_boundary_ms'] == B1 and r2['kind'] == 'TEST'
    assert len(r2['leads']) == 6 and all('primary' in v for v in r2['leads'].values())
    n1 = sum(v['primary']['stats']['n'] for v in r1['leads'].values()); n2 = sum(v['primary']['stats']['n'] for v in r2['leads'].values())
    assert 0 < n1 < n2
    for v in r2['leads'].values():
        for t in v['primary']['closed']: assert t['t_in'] >= '2026-01-01'
        for s in v['signals']['rows']: assert s['decision_bar_open_utc'] >= '2026-01-01'
    assert RF.parity(r1, r2) == []
    r = run(B1)
    assert r.returncode != 0 and 'never rewritten' in (r.stderr + r.stdout)
