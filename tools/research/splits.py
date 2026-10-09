"""`zb-splits/1`: immutable UTC splits with purge + embargo (RES-01 R3; plan section 4).

A split plan is fixed before any run: named UTC windows `[start, end)` (calibration, train, walk-forward folds,
holdout), the signal interval, the strategy lookback and the declared maximum holding horizon (a finite ex-ante cap;
an uncapped runner cannot build a plan, so it can never open a sealed split). Its digest is the SHA-256 of its
canonical JSON, and the report cites it.

Purge and embargo (declared cap, never realised durations):
  purge   = max(lookback_bars, horizon_bars) x interval
  embargo = 1 x interval
  A decision at bar close `t` enters at the next bar open (= t) and exits by `t + horizon`. Decisions of a split are
  allowed in `[start + purge + embargo, end - horizon]`, so the lookback a decision reads starts at or after
  `start + embargo` and every episode ends at or before `end`: an episode, its look-back and its whole hold lie inside
  one split, and a view never reads across a split boundary (`access_range` == the split window).
The lookback must cover slip-v1 (ATR14 on closed bars = 15 bars), so `lookback_bars >= MIN_LOOKBACK`.

Holdout rules (the guard itself lives in pit.Access): exactly one holdout, chronologically last; nothing overlaps.

Stdlib only, no clock, no I/O.
"""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manifest as M                                                                        # noqa: E402

FORMAT = 'zb-splits/1'
NAMES = ('calibration', 'train', 'walk_forward', 'holdout')      # a subset of ledger.SPLITS
TS_FMT = '%Y-%m-%dT%H:%M:%SZ'
MIN_LOOKBACK = 15                                                # slip-v1: ATR14 over closed bars needs 15 bars


class SplitError(ValueError):
    pass


def parse_utc(s) -> int:
    """Strict `YYYY-MM-DDTHH:MM:SSZ` (the ledger window format) -> epoch ms."""
    try:
        d = datetime.strptime(s, TS_FMT)
    except (TypeError, ValueError):
        raise SplitError(f'{s!r}: boundary must be UTC YYYY-MM-DDTHH:MM:SSZ')
    if d.strftime(TS_FMT) != s:
        raise SplitError(f'{s!r}: boundary must be UTC YYYY-MM-DDTHH:MM:SSZ')
    return int(d.replace(tzinfo=timezone.utc).timestamp() * 1000)


def utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(TS_FMT)


def key_of(name: str, fold) -> str:
    return name if fold is None else f'{name}[{fold}]'


class SplitPlan:
    """Immutable after construction (attributes are set once; `doc` is a fresh copy each call)."""

    __slots__ = ('_doc', '_splits', 'interval_ms', 'purge_ms', 'embargo_ms', 'horizon_ms', 'lookback_ms', 'digest')

    def __init__(self, splits, *, interval: str, lookback_bars: int, horizon_bars):
        if interval not in M.INTERVALS:
            raise SplitError(f'interval must be one of {sorted(M.INTERVALS)}')
        if type(horizon_bars) is not int or horizon_bars < 1:
            raise SplitError('a finite ex-ante maximum holding horizon (int bars >= 1) is required; '
                             'an uncapped runner cannot open a split')
        if type(lookback_bars) is not int or lookback_bars < MIN_LOOKBACK:
            raise SplitError(f'lookback_bars must be an int >= {MIN_LOOKBACK} (strategy lookback, covering slip-v1)')
        iv = M.INTERVALS[interval]
        rows, folds = [], 0
        for s in splits:
            if not isinstance(s, dict) or set(s) - {'name', 'start', 'end', 'fold'} or not {'name', 'start', 'end'} <= set(s):
                raise SplitError('each split is {name, start, end[, fold]}')
            name, fold = s['name'], s.get('fold')
            if name not in NAMES:
                raise SplitError(f'split name must be one of {NAMES}')
            if (name == 'walk_forward') != (fold is not None):
                raise SplitError('walk_forward splits (and only they) carry a fold number')
            if fold is not None:
                if type(fold) is not int or fold != folds:
                    raise SplitError('walk_forward folds are numbered 0, 1, 2 ... in time order')
                folds += 1
            lo, hi = parse_utc(s['start']), parse_utc(s['end'])
            if lo % iv or hi % iv:
                raise SplitError(f'{key_of(name, fold)}: boundaries must align to {interval}')
            rows.append({'name': name, 'fold': fold, 'start': s['start'], 'end': s['end'], 'lo': lo, 'hi': hi})
        if [r['name'] for r in rows].count('holdout') != 1 or not any(r['name'] == 'train' for r in rows):
            raise SplitError('a plan needs exactly one holdout and at least one train split')
        if any(b['lo'] < a['hi'] for a, b in zip(rows, rows[1:])) or any(r['lo'] >= r['hi'] for r in rows):
            raise SplitError('splits must be listed in time order, non-empty and non-overlapping')
        if rows[-1]['name'] != 'holdout':
            raise SplitError('the holdout must be the chronologically last split')
        self.interval_ms, self.horizon_ms, self.lookback_ms = iv, horizon_bars * iv, lookback_bars * iv
        self.purge_ms, self.embargo_ms = max(lookback_bars, horizon_bars) * iv, iv
        for r in rows:
            if r['lo'] + self.purge_ms + self.embargo_ms > r['hi'] - self.horizon_ms:
                raise SplitError(f'{key_of(r["name"], r["fold"])}: no decision fits after purge/embargo and horizon')
        self._splits = {key_of(r['name'], r['fold']): r for r in rows}
        if len(self._splits) != len(rows):
            raise SplitError('split names must be unique (walk_forward by fold)')
        self._doc = {'format': FORMAT, 'interval': interval, 'lookback_bars': lookback_bars,
                     'horizon_bars': horizon_bars, 'purge_bars': max(lookback_bars, horizon_bars), 'embargo_bars': 1,
                     'splits': [{k: r[k] for k in ('name', 'fold', 'start', 'end')} for r in rows]}
        self.digest = hashlib.sha256(M.canonical(self._doc)).hexdigest()

    def __setattr__(self, k, v):
        if hasattr(self, k):
            raise AttributeError('a split plan is immutable')
        object.__setattr__(self, k, v)

    def doc(self) -> dict:
        return {**self._doc, 'splits': [dict(s) for s in self._doc['splits']], 'digest': self.digest}

    def keys(self) -> list[str]:
        return list(self._splits)

    def split(self, key: str) -> dict:
        if key not in self._splits:
            raise SplitError(f'unknown split {key!r}; plan has {self.keys()}')
        return dict(self._splits[key])

    def window(self, key: str) -> dict:
        s = self.split(key)
        return {'start': s['start'], 'end': s['end']}

    def access_range(self, key: str) -> tuple[int, int]:
        """[lo, hi] in ms: the only data a view of this split may read (rows with `open >= lo`, `available <= hi`)."""
        s = self.split(key)
        return s['lo'], s['hi']

    def decision_range(self, key: str) -> tuple[int, int]:
        """Inclusive [first, last] decision time (bar close) whose episode fits wholly in the split."""
        s = self.split(key)
        return s['lo'] + self.purge_ms + self.embargo_ms, s['hi'] - self.horizon_ms

    def holdout_key(self) -> str:
        return 'holdout'

    def overlaps_holdout(self, lo: int, hi: int) -> bool:
        h = self._splits['holdout']
        return lo < h['hi'] and h['lo'] < hi

    def split_of_episode(self, entry_ms: int, exit_ms: int) -> str:
        """The split an episode belongs to; raises unless it lies wholly in one split's decision range + horizon."""
        for k, s in self._splits.items():
            first, last = self.decision_range(k)
            if first <= entry_ms <= last and entry_ms <= exit_ms <= min(s['hi'], entry_ms + self.horizon_ms):
                return k
        raise SplitError(f'episode {utc(entry_ms)} -> {utc(exit_ms)} is not wholly inside one split (purge/embargo/cap)')
