"""Restart-stable identity over the NC-01 DecisionKey (STEP0_INTERFACE.md sections 1-2).

The key is newcore.domain.DecisionKey itself and its bytes are NC-01 codec.canonical_bytes(key): no mirror type.
`decision_key()` is the ONE runner-boundary constructor: it builds `strategy` as the canonical strategy instance
'<name>@<tf>' (the timeframe is part of the identity) and refuses a candle close that is not on the timeframe grid.

Derivations (pure, deterministic, no clock / randomness / environment):
    decision_id  = 'dec_' + H('decision_id', account_id, key_bytes)[:32 hex]
    intent_id    = 'int_' + H('intent_id', account_id, key_bytes, '0')[:32 hex]          one intent per keyed decision
    lot_id       = 'lot_' + H('lot_id', account_id, entry_intent_id)[:32 hex]
    child intent = 'int_' + H('child_intent_id', account_id, owner_id, purpose, ordinal)[:32 hex]
                   owner_id = the int_ entry or lot_ the intent works for; ordinal = how many intents of that
                   (owner, purpose) the journal already holds (the journal, not the caller, decides it)
    client id    = 'zbn1' + ('o' classic | 'a' algo) + '-' + base32(H('client_id', intent_id, route))[:26]   (32 chars)
H(tag, *parts) = sha256(b'zackbot.newcore.' + tag + b'.v1' + (b'\\x00' + part)...); no part can contain NUL.
"""
from __future__ import annotations

import base64
import hashlib
import re

from newcore.domain import DecisionKey, Purpose, Side
from newcore.domain.codec import canonical_bytes

from .values import ROUTES, check_choice, check_id, check_int, check_ms, plain, req

TIMEFRAMES = {'1m': 60_000, '3m': 180_000, '5m': 300_000, '15m': 900_000, '30m': 1_800_000, '1h': 3_600_000,
              '2h': 7_200_000, '4h': 14_400_000, '6h': 21_600_000, '8h': 28_800_000, '12h': 43_200_000,
              '1d': 86_400_000}
STRATEGY_NAME_RE = re.compile(r'[a-z0-9_]{1,24}')
STRATEGY_INSTANCE_RE = re.compile(r'([a-z0-9_]{1,24})@(' + '|'.join(TIMEFRAMES) + ')')
VERSION_RE = re.compile(r'v[0-9]{1,6}')
MAX_ORDINAL = 2 ** 31 - 1
CLIENT_ID_PREFIX = {'classic': 'zbn1o-', 'algo': 'zbn1a-'}
CLIENT_ID_HASH_CHARS = 26           # 130 bits of base32; total length 32 <= Binance's 36
NEWCORE_CLIENT_ID_RE = re.compile(r'zbn1[oa]-[a-z2-7]{26}')


# ---------------------------------------------------------------------------------------------- the decision key
def strategy_instance(name: str, tf: str) -> str:
    """The canonical strategy-instance name '<rule name>@<timeframe>', e.g. strategy_instance('trend_ema_mom', '4h')."""
    req(isinstance(name, str) and STRATEGY_NAME_RE.fullmatch(name) is not None, 'strategy.name', '[a-z0-9_]{1,24}')
    check_choice(tf, 'strategy.tf', tuple(TIMEFRAMES))
    return f'{name}@{tf}'


def check_decision_key(key) -> int:
    """A keyed decision's key must come from decision_key(): instance name, version, on-grid close. Returns tf_ms."""
    req(isinstance(key, DecisionKey), 'key', 'an NC-01 DecisionKey')
    m = STRATEGY_INSTANCE_RE.fullmatch(key.strategy)
    req(m is not None, 'DecisionKey.strategy', f'{key.strategy!r} is not a <name>@<tf> strategy instance')
    req(VERSION_RE.fullmatch(key.strategy_version) is not None, 'DecisionKey.strategy_version', 'v<digits>')
    tf_ms = TIMEFRAMES[m.group(2)]
    req(key.candle_close_ms % tf_ms == 0, 'DecisionKey.candle_close_ms', f'not on the {m.group(2)} candle grid')
    return tf_ms


def decision_key(name: str, version: str, tf: str, symbol: str, side, candle_close_ms: int, purpose) -> DecisionKey:
    """The one runner-boundary constructor of a strategy DecisionKey."""
    check_ms(candle_close_ms, 'candle_close_ms')
    key = DecisionKey(strategy=strategy_instance(name, tf), strategy_version=version, symbol=symbol,
                      side=Side(plain(side)), candle_close_ms=candle_close_ms, purpose=Purpose(plain(purpose)))
    check_decision_key(key)
    return key


# ---------------------------------------------------------------------------------------------- derivations
def _h(tag, *parts):
    h = hashlib.sha256(b'zackbot.newcore.' + tag.encode('ascii') + b'.v1')
    for part in parts:
        h.update(b'\x00' + part)
    return h.digest()


def _key_bytes(key):
    req(isinstance(key, DecisionKey), 'key', 'an NC-01 DecisionKey')
    return canonical_bytes(key)


def derive_decision_id(account_id: str, key: DecisionKey) -> str:
    """One decision per (account, key): the consumed-signal rule."""
    check_id(account_id, 'account_id', 'acct')
    return 'dec_' + _h('decision_id', account_id.encode('ascii'), _key_bytes(key)).hex()[:32]


def _derive_leg(account_id, key, leg):
    """Leg-n id (the hash keeps a leg slot so a later multi-intent decision can be added under a new contract)."""
    check_id(account_id, 'account_id', 'acct')
    check_int(leg, 'leg', 0, 15)
    return 'int_' + _h('intent_id', account_id.encode('ascii'), _key_bytes(key), str(leg).encode('ascii')).hex()[:32]


def derive_intent_id(account_id: str, key: DecisionKey) -> str:
    """The one intent a keyed decision may authorize."""
    return _derive_leg(account_id, key, 0)


def derive_lot_id(account_id: str, entry_intent_id: str) -> str:
    """The lot opened by an entry intent's fill."""
    check_id(account_id, 'account_id', 'acct')
    check_id(entry_intent_id, 'entry_intent_id', 'int')
    return 'lot_' + _h('lot_id', account_id.encode('ascii'), entry_intent_id.encode('ascii')).hex()[:32]


def derive_child_intent_id(account_id: str, owner_id: str, purpose, ordinal: int) -> str:
    """Id of a follow-up intent no candle keys (the protective stop, its route fallback / replacements, a reduce or
    close of a lot). ordinal = the journal's count of earlier intents of this (owner, purpose)."""
    check_id(account_id, 'account_id', 'acct')
    check_id(owner_id, 'owner_id', 'int', 'lot')
    purpose = Purpose(plain(purpose))
    check_int(ordinal, 'ordinal', 0, MAX_ORDINAL)
    return 'int_' + _h('child_intent_id', account_id.encode('ascii'), owner_id.encode('ascii'),
                       purpose.value.encode('ascii'), str(ordinal).encode('ascii')).hex()[:32]


def client_id_for(intent_id: str, route: str = 'classic') -> str:
    """The venue client id (newClientOrderId / clientAlgoId) of an intent on its one route."""
    check_id(intent_id, 'intent_id', 'int')
    route = plain(route)
    check_choice(route, 'route', ROUTES)
    b32 = base64.b32encode(_h('client_id', intent_id.encode('ascii'), route.encode('ascii'))).decode('ascii')
    return CLIENT_ID_PREFIX[route] + b32.lower()[:CLIENT_ID_HASH_CHARS]


def route_of(intent_id: str, client_id: str):
    """'classic' / 'algo' when client_id is that route's derived id for intent_id, else None."""
    return next((r for r in ROUTES if client_id_for(intent_id, r) == client_id), None)


def is_newcore_client_id(client_id) -> bool:
    """Shape test only (owned-vs-foreign triage). Never parsed for business data."""
    return isinstance(client_id, str) and NEWCORE_CLIENT_ID_RE.fullmatch(client_id) is not None
