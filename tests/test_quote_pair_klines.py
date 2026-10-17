import json
import os
import sys

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
        return {'format': 'zb-klines-research/1', 'files': {'BTCUSDC': {'status': 'complete'}},
                'incomplete': [], 'tool': {'collect_klines_research.py': 'abc'}}
    monkeypatch.setattr(Q.CK, 'collect', fake_collect)
    manifest = tmp_path / 'm.json'
    man = Q.collect_pair(symbols=['BTCUSDC'], quote='USDC', interval='4h', window='a/b',
                         out=str(tmp_path / 'cache'), manifest=str(manifest))
    assert calls[0]['symbols'] == ['BTCUSDC'] and calls[0]['manifest'] is None
    assert man['format'] == Q.FORMAT and man['quote'] == 'USDC'
    assert man['pairs']['BTCUSDC'] == {'base': 'BTC', 'usdt_twin': 'BTCUSDT'}
    assert json.load(open(manifest, encoding='utf-8')) == man
    with pytest.raises(Q.PairCollectError, match='never overwritten'):
        Q.collect_pair(symbols=['BTCUSDC'], quote='USDC', interval='4h', window='a/b',
                       out=str(tmp_path / 'cache'), manifest=str(manifest))
