"""Record base class and value rules shared by every record family.

- Records are `@record` classes: stdlib dataclasses with frozen=True, slots=True, kw_only=True. `dataclasses.replace` is
  the only way to "change" one, and it re-runs every check.
- `Record.__post_init__` type-checks every field from its annotation, then runs the record's own `_validate`. Decoding goes
  through the constructor, so an invalid record can exist neither in memory nor on disk.
- Money, quantity and price are `Decimal` (ruling 2). A float, bool, NaN or Infinity is rejected wherever a Decimal is
  declared. Bounds: |x| < 10**15 and at most 12 fractional digits, so every value is exact within the default 28-digit
  context and a hostile 1e300 can never be stored.
- Timestamps are integer UTC milliseconds. By convention every timestamp field name ends in `_ms`, and its value must lie
  in [2000-01-01, 2100-01-01), which also catches seconds passed as milliseconds.
- Ids are opaque `<prefix>_<32 lowercase hex>`, never time-derived. Bot client order ids keep the legacy `new_cid` shape.
"""
from __future__ import annotations

import dataclasses
import enum
import functools
import hashlib
import re
import types
import typing
from decimal import Decimal

from .errors import InvalidRecord

MAX_ABS = Decimal(10) ** 15
MAX_FRACTION_DIGITS = 12
INT64 = (-(2 ** 63), 2 ** 63 - 1)
MIN_TS_MS = 946_684_800_000          # 2000-01-01T00:00:00Z
MAX_TS_MS = 4_102_444_800_000        # 2100-01-01T00:00:00Z
ZERO = Decimal(0)

SYMBOL_RE = re.compile(r'[A-Z0-9]{2,30}')
CID_RE = re.compile(r'z[a-z][0-9a-f]{22}')             # bot client order id (legacy new_cid shape); nothing else is ours
ID_RE = re.compile(r'([a-z]{2,4})_[0-9a-f]{32}')
ID_PREFIXES = frozenset({'acct', 'lot', 'int', 'dec', 'rec'})


def req(cond, path, msg):
    if not cond:
        raise InvalidRecord(path, msg)


# ----------------------------------------------------------------------------------------------------------- values
def check_decimal(v, path):
    req(type(v) is Decimal, path, f'not a Decimal ({type(v).__name__})')
    req(v.is_finite(), path, 'not finite')
    req(abs(v) < MAX_ABS, path, f'|{v}| is not below 1e15')
    req(-v.as_tuple().exponent <= MAX_FRACTION_DIGITS, path, f'more than {MAX_FRACTION_DIGITS} fractional digits')
    req(not (v.is_zero() and v.is_signed()), path, 'negative zero')


def dec_str(v):
    """Canonical decimal text: equal values give equal bytes ('1.50' and '1.5' both encode as '1.5'), never an exponent."""
    return format(v.normalize(), 'f')


def check_int(v, path):
    req(type(v) is int, path, f'not an int ({type(v).__name__})')
    req(INT64[0] <= v <= INT64[1], path, 'outside int64')


def check_ms(v, path):
    req(type(v) is int, path, f'a timestamp is integer UTC milliseconds, not {type(v).__name__}')
    req(MIN_TS_MS <= v < MAX_TS_MS, path, f'{v} is not UTC milliseconds in [2000, 2100)')


def positive(v, path):
    req(v > 0, path, f'{v} must be > 0')


def non_negative(v, path):
    req(v >= 0, path, f'{v} must be >= 0')


def check_id(v, path, *prefixes):
    m = ID_RE.fullmatch(v) if type(v) is str else None
    req(m is not None and m.group(1) in prefixes, path, f'{v!r} is not a {"/".join(prefixes)}_<32 hex> id')


def check_cid(v, path):
    req(type(v) is str and CID_RE.fullmatch(v) is not None, path, f'{v!r} is not a bot client order id')


def check_symbol(v, path):
    req(type(v) is str and SYMBOL_RE.fullmatch(v) is not None, path, f'{v!r} is not a symbol')


def make_id(prefix, n):
    """`prefix_<32 hex>` from an integer the caller supplies (uuid4().int in the shell). The domain owns no randomness."""
    req(prefix in ID_PREFIXES, 'id.prefix', f'unknown id prefix {prefix!r}')
    req(type(n) is int and 0 <= n < 2 ** 128, 'id.n', 'needs a 128-bit non-negative int')
    return f'{prefix}_{n:032x}'


def make_cid(kind, n):
    """Bot client order id `z<kind><22 hex>` from a caller-supplied integer (88 bits)."""
    req(type(kind) is str and re.fullmatch(r'[a-z]', kind) is not None, 'cid.kind', 'one lowercase letter')
    req(type(n) is int and 0 <= n < 2 ** 88, 'cid.n', 'needs an 88-bit non-negative int')
    return f'z{kind}{n:022x}'


def deterministic_cid(kind, *parts):
    """A client order id that is a pure function of its parts. A retried placement after a crash re-sends the SAME id,
    so the exchange refuses a duplicate instead of resting a second order (hard-HOLD rule 2)."""
    req(type(kind) is str and re.fullmatch(r'[a-z]', kind) is not None, 'cid.kind', 'one lowercase letter')
    digest = hashlib.sha256('|'.join(str(p) for p in parts).encode('ascii')).hexdigest()
    return f'z{kind}{digest[:22]}'


# ----------------------------------------------------------------------------------------------------------- records
def _checker(tp, name):
    origin = typing.get_origin(tp)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        req(len(args) == 1, name, 'only Optional[X] unions are supported')
        inner = _checker(args[0], name)
        return lambda v, p: None if v is None else inner(v, p)
    if origin is tuple:
        args = typing.get_args(tp)
        req(len(args) == 2 and args[1] is Ellipsis, name, 'tuples are tuple[X, ...]')
        el = _checker(args[0], name)

        def check_tuple(v, p):
            req(type(v) is tuple, p, f'not a tuple ({type(v).__name__})')
            for i, x in enumerate(v):
                el(x, f'{p}[{i}]')
        return check_tuple
    if tp is Decimal:
        return check_decimal
    if tp is int:
        return check_ms if name.endswith('_ms') else check_int
    if tp is bool:
        return lambda v, p: req(type(v) is bool, p, f'not a bool ({type(v).__name__})')
    if tp is str:
        return lambda v, p: req(type(v) is str, p, f'not a str ({type(v).__name__})')
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return lambda v, p: req(isinstance(v, tp), p, f'{v!r} is not a {tp.__name__}')
    if isinstance(tp, type) and issubclass(tp, Record):
        return lambda v, p: req(isinstance(v, tp), p, f'not a {tp.__name__} ({type(v).__name__})')
    raise TypeError(f'{name}: unsupported field type {tp!r}')


@functools.cache
def field_spec(cls):
    """((name, annotation, checker), ...) in declaration order. Cached: hints are resolved once per class."""
    hints = typing.get_type_hints(cls)
    return tuple((f.name, hints[f.name], _checker(hints[f.name], f.name)) for f in dataclasses.fields(cls))


class Record:
    """Base of every domain record: field types first, then the record's own invariants."""
    __slots__ = ()

    def __post_init__(self):
        cls = type(self)
        for name, _, check in field_spec(cls):
            check(getattr(self, name), f'{cls.__name__}.{name}')
        self._validate(cls.__name__)

    def _validate(self, p):        # p = class name, the path prefix for errors
        pass


record = dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
