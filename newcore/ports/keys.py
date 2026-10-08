"""DecisionKey and the restart-stable id derivations (STEP0_INTERFACE.md sections 1-2).

DecisionKey = strategy / version + symbol + side + candle close + purpose. Its canonical bytes are byte-identical to
NC-01's `codec.canonical_bytes(DecisionKey)` (same envelope, sorted keys, no whitespace, ASCII), so the binding to
newcore.domain.DecisionKey when NC-01 lands changes no derived id.

Derivations (pure, deterministic, no clock / randomness / environment):
    decision_id  = 'dec_' + H('decision_id', account_id, key_bytes)[:32 hex]
    intent_id    = 'int_' + H('intent_id', account_id, key_bytes, leg)[:32 hex]            leg 0..MAX_LEGS-1
    child intent = 'int_' + H('child_intent_id', account_id, parent_intent_id, purpose, ordinal)[:32 hex]
    client id    = 'zbn1' + ('o' classic | 'a' algo) + '-' + base32(H('client_id', intent_id, route))[:26]   (32 chars)
H(tag, *parts) = sha256(b'zackbot.newcore.' + tag + b'.v1' + (b'\\x00' + part)...); no part can contain NUL.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass

from .values import (PURPOSES, ROUTES, SIDES, PortValueError, check_choice, check_id, check_int, check_ms, check_symbol,
                     check_text, plain, req)

FORMAT = 'zackbot.newcore'          # NC-01 codec.FORMAT
SCHEMA_VERSION = 1                  # NC-01 codec.SCHEMA_VERSION
RECORD_TYPE = 'decision_key'        # NC-01 codec.RECORD_TYPES key
KEY_FIELDS = ('candle_close_ms', 'purpose', 'side', 'strategy', 'strategy_version', 'symbol')
MAX_LEGS = 16                       # intents one keyed decision may create (VS-01 uses leg 0 only)
MAX_ORDINAL = 2 ** 31 - 1
CLIENT_ID_PREFIX = {'classic': 'zbn1o-', 'algo': 'zbn1a-'}
CLIENT_ID_HASH_CHARS = 26           # 130 bits of base32; total length 32 <= Binance's 36
NEWCORE_CLIENT_ID_RE = re.compile(r'zbn1[oa]-[a-z2-7]{26}')


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionKey:
    """The canonical key of one strategy decision. Bound to newcore.domain.DecisionKey when NC-01 lands.

    candle_close_ms = the signal candle's open_ms + tf_ms (exclusive end; Binance kline closeTime + 1).
    strategy names the strategy INSTANCE including its timeframe (e.g. 'trend_ema_mom@4h'); strategy_version the rule."""
    strategy: str
    strategy_version: str
    symbol: str
    side: str
    candle_close_ms: int
    purpose: str

    def __post_init__(self):
        for name in ('strategy', 'strategy_version', 'side', 'purpose', 'symbol'):
            object.__setattr__(self, name, plain(getattr(self, name)))
        check_text(self.strategy, 'DecisionKey.strategy', 32)
        check_text(self.strategy_version, 'DecisionKey.strategy_version', 32)
        check_symbol(self.symbol, 'DecisionKey.symbol')
        check_choice(self.side, 'DecisionKey.side', SIDES)
        check_ms(self.candle_close_ms, 'DecisionKey.candle_close_ms')
        check_choice(self.purpose, 'DecisionKey.purpose', PURPOSES)

    def canonical_bytes(self) -> bytes:
        body = {f: getattr(self, f) for f in KEY_FIELDS}
        doc = {'format': FORMAT, 'schema_version': SCHEMA_VERSION, 'record_type': RECORD_TYPE, 'body': body}
        return json.dumps(doc, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')

    def sha256(self) -> str:
        """Lowercase hex SHA-256 of canonical_bytes (equals NC-01 contract_sha256(key))."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_canonical(cls, data) -> 'DecisionKey':
        """Strict decode: exact envelope and body keys, no duplicate keys, ints never bools / floats, and the input must
        be the canonical spelling (re-encoding gives the identical bytes)."""
        raw = data.encode('utf-8') if isinstance(data, str) else data
        req(isinstance(raw, (bytes, bytearray)), 'DecisionKey', 'bytes or text')
        try:
            doc = json.loads(bytes(raw).decode('ascii'), object_pairs_hook=_no_duplicates,
                             parse_float=_refuse, parse_constant=_refuse)
        except (UnicodeDecodeError, ValueError) as ex:
            raise PortValueError('DecisionKey', f'not canonical ASCII JSON ({type(ex).__name__})') from None
        req(type(doc) is dict and set(doc) == {'format', 'schema_version', 'record_type', 'body'}, 'DecisionKey',
            'envelope keys')
        req(doc['format'] == FORMAT and doc['record_type'] == RECORD_TYPE, 'DecisionKey', 'not a decision_key document')
        req(type(doc['schema_version']) is int and doc['schema_version'] == SCHEMA_VERSION, 'DecisionKey.schema_version',
            f'only version {SCHEMA_VERSION}')
        body = doc['body']
        req(type(body) is dict and set(body) == set(KEY_FIELDS), 'DecisionKey.body', f'exactly {KEY_FIELDS}')
        req(all(type(body[f]) is str for f in KEY_FIELDS if f != 'candle_close_ms'), 'DecisionKey.body', 'text fields')
        key = cls(**body)
        req(key.canonical_bytes() == bytes(raw), 'DecisionKey', 'not the canonical spelling')
        return key


def _no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError('duplicate key')
        out[k] = v
    return out


def _refuse(text):
    raise ValueError(f'float / constant {text!r}')


def _h(tag, *parts):
    h = hashlib.sha256(b'zackbot.newcore.' + tag.encode('ascii') + b'.v1')
    for part in parts:
        h.update(b'\x00' + part)
    return h.digest()


def _check_key(key):
    req(isinstance(key, DecisionKey) or hasattr(key, 'candle_close_ms'), 'key', 'a DecisionKey')
    return key if isinstance(key, DecisionKey) else DecisionKey(**{f: getattr(key, f) for f in KEY_FIELDS})


def derive_decision_id(account_id: str, key: DecisionKey) -> str:
    """The restart-stable decision id of a keyed (strategy) decision. One per (account, key): the consumed-signal rule."""
    check_id(account_id, 'account_id', 'acct')
    return 'dec_' + _h('decision_id', account_id.encode('ascii'), _check_key(key).canonical_bytes()).hex()[:32]


def derive_intent_id(account_id: str, key: DecisionKey, leg: int = 0) -> str:
    """The restart-stable id of intent number `leg` created by the keyed decision."""
    check_id(account_id, 'account_id', 'acct')
    check_int(leg, 'leg', 0, MAX_LEGS - 1)
    k = _check_key(key).canonical_bytes()
    return 'int_' + _h('intent_id', account_id.encode('ascii'), k, str(leg).encode('ascii')).hex()[:32]


def derive_child_intent_id(account_id: str, parent_intent_id: str, purpose: str, ordinal: int) -> str:
    """Restart-stable id of a follow-up intent that no candle decision keys (the protective stop of an entry, its
    replacements, a drain). ordinal = how many `purpose` intents of this parent the journal already recorded."""
    check_id(account_id, 'account_id', 'acct')
    check_id(parent_intent_id, 'parent_intent_id', 'int')
    purpose = plain(purpose)
    check_choice(purpose, 'purpose', PURPOSES)
    check_int(ordinal, 'ordinal', 0, MAX_ORDINAL)
    return 'int_' + _h('child_intent_id', account_id.encode('ascii'), parent_intent_id.encode('ascii'),
                       purpose.encode('ascii'), str(ordinal).encode('ascii')).hex()[:32]


def client_id_for(intent_id: str, route: str = 'classic') -> str:
    """The venue client id (newClientOrderId / clientAlgoId) of an intent: one per (intent, route), never reused."""
    check_id(intent_id, 'intent_id', 'int')
    route = plain(route)
    check_choice(route, 'route', ROUTES)
    b32 = base64.b32encode(_h('client_id', intent_id.encode('ascii'), route.encode('ascii'))).decode('ascii')
    return CLIENT_ID_PREFIX[route] + b32.lower()[:CLIENT_ID_HASH_CHARS]


def is_newcore_client_id(client_id) -> bool:
    """Shape test only (owned-vs-foreign triage). Never parsed for business data."""
    return isinstance(client_id, str) and NEWCORE_CLIENT_ID_RE.fullmatch(client_id) is not None
