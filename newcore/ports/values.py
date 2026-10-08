"""Shared validators for the step-0 port value types (docs/newcore/STEP0_INTERFACE.md).

They mirror the NC-01 dialect exactly (integer UTC-ms timestamps, `<prefix>_<32 hex>` ids, symbols, sides, purposes,
finite Decimals) so a value accepted here is accepted by newcore.domain. Bound to newcore.domain.base when NC-01 lands;
until then this package must not import it (it is not merged).
"""
from __future__ import annotations

import re
from decimal import Decimal

MIN_TS_MS = 946_684_800_000          # 2000-01-01T00:00:00.000Z  (NC-01 invariant 3)
MAX_TS_MS = 4_102_444_799_999        # 2099-12-31T23:59:59.999Z, inclusive
SYMBOL_RE = re.compile(r'[A-Z0-9]{2,30}')                 # NC-01 check_symbol
ID_RE = re.compile(r'([a-z]{2,4})_[0-9a-f]{32}')          # NC-01 opaque id
CLIENT_ID_RE = re.compile(r'[.A-Z:/a-z0-9_-]{1,36}')      # Binance newClientOrderId / clientAlgoId grammar
SIDES = ('LONG', 'SHORT')                                 # NC-01 orders.Side values (position side)
PURPOSES = ('entry', 'add', 'reduce', 'close', 'protect')  # NC-01 orders.Purpose values
ROUTES = ('classic', 'algo')                              # transport StopRoute values: /fapi/v1/order | /fapi/v1/algoOrder


class PortValueError(ValueError):
    """A port value violates the step-0 interface. `path` names the field."""

    def __init__(self, path, msg):
        super().__init__(f'{path}: {msg}')
        self.path = path
        self.msg = msg


def req(cond, path, msg):
    if not cond:
        raise PortValueError(path, msg)


def check_ms(v, path):
    req(type(v) is int, path, f'integer UTC milliseconds, not {type(v).__name__}')
    req(MIN_TS_MS <= v <= MAX_TS_MS, path, f'{v} outside [{MIN_TS_MS}, {MAX_TS_MS}]')


def check_int(v, path, lo, hi):
    req(type(v) is int and lo <= v <= hi, path, f'an int in [{lo}, {hi}]')


def check_text(v, path, n):
    req(isinstance(v, str) and 0 < len(v) <= n and v.isprintable(), path, f'1..{n} printable characters')


def check_symbol(v, path):
    req(isinstance(v, str) and SYMBOL_RE.fullmatch(v) is not None, path, f'{v!r} is not a symbol')


def check_choice(v, path, allowed):
    req(isinstance(v, str) and v in allowed, path, f'{v!r} is not one of {allowed}')


def check_id(v, path, prefix):
    m = ID_RE.fullmatch(v) if isinstance(v, str) else None
    req(m is not None and m.group(1) == prefix, path, f'{v!r} is not a {prefix}_<32 hex> id')


def check_client_id(v, path):
    req(isinstance(v, str) and CLIENT_ID_RE.fullmatch(v) is not None, path, f'{v!r} is not a venue client id')


def check_decimal(v, path, *, positive=False, nonneg=False):
    req(type(v) is Decimal and v.is_finite(), path, f'a finite Decimal, not {type(v).__name__}')
    req(not positive or v > 0, path, f'{v} must be > 0')
    req(not nonneg or v >= 0, path, f'{v} must be >= 0')


def plain(v):
    """A str subclass (NC-01 StrEnum member) becomes its plain string value, so equal values compare and hash alike."""
    return str(v) if isinstance(v, str) else v
