"""The raw-pack adapter trims Integration's accounting pack to [warm-up, sealed cutoff) without parsing a single row at /
after the cutoff, and its output is exactly what the paired runner validates."""
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = os.path.join(ROOT, 'tools', 'research')
if R not in sys.path:
    sys.path.insert(0, R)
import build_quote_pair_accounting as A  # noqa: E402
import run_quote_pair_usdc as Q  # noqa: E402

H = Q.HOUR


def raw_pack(tmp, info_sha_ok=True):
    root = tmp / 'raw'
    for d in ('funding_rate', 'mark_klines', 'exchange_info'):
        (root / d).mkdir(parents=True)
    syms = []
    for q in Q.QUOTES:
        for j, b in enumerate(Q.BASES):
            s = b + q
            syms.append({'symbol': s, 'status': 'TRADING', 'contractType': 'PERPETUAL', 'baseAsset': b, 'quoteAsset': q,
                         'filters': [{'filterType': 'PRICE_FILTER', 'tickSize': '0.01', 'minPrice': '1'},
                                     {'filterType': 'LOT_SIZE', 'stepSize': '0.001'},
                                     {'filterType': 'MARKET_LOT_SIZE', 'stepSize': '0.001'},
                                     {'filterType': 'MIN_NOTIONAL', 'notional': '5'},
                                     {'filterType': 'PERCENT_PRICE', 'multiplierUp': '1.05'}]})
            ft = np.arange(Q.WARM - 30 * 8 * H, Q.END, 8 * H)
            rate = 0.0001 if q == 'USDT' else 0.00007
            lines = ['fundingTime,fundingRate,markPrice,time_utc'] + \
                [f'{t},{rate},{100 + j + (q == "USDC") * 0.01},x' for t in ft] + \
                [f'{Q.END},HOLDOUT,HOLDOUT,x', 'not-a-time,HOLDOUT,HOLDOUT,x']        # never parsed
            (root / 'funding_rate' / f'{s}.csv').write_text('\n'.join(lines) + '\n')
            mt = np.arange(Q.WARM - 5 * H, Q.END, H)
            mp = 100 + j + (q == 'USDC') * 0.02
            lines = ['open_time,open,high,low,close,close_time,time_utc'] + \
                [f'{t},{mp},{mp + 1},{mp - 1},{mp},{t + H - 1},x' for t in mt] + [f'{Q.END},HOLDOUT,,,,,x']
            (root / 'mark_klines' / f'{s}_1h.csv').write_text('\n'.join(lines) + '\n')
    info = json.dumps({'symbols': syms}).encode()
    (root / 'exchange_info' / 'latest.json').write_bytes(info)
    sha = hashlib.sha256(info).hexdigest() if info_sha_ok else '0' * 64
    (root / 'manifest.json').write_text(json.dumps({'series': {'exchange_info/mainnet': {
        'file': 'exchange_info/latest.json', 'sha256': sha, 'fetched_utc': '2026-10-18T05:10:10Z'}}}))
    return root, hashlib.sha256(info).hexdigest()


def test_the_trimmed_pack_validates_and_holds_nothing_past_the_cutoff(tmp_path):
    root, _ = raw_pack(tmp_path)
    paths = A.build(str(root), str(tmp_path / 'out'))
    for q, p in paths.items():
        m, acc, files = Q.load_accounting(p, q)
        assert m['exchange_rules']['sha_pinned_by_operator'] is False
        for s, v in acc.items():
            assert v['funding'].fundingTime.max() < Q.END and v['funding'].fundingTime.min() == Q.WARM
            assert v['mark'].open_time.min() == Q.WARM and len(v['mark']) == (Q.END - Q.WARM) // H
            assert set(v['rules']['filters']) == set(Q.REQUIRED_FILTERS)          # only the filters the runner reads
    blob = b''.join(p.read_bytes() for p in (tmp_path / 'out').rglob('*') if p.is_file())
    assert b'HOLDOUT' not in blob and b'time_utc' not in blob
    with pytest.raises(A.BuildError, match='never overwritten'):
        A.build(str(root), str(tmp_path / 'out'))


def test_an_unverifiable_rules_snapshot_needs_an_explicit_pin(tmp_path):
    root, sha = raw_pack(tmp_path, info_sha_ok=False)
    with pytest.raises(A.BuildError, match='pin the file hash'):
        A.build(str(root), str(tmp_path / 'a'))
    with pytest.raises(A.BuildError, match='pin the file hash'):
        A.build(str(root), str(tmp_path / 'b'), pin_info_sha='1' * 64)          # a wrong pin is no pin
    assert not (tmp_path / 'a').exists() and not (tmp_path / 'b').exists()
    p = A.build(str(root), str(tmp_path / 'c'), pin_info_sha=sha)
    m = json.loads(open(p['USDC']).read())
    assert m['exchange_rules']['sha_pinned_by_operator'] is True and m['exchange_rules']['sha256'] == sha
    assert m['exchange_rules']['raw_manifest_sha256'] == '0' * 64


def test_an_unordered_or_missing_raw_series_refuses_and_writes_nothing(tmp_path):
    root, _ = raw_pack(tmp_path)
    f = root / 'funding_rate' / 'SOLUSDC.csv'
    lines = f.read_text().splitlines()
    lines[40], lines[41] = lines[41], lines[40]
    f.write_text('\n'.join(lines) + '\n')
    with pytest.raises(A.BuildError, match='not strictly time ordered'):
        A.build(str(root), str(tmp_path / 'o'))
    assert not (tmp_path / 'o').exists()
    root2, _ = raw_pack(tmp_path / 'm')
    os.remove(root2 / 'mark_klines' / 'XRPUSDT_1h.csv')
    with pytest.raises(A.BuildError, match='missing'):
        A.build(str(root2), str(tmp_path / 'm2'))


def test_a_gap_in_the_raw_series_is_written_as_is_and_refused_by_the_runner(tmp_path):
    root, _ = raw_pack(tmp_path)
    f = root / 'mark_klines' / 'BNBUSDT_1h.csv'
    lines = f.read_text().splitlines()
    del lines[500]
    f.write_text('\n'.join(lines) + '\n')
    p = A.build(str(root), str(tmp_path / 'out'))
    with pytest.raises(Q.PairRunnerError, match='mark grid'):
        Q.load_accounting(p['USDT'], 'USDT')
    pd.read_csv(tmp_path / 'out' / 'usdt' / 'BNBUSDT_markPriceKlines_1h.csv')     # the adapter never fills it
