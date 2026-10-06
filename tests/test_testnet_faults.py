"""T03c exceptional-path canary: the testnet-only leverage-refusal injector is inert on mainnet and without the flag."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import binance_client as BC                                                # noqa: E402


def _client(base, monkeypatch, calls):
    c = BC.Futures.__new__(BC.Futures)
    c.key, c.secret, c.base, c.rw, c.offset, c.last_ok = 'k', b's', base, 6000, 0, 0
    monkeypatch.setattr(c, '_req', lambda *a, **k: calls.append((a, k)) or {'leverage': a[2]['leverage']}, raising=False)
    return c


def test_flag_refuses_only_the_listed_symbol_on_testnet(monkeypatch):
    monkeypatch.setenv('ZB_TESTNET_FAULTS', 'lev_refuse:SOLUSDT')
    calls = []; c = _client(BC.TESTNET, monkeypatch, calls)
    with pytest.raises(BC.BinanceError) as e: c.set_leverage('SOLUSDT', 10)
    assert e.value.code == -1000 and 'injected' in e.value.msg and calls == []     # nothing sent
    c.set_leverage('BTCUSDT', 10)
    assert len(calls) == 1


@pytest.mark.parametrize('base', [BC.MAINNET, 'https://fapi.binance.com/', '', None, 'https://testnet.binancefuture.com.evil'])
def test_flag_is_inert_unless_the_base_is_exactly_testnet(monkeypatch, base):
    monkeypatch.setenv('ZB_TESTNET_FAULTS', 'lev_refuse:SOLUSDT')
    calls = []; c = _client(base, monkeypatch, calls)
    c.set_leverage('SOLUSDT', 10)
    assert len(calls) == 1 and BC.testnet_faults(base, 'lev_refuse') == frozenset()


@pytest.mark.parametrize('val', [None, '', 'lev_refuse', 'lev_refuse:', 'lev_refuse:sol', 'other:SOLUSDT', 'lev_refuse:SOL USDT'])
def test_absent_or_malformed_flag_injects_nothing(monkeypatch, val):
    if val is None: monkeypatch.delenv('ZB_TESTNET_FAULTS', raising=False)
    else: monkeypatch.setenv('ZB_TESTNET_FAULTS', val)
    calls = []; c = _client(BC.TESTNET, monkeypatch, calls)
    c.set_leverage('SOLUSDT', 10)
    assert len(calls) == 1 and BC.testnet_faults(BC.TESTNET, 'lev_refuse') == frozenset()


def test_several_symbols_and_whitespace(monkeypatch):
    monkeypatch.setenv('ZB_TESTNET_FAULTS', ' lev_refuse:SOLUSDT , lev_refuse:ETHUSDT,junk')
    assert BC.testnet_faults(BC.TESTNET, 'lev_refuse') == frozenset({'SOLUSDT', 'ETHUSDT'})
