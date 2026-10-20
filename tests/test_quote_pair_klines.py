import json
import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import collect_quote_pair_klines as Q  # noqa: E402


STATIC = {'symbols': [{'symbol': 'BTCUSDT'}, {'symbol': 'ETHUSDT'}, {'symbol': 'SOLUSDT'}]}


def test_resolve_quote_symbols_are_explicit_frozen_twins():
    assert Q.resolve_symbols(['btcusdc', 'ETHUSDC', 'btcusdc'], 'USDC', STATIC) == ['BTCUSDC', 'ETHUSDC']
    assert Q.resolve_symbols(['BTCUSDT'], 'USDT', STATIC) == ['BTCUSDT']
    for bad in (['BTC'], ['BTCUSDT'], ['XRPUSDC'], ['../../BTCUSDC'], ['ßßUSDC'], []):
        with pytest.raises(Q.PairCollectError):
            Q.resolve_symbols(bad, 'USDC', STATIC)


def test_pair_manifest_is_write_once_and_keeps_underlying_completeness(tmp_path, monkeypatch):
    monkeypatch.setattr(Q.VC, 'frozen_universe', lambda: STATIC)
    calls = []
    def fake_collect(**kwargs):
        calls.append(kwargs)
        os.makedirs(kwargs['out'], exist_ok=True)
        pd.DataFrame([[1, 1, 2, .5, 1.5, 10, 15]], columns=Q.CK.COLS).to_csv(
            os.path.join(kwargs['out'], 'BTCUSDC_4h.csv'), index=False)
        return {'format': 'zb-klines-research/1', 'files': {'BTCUSDC': {
                    'file': 'BTCUSDC_4h.csv', 'status': 'complete', 'fetched': 'this run'}},
                'incomplete': [], 'tool': {'collect_klines_research.py': 'abc'}}
    monkeypatch.setattr(Q.CK, 'collect', fake_collect)
    manifest = tmp_path / 'm.json'
    man = Q.collect_pair(symbols=['BTCUSDC'], quote='USDC', interval=Q.INTERVAL, window=Q.WINDOW,
                         out=str(tmp_path / 'cache'), manifest=str(manifest))
    assert calls[0]['symbols'] == ['BTCUSDC'] and calls[0]['manifest'] is None
    assert man['format'] == Q.FORMAT and man['contract'] == Q.CONTRACT and man['quote'] == 'USDC'
    assert man['files']['BTCUSDC']['validation']['status'] == 'complete'
    assert man['pairs']['BTCUSDC'] == {'base': 'BTC', 'usdt_twin': 'BTCUSDT'}
    assert json.load(open(manifest, encoding='utf-8')) == man
    with pytest.raises(Q.PairCollectError, match='never overwritten'):
        Q.collect_pair(symbols=['BTCUSDC'], quote='USDC', interval=Q.INTERVAL, window=Q.WINDOW,
                       out=str(tmp_path / 'cache'), manifest=str(manifest))


def test_contract_and_bad_cache_are_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(Q.VC, 'frozen_universe', lambda: STATIC)
    with pytest.raises(Q.PairCollectError, match='requires'):
        Q.collect_pair(symbols=['BTCUSDC'], quote='USDC', interval='1h', window=Q.WINDOW,
                       out=str(tmp_path / 'x'), manifest=str(tmp_path / 'x.json'))
    def bad_collect(**kwargs):
        os.makedirs(kwargs['out'], exist_ok=True)
        pd.DataFrame([[2, 1, .5, 2, 3, -1, 1], [1, 1, 2, .5, 1.5, 1, 1]], columns=Q.CK.COLS).to_csv(
            os.path.join(kwargs['out'], 'BTCUSDC_4h.csv'), index=False)
        return {'files': {'BTCUSDC': {'file': 'BTCUSDC_4h.csv', 'status': 'complete', 'fetched': 'this run'}},
                'incomplete': [], 'tool': {}}
    monkeypatch.setattr(Q.CK, 'collect', bad_collect)
    man = Q.collect_pair(symbols=['BTCUSDC'], quote='USDC', interval=Q.INTERVAL, window=Q.WINDOW,
                         out=str(tmp_path / 'bad'), manifest=str(tmp_path / 'bad.json'))
    assert man['incomplete'] == ['BTCUSDC']
    assert set(man['files']['BTCUSDC']['validation']['problems']) == {
        'invalid_ohlcv_rows=1', 'timestamps_not_sorted'}
