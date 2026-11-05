"""STRAT-01 candle feeds.  ``CsvCandleFeed`` reads ``<dir>/<SYMBOL>_<tf>.csv`` (columns t,o,h,l,c,v; t = UTC open
time, the layout of ``data/`` and ``data1h/``) and returns only candles that have CLOSED at ``now_wall_ms``.

It re-reads a file only when its size or mtime changes.  No network, no credentials.
"""
from __future__ import annotations

import os

import pandas as pd

from newcore.ports.keys import TIMEFRAMES


class CsvCandleFeed:
    def __init__(self, directory: str):
        if type(directory) is not str or not os.path.isdir(directory):
            raise ValueError('candle_dir_missing')
        self.directory = directory
        self._cache: dict[str, tuple[tuple[int, int], pd.DataFrame]] = {}

    def _load(self, path: str) -> pd.DataFrame | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        stamp = (st.st_mtime_ns, st.st_size)
        hit = self._cache.get(path)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        try:
            frame = pd.read_csv(path, encoding='utf-8')
            if pd.api.types.is_numeric_dtype(frame['t']):            # the app cache writes epoch milliseconds
                frame['t'] = pd.to_datetime(frame['t'].astype('int64'), unit='ms')
            else:
                frame['t'] = pd.to_datetime(frame['t'], utc=True).dt.tz_localize(None)
        except Exception:
            return None
        self._cache[path] = (stamp, frame)
        return frame

    def closed(self, symbol: str, tf: str, bars: int, now_wall_ms: int) -> pd.DataFrame | None:
        if tf not in TIMEFRAMES or os.sep in symbol or '/' in symbol or '..' in symbol:
            return None
        frame = self._load(os.path.join(self.directory, f'{symbol}_{tf}.csv'))
        if frame is None or frame.empty:
            return None
        open_ms = frame['t'].astype('datetime64[ms]').astype('int64')
        done = frame[open_ms + TIMEFRAMES[tf] <= now_wall_ms]
        return done.iloc[-bars:].reset_index(drop=True)


__all__ = ['CsvCandleFeed']
