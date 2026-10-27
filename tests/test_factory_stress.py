"""tools/research/factory_stress.py - concentration / drawdown / cost stress over breadth-candidate packets.

Synthetic only: packets are built here in the runner's schema (run_breadth_candidate.run) with packet_sha256 computed the
runner's way; one conformance test feeds a packet the real runner wrote from the synthetic candle fixtures of
test_vwaprev_candidate_runner. No real packet, cache or data folder is read."""
import copy
import hashlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import factory_stress as FS                                                                 # noqa: E402

RISK = 10.0


def T(sym, t_in, t_out, R, risk=RISK, fees=0.1, funding=0.0, **kw):
    d = dict(symbol=sym, side=1, t_in=t_in, t_out=t_out, pnl=R * risk, R=R, why='tp', gross=R * risk + fees + funding,
             fees=fees, funding=funding, mfe_r=max(R, 0.0), mae_r=0.0, giveback_r=0.0, regime='btc_above_ema200')
    d.update(kw)
    return d


def seal(p):
    p = {k: v for k, v in p.items() if k != 'packet_sha256'}
    p['packet_sha256'] = hashlib.sha256(FS.canon(p)).hexdigest()
    return p


def mk(trades, status='ok', key='breakout_pyramid', side='long', costs=None, max_dd_r=None, start='2025-01-01T00:00:00Z'):
    trades = copy.deepcopy(trades)
    rs = sum(t['R'] for t in trades)
    p = {
        'format': FS.FORMAT_IN, 'evidence_class': 'candidate-runtime', 'non_promotable': True, 'purpose': 'synthetic',
        'status': status, 'raw_entry_signals': len(trades), 'commit': 'f' * 40, 'tree': 'clean',
        'runner_sha256': {'run_breadth_candidate.py': '0' * 64},
        'window': {'start': start, 'end': '2026-01-01T00:00:00Z', 'tf': '4h'}, 'warmup': {'bars': 220},
        'universe': {'run': sorted({t['symbol'] for t in trades})}, 'cache': {}, 'nan_qv': {},
        'strategy': {'key': key, 'mgmt': {'pyramid': {'n': 2, 'step_r': 1.0}}}, 'side': side, 'sleeve': {'sides': side},
        'account': {'start': 500.0}, 'engine': {}, 'definitions': {}, 'bust': {'busted': False},
        'costs': costs if costs is not None else {'fee_taker': 0.0005, 'slip': 0.0002, 'fund_per_bar': 5e-05,
                                                  'entry_order': 'market (taker)'},
        'stats': {'aggregate': {'trades': len(trades), 'r_sum': round(rs, 6), 'max_dd_r': max_dd_r}},
        'trades': trades}
    return seal(p)


def run(pkts, **kw):
    return FS.analyse(pkts, [f'S{i + 1}' for i in range(len(pkts))], **dict(dict(n_boot=200), **kw))


# ------------------------------------------------------------------ 1. concentration (hand-computed)
CONC = [T('A', '2025-01-09T00:00:00Z', '2025-01-10T00:00:00Z', 3.0),
        T('A', '2025-02-04T00:00:00Z', '2025-02-05T00:00:00Z', 1.0),
        T('B', '2025-02-19T00:00:00Z', '2025-02-20T00:00:00Z', 2.0),
        T('C', '2025-03-31T00:00:00Z', '2025-04-01T00:00:00Z', 1.0),
        T('D', '2025-04-14T00:00:00Z', '2025-04-15T00:00:00Z', -2.0)]


def test_concentration_hand_values():
    c = run([mk(CONC)])['pooled']['concentration']
    assert c['net_r'] == 5.0 and c['trades'] == 5
    assert c['r_by_symbol'] == {'A': 4.0, 'B': 2.0, 'C': 1.0, 'D': -2.0}
    assert c['top1_symbol'] == 'A' and c['top1_share'] == 0.8 and c['top3_share'] == 1.4
    assert c['hhi_positive'] == round(21 / 49, 6)
    assert c['pf_r'] == 3.5
    lv = c['leave_top_k_out']
    assert lv['1'] == dict(dropped=['A'], trades=3, net_r=1.0, pf_r=1.5) == c['without_top1']
    assert lv['2'] == dict(dropped=['A', 'B'], trades=2, net_r=-1.0, pf_r=0.5)
    assert lv['3'] == dict(dropped=['A', 'B', 'C'], trades=1, net_r=-2.0, pf_r=0.0)
    m, q = c['periods']['month'], c['periods']['quarter']
    assert m['r_by_period'] == {'2025-01': 3.0, '2025-02': 3.0, '2025-04': -1.0}
    assert m['top_period'] == '2025-01' and m['top_period_share'] == 0.6        # tie 3 vs 3 -> name order
    assert q['r_by_period'] == {'2025-Q1': 6.0, '2025-Q2': -1.0} and q['top_period_share'] == 1.2


def test_concentration_undefined_when_net_nonpositive():
    tr = [T('A', '2025-01-01T00:00:00Z', '2025-01-02T00:00:00Z', 1.0),
          T('B', '2025-01-01T00:00:00Z', '2025-01-02T00:00:00Z', -1.0)]
    rep = run([mk(tr)])['pooled']
    assert rep['concentration']['top1_share'] is None
    assert rep['flags']['CONCENTRATED']['raised'] is None


# ------------------------------------------------------------------ 2. drawdown: ordering, ties, streak, bootstrap
def test_dd_tie_broken_by_entry_time_not_list_order():
    tr = [T('A', '2025-01-01T00:00:00Z', '2025-01-02T00:00:00Z', 1.0),
          T('Y', '2025-01-03T08:00:00Z', '2025-01-05T00:00:00Z', 5.0),   # listed first, entered later
          T('X', '2025-01-03T00:00:00Z', '2025-01-05T00:00:00Z', -3.0),
          T('B', '2025-01-06T00:00:00Z', '2025-01-07T00:00:00Z', -1.0),
          T('C', '2025-01-06T00:00:00Z', '2025-01-08T00:00:00Z', -1.0)]
    # exit order: +1, X -3, Y +5, -1, -1 -> cum 1, -2, 3, 2, 1 -> max DD 3 (Y before X would give 5)
    rep = run([mk(tr, max_dd_r=3.0)])
    assert rep['pooled']['drawdown']['max_dd_r'] == 3.0
    assert rep['packets'][0]['runner_crosscheck'] == dict(runner_max_dd_r=3.0, match=True)


def test_dd_full_tie_keeps_packet_trade_order():
    t = ('2025-01-03T00:00:00Z', '2025-01-05T00:00:00Z')
    z = T('C', '2025-01-06T00:00:00Z', '2025-01-07T00:00:00Z', -1.0)
    a = run([mk([T('A', *t, -3.0), T('B', *t, 5.0), z])])['pooled']['drawdown']['max_dd_r']
    b = run([mk([T('B', *t, 5.0), T('A', *t, -3.0), z])])['pooled']['drawdown']['max_dd_r']
    assert a == 3.0     # -3, +2, +1 -> DD 3
    assert b == 4.0     # +5, +2, +1 -> DD 4


def test_dd_counts_from_zero_and_streak():
    days = ['2025-01-0%d' % d for d in range(1, 9)]
    rs = [-2.0, 1.0, -1.0, -1.0, 0.0, -1.0, -1.0, -1.0]
    tr = [T('A', d + 'T00:00:00Z', d + 'T04:00:00Z', r) for d, r in zip(days, rs)]
    d = run([mk(tr)])['pooled']['drawdown']
    # cum -2,-1,-2,-3,-3,-4,-5,-6 ; peak stays 0 -> DD 6
    assert d['max_dd_r'] == 6.0
    assert d['longest_losing_streak'] == 3          # R == 0 breaks a streak
    assert run([mk(tr[:1])])['pooled']['drawdown']['max_dd_r'] == 2.0


MIXED = [T('ABCD'[i % 4], f'2025-0{1 + i // 9}-{1 + i % 9:02d}T00:00:00Z', f'2025-0{1 + i // 9}-{1 + i % 9:02d}T08:00:00Z',
           r) for i, r in enumerate([2.0, -1.0, -1.0, 3.0, -1.0, -1.0, -1.0, 2.5, -1.0, -1.0, 1.5, -1.0])]


def test_bootstrap_deterministic_and_seed_sensitive():
    p = mk(MIXED)
    a = run([p], seed=7, n_boot=500)['pooled']['drawdown']['bootstrap']
    b = run([p], seed=7, n_boot=500)['pooled']['drawdown']['bootstrap']
    c = run([p], seed=8, n_boot=500)['pooled']['drawdown']['bootstrap']
    assert a == b and a['seed'] == 7 and a['n'] == 500
    assert (a['dd_p50'], a['dd_p95'], a['p_net_le_0']) != (c['dd_p50'], c['dd_p95'], c['p_net_le_0'])
    assert 0.0 < a['dd_p50'] <= a['dd_p95'] <= len(MIXED) * 1.0      # every loss is -1R, n = 12 draws per path
    assert 0.0 < a['p_net_le_0'] < 1.0


def test_bootstrap_all_wins():
    tr = [T('A', f'2025-01-0{d}T00:00:00Z', f'2025-01-0{d}T04:00:00Z', 1.0) for d in range(1, 6)]
    b = run([mk(tr)])['pooled']['drawdown']['bootstrap']
    assert b['dd_p50'] == b['dd_p95'] == 0.0 and b['p_net_le_0'] == 0.0


# ------------------------------------------------------------------ 3. cost stress
COST = [T('A', '2025-01-01T00:00:00Z', '2025-01-02T00:00:00Z', 2.0, risk=10.0, fees=1.0, funding=0.5),
        T('B', '2025-01-03T00:00:00Z', '2025-01-04T00:00:00Z', -1.0, risk=20.0, fees=2.0, funding=-0.4)]


def test_cost_stress_hand_values():
    s = run([mk(COST)], slip_bps=2.0)['pooled']['cost_stress']
    assert s['base_net_r'] == 1.0
    assert s['fees_x1.5'] == dict(status='OK', net_r=0.9)        # -0.5*1/10 - 0.5*2/20
    assert s['fees_x2'] == dict(status='OK', net_r=0.8)
    assert s['funding_x2'] == dict(status='OK', net_r=0.97)      # -0.5/10 + 0.4/20
    assert s['slip_plus_2bps'] == dict(status='OK', net_r=0.92)  # 2bps x (fees/0.0005) / risk: 0.4/10 + 0.8/20


def test_cost_stress_unavailable_missing_fields():
    nofee = [dict(t) for t in COST]
    for t in nofee:
        del t['fees']
    s = run([mk(nofee)])['pooled']
    cs = s['cost_stress']
    assert cs['fees_x2']['status'] == cs['fees_x1.5']['status'] == cs['slip_plus_2bps']['status'] == 'UNAVAILABLE'
    assert 'fees' in cs['fees_x2']['reason'] and cs['fees_x2']['net_r'] is None
    assert cs['funding_x2']['status'] == 'OK'
    assert s['flags']['COST_FRAGILE']['raised'] is None
    nofund = [dict(t, funding=None) for t in COST]
    cs = run([mk(nofund)])['pooled']['cost_stress']
    assert cs['funding_x2']['status'] == 'UNAVAILABLE' and cs['fees_x2']['status'] == 'OK'
    cs = run([mk(COST, costs={'slip': 0.0002})])['pooled']['cost_stress']
    assert cs['slip_plus_2bps']['status'] == 'UNAVAILABLE' and 'fee_taker' in cs['slip_plus_2bps']['reason']
    assert cs['fees_x2']['status'] == 'OK'


def test_cost_stress_unavailable_when_risk_not_derivable():
    tr = COST + [T('C', '2025-01-05T00:00:00Z', '2025-01-06T00:00:00Z', 0.0)]
    cs = run([mk(tr)])['pooled']['cost_stress']
    assert all(cs[k]['status'] == 'UNAVAILABLE' for k in ('fees_x1.5', 'fees_x2', 'funding_x2', 'slip_plus_2bps'))
    assert 'risk_usd' in cs['fees_x2']['reason']


def test_pyramid_attribution_unavailable_for_runner_schema():
    pa = run([mk(COST)])['pooled']['pyramid_attribution']
    assert pa['status'] == 'UNAVAILABLE' and 'add-leg' in pa['reason']


# ------------------------------------------------------------------ 5. flags: threshold boundaries
def _sec(top1=0.1, wo=1.0, p=0.0, f2=1.0):
    return dict(concentration=dict(top1_share=top1, without_top1=dict(net_r=wo), trades=3),
                drawdown=dict(bootstrap=dict(p_net_le_0=p)), cost_stress=dict(fees_x2=dict(net_r=f2)))


@pytest.mark.parametrize('flag,kw,raised', [
    ('CONCENTRATED', dict(top1=0.40), True), ('CONCENTRATED', dict(top1=0.399999), False),
    ('DEPENDENT', dict(wo=0.0), True), ('DEPENDENT', dict(wo=1e-6), False),
    ('FRAGILE', dict(p=0.10), True), ('FRAGILE', dict(p=0.099999), False),
    ('COST_FRAGILE', dict(f2=0.0), True), ('COST_FRAGILE', dict(f2=1e-6), False)])
def test_flag_boundaries(flag, kw, raised):
    f = FS.flags(_sec(**kw))
    assert f[flag]['raised'] is raised
    assert sum(bool(v['raised']) for v in f.values()) == int(raised)


def test_flags_end_to_end_concentrated_at_exactly_040():
    tr = [T('A', '2025-01-01T00:00:00Z', '2025-01-02T00:00:00Z', 2.0),
          T('B', '2025-01-01T00:00:00Z', '2025-01-02T00:00:00Z', 1.5),
          T('C', '2025-01-01T00:00:00Z', '2025-01-02T00:00:00Z', 1.5)]
    f = run([mk(tr)])['pooled']['flags']
    assert f['CONCENTRATED']['value'] == 0.4 and f['CONCENTRATED']['raised'] is True
    assert f['DEPENDENT']['raised'] is False and f['COST_FRAGILE']['raised'] is False
    f = run([mk(CONC)])['pooled']['flags']
    assert f['CONCENTRATED']['raised'] is True and f['DEPENDENT']['raised'] is False   # w/o A: +1.0


# ------------------------------------------------------------------ fail closed
def test_canon_matches_runner():
    import run_vwaprev_candidate as VC
    obj = {'b': [1, 2.5, None], 'a': {'z': 'x', 'y': True}}
    assert FS.canon(obj) == VC._canon(obj)


def test_bad_sha_refused():
    p = mk(COST)
    p['trades'][0]['R'] = 2.5
    p['stats']['aggregate']['r_sum'] = 1.5
    with pytest.raises(FS.StressError, match='sha256 mismatch'):
        run([p])


def test_incomplete_status_refused():
    with pytest.raises(FS.StressError, match='status'):
        run([mk(COST, status='not emitted')])


def test_nan_refused(tmp_path):
    p = mk(COST)
    p['trades'][0]['R'] = float('nan')
    with pytest.raises(FS.StressError, match='non-finite'):
        run([p])
    raw = json.dumps(mk(COST)).replace('"funding": 0.5', '"funding": NaN')
    assert 'NaN' in raw
    (tmp_path / 'n.json').write_text(raw)
    with pytest.raises(FS.StressError, match='non-finite'):
        FS.load(str(tmp_path / 'n.json'))


@pytest.mark.parametrize('where', ['top', 'trade', 'null_r', 'stats'])
def test_missing_field_refused(where):
    p = mk(COST)
    if where == 'top':
        del p['costs']
    elif where == 'trade':
        del p['trades'][1]['t_out']
    elif where == 'null_r':
        p['trades'][1]['R'] = None
    else:
        p['stats']['aggregate']['trades'] = 3
    with pytest.raises(FS.StressError):
        run([seal(p)])


def test_mixed_or_duplicate_packets_refused():
    with pytest.raises(FS.StressError, match='mix'):
        run([mk(COST), mk(CONC, key='ema_mom')])
    with pytest.raises(FS.StressError, match='twice'):
        run([mk(COST), mk(COST)])


# ------------------------------------------------------------------ pooled vs per packet
def test_pooled_consistent_with_packets():
    s1 = mk(CONC, start='2025-01-01T00:00:00Z')
    s2 = mk(COST, start='2025-06-01T00:00:00Z')
    s3 = mk(MIXED, start='2025-09-01T00:00:00Z')
    rep = run([s1, s2, s3])
    per, pool = rep['packets'], rep['pooled']
    assert pool['concentration']['trades'] == sum(p['concentration']['trades'] for p in per)
    assert pool['concentration']['net_r'] == pytest.approx(sum(p['concentration']['net_r'] for p in per), abs=1e-6)
    syms = {}
    for p in per:
        for k, v in p['concentration']['r_by_symbol'].items():
            syms[k] = syms.get(k, 0.0) + v
    assert pool['concentration']['r_by_symbol'] == pytest.approx(syms, abs=1e-6)
    for k in ('fees_x1.5', 'fees_x2', 'funding_x2', 'slip_plus_2bps'):
        d_pool = pool['cost_stress'][k]['net_r'] - pool['cost_stress']['base_net_r']
        d_per = sum(p['cost_stress'][k]['net_r'] - p['cost_stress']['base_net_r'] for p in per)
        assert d_pool == pytest.approx(d_per, abs=1e-5)
    # one packet: the pooled section is that packet's section
    one = run([s2])
    for k in ('concentration', 'drawdown', 'cost_stress', 'flags', 'pyramid_attribution'):
        assert one['pooled'][k] == one['packets'][0][k]


# ------------------------------------------------------------------ CLI
def test_cli_deterministic_and_never_overwrites(tmp_path, capsys):
    paths = []
    for i, (tr, st) in enumerate([(CONC, '2025-01-01T00:00:00Z'), (MIXED, '2025-09-01T00:00:00Z')]):
        f = tmp_path / f's{i}.json'
        f.write_text(json.dumps(mk(tr, start=st), sort_keys=True, indent=1))
        paths.append(str(f))
    args = sum((['--packet', p] for p in paths), []) + ['--boot', '300', '--seed', '5', '--slip-bps', '3']
    assert FS.main(args + ['--out', str(tmp_path / 'r1.json')]) == 0
    assert 'POOLED' in capsys.readouterr().out
    assert FS.main(args + ['--out', str(tmp_path / 'r2.json')]) == 0
    assert (tmp_path / 'r1.json').read_bytes() == (tmp_path / 'r2.json').read_bytes()
    rep = json.loads((tmp_path / 'r1.json').read_text())
    assert rep['params']['seed'] == 5 and rep['params']['boot'] == 300 and 'slip_plus_3bps' in rep['pooled']['cost_stress']
    assert FS.main(args + ['--out', str(tmp_path / 'r1.json')]) == 2
    bad = json.loads(open(paths[0]).read())
    bad['status'] = 'not emitted'
    (tmp_path / 'bad.json').write_text(json.dumps(bad))
    assert FS.main(['--packet', str(tmp_path / 'bad.json'), '--out', str(tmp_path / 'r3.json')]) == 2
    assert not (tmp_path / 'r3.json').exists()


# ------------------------------------------------------------------ conformance with the real runner (synthetic candles)
def test_real_runner_packet_conforms(tmp_path):
    import run_breadth_candidate as B
    import test_vwaprev_candidate_runner as VT
    cache = VT.write_cache(str(tmp_path / 'candles'), VT.frames())
    repo = VT.git_repo(str(tmp_path / 'repo'))
    out = tmp_path / 'p.json'
    B.run(cache=cache, window=VT.WINDOW, symbols=VT.SYMS, repo=repo, key='exp_range', side='long', out=str(out))
    pkt = FS.load(str(out))
    assert pkt['trades']
    rep = FS.analyse([pkt], ['runner'], n_boot=50)
    assert rep['packets'][0]['runner_crosscheck']['match'] is True
    cs = rep['pooled']['cost_stress']
    assert cs['fees_x2']['status'] in ('OK', 'UNAVAILABLE')
    assert cs['fees_x2']['status'] == 'UNAVAILABLE' or cs['fees_x2']['net_r'] < cs['base_net_r']
