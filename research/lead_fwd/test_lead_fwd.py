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
FS = T0 + 90 * H            # forward fetch start in the merge unit tests (10 overlap bars with a 100-bar base)


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
                                's40_relative_weakness_4h', 's51_momentum_accel_4h', 's54_double_top_4h'}
    assert fz['leads']['s54_double_top_4h']['addendum'] == 'LEADFWD-01-A1-S54'
    assert fz['leads']['s54_double_top_4h']['t_fwd_ms'] >= fz['t_fwd_ms']
    for k, L in fz['leads'].items():
        c = json.load(open(os.path.join(ROOT, L['cases_file']), encoding='utf-8'))
        case = c['cases'][k]
        assert case['management'] == L['management'] and case['params'] == L['params'] and case['side'] == L['side']
        assert c['sources'][case['source']]['commit'] == L['commit']
    assert fz['forward_fetch']['start_ms'] < pd.Timestamp('2026-10-04 04:00', tz='UTC').value // 10 ** 6   # overlap with the base


def test_runner_code_is_pinned(tmp_path, monkeypatch):
    fz = RF.load_freeze(check=True)
    RF.verify_code(fz)
    p = tmp_path / 'sieve_v1_panel.py'; p.write_text('# changed\n', encoding='utf-8')
    monkeypatch.setitem(RF.CODE, 'sieve_v1_panel.py', str(p))
    with pytest.raises(SystemExit): RF.verify_code(fz)


def test_addendum_is_pinned_and_cannot_touch_existing_books(tmp_path, monkeypatch):
    fz = json.load(open(RF.FREEZE, encoding='utf-8'))
    A = json.load(open(os.path.join(HERE, 'freeze_a1_s54.json'), encoding='utf-8'))
    d = tmp_path / 'lf'; d.mkdir()
    def setup(addendum, pin=True):
        (d / 'freeze_a1_s54.json').write_text(json.dumps(addendum), encoding='utf-8')
        f = dict(fz, addenda=[dict(fz['addenda'][0], sha256=hashlib.sha256((d / 'freeze_a1_s54.json').read_bytes()).hexdigest() if pin else '0' * 64)])
        (d / 'freeze.json').write_text(json.dumps(f), encoding='utf-8')
        (d / 'freeze.sha256').write_text(hashlib.sha256((d / 'freeze.json').read_bytes()).hexdigest(), encoding='utf-8')
    monkeypatch.setattr(RF, 'HERE', str(d)); monkeypatch.setattr(RF, 'FREEZE', str(d / 'freeze.json'))
    monkeypatch.setattr(RF, 'INTEGRITY_AMENDMENTS', [])
    monkeypatch.setattr(RF, 'FREEZE_SHA_FILE', str(d / 'freeze.sha256'))
    setup(A); assert 's54_double_top_4h' in RF.load_freeze(check=True)['leads']
    setup(A, pin=False)
    with pytest.raises(SystemExit): RF.load_freeze(check=True)
    setup(dict(A, leads={'l24_breakaway_day_4h': A['leads']['s54_double_top_4h']}))
    with pytest.raises(SystemExit): RF.load_freeze(check=True)
    setup(dict(A, t_fwd_ms=A['t_fwd_ms'] - 86400000))
    with pytest.raises(SystemExit): RF.load_freeze(check=True)


def test_integrity_amendment_needs_its_exact_parents(tmp_path, monkeypatch):
    fz = RF.load_freeze(check=True)
    assert fz['integrity_amendments'] == ['A2'] and fz['code_sha256'] == RF.code_shas()
    f = tmp_path / 'freeze.json'; f.write_text(open(RF.FREEZE, encoding='utf-8').read() + '\n', encoding='utf-8')   # a different parent
    (tmp_path / 'freeze.sha256').write_text(hashlib.sha256(f.read_bytes()).hexdigest(), encoding='utf-8')
    monkeypatch.setattr(RF, 'FREEZE', str(f)); monkeypatch.setattr(RF, 'FREEZE_SHA_FILE', str(tmp_path / 'freeze.sha256'))
    with pytest.raises(SystemExit, match='parent freeze'): RF.load_freeze(check=True)


def test_tampered_freeze_is_refused(tmp_path, monkeypatch):
    p = tmp_path / 'freeze.sha256'; p.write_text('0' * 64 + '\n', encoding='utf-8')
    monkeypatch.setattr(RF, 'FREEZE_SHA_FILE', str(p))
    with pytest.raises(SystemExit): RF.load_freeze(check=True)


# ------------------------------------------------------------------ merge / coverage
def test_merge_ok_with_identical_overlap():
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 60)
    B = T0 + 150 * H
    m, info = RF.merge_coin(base, fwd, B, FS)
    assert info['status'] == 'OK' and info['overlap_bars'] == 10
    assert len(m) == 150 and (np.diff(m.t) == H).all() and m.t.iloc[-1] == B - H


def test_merge_mismatch_names_the_bar():
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 60)
    fwd.loc[3, 'c'] = 1.5
    m, info = RF.merge_coin(base, fwd, T0 + 150 * H, FS)
    assert m is None and info['status'] == 'MISMATCH' and info['column'] == 'c' and info['t_ms'] == T0 + 93 * H


@pytest.mark.parametrize('col', RF.COLS)
def test_merge_mismatch_any_column(col):
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 60)
    fwd.loc[0, col] = fwd.loc[0, col] + 1e-9
    assert RF.merge_coin(base, fwd, T0 + 150 * H, FS)[1]['status'] == 'MISMATCH'


def test_merge_cuts_at_first_forward_gap():
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 60, skip=(30,))
    m, info = RF.merge_coin(base, fwd, T0 + 150 * H, FS)
    assert info['status'] == 'CUT' and info['cut_after_ms'] == T0 + 119 * H and m.t.iloc[-1] == T0 + 119 * H
    assert (np.diff(m.t) == H).all()


def test_merge_forward_starting_after_a_gap_is_missing_overlap():
    base = frame(T0, 100); fwd = frame(T0 + 101 * H, 40)
    m, info = RF.merge_coin(base, fwd, T0 + 141 * H, FS)
    assert info['status'] == 'OVERLAP_MISSING' and m.t.iloc[-1] == T0 + 99 * H and info['first_missing_ms'] == FS


def test_merge_zero_overlap_is_never_ok():                 # Codex A2 witness: forward starts exactly one hour after the base
    base = frame(T0, 100); fwd = frame(T0 + 100 * H, 50)
    m, info = RF.merge_coin(base, fwd, T0 + 150 * H, T0 + 100 * H)
    assert info['status'] == 'OVERLAP_MISSING' and info['overlap_expected'] == 0 and len(m) == 100
    m, info = RF.merge_coin(base, fwd, T0 + 150 * H, FS)     # fetch claims to start at FS but holds none of the overlap
    assert info['status'] == 'OVERLAP_MISSING' and info['n_missing'] == 10


def test_merge_partial_overlap_is_missing():
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 60, skip=(4,))       # one overlap hour absent
    m, info = RF.merge_coin(base, fwd, T0 + 150 * H, FS)
    assert info['status'] == 'OVERLAP_MISSING' and info['first_missing_ms'] == T0 + 94 * H and info['n_missing'] == 1
    m, info = RF.merge_coin(base, frame(T0 + 95 * H, 55), T0 + 150 * H, FS)  # fetch began later than its declared start
    assert info['status'] == 'OVERLAP_MISSING' and info['n_missing'] == 5
    m, info = RF.merge_coin(base, frame(T0 + 90 * H, 60), T0 + 150 * H, FS)
    assert info['status'] == 'OK' and info['overlap_bars'] == info['overlap_expected'] == 10


def test_merge_cuts_early_end_and_no_fwd():
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 40)
    m, info = RF.merge_coin(base, fwd, T0 + 150 * H, FS)
    assert info['status'] == 'CUT' and info['reason'] == 'forward data ends before the boundary'
    m, info = RF.merge_coin(base, frame(T0 + 90 * H, 59), T0 + 150 * H, FS)     # one bar short of B-1h
    assert info['status'] == 'CUT' and info['cut_after_ms'] == T0 + 148 * H
    m, info = RF.merge_coin(base, None, T0 + 150 * H, FS)
    assert info['status'] == 'NO_FWD' and len(m) == 100
    m, info = RF.merge_coin(base, None, T0 + 50 * H, FS)          # never a bar at or after B, even without forward data
    assert info['status'] == 'NO_FWD' and m.t.max() == T0 + 49 * H


def test_merge_drops_bars_at_or_after_the_boundary():
    base = frame(T0, 100); fwd = frame(T0 + 90 * H, 80)
    B = T0 + 150 * H
    m, info = RF.merge_coin(base, fwd, B, FS)
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
    cov, probs = RF.build_data({'BTCUSDT': str(base / 'BTCUSDT_1h.csv')}, {'BTCUSDT': str(fwd / 'BTCUSDT_1h.csv')}, T0 + 150 * H, str(tmp_path / 'w'), FS)
    assert probs and 'BTCUSDT incomplete' in probs[0]


def test_cut_coin_makes_the_row_incomplete_but_btc_ok(tmp_path):
    base = tmp_path / 'b'; fwd = tmp_path / 'f'; base.mkdir(); fwd.mkdir()
    for s_ in ('BTCUSDT', 'AAVEUSDT'):
        frame(T0, 100).to_csv(base / f'{s_}_1h.csv', index=False, encoding='utf-8')
    frame(T0 + 90 * H, 60).to_csv(fwd / 'BTCUSDT_1h.csv', index=False, encoding='utf-8')
    frame(T0 + 90 * H, 60, skip=(40,)).to_csv(fwd / 'AAVEUSDT_1h.csv', index=False, encoding='utf-8')
    paths = lambda d: {s_: str(d / f'{s_}_1h.csv') for s_ in ('BTCUSDT', 'AAVEUSDT')}
    cov, probs = RF.build_data(paths(base), paths(fwd), T0 + 150 * H, str(tmp_path / 'w'), FS)
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


def test_boundary_is_the_last_settled_utc_midnight():
    assert FF.boundary(T0 + 5 * H) == T0 and FF.boundary(T0 + 600000) == T0
    assert FF.boundary(T0) == T0 - 86400000 and FF.boundary(T0 + 599999) == T0 - 86400000


# ------------------------------------------------------------------ parity / flags / open positions
def _row(closed, sigs, opn=(), B='2026-01-05 00:00'):
    return dict(boundary_utc=B, leads={'x': dict(primary=dict(closed=closed, open=list(opn)), signals=dict(rows=sigs))})


def test_parity_detects_changed_missing_added_trades_signals_and_opens():
    t1 = dict(sym='A', t_in='2026-01-02 04:00:00', t_out='2026-01-03 04:00:00', R15=1.0)
    t2 = dict(sym='B', t_in='2026-01-06 04:00:00', t_out='2026-01-07 04:00:00', R15=-1.0)
    t0 = dict(sym='C', t_in='2026-01-01 04:00:00', t_out='2026-01-01 12:00:00', R15=0.5)       # exited before the earlier boundary
    s1 = dict(sym='A', decision_bar_open_utc='2026-01-02 00:00:00', side='long', eligible=True)
    s0 = dict(sym='C', decision_bar_open_utc='2026-01-01 00:00:00', side='long', eligible=True)
    s2 = dict(sym='B', decision_bar_open_utc='2026-01-06 00:00:00', side='long', eligible=True)
    op = dict(sym='D', t_in='2026-01-04 04:00:00')
    prev = _row([t1], [s1], [op])
    assert RF.parity(prev, _row([t1, t2], [s1, s2], [op], '2026-01-08 00:00')) == []
    assert RF.parity(prev, _row([t1, t2, dict(op, t_out='2026-01-07 00:00:00', R15=2.0)], [s1, s2], [], '2026-01-08 00:00')) == []
    assert RF.parity(prev, _row([dict(t1, R15=0.9)], [s1], [op]))
    assert RF.parity(prev, _row([t2], [s1], [op]))
    assert RF.parity(prev, _row([t1], [], [op]))
    assert RF.parity(prev, _row([t1], [dict(s1, eligible=False)], [op]))
    assert RF.parity(prev, _row([t0, t1], [s1], [op]))                  # a new trade in the earlier past
    assert RF.parity(prev, _row([t1], [s0, s1], [op]))                  # a new signal in the earlier past
    assert RF.parity(prev, _row([t1], [s1], []))                        # an earlier open position vanished
    assert RF.parity(prev, dict(boundary_utc='2026-01-08 00:00', leads={}))   # a book vanished


def test_parity_rejects_changed_entry_facts_of_an_earlier_open_position():
    E = dict(side=-1, entry_ref=100.0, entry_fill=99.98, qty=2.0, risk_usd=8.0, stop_dist=4.0, initial_stop=103.98, target=91.98)
    op = dict(sym='D', t_in='2026-01-04 04:00:00', mark=101.0, bars_held=3, stale=False, **E)
    prev = _row([], [], [op])
    ok_open = dict(op, mark=95.0, bars_held=9)                                   # mark and age legitimately change
    ok_closed = dict(sym='D', t_in=op['t_in'], t_out='2026-01-06 08:00:00', why='tp', R15=1.9, R7=1.95, R23=1.85, **E)
    assert RF.parity(prev, _row([], [], [ok_open], '2026-01-08 00:00')) == []
    assert RF.parity(prev, _row([ok_closed], [], [], '2026-01-08 00:00')) == []
    for f, v in (('entry_fill', 200.0), ('entry_ref', 200.0), ('side', 1), ('qty', 3.0), ('risk_usd', 9.0), ('stop_dist', 5.0),
                 ('initial_stop', 110.0), ('target', 80.0)):
        assert RF.parity(prev, _row([], [], [dict(ok_open, **{f: v})], '2026-01-08 00:00')), f'OPEN->OPEN {f}'
        assert RF.parity(prev, _row([dict(ok_closed, **{f: v})], [], [], '2026-01-08 00:00')), f'OPEN->CLOSED {f}'


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
    o = RF.open_positions(tr, log, book, T, dict(tp_r=3.0, max_bars=60), 0.0)
    assert len(o) == 1 and o[0]['sym'] == 'A' and o[0]['mark'] == 11.0 and o[0]['stale'] is True
    assert o[0]['initial_stop'] == 8.0 and o[0]['target'] == 16.0 and o[0]['unrealized_R_before_costs'] == 0.5
    o = RF.open_positions(tr, log, book, T, dict(tp_r=3.0, max_bars=60), 0.01)     # stop/target from the slipped fill
    assert o[0]['entry_fill'] == pytest.approx(10.1) and o[0]['initial_stop'] == pytest.approx(8.1) and o[0]['target'] == pytest.approx(16.1)


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


def _fixture(tmp, Bs, drop=None):
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
            f = d[(d.t >= cut - 7 * 24 * H) & (d.t < B)]
            if drop and dst in drop and B in drop[dst]: f = f[f.t != drop[dst][B]]
            f.to_csv(fd / f'{dst}_1h.csv', index=False, encoding='utf-8')
    for B in Bs:
        fd = tmp / 'fwd' / str(B)
        json.dump(dict(boundary_ms=B, freeze_sha256=sha(RF.FREEZE), start_ms=int(cut - 7 * 24 * H),
                       symbols={s: dict(sha256=sha(fd / f'{s}_1h.csv')) for s in MAP.values()}),
                  open(fd / 'fetch_manifest.json', 'w', encoding='utf-8'))
    return base


def _run(tmp, base, B, rows='rows'):
    return subprocess.run([sys.executable, os.path.join(HERE, 'run_fwd.py'), str(tmp / 'fwd'), str(tmp / rows), '--boundary-ms', str(B),
                           '--fresh', str(base), '--skip-base-verify', '--orders', '2', '--t-fwd', '2026-01-01 00:00'],
                          capture_output=True, text=True, encoding='utf-8')


SAMPLE = os.path.exists(os.path.join(ROOT, 'data_long', '1h', 'BTCUSDT_1h.csv'))


@pytest.mark.skipif(not SAMPLE, reason='sample data missing')
def test_two_boundaries_replay_exactly_and_never_rewrite(tmp_path):
    B1 = pd.Timestamp('2026-03-15').value // 10 ** 6; B2 = pd.Timestamp('2026-10-04').value // 10 ** 6
    base = _fixture(tmp_path, [B1, B2])
    for B in (B1, B2):
        r = _run(tmp_path, base, B); assert r.returncode == 0, r.stderr[-3000:]
    r1 = json.load(open(tmp_path / 'rows' / 'test' / str(B1) / 'row.json', encoding='utf-8'))
    r2 = json.load(open(tmp_path / 'rows' / 'test' / str(B2) / 'row.json', encoding='utf-8'))
    assert r1['status'] == r2['status'] == 'COMPLETE' and r2['parity_with_boundary_ms'] == B1 and r2['kind'] == 'TEST'
    assert len(r2['leads']) == 7 and all('primary' in v for v in r2['leads'].values())
    n1 = sum(v['primary']['stats']['n'] for v in r1['leads'].values()); n2 = sum(v['primary']['stats']['n'] for v in r2['leads'].values())
    assert 0 < n1 < n2
    for v in r2['leads'].values():
        for t in v['primary']['closed']: assert t['t_in'] >= '2026-01-01'
        for s in v['signals']['rows']: assert s['decision_bar_open_utc'] >= '2026-01-01'
    assert RF.parity(r1, r2) == []
    assert not (tmp_path / 'rows' / '_work' / str(B2)).exists()
    r = _run(tmp_path, base, B1)
    assert r.returncode != 0 and 'never rewritten' in (r.stderr + r.stdout)


@pytest.mark.skipif(not SAMPLE, reason='sample data missing')
def test_cut_coin_row_is_incomplete_but_books_still_run(tmp_path):
    B1 = pd.Timestamp('2026-02-10').value // 10 ** 6; B2 = pd.Timestamp('2026-02-20').value // 10 ** 6
    gap = pd.Timestamp('2026-02-11 03:00').value // 10 ** 6
    base = _fixture(tmp_path, [B1, B2], drop={'1000SHIBUSDT': {B2: gap}})
    for B in (B1, B2):
        r = _run(tmp_path, base, B); assert r.returncode == 0, r.stderr[-3000:]
    r1 = json.load(open(tmp_path / 'rows' / 'test' / str(B1) / 'row.json', encoding='utf-8'))
    r2 = json.load(open(tmp_path / 'rows' / 'test' / str(B2) / 'row.json', encoding='utf-8'))
    assert r1['status'] == 'COMPLETE'
    assert r2['status'] in ('INCOMPLETE', 'NONPARITY') and r2['dropped_from_universe'] == ['1000SHIBUSDT']
    assert any(p.startswith('1000SHIBUSDT: missing prices') for p in r2['problems'])
    assert len(r2['leads']) == 7 and all('primary' in v for v in r2['leads'].values())
    for v in r2['leads'].values():
        assert all(t['sym'] != '1000SHIBUSDT' or t['t_out'] < r1['boundary_utc'] for t in v['primary']['closed'])
    held = any(t['sym'] == '1000SHIBUSDT' for v in r1['leads'].values() for t in v['primary']['closed'] + v['primary']['open'])
    assert (r2['status'] == 'NONPARITY') == held      # dropping the coin rewrites the past only if it had a trade
