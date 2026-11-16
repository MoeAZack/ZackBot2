"""Validators for the step-0 port values. Every NC-01 value rule is the NC-01 function itself (no mirror); only the
port-local helpers below are defined here. Errors are newcore.domain InvalidRecord (PortValueError is its alias)."""
from __future__ import annotations

from newcore.domain.base import (canonical_decimal, check_client_id, check_id, check_ms, check_symbol,  # noqa: F401
                                 check_text)
from newcore.domain.errors import InvalidRecord

PortValueError = InvalidRecord
ROUTES = ('classic', 'algo')        # transport StopRoute values: /fapi/v1/order | /fapi/v1/algoOrder


def req(cond, path, msg):
    if not cond:
        raise PortValueError(path, msg)


def check_int(v, path, lo, hi):
    req(type(v) is int and lo <= v <= hi, path, f'an int in [{lo}, {hi}]')


def check_choice(v, path, allowed):
    req(isinstance(v, str) and v in allowed, path, f'{v!r} is not one of {allowed}')


def check_decimal(v, path, *, positive=False, nonneg=False):
    canonical_decimal(v, path)                       # NC-01: a finite, bounded Decimal (never float / bool / int)
    req(not positive or v > 0, path, f'{v} must be > 0')
    req(not nonneg or v >= 0, path, f'{v} must be >= 0')


def plain(v):
    """A str subclass (an NC-01 StrEnum member) becomes its plain value, so equal values compare and hash alike."""
    return str(v) if isinstance(v, str) else v
