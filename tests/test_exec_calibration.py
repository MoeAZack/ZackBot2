"""EXEC-CAL-01: execution calibration reader, frozen profile, opt-in backtest hook and promotion guard (synthetic only)."""
import ast
import copy
import json
import os
import pickle
import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

import backtest as BT
import exec_calibration as X

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
FAKE_IDS = ('FAKEORDER123456', 'zqFAKECLIENT0001', 'acctFAKE999')


def rec(kind='entry_market', sym='BTCUSDT', side='LONG', ref=100.0, act=100.0, t=NOW - timedelta(hours=1), outcome='filled',
        wait=0.5, **extra):
    buy = (side == 'LONG') if kind in X.ENTRY_KINDS else (side == 'SHORT')
    r = dict(time=t.isoformat(timespec='seconds'), kind=kind, symbol=sym, side=side, buy=buy, expected=ref, actual=act,
             slip_bps=None, qty_req=1.0, qty_fill=1.0 if outcome in ('filled', 'partial') else None, outcome=outcome,
             wait_s=wait, orderId=FAKE_IDS[0], clientOrderId=FAKE_IDS[1], account=FAKE_IDS[2])
    r.update(extra)
    return r


def adverse_px(ref, bps, buy):
    return ref * (1 + bps / 1e4) if buy else ref * (1 - bps / 1e4)


def write(tmp_path, recs, name='fills.jsonl'):
    p = tmp_path / name
    with open(p, 'w', encoding='utf-8') as fh:
        for r in recs: fh.write((r if isinstance(r, str) else json.dumps(r)) + '\n')
    return str(p)


def big_set(n=240, t=None):
    """Market entries + exits across tiers / hours with known adverse slippage 0..9 bps."""
    out = []
    for i in range(n):
        side = 'LONG' if i % 2 == 0 else 'SHORT'
        kind = 'entry_market' if i % 3 else 'exit'
        buy = (side == 'LONG') if kind != 'exit' else (side == 'SHORT')
        sym = ('BTCUSDT', 'SOLUSDT', 'SUIUSDT')[i % 3]
        tt = (t or NOW) - timedelta(hours=i % 24, minutes=i)
        out.append(rec(kind, sym, side, 100.0, adverse_px(100.0, i % 10, buy), tt))
    return out


def profile_of(tmp_path, recs, as_of=NOW, **th):
    rows, src = X.read_fills([write(tmp_path, recs)])
    return X.build_profile(rows, src, as_of, th or None)


# ---- reader ---------------------------------------------------------------------------------------------------------------
def test_maker_fill_fallback_and_unfilled(tmp_path):
    recs = ([rec('entry_maker', act=99.99, signal_px=100.0) for _ in range(6)]
            + [rec('entry_maker', act=None, outcome='unfilled', fallback_attempted=True, fallback_confirmed=True) for _ in range(3)]
            + [rec('entry_maker', act=None, outcome='unfilled', fallback_attempted=True) for _ in range(1)]
            + [rec('entry_fallback', act=100.05, signal_px=100.0, fallback_order=True) for _ in range(4)])
    p = profile_of(tmp_path, recs, min_cell_n=1)
    m = p['levels']['entry_maker']
    assert (m['n'], m['filled'], m['unfilled']) == (10, 6, 4)
    assert m['maker_fill_rate'] == 0.6 and m['fallback_rate'] == 0.4 and m['fallback_confirmed_rate'] == 0.3
    assert m['slip_n'] == 6 and m['slip_p50_bps'] == -1.0          # filled 1 bp better than the decision price
    f = p['levels']['entry_fallback']
    assert f['maker_fill_rate'] is None and f['slip_p50_bps'] == 5.0
    assert p['levels']['maker']['fee_rate'] == 0.0002 and p['levels']['taker']['fee_rate'] == 0.0005
    assert p['levels']['maker']['fee_basis'] == 'assumed_vip0'
    assert p['coverage']['filled'] == 10 and p['coverage']['slip_n'] == 10


@pytest.mark.parametrize('kind', ['entry_market', 'exit'])
def test_adverse_favourable_long_short_mirrored(tmp_path, kind):
    out = {}
    for side in ('LONG', 'SHORT'):
        buy = (side == 'LONG') if kind != 'exit' else (side == 'SHORT')
        recs = [rec(kind, side=side, act=adverse_px(100.0, 7, buy))] * 3 + [rec(kind, side=side, act=adverse_px(100.0, -4, buy))]
        st = profile_of(tmp_path, recs, min_cell_n=1)['levels'][kind]
        out[side] = (st['slip_p50_bps'], st['slip_worst_bps'], st['slip_n'])
    assert out['LONG'] == out['SHORT'] == (7.0, 7.0, 4)
    rows, _ = X.read_fills([write(tmp_path, [rec(kind, side='SHORT', act=adverse_px(100.0, -4, kind == 'exit'))])])
    assert rows[0]['slip'] == pytest.approx(-4.0)                 # favourable = negative


def test_measured_fee_and_ref_basis(tmp_path):
    recs = [rec(act=100.0, fee=0.04) for _ in range(5)] + [rec(act=100.0, commission=0.02)]
    st = profile_of(tmp_path, recs, min_cell_n=1)['levels']['entry_market']
    assert st['fee_basis'] == 'measured_p50' and st['fee_rate'] == 0.0004 and st['fee_n'] == 6


def test_malformed_rows_rejected_and_flagged(tmp_path):
    good = rec()
    bad = ['not json', '[1,2]', json.dumps(dict(good, kind='teleport')), json.dumps(dict(good, time='yesterday')),
           json.dumps(dict(good, time='2026-10-16T10:00:00')),                       # naive time
           json.dumps(dict(good, buy=False)), json.dumps(dict(good, side='UP')), json.dumps(dict(good, symbol='../x')),
           json.dumps(dict(good, outcome='maybe')), json.dumps(dict(good, wait_s='5')), json.dumps(dict(good, actual=True)),
           '{"kind": "exit", "time": "2026-10-16T10:00:00+00:00", "actual": NaN}']
    flagged = [rec(act=250.0), rec(act=None), rec(outcome='unfilled', act=101.0), dict(rec(act=100.1), slip_bps=-3.0),
               rec(wait=-1.0)]
    rows, src = X.read_fills([write(tmp_path, [good] + bad + flagged)])
    assert src['rows_read'] == 18 and src['rows_used'] == 6 and sum(src['rejected'].values()) == 12
    assert set(src['rejected']) >= {'bad_json', 'not_object', 'unknown_kind', 'bad_time', 'buy_side_mismatch', 'bad_side',
                                    'bad_symbol', 'bad_outcome', 'bad_wait_s', 'bad_actual'}
    assert src['flagged'] == {'negative_wait': 1, 'no_fill_price': 1, 'price_on_unfilled': 1, 'slip_mismatch': 1,
                              'slip_outlier': 1}
    p = X.build_profile(rows, src, NOW)
    assert p['coverage']['rejected_share'] == round(12 / 18, 4)
    assert 'rejected_share' in X.promotable(p, NOW)[1]


def test_rotated_file_read_first_and_missing_skipped(tmp_path):
    a = write(tmp_path, [rec(t=NOW - timedelta(days=2))], 'fills.jsonl.1')
    b = write(tmp_path, [rec()], 'fills.jsonl')
    rows, src = X.read_fills([a, b, str(tmp_path / 'absent.jsonl')])
    assert src['files'] == ['fills.jsonl.1', 'fills.jsonl'] and len(rows) == 2


# ---- buckets / collapse ---------------------------------------------------------------------------------------------------
def test_bucket_definitions():
    assert X.liq_tier('BTCUSDT') == 'L1' and X.liq_tier('SOLUSDT') == 'L2' and X.liq_tier('SUIUSDT') == 'L3'
    assert X.liq_tier('XAUUSDT') == 'L4'
    assert [X.hour_bucket(h) for h in (0, 5, 6, 11, 12, 17, 18, 23)] == ['h00_06'] * 2 + ['h06_12'] * 2 + ['h12_18'] * 2 + ['h18_24'] * 2
    with pytest.raises(ValueError): X.hour_bucket(24)


def test_buckets_pinned_to_engine():
    """Mirrored constants equal engine.CORE8 / TOP40 / Engine.FILL_KINDS (read by AST: no engine import side effects)."""
    tree = ast.parse(open(os.path.join(REPO, 'engine.py'), encoding='utf-8').read())
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if getattr(t, 'id', None) in ('CORE8', 'TOP40', 'FILL_KINDS'): found[t.id] = tuple(ast.literal_eval(node.value))
    assert found['CORE8'] == X._CORE8 and found['TOP40'] == X._TOP40 and found['FILL_KINDS'] == X.FILL_KINDS


def test_sparse_cell_collapse(tmp_path):
    t = NOW.replace(hour=14)
    dense = [rec('entry_market', 'BTCUSDT', t=t - timedelta(minutes=i)) for i in range(30)]      # L1 h12_18: 30 rows
    sparse = [rec('entry_market', 'BTCUSDT', t=NOW.replace(hour=3) - timedelta(minutes=i)) for i in range(3)]  # L1 h00_06
    other = [rec('entry_market', 'SUIUSDT', t=t - timedelta(minutes=i)) for i in range(2)]                     # L3: 2 rows
    lone = [rec('safety_order', 'SOLUSDT', t=t)]
    p = profile_of(tmp_path, dense + sparse + other + lone)
    c = p['cells']
    assert c['entry_market|L1|h12_18'] == dict(n=30, stale=False, use='entry_market|L1|h12_18', fallback_level=0,
                                               fallback_name='kind_liq_hour')
    assert c['entry_market|L1|h00_06']['use'] == 'entry_market|L1' and c['entry_market|L1|h00_06']['fallback_level'] == 1
    assert c['entry_market|L3|h12_18']['use'] == 'entry_market' and c['entry_market|L3|h12_18']['fallback_level'] == 2
    assert c['safety_order|L2|h12_18']['use'] == 'taker' and c['safety_order|L2|h12_18']['fallback_level'] == 3
    assert p['coverage']['cells_collapsed'] == 3
    few = profile_of(tmp_path, other)
    assert few['cells']['entry_market|L3|h12_18'] == dict(n=2, stale=False, use=None, fallback_level=None,
                                                          fallback_name='below_min_at_all_levels')


# ---- frozen profile ------------------------------------------------------------------------------------------------------
def test_digest_stable_and_order_independent(tmp_path):
    recs = big_set()
    p1 = profile_of(tmp_path, recs)
    p2 = profile_of(tmp_path, list(reversed(recs)))
    assert p1['digest'] == p2['digest'] == X.digest_of(p1)
    assert X.dumps_profile(p1) == X.dumps_profile(p2)
    path = tmp_path / 'profile.json'
    X.save_profile(p1, path)
    assert X.load_profile(path) == p1 and X.digest_of(X.load_profile(path)) == p1['digest']
    assert re.fullmatch(r'[0-9a-f]{64}', p1['digest']) and p1['schema'] == X.SCHEMA


def test_tamper_detected(tmp_path):
    p = profile_of(tmp_path, big_set())
    assert X.promotable(p, NOW) == (True, [])
    t = copy.deepcopy(p); t['levels']['taker']['slip_p90_bps'] = 0.0
    assert 'digest_mismatch' in X.promotable(t, NOW)[1]
    with pytest.raises(X.CalibrationError, match='digest'): X.backtest_params(t, 'median')
    t2 = copy.deepcopy(p); t2['thresholds']['min_profile_n'] = 1; t2['digest'] = X.digest_of(t2)   # re-signed, looser
    t2['coverage']['rows'] = 50; t2['digest'] = X.digest_of(t2)
    assert 'small_sample' in X.promotable(t2, NOW)[1]               # thresholds never looser than the module's
    t3 = dict(p, schema='zb-exec-cal/0')
    assert 'schema_mismatch' in X.promotable(t3, NOW)[1]
    assert X.promotable(dict(p, digest=None), NOW)[1][0] == 'digest_missing'
    assert X.promotable('x', NOW) == (False, ['malformed_profile'])


def test_no_id_fields_in_output(tmp_path):
    p = profile_of(tmp_path, big_set() + [rec('entry_maker', outcome='unfilled', act=None, fallback_attempted=True)])
    keys = set()
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items(): keys.add(k); walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(p)
    bad = re.compile(r'(^id$|_id$|^ids?_|orderid|order_id|client|^cid$|account|secret|token|apikey|api_key|signature)', re.I)
    assert not [k for k in keys if bad.search(k)]
    text = X.dumps_profile(p)
    assert not any(f in text for f in FAKE_IDS)
    assert str(tmp_path) not in text and 'princ' not in text      # basenames only, no paths


# ---- promotion guard -----------------------------------------------------------------------------------------------------
def test_stale_and_small_refused(tmp_path):
    old = profile_of(tmp_path, big_set(t=NOW - timedelta(days=30)), as_of=NOW)
    ok, why = X.promotable(old, NOW)
    assert not ok and 'stale_profile' in why and 'stale_cells' in why
    small = profile_of(tmp_path, big_set(n=60))
    ok, why = X.promotable(small, NOW)
    assert not ok and 'small_sample' in why
    fresh = profile_of(tmp_path, big_set())
    assert X.promotable(fresh, NOW + timedelta(days=20))[1] == ['stale_profile']    # ages out by itself
    assert 'future_dated' in X.promotable(fresh, NOW - timedelta(days=1))[1]


def test_no_slippage_refused(tmp_path):
    """The shape of the real PAPER audit today: fills recorded without an executed price -> no slippage coverage."""
    recs = [dict(r, actual=None) for r in big_set()]
    p = profile_of(tmp_path, recs)
    ok, why = X.promotable(p, NOW)
    assert not ok and 'insufficient_slip_coverage' in why and 'no_taker_slippage' in why
    assert p['source']['flagged'] == {'no_fill_price': 240}
    with pytest.raises(X.CalibrationError, match='slippage samples'): X.backtest_params(p, 'median')


# ---- application -----------------------------------------------------------------------------------------------------------
def test_cost_params_modes_and_provenance(tmp_path):
    p = profile_of(tmp_path, big_set())
    med, pes = X.cost_params(p, 'median', 'entry_market', 'BTCUSDT', 14), X.cost_params(p, 'pessimistic', 'exit', 'SUIUSDT', NOW)
    assert med['provenance']['digest'] == p['digest'] and med['provenance']['mode'] == 'median'
    assert pes['slip_bps'] >= med['slip_bps'] and pes['provenance']['stat'] == 'p90'
    assert med['fee_rate'] == 0.0005
    bp = X.backtest_params(p, 'pessimistic')
    assert bp['SLIP'] == p['levels']['taker']['slip_p90_bps'] / 1e4 and bp['FEE'] == 0.0005 and bp['FEE_MAKER'] is None
    with pytest.raises(X.CalibrationError): X.cost_params(p, 'optimistic', 'exit', 'BTCUSDT', 1)
    fav = profile_of(tmp_path, [dict(r, actual=adverse_px(100.0, -3, r['buy'])) for r in big_set()])
    assert X.backtest_params(fav, 'median')['SLIP'] == 0.0 and X.backtest_params(fav, 'median')['provenance']['floored']


def _book():
    rng = np.random.default_rng(3); t = pd.date_range('2024-01-01', periods=400, freq='4h'); out = {}
    for j, s in enumerate(('BTCUSDT', 'ETHUSDT', 'SOLUSDT')):
        r = rng.standard_t(4, 400) * 0.008 + 0.003 * np.sin(np.arange(400) / (15 + 9 * j))
        c = 100 * (j + 1) * np.exp(np.cumsum(r)); o = np.r_[c[0], c[:-1]]
        w = np.abs(rng.normal(0, 0.005, 400)) * c
        out[s] = pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) + w, l=np.minimum(o, c) - w, c=c, v=1000.0))
    return BT.Book(out)


SLEEVES = [dict(key='donchian_ens', share=1.0, risk=.02, max_pos=3, sides='both', mgmt={})]


def _bytes(res):
    tr, cv = res
    return pickle.dumps((tr, cv, dict(getattr(cv, "attrs", {}))))


def test_backtest_byte_identical_when_hook_off(tmp_path):
    bk = _book()
    consts = (BT.FEE, BT.SLIP, BT.FEE_MAKER)
    base = BT.run(bk, SLEEVES, warmup=220)
    off = X.run_backtest(bk, SLEEVES, warmup=220)                     # profile None: plain backtest.run
    assert _bytes(off) == _bytes(base) and len(base[0]) > 0
    p = profile_of(tmp_path, [dict(r, actual=adverse_px(100.0, 40, r['buy'])) for r in big_set()])
    tr_c, cv_c = X.run_backtest(bk, SLEEVES, profile=p, mode='pessimistic', now=NOW, warmup=220)
    assert cv_c.attrs['exec_calibration']['digest'] == p['digest'] and cv_c.attrs['exec_calibration']['promotable']
    assert (BT.FEE, BT.SLIP, BT.FEE_MAKER) == consts                 # restored
    assert _bytes((tr_c, cv_c)) != _bytes(base)                      # the hook did change costs
    after = BT.run(bk, SLEEVES, warmup=220)
    assert _bytes(after) == _bytes(base)                             # nothing leaked into later runs
    with pytest.raises(RuntimeError):
        with X.calibrated_backtest(p, 'median', NOW):
            assert BT.SLIP == p['levels']['taker']['slip_p50_bps'] / 1e4
            raise RuntimeError('boom')
    assert (BT.FEE, BT.SLIP, BT.FEE_MAKER) == consts


def test_backtest_source_untouched():
    """backtest.py is not edited by this ticket: the hook lives here only."""
    src = open(os.path.join(REPO, 'backtest.py'), encoding='utf-8').read()
    assert 'exec_calibration' not in src


def test_cli_writes_profile(tmp_path, capsys):
    f = write(tmp_path, big_set())
    out = tmp_path / 'out'
    assert X.main(['--fills', f, '--out', str(out)]) == 0
    s = json.loads(capsys.readouterr().out)
    p = X.load_profile(out / 'profile.json')
    assert s['digest'] == p['digest'] and s['by_kind']['exit']['n'] == 80
