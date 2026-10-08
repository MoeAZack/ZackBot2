"""Record base class and the value rules every record family shares.

- Records are `@record` classes: stdlib dataclasses with frozen=True, slots=True, kw_only=True. `dataclasses.replace` is
  the only way to "change" one, and it re-runs every check.
- `Record.__post_init__` type-checks every field from its annotation, then runs the record's own `_validate`. Decoding goes
  through the constructor, so an invalid record can exist neither in memory nor on disk.
- Owned price / quantity / fee / risk values are `Decimal` (contract invariant 2). A float, bool, int, NaN or Infinity is
  rejected wherever a Decimal is declared. Bounds: at most 38 significant digits and |adjusted exponent| <= 18. The
  constructor NORMALIZES every Decimal field to its canonical value (1, 1.0 and 10E-1 become the same Decimal('1')), and
  domain arithmetic runs in the exact context CTX, so nothing depends on the ambient decimal context.
- Timestamps are integer (never bool) UTC milliseconds in [946684800000, 4102444799999] (2000-01-01 .. 2099-12-31 UTC).
  Every timestamp field name ends in `_ms`. Cairo / trading-day labels are presentation only and never record fields.
- Ids are opaque `<prefix>_<32 lowercase hex>`, supplied by the caller (make_id only formats a caller-supplied 128-bit
  integer); never derived from text or time, and the prefix only tags the id family - it is never parsed for business
  data. The domain reads no clock, random source, UUID generator or environment. Exchange client order ids are opaque
  strings the venue adapter derives (NC-03); the domain only checks they are present and well-formed.
"""
from __future__ import annotations

import dataclasses
import enum
import functools
import re
import types
import typing
import unicodedata
from decimal import Context, Decimal, Inexact, InvalidOperation, Overflow, Rounded

from .errors import InvalidRecord

MAX_SIGNIFICANT = 38
MAX_ADJUSTED = 18                    # |adjusted exponent| <= 18: 1e-18 <= |x| < 1e19 (or exactly 0)
INT64 = (-(2 ** 63), 2 ** 63 - 1)
MIN_TS_MS = 946_684_800_000          # 2000-01-01T00:00:00.000Z
MAX_TS_MS = 4_102_444_799_999        # 2099-12-31T23:59:59.999Z (inclusive)
ZERO = Decimal(0)
# Exact arithmetic for bounded values: sums / differences / quantization never round silently (Inexact / Rounded trap).
CTX = Context(prec=80, Emin=-999, Emax=999, traps=[InvalidOperation, Overflow, Inexact, Rounded])

SYMBOL_RE = re.compile(r'[A-Z0-9]{2,30}')
CLIENT_ID_RE = re.compile(r'[A-Za-z0-9._:/-]{1,36}')   # opaque; the venue adapter chooses the format
ID_RE = re.compile(r'([a-z]{2,4})_[0-9a-f]{32}')
ID_PREFIXES = frozenset({'acct', 'pf', 'pos', 'lot', 'int', 'res', 'dec', 'rec', 'evt', 'inc'})


def show(v, n=40):
    """A short, safe rendering of a value for an error message (never the whole of a huge value)."""
    r = repr(v) if not isinstance(v, str) or len(v) <= n else f'{v[:n]!r}...<{len(v)} chars>'
    return r if len(r) <= n + 30 else f'{r[:n]}...<{type(v).__name__}>'


def req(cond, path, msg):
    if not cond:
        raise InvalidRecord(path, msg)


# ----------------------------------------------------------------------------------------------------------- values
def canonical_decimal(v, path):
    """Validate a Decimal and return its canonical value (trailing zeroes removed, no positive exponent)."""
    if type(v) is not Decimal:
        raise InvalidRecord(path, f'not a Decimal ({type(v).__name__})')
    if not v.is_finite():
        raise InvalidRecord(path, 'not finite')
    if v.is_zero():
        if v.is_signed():
            raise InvalidRecord(path, 'negative zero')
        return ZERO
    if abs(v.adjusted()) > MAX_ADJUSTED:
        raise InvalidRecord(path, f'|adjusted exponent| {abs(v.adjusted())} above {MAX_ADJUSTED}')
    sign, digits, exp = v.as_tuple()
    while len(digits) > 1 and digits[-1] == 0:           # strip trailing zeroes exactly (no context rounding)
        digits, exp = digits[:-1], exp + 1
    if len(digits) > MAX_SIGNIFICANT:
        raise InvalidRecord(path, f'more than {MAX_SIGNIFICANT} significant digits')
    if exp > 0:                                           # 1E+2 -> 100: a canonical value has no positive exponent
        digits, exp = digits + (0,) * exp, 0
    return Decimal((sign, digits, exp))


def check_decimal(v, path):
    canonical_decimal(v, path)


def dec_str(v):
    """Canonical decimal text (contract invariant 2): equal values give equal bytes, never an exponent or a '+'."""
    return format(canonical_decimal(v, 'decimal'), 'f')


def check_int(v, path):
    req(type(v) is int, path, f'not an int ({type(v).__name__})')
    req(INT64[0] <= v <= INT64[1], path, 'outside int64')


def check_ms(v, path):
    req(type(v) is int, path, f'a timestamp is integer UTC milliseconds, not {type(v).__name__}')
    req(MIN_TS_MS <= v <= MAX_TS_MS, path, f'{show(v)} is not UTC milliseconds in [{MIN_TS_MS}, {MAX_TS_MS}]')


def positive(v, path):
    req(v > 0, path, f'{show(v)} must be > 0')


def non_negative(v, path):
    req(v >= 0, path, f'{show(v)} must be >= 0')


def check_id(v, path, *prefixes):
    m = ID_RE.fullmatch(v) if type(v) is str else None
    req(m is not None and m.group(1) in prefixes, path, f'{show(v)} is not a {"/".join(prefixes)}_<32 hex> id')


def check_client_id(v, path):
    req(type(v) is str and CLIENT_ID_RE.fullmatch(v) is not None, path, f'{show(v)} is not a client order id')


def check_symbol(v, path):
    req(type(v) is str and SYMBOL_RE.fullmatch(v) is not None, path, f'{show(v)} is not a symbol')


def check_text(v, path, n):
    """Bounded printable text that is never blank: an absent value is None, never '' or whitespace (Codex ruling 2)."""
    req(type(v) is str and 0 < len(v) <= n and v.isprintable(), path, f'1..{n} printable characters')
    req(v.strip() != '', path, 'whitespace only (an absent value is None, never blank text)')
    check_nfc(v, path)


def check_nfc(v, path):
    """PR #44 (Cowork 5): free text has ONE canonical spelling - Unicode NFC. A decomposed (NFD) spelling of the same
    visible text is refused, never silently normalized (normalizing would change the caller's bytes)."""
    req(unicodedata.is_normalized('NFC', v), path, 'text must be Unicode NFC (one canonical spelling)')


ASCII_TEXT_RE = re.compile(r'[ -~]+')              # printable ASCII


def check_ascii_text(v, path, n):
    """Identifier-like / incident text: 1..n printable ASCII characters, never blank. ASCII has one spelling per
    visible value (no NFC / NFD forms, no homoglyphs)."""
    req(type(v) is str and 0 < len(v) <= n and ASCII_TEXT_RE.fullmatch(v) is not None, path,
        f'1..{n} printable ASCII characters')
    req(v.strip() != '', path, 'whitespace only (an absent value is None, never blank text)')


def make_id(prefix, n):
    """`prefix_<32 hex>` from a 128-bit integer the caller supplies (uuid4().int in the shell): no randomness here."""
    req(prefix in ID_PREFIXES, 'id.prefix', f'unknown id prefix {prefix!r}')
    req(type(n) is int and 0 <= n < 2 ** 128, 'id.n', 'needs a 128-bit non-negative int')
    return f'{prefix}_{n:032x}'


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
        return canonical_decimal
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
        for name, _, check, is_dec in _spec(cls):
            v = getattr(self, name)
            out = check(v, f'{cls.__name__}.{name}')
            if is_dec and v is not None and out.as_tuple() != v.as_tuple():
                object.__setattr__(self, name, out)       # normalize to the one canonical Decimal value
        self._validate(cls.__name__)

    def _validate(self, p):        # p = class name, the path prefix for errors
        pass


@functools.cache
def _spec(cls):
    return tuple((name, tp, check, Decimal in (typing.get_args(tp) or (tp,))) for name, tp, check in field_spec(cls))


record = dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
