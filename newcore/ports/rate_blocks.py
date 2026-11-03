"""Durable NC-04 rate-block records.

This is deliberately separate from the NC-01 DomainEvent grammar.  A rate block is
transport admission state, not portfolio truth.  Implementations append records and
return them in durable order; they never rewrite or infer a missing record.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .values import check_id, check_int, check_ms, check_text, plain, req


class RateBlockState(enum.StrEnum):
    OPEN = 'open'
    BLOCKED_UNTIL = 'blocked_until'
    BLOCKED_UNKNOWN_DURATION = 'blocked_unknown_duration'


@dataclass(frozen=True, slots=True, kw_only=True)
class RateBlockRecord:
    """One append-only v1 transition/shutdown marker for an account and egress."""

    schema_version: int
    account_id: str
    egress_id: str
    state: RateBlockState
    wall_deadline_ms: int | None
    observed_wall_ms: int
    observed_monotonic_ms: int
    cause: str
    clean_shutdown: bool

    def __post_init__(self):
        check_int(self.schema_version, 'RateBlockRecord.schema_version', 1, 1)
        check_id(self.account_id, 'RateBlockRecord.account_id', 'acct')
        check_text(self.egress_id, 'RateBlockRecord.egress_id', 64)
        object.__setattr__(self, 'state', RateBlockState(plain(self.state)))
        check_ms(self.observed_wall_ms, 'RateBlockRecord.observed_wall_ms')
        check_int(self.observed_monotonic_ms, 'RateBlockRecord.observed_monotonic_ms', 0, 2 ** 63 - 1)
        req((self.wall_deadline_ms is not None) == (self.state is RateBlockState.BLOCKED_UNTIL),
            'RateBlockRecord.wall_deadline_ms', 'set exactly for blocked_until')
        if self.wall_deadline_ms is not None:
            check_ms(self.wall_deadline_ms, 'RateBlockRecord.wall_deadline_ms')
            req(self.wall_deadline_ms >= self.observed_wall_ms, 'RateBlockRecord.wall_deadline_ms',
                'not before the observation')
        check_text(self.cause, 'RateBlockRecord.cause', 64)
        req(type(self.clean_shutdown) is bool, 'RateBlockRecord.clean_shutdown', 'a bool')


@runtime_checkable
class RateBlockJournalPort(Protocol):
    """Journal-owned append-only side record used before any venue dial."""

    def append_rate_block(self, record: RateBlockRecord) -> None: ...

    def load_rate_blocks(self, account_id: str, egress_id: str) -> tuple[RateBlockRecord, ...]: ...


__all__ = ['RateBlockJournalPort', 'RateBlockRecord', 'RateBlockState']
