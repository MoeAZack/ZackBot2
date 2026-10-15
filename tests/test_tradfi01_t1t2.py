"""TRADFI-01 T1 (metadata admission) + T2 (US cash-session gate). Synthetic data only - no network."""
import json
from datetime import datetime, timezone

import pytest

import feasibility as F
import tradfi_session as TS
import tradfi_universe as TU


def _sym(symbol, ct='PERPETUAL', status='TRADING', step='0.001', minq='0.001', tick='0.01', notional='5'):
    return dict(symbol=symbol, contractType=ct, status=status, filters=[
        {'filterType': 'MARKET_LOT_SIZE', 'stepSize': step, 'minQty': minq},
        {'filterType': 'LOT_SIZE', 'stepSize': '0.0001', 'minQty': '0.0001'},
        {'filterType': 'PRICE_FILTER', 'tickSize': tick},
        {'filterType': 'MIN_NOTIONAL', 'notional': notional}])


INFO = {'symbols': [
    _sym('BTCUSDT', step='0.001', tick='0.1', notional='100'),
    _sym('ETHUSDT'),
    _sym('PAXGUSDT', step='0.001', tick='0.01'),
    _sym('BTCUSDT_261225', ct='CURRENT_QUARTER'),
    _sym('XAUUSDT', ct='TRADIFI_PERPETUAL', step='0.001', tick='0.01'),
    _sym('NVDAUSDT', ct='TRADIFI_PERPETUAL', step='0.01', tick='0.01'),
    _sym('TSLAUSDT', ct='TRADIFI_PERPETUAL', status='PENDING_TRADING'),
    _sym('NATGASUSDT', ct='TRADIFI_PERPETUAL'),
    _sym('SOXLUSDT', ct='TRADIFI_PERPETUAL'),
    _sym('OLDUSDT', status='SETTLING'),
    # no filters at all: the default path must never even parse it
    dict(symbol='NOFILT', contractType='TRADIFI_PERPETUAL', status='TRADING', filters=[]),
]}
INFO_NO_NOFILT = {'symbols': [s for s in INFO['symbols'] if s['symbol'] != 'NOFILT']}


def _old_rules(info):
    """Verbatim copy of the pre-TRADFI-01 rules_from_exchange_info."""
    out = {}
    for s in info['symbols']:
        if s.get('contractType') != 'PERPETUAL' or s.get('status') != 'TRADING': continue
        f = {x['filterType']: x for x in s['filters']}
        out[s['symbol']] = dict(step=float(f['MARKET_LOT_SIZE']['stepSize']), min_qty=float(f['MARKET_LOT_SIZE']['minQty']),
                                tick=float(f['PRICE_FILTER']['tickSize']),
                                min_notional=float(f.get('MIN_NOTIONAL', {}).get('notional', F.DEFAULT_MIN_NOTIONAL)))
    return out


# ---------------- T1 ----------------
def test_default_call_byte_identical_to_old():
    old, new = _old_rules(INFO), F.rules_from_exchange_info(INFO)
    assert json.dumps(new) == json.dumps(old)
    assert list(new) == ['BTCUSDT', 'ETHUSDT', 'PAXGUSDT']
    assert json.dumps(F.rules_from_exchange_info(INFO, allow_tradfi=False)) == json.dumps(old)


def test_tradifi_refused_without_flag():
    r = F.rules_from_exchange_info(INFO)
    assert 'XAUUSDT' not in r and 'NVDAUSDT' not in r


def test_flag_admits_only_allow_listed_trading_tradifi():
    r = F.rules_from_exchange_info(INFO_NO_NOFILT, allow_tradfi=True)
    assert {'XAUUSDT', 'NVDAUSDT'} <= set(r)
    for bad in ('NATGASUSDT', 'SOXLUSDT', 'TSLAUSDT', 'OLDUSDT', 'BTCUSDT_261225'):
        assert bad not in r
    assert r['XAUUSDT'] == dict(step=0.001, min_qty=0.001, tick=0.01, min_notional=5.0, kind='tradfi')


def test_non_allow_listed_tradifi_refused_with_flag():
    info = {'symbols': [_sym('NATGASUSDT', ct='TRADIFI_PERPETUAL'), _sym('SOXSUSDT', ct='TRADIFI_PERPETUAL'),
                        _sym('AAPLUSDT', ct='TRADIFI_PERPETUAL')]}
    assert F.rules_from_exchange_info(info, allow_tradfi=True) == {}


def test_pending_trading_refused():
    info = {'symbols': [_sym('TSLAUSDT', ct='TRADIFI_PERPETUAL', status='PENDING_TRADING'),
                        _sym('SPYUSDT', ct='TRADIFI_PERPETUAL', status='BREAK')]}
    assert F.rules_from_exchange_info(info, allow_tradfi=True) == {}


def test_paxg_via_normal_perpetual_path():
    r0 = F.rules_from_exchange_info(INFO)
    r1 = F.rules_from_exchange_info(INFO_NO_NOFILT, allow_tradfi=True)
    assert 'PAXGUSDT' in r0 and 'kind' not in r0['PAXGUSDT']
    assert r1['PAXGUSDT'] == r0['PAXGUSDT'] and 'kind' not in r1['PAXGUSDT']


def test_kind_only_on_tradfi_rules():
    r = F.rules_from_exchange_info(INFO_NO_NOFILT, allow_tradfi=True)
    for sym, rule in r.items():
        if sym in ('XAUUSDT', 'NVDAUSDT'): assert rule['kind'] == 'tradfi'
        else: assert 'kind' not in rule
    assert {k: v for k, v in r.items() if 'kind' not in v} == _old_rules(INFO)


def test_universe_constants():
    assert TU.TRADFI_GOLD == ['XAUUSDT', 'PAXGUSDT'] and TU.GOLD_FAMILY == {'XAUUSDT', 'PAXGUSDT'}
    assert len(TU.TRADFI_STOCKS) == 12 and TU.HIGH_BETA <= set(TU.TRADFI_STOCKS)
    assert TU.TRADFI_ALLOW_LIST == TU.TRADFI_GOLD + TU.TRADFI_STOCKS
    assert TU.TRADFI_OIL == [] and TU.TRADFI_OIL_ENABLED is False
    joined = ' '.join(TU.TRADFI_ALLOW_LIST)
    for banned in ('NATGAS', 'SOXL', 'SOXS', 'USOIL', 'WTI', 'BRENT'):
        assert banned not in joined


def test_oil_flag_true_raises():
    with pytest.raises(ValueError):
        TU.check_oil_flag(True)
    assert TU.check_oil_flag(False) is False


# ---------------- T2 ----------------
def U(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).timestamp()


# Winter: Wed 2026-01-14, ET = UTC-5. Summer: Wed 2026-07-15, ET = UTC-4.
@pytest.mark.parametrize('utc_off,day', [(5, (2026, 1, 14)), (4, (2026, 7, 15))])
@pytest.mark.parametrize('hh,mm,exp', [(9, 29, 'CLOSED'), (9, 30, 'OPEN'), (15, 59, 'OPEN'), (16, 0, 'CLOSED')])
def test_session_boundaries_dst(utc_off, day, hh, mm, exp):
    assert TS.us_cash_session(U(*day, hh + utc_off, mm)) == exp


def test_dst_actually_matters():
    # 13:35 UTC is 09:35 ET in summer (OPEN) but 08:35 ET in winter (CLOSED)
    assert TS.us_cash_session(U(2026, 7, 15, 13, 35)) == 'OPEN'
    assert TS.us_cash_session(U(2026, 1, 14, 13, 35)) == 'CLOSED'


def test_datetime_input_naive_is_utc():
    assert TS.us_cash_session(datetime(2026, 7, 15, 14, 0)) == 'OPEN'


def test_weekend_closed():
    assert TS.us_cash_session(U(2026, 7, 18, 15, 0)) == 'CLOSED'  # Saturday 11:00 ET
    assert TS.us_cash_session(U(2026, 7, 19, 15, 0)) == 'CLOSED'  # Sunday


def test_holiday_closed():
    assert TS.us_cash_session(U(2026, 11, 26, 16, 0)) == 'CLOSED'  # Thanksgiving 11:00 ET
    assert TS.us_cash_session(U(2026, 7, 3, 15, 0)) == 'CLOSED'    # Independence Day observed
    assert TS.us_cash_session(U(2027, 3, 26, 15, 0)) == 'CLOSED'   # Good Friday 2027


def test_early_close():
    # 2026-11-27 (EST, UTC-5): 12:59 ET open, 13:00 ET closed
    assert TS.us_cash_session(U(2026, 11, 27, 17, 59)) == 'OPEN'
    assert TS.us_cash_session(U(2026, 11, 27, 18, 0)) == 'CLOSED'


def test_beyond_table_unknown_blocks_entry():
    ts = U(2028, 3, 15, 15, 0)  # Wednesday 11:00 ET, outside the table
    assert TS.us_cash_session(ts) == 'UNKNOWN'
    assert TS.entry_allowed('SPYUSDT', 'range', ts) is False
    assert TS.entry_allowed('NVDAUSDT', 'trend', ts) is False
    assert TS.entry_allowed('XAUUSDT', 'range', ts) is True


def test_zoneinfo_unavailable_unknown(monkeypatch):
    monkeypatch.setattr(TS, '_NY', None)
    ts = U(2026, 7, 15, 15, 0)
    assert TS.us_cash_session(ts) == 'UNKNOWN'
    assert TS.entry_allowed('QQQUSDT', 'trend', ts) is False


def test_entry_allowed_matrix():
    closed, opened = U(2026, 7, 18, 15, 0), U(2026, 7, 15, 15, 0)
    assert TS.us_cash_session(closed) == 'CLOSED'
    for g in ('XAUUSDT', 'PAXGUSDT'):  # gold allowed while stocks are CLOSED
        for fam in ('range', 'scalp', 'trend'):
            assert TS.entry_allowed(g, fam, closed) is True
    for fam in ('range', 'scalp'):
        assert TS.entry_allowed('SPYUSDT', fam, closed) is False
        assert TS.entry_allowed('SPYUSDT', fam, opened) is True
    assert TS.entry_allowed('TSLAUSDT', 'trend', closed) is True
    assert TS.entry_allowed('TSLAUSDT', 'trend', opened) is True
    assert TS.entry_allowed('ANYUSDT', 'oil', opened) is False
