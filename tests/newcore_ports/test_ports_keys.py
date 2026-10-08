"""Step-0 DecisionKey canonical form and the restart-stable id derivations (STEP0_INTERFACE.md sections 1-2)."""
import dataclasses
import re

import pytest

from newcore.ports import keys as K
from newcore.ports.values import PortValueError

ACCT = 'acct_' + '0123456789abcdef' * 2
ACCT2 = 'acct_' + 'fedcba9876543210' * 2
KEY = dict(strategy='trend_ema_mom@4h', strategy_version='v1', symbol='BTCUSDT', side='LONG',
           candle_close_ms=1759924800000, purpose='entry')
CANONICAL = (b'{"body":{"candle_close_ms":1759924800000,"purpose":"entry","side":"LONG","strategy":"trend_ema_mom@4h",'
             b'"strategy_version":"v1","symbol":"BTCUSDT"},"format":"zackbot.newcore","record_type":"decision_key",'
             b'"schema_version":1}')
BINANCE_CID = re.compile(r'^[.A-Z:/a-z0-9_-]{1,36}$')
LEGACY_BOT_CID = re.compile(r'z[ab][0-9a-f]{22}')      # engine.BOT_STOP_CID_RE: legacy ids must never look like ours


def key(**kw):
    return K.DecisionKey(**{**KEY, **kw})


# ------------------------------------------------------------------------------------------------ canonical form
def test_canonical_bytes_are_pinned_and_match_the_nc01_envelope():
    k = key()
    assert k.canonical_bytes() == CANONICAL
    assert k.sha256() == 'eed91949b70e10d99ac957dd6a1e255d0ef75b89fef07b41e70883eb5730ac04'


@pytest.mark.parametrize('kw', [{}, {'side': 'SHORT', 'purpose': 'close'}, {'strategy': 'é strat', 'symbol': 'X1'},
                                {'candle_close_ms': 946684800000}, {'candle_close_ms': 4102444799999}])
def test_round_trip(kw):
    k = key(**kw)
    assert K.DecisionKey.from_canonical(k.canonical_bytes()) == k
    assert K.DecisionKey.from_canonical(k.canonical_bytes().decode()) == k


def test_str_enum_members_normalize_to_plain_strings():
    import enum

    class Side(enum.StrEnum):
        LONG = 'LONG'
    k = key(side=Side.LONG)
    assert type(k.side) is str and k == key() and k.canonical_bytes() == CANONICAL


@pytest.mark.parametrize('bad', [
    CANONICAL.replace(b'{"body"', b'{ "body"'),                                   # non-canonical whitespace
    CANONICAL.replace(b'"schema_version":1', b'"schema_version":true'),            # bool for int
    CANONICAL.replace(b'"schema_version":1', b'"schema_version":2'),               # other version
    CANONICAL.replace(b'1759924800000', b'1759924800000.0'),                       # float
    CANONICAL.replace(b'1759924800000', b'"1759924800000"'),                       # timestamp as text
    CANONICAL.replace(b'"symbol":"BTCUSDT"', b'"symbol":"BTCUSDT","symbol":"BTCUSDT"'),  # duplicate key
    CANONICAL.replace(b',"symbol":"BTCUSDT"', b''),                                # missing field
    CANONICAL.replace(b'"symbol":"BTCUSDT"', b'"symbol":"BTCUSDT","tf":"4h"'),     # unknown field
    CANONICAL.replace(b'decision_key', b'order_intent'),                           # other record type
    CANONICAL.replace(b'"side":"LONG"', b'"side":"long"'),                         # enum spelling
    CANONICAL[:-1],                                                                # truncated
    b'[]', b'',
])
def test_strict_decode_rejects(bad):
    with pytest.raises(PortValueError):
        K.DecisionKey.from_canonical(bad)


@pytest.mark.parametrize('kw', [{'strategy': ''}, {'strategy': 'x' * 33}, {'strategy': 'a\nb'}, {'symbol': 'btcusdt'},
                                {'side': 'BOTH'}, {'purpose': 'ENTRY'}, {'candle_close_ms': True},
                                {'candle_close_ms': 1759924800000.0}, {'candle_close_ms': 946684799999},
                                {'candle_close_ms': 4102444800000}, {'strategy_version': 7}])
def test_constructor_rejects(kw):
    with pytest.raises(PortValueError):
        key(**kw)


def test_key_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        key().side = 'SHORT'


# ------------------------------------------------------------------------------------------------ derivations
def test_pinned_vectors_are_restart_stable():
    """Changing any of these strings orphans every journal written before the change: bump the .v1 tags instead."""
    i = K.derive_intent_id(ACCT, key())
    assert K.derive_decision_id(ACCT, key()) == 'dec_1b68c1f8c6cd51de38b74ff95bf3ae0d'
    assert i == 'int_d4bbf9ba13b6d5a0d78481b4ca7f03b7'
    c = K.derive_child_intent_id(ACCT, i, 'protect', 0)
    assert c == 'int_48597b182eae2f00b76fc331bacf47a4'
    assert K.client_id_for(i) == 'zbn1o-bw72dangb4tswgg3zlvukcybva'
    assert K.client_id_for(c, 'algo') == 'zbn1a-ulh3g3nn34jhqydvoho6aviezp'


def test_derivation_is_deterministic_and_separates_inputs():
    i0 = K.derive_intent_id(ACCT, key())
    assert K.derive_intent_id(ACCT, K.DecisionKey.from_canonical(CANONICAL)) == i0
    assert K.derive_intent_id(ACCT, key(), 1) != i0
    assert K.derive_intent_id(ACCT2, key()) != i0
    assert K.derive_decision_id(ACCT, key())[4:] != i0[4:]
    assert K.client_id_for(i0, 'classic') != K.client_id_for(i0, 'algo')
    assert K.derive_child_intent_id(ACCT, i0, 'protect', 0) != K.derive_child_intent_id(ACCT, i0, 'protect', 1)
    assert K.derive_child_intent_id(ACCT, i0, 'protect', 0) != K.derive_child_intent_id(ACCT, i0, 'close', 0)


@pytest.mark.parametrize('call', [
    lambda: K.derive_intent_id(ACCT, key(), K.MAX_LEGS), lambda: K.derive_intent_id(ACCT, key(), -1),
    lambda: K.derive_intent_id(ACCT, key(), True), lambda: K.derive_intent_id('acct_x', key()),
    lambda: K.derive_decision_id('int_' + '0' * 32, key()), lambda: K.client_id_for('int_' + '0' * 32, 'ALGO'),
    lambda: K.client_id_for('dec_' + '0' * 32), lambda: K.derive_child_intent_id(ACCT, 'lot_' + '0' * 32, 'protect', 0),
    lambda: K.derive_child_intent_id(ACCT, 'int_' + '0' * 32, 'stop', 0),
])
def test_derivation_rejects_bad_input(call):
    with pytest.raises(PortValueError):
        call()


def test_client_id_shape_length_and_charset():
    for n in range(2000):
        intent = K.derive_intent_id(ACCT, key(candle_close_ms=1759924800000 + n * 900_000))
        for route in ('classic', 'algo'):
            cid = K.client_id_for(intent, route)
            assert len(cid) == 32 <= 36
            assert BINANCE_CID.fullmatch(cid) and K.is_newcore_client_id(cid)
            assert not LEGACY_BOT_CID.fullmatch(cid)
    assert not K.is_newcore_client_id('zb' + '0' * 22) and not K.is_newcore_client_id(None)


def test_no_collisions_across_a_large_deterministic_sample():
    symbols = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT')
    t0 = 1640995200000
    ids, cids, keys_seen = set(), set(), set()
    n = 0
    for sym in symbols:
        for side in ('LONG', 'SHORT'):
            for purpose in ('entry', 'close'):
                for strategy in ('trend_ema_mom@4h', 'trend_ema_mom@15m'):
                    for j in range(800):
                        k = key(symbol=sym, side=side, purpose=purpose, strategy=strategy,
                                candle_close_ms=t0 + j * 900_000)
                        keys_seen.add(k.canonical_bytes())
                        for acct in (ACCT, ACCT2):
                            d = K.derive_decision_id(acct, k)
                            i = K.derive_intent_id(acct, k)
                            ids.update((d[4:], i[4:]))
                            cids.update((K.client_id_for(i), K.client_id_for(i, 'algo')))
                            n += 1
    assert len(keys_seen) == n // 2
    assert len(ids) == 2 * n and len(cids) == 2 * n        # 102,400 keyed derivations, no collision anywhere
