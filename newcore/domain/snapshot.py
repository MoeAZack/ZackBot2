"""Snapshot header and store high-water mark: the record shapes NC-02 needs to detect a generation rollback.

A Snapshot binds a Portfolio to its account, generation and the last event it includes. The store keeps a HighWater
record of the highest generation / sequence ever committed. `check_generation` is the pure comparison: a snapshot
older than the high-water mark is a ROLLBACK, whatever its content looks like.
"""
from __future__ import annotations

import enum

from .base import Record, check_id, record, req
from .portfolio import Portfolio, ProofKind


class GenerationVerdict(enum.StrEnum):
    CURRENT = 'current'
    ROLLED_BACK = 'rolled_back'          # older generation / sequence than the store ever committed
    AHEAD = 'ahead'                      # newer than the high-water mark: the mark lags (crash between commits)
    FOREIGN = 'foreign'                  # another account's snapshot


def _build(v, p):
    req(0 < len(v) <= 64 and v.isprintable(), p, '1..64 printable characters')


@record
class Snapshot(Record):
    account_id: str
    generation: int
    last_sequence: int                        # the last event included (0 = none)
    written_at_ms: int
    writer_build: str
    portfolio: Portfolio

    def _validate(self, p):
        check_id(self.account_id, p + '.account_id', 'acct')
        req(self.generation >= 0 and self.last_sequence >= 0, p + '.generation', '>= 0')
        _build(self.writer_build, p + '.writer_build')
        req(self.portfolio.account_id == self.account_id, p + '.portfolio', 'portfolio of another account')
        req(self.portfolio.generation == self.generation, p + '.portfolio.generation', 'differs from the snapshot header')
        proof = self.portfolio.proof
        if proof is not None and proof.kind is ProofKind.JOURNAL:
            req(proof.through_sequence <= self.last_sequence, p + '.portfolio.proof', 'proven by events the snapshot does not include')


@record
class HighWater(Record):
    account_id: str
    generation: int
    last_sequence: int
    writer_build: str

    def _validate(self, p):
        check_id(self.account_id, p + '.account_id', 'acct')
        req(self.generation >= 0 and self.last_sequence >= 0, p + '.generation', '>= 0')
        _build(self.writer_build, p + '.writer_build')


def check_generation(snapshot, high_water):
    if snapshot.account_id != high_water.account_id:
        return GenerationVerdict.FOREIGN
    mine, mark = (snapshot.generation, snapshot.last_sequence), (high_water.generation, high_water.last_sequence)
    if snapshot.generation < high_water.generation or snapshot.last_sequence < high_water.last_sequence:
        return GenerationVerdict.ROLLED_BACK
    return GenerationVerdict.CURRENT if mine == mark else GenerationVerdict.AHEAD
