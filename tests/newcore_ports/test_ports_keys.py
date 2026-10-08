"""Step-0 identity over the NC-01 DecisionKey: the decision_key() constructor and the restart-stable derivations."""
import re

import pytest

from newcore.domain import DecisionKey, Purpose, Side
from newcore.domain.codec import canonical_bytes, contract_sha256, loads
from newcore.domain.errors import InvalidRecord
from newcore.ports import keys as K

ACCT = 'acct_' + '0123456789abcdef' * 2
ACCT2 = 'acct_' + 'fedcba9876543210' * 2
T = 1759924800000
CANONICAL = (b'{"body":{"candle_close_ms":1759924800000,"purpose":"entry","side":"LONG","strategy":"trend_ema_mom@4h",'
             b'"strategy_version":"v1","symbol":"BTCUSDT"},"format":"zackbot.newcore","record_type":"decision_key",'
             b'"schema_version":1}')
BINANCE_CID = re.compile(r'^[.A-Z:/a-z0-9_-]{1,36}$')
LEGACY_BOT_CID = re.compile(r'z[ab][0-9a-f]{22}')      # engine.BOT_STOP_CID_RE: legacy ids must never look like ours


def key(name='trend_ema_mom', version='v1', tf='4h', symbol='BTCUSDT', side=Side.LONG, close=T, purpose=Purpose.ENTRY):
    return K.decision_key(name, version, tf, symbol, side, close, purpose)


# ------------------------------------------------------------------------------------------------ the key
def test_decision_key_is_the_nc01_record_with_pinned_bytes():
    k = key()
    assert type(k) is DecisionKey and k.strategy == 'trend_ema_mom@4h'
    assert canonical_bytes(k) == CANONICAL
    assert contract_sha256(k) == 'eed91949b70e10d99ac957dd6a1e255d0ef75b89fef07b41e70883eb5730ac04'
    assert loads(CANONICAL) == k


@pytest.mark.parametrize('kw', [dict(name='Trend'), dict(name='x' * 25), dict(name='a@b'), dict(tf='7m'),
                                dict(tf='4H'), dict(version='1'), dict(version='v1.2'), dict(close=T + 900_000),
                                dict(close=T + 1), dict(close=True), dict(side='BOTH'), dict(purpose='stop'),
                                dict(symbol='btcusdt')])
def test_decision_key_constructor_rejects(kw):
    with pytest.raises((InvalidRecord, ValueError)):
        key(**kw)


def test_fifteen_minute_close_on_the_four_hour_grid_is_still_a_distinct_instance():
    k4, k15 = key(tf='4h'), key(tf='15m', close=T)
    assert k4.candle_close_ms == k15.candle_close_ms and k4 != k15
    assert canonical_bytes(k4) != canonical_bytes(k15)
    assert key(tf='15m', close=T + 900_000).candle_close_ms == T + 900_000       # off the 4h grid, on the 15m grid


def test_bare_nc01_key_is_not_a_runner_key():
    bare = DecisionKey(strategy='trend_ema_mom', strategy_version='v1', symbol='BTCUSDT', side=Side.LONG,
                       candle_close_ms=T, purpose=Purpose.ENTRY)
    with pytest.raises(InvalidRecord):
        K.check_decision_key(bare)
    assert K.check_decision_key(key()) == 14_400_000


# ------------------------------------------------------------------------------------------------ derivations
def test_pinned_vectors_are_restart_stable():
    """Changing any of these strings orphans every journal written before: bump the .v1 tags instead."""
    i = K.derive_intent_id(ACCT, key())
    assert K.derive_decision_id(ACCT, key()) == 'dec_1b68c1f8c6cd51de38b74ff95bf3ae0d'
    assert i == 'int_d4bbf9ba13b6d5a0d78481b4ca7f03b7'
    c = K.derive_child_intent_id(ACCT, i, Purpose.PROTECT, 0)
    assert c == 'int_48597b182eae2f00b76fc331bacf47a4'
    assert K.client_id_for(i) == 'zbn1o-bw72dangb4tswgg3zlvukcybva'
    assert K.client_id_for(c, 'algo') == 'zbn1a-ulh3g3nn34jhqydvoho6aviezp'
    assert K.derive_lot_id(ACCT, i) == K.derive_lot_id(ACCT, i) != K.derive_lot_id(ACCT2, i)


def test_derivation_separates_its_inputs():
    i0 = K.derive_intent_id(ACCT, key())
    lot = K.derive_lot_id(ACCT, i0)
    assert K._derive_leg(ACCT, key(), 1) != i0 and K._derive_leg(ACCT, key(), 0) == i0
    assert K.derive_intent_id(ACCT2, key()) != i0
    assert K.client_id_for(i0, 'classic') != K.client_id_for(i0, 'algo')
    assert K.route_of(i0, K.client_id_for(i0, 'algo')) == 'algo' and K.route_of(i0, 'zbn1o-' + 'a' * 26) is None
    ids = {K.derive_child_intent_id(ACCT, o, p, n) for o in (i0, lot) for p in (Purpose.PROTECT, Purpose.CLOSE)
           for n in (0, 1)}
    assert len(ids) == 8


@pytest.mark.parametrize('call', [
    lambda: K._derive_leg(ACCT, key(), 16), lambda: K.derive_intent_id('acct_x', key()),
    lambda: K.derive_decision_id('int_' + '0' * 32, key()), lambda: K.client_id_for('int_' + '0' * 32, 'ALGO'),
    lambda: K.client_id_for('dec_' + '0' * 32), lambda: K.derive_child_intent_id(ACCT, 'pf_' + '0' * 32, 'protect', 0),
    lambda: K.derive_child_intent_id(ACCT, 'int_' + '0' * 32, 'stop', 0),
    lambda: K.derive_child_intent_id(ACCT, 'int_' + '0' * 32, 'protect', -1),
    lambda: K.derive_lot_id(ACCT, 'lot_' + '0' * 32), lambda: K.derive_intent_id(ACCT, 'not a key'),
])
def test_derivation_rejects_bad_input(call):
    with pytest.raises((InvalidRecord, ValueError)):
        call()


def test_client_id_shape_length_and_charset():
    for n in range(2000):
        intent = K.derive_intent_id(ACCT, key(close=T + n * 14_400_000))
        for route in ('classic', 'algo'):
            cid = K.client_id_for(intent, route)
            assert len(cid) == 32 <= 36
            assert BINANCE_CID.fullmatch(cid) and K.is_newcore_client_id(cid)
            assert not LEGACY_BOT_CID.fullmatch(cid)
    assert not K.is_newcore_client_id('zb' + '0' * 22) and not K.is_newcore_client_id(None)


def test_no_collisions_across_a_large_deterministic_sample():
    symbols = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT')
    t0 = 1640995200000
    ids, cids, keys_seen, n = set(), set(), set(), 0
    for sym in symbols:
        for side in Side:
            for purpose in (Purpose.ENTRY, Purpose.CLOSE):
                for tf in ('4h', '15m'):
                    for j in range(800):
                        k = key(symbol=sym, side=side, purpose=purpose, tf=tf, close=t0 + j * 14_400_000)
                        keys_seen.add(canonical_bytes(k))
                        for acct in (ACCT, ACCT2):
                            d, i = K.derive_decision_id(acct, k), K.derive_intent_id(acct, k)
                            lot = K.derive_lot_id(acct, i)
                            ids.update((d[4:], i[4:], lot[4:]))
                            cids.update((K.client_id_for(i), K.client_id_for(i, 'algo')))
                            n += 1
    assert len(keys_seen) == n // 2
    assert len(ids) == 3 * n and len(cids) == 2 * n        # 102,400 keyed derivations, no collision anywhere
