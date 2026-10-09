"""Point-in-time data view + holdout guard (RES-01 R3; plan sections 1, 1a, 3, 4; the plan's `data.py`).

Layers (each the only way to reach the next):
  Dataset  one verified `zb-data-manifest/1` + its `zb-pit-universe/1`. Every file is read only through the manifest:
           its bytes are re-hashed and must equal the manifest SHA-256 (and, for archives, the published checksum)
           before a row is parsed - a mismatch fails closed. Rows carry `available_ms` (klines / mark klines:
           open + interval; funding: `calc_time`).
  Access   opens one split of an immutable `splits.SplitPlan` (or a development window) for one hypothesis family and
           appends a `data_access` record to the family ledger BEFORE returning any data. The sealed holdout opens only
           when the ledger's last open group is the atomic `holdout_reveal` / `holdout_rerun` of exactly this frozen,
           clean-tree run envelope (`report.py`); anything else raises. A development window may not touch the plan's
           holdout.
  Window   the data range of the opened split: rows with `open_ms >= lo` and `available_ms <= hi`, nothing else.
  View     `window.view(t)`: a frozen decision time. Every accessor filters `available_ms <= t`; there is no accessor
           that takes another time, so lookahead is impossible by construction. Symbols are served only while they are
           universe members at `t` (or, for an open position, at its entry decision `entered_ms <= t`). A survivor-only
           legacy manifest (no universe) is served with the label SURVIVOR-ONLY.
Funding joins: `funding_events` returns (time, rate, mark) for entry <= time < exit, the mark being the close of the
1h mark bar available at the funding time, never forward-filled across a gap > 1 bar.

Smoke (shape/coverage counts only, never returns; logged in the ledger as a development access):
  python tools/research/pit.py smoke --manifest research_evidence/manifests/binance-um-archive-v1.json.gz
      --store C:/Dev/ZackBot2_data/binance_um --universe research_evidence/universe/pit-top40-qv30d-v1.json
      --ledger research_evidence/ledger/res01_infra.jsonl --start 2026-01-05T00:00:00Z --end 2026-01-12T00:00:00Z
      --symbols BTCUSDT,XAUUSDT --author claude-code --cairo-date 2026-10-09
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import sys
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger as L                                                                          # noqa: E402
import manifest as M                                                                        # noqa: E402
import report as R                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402
import universe as U                                                                        # noqa: E402

Bar = namedtuple('Bar', 'open_ms open high low close volume quote_volume available_ms')
Funding = namedtuple('Funding', 'time_ms rate interval_hours available_ms')
HOUR = 3_600_000
SURVIVOR_ONLY = 'SURVIVOR-ONLY'
GROUP_KINDS = ('holdout_reveal', 'holdout_rerun')


class PITError(ValueError):
    pass


class Dataset:
    def __init__(self, manifest: dict, store: str, universe: dict | None):
        M.validate(manifest)
        self.manifest, self.store, self.digest = manifest, store, manifest['digest']
        self.archive = manifest['loader_version'] == M.ARCHIVE_LOADER
        if universe is None:
            if not manifest['survivor_only']:
                raise PITError('an all-listed manifest is served only through its PIT universe')
            self.universe_digest, self.labels = None, (SURVIVOR_ONLY,)
            self._mondays, self._members = None, None
            self._all = frozenset(f['symbol'] for f in manifest['files'])
        else:
            if universe.get('format') != U.FORMAT or universe.get('digest') != U.digest_of(universe):
                raise PITError('universe digest does not match its content')
            if universe['manifest_digest'] != self.digest:
                raise PITError('universe was built from a different manifest')
            self.universe_digest, self.labels = universe['digest'], ()
            self._mondays = [w['monday_ms'] for w in universe['weeks']]
            self._members = [frozenset(w['members']) for w in universe['weeks']]
        self._files: dict[tuple, list] = {}
        for f in manifest['files']:
            key = (f.get('series', 'klines'), f['symbol'], f['interval'])
            self._files.setdefault(key, []).append(f)
        for v in self._files.values():
            v.sort(key=lambda f: (f['first_open_ms'] is None, f['first_open_ms'] or 0))
        self._cache: dict[str, list] = {}
        self._keys: dict[str, tuple] = {}

    def members(self, at: int) -> frozenset:
        if self._mondays is None:
            return self._all
        i = bisect.bisect_right(self._mondays, at) - 1
        if i < 0:
            return frozenset()
        if at >= self._mondays[-1] + U.WEEK:
            raise PITError(f'{S.utc(at)} lies beyond the last universe week')
        return self._members[i]

    def files(self, series: str, symbol: str, interval) -> list:
        return self._files.get((series, symbol, interval), [])

    def rows(self, f: dict) -> list:
        """Parsed rows of one manifest file, after the SHA-256 re-check (fail closed)."""
        p = f['path']
        if p in self._cache:
            return self._cache[p]
        ap = M.contained(self.store, p)
        if self.archive:
            _, published, data = M.read_archive(ap, p)
            if published != f['sha256']:
                raise PITError(f'{p}: bytes no longer match the manifest sha256')
            raw = M.parse_archive_csv(p, data, M.archive_meta(p))
            if f['series'] == 'fundingRate':
                out = [Funding(int(r[0]), float(r[2]), int(r[1]), int(r[0])) for r in raw]
            else:
                iv = M.INTERVALS[f['interval']]
                out = [Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[7]),
                           int(r[0]) + iv) for r in raw]
        else:
            with open(ap, 'rb') as fh:
                data = fh.read()
            if hashlib.sha256(data).hexdigest() != f['sha256']:
                raise PITError(f'{p}: bytes no longer match the manifest sha256')
            iv = M.INTERVALS[f['interval']]
            out = []
            for ln in data.decode('utf-8').splitlines()[1:]:
                if ln.strip():
                    t, o, h, lo, c, v = ln.split(',')
                    om = M.parse_open_ms(t)
                    out.append(Bar(om, float(o), float(h), float(lo), float(c), float(v), None, om + iv))
        if len(out) != f['rows']:
            raise PITError(f'{p}: {len(out)} rows != manifest {f["rows"]}')
        self._cache[p] = out
        self._keys[p] = ([r[0] for r in out], [r.available_ms for r in out])
        return out

    def slice(self, f: dict, lo: int, t: int) -> list:
        """Rows of one file with time >= lo and available_ms <= t (both sorted, so two bisections)."""
        rows = self.rows(f)
        times, avail = self._keys[f['path']]
        return rows[bisect.bisect_left(times, lo):bisect.bisect_right(avail, t)]


class Window:
    """The rows of one opened split: open_ms >= lo and available_ms <= hi. Created only by Access."""

    def __init__(self, ds: Dataset, lo: int, hi: int, key: str, _token=None):
        if _token is not _TOKEN:
            raise PITError('a Window is opened only through Access (ledger-recorded)')
        self.ds, self.lo, self.hi, self.key = ds, lo, hi, key

    def view(self, t: int) -> 'View':
        if not self.lo <= t <= self.hi:
            raise PITError(f'decision time {S.utc(t)} outside the opened window {self.key}')
        return View(self, t)


_TOKEN = object()


class View:
    __slots__ = ('_w', 't')

    def __init__(self, w: Window, t: int):
        object.__setattr__(self, '_w', w)
        object.__setattr__(self, 't', t)

    def __setattr__(self, k, v):
        raise AttributeError('a view is frozen at its decision time')

    @property
    def labels(self):
        return self._w.ds.labels

    def members(self) -> frozenset:
        return self._w.ds.members(self.t)

    def _check(self, symbol: str, entered_ms):
        at = self.t if entered_ms is None else entered_ms
        if not self._w.lo <= at <= self.t:
            raise PITError('entered_ms must lie inside the window and not after the decision time')
        if symbol not in self._w.ds.members(at):
            raise PITError(f'{symbol} is not a universe member at {S.utc(at)}')

    def _rows(self, series, symbol, interval, n=None, since=None):
        """Rows with lo <= time, (since <= time) and available <= t; the last n if n is given."""
        lo, out = max(self._w.lo, since or self._w.lo), []
        for f in reversed(self._w.ds.files(series, symbol, interval)):
            if f['first_open_ms'] is None or f['first_open_ms'] > self.t or f['last_open_ms'] < lo:
                continue
            rows = self._w.ds.slice(f, lo, self.t)
            out = rows + out
            if n is not None and len(out) >= n:
                break
        return tuple(out[-n:] if n is not None else out)

    def bars(self, symbol: str, interval: str, n: int, *, entered_ms=None, series='klines') -> tuple:
        """The last `n` closed bars (oldest first) available at t."""
        self._check(symbol, entered_ms)
        if type(n) is not int or n < 1:
            raise PITError('n must be an int >= 1')
        return self._rows(series, symbol, interval, n=n)

    def mark_bars(self, symbol: str, n: int, *, entered_ms=None) -> tuple:
        return self.bars(symbol, '1h', n, entered_ms=entered_ms, series='markPriceKlines')

    def funding(self, symbol: str, since_ms: int, *, entered_ms=None) -> tuple:
        self._check(symbol, entered_ms)
        return self._rows('fundingRate', symbol, None, since=since_ms)

    def funding_events(self, symbol: str, entry_ms: int, exit_ms: int) -> list:
        """[(time_ms, rate, mark_close)] for entry <= time < exit (entry == T counts, exit == T does not)."""
        if not entry_ms <= exit_ms <= self.t:
            raise PITError('funding events need entry <= exit <= t')
        rows = [f for f in self.funding(symbol, entry_ms, entered_ms=entry_ms) if f.time_ms < exit_ms]
        marks = self._rows('markPriceKlines', symbol, '1h', since=entry_ms - 2 * HOUR)
        avail = [m.available_ms for m in marks]
        out = []
        for f in rows:
            i = bisect.bisect_right(avail, f.time_ms) - 1
            if i < 0 or f.time_ms - avail[i] >= HOUR:
                raise PITError(f'{symbol}: no 1h mark bar available at funding {S.utc(f.time_ms)} (gap > 1 bar)')
            out.append((f.time_ms, f.rate, marks[i].close))
        return out

    def minute_bars(self, symbol: str, open_ms: int, close_ms: int, *, entered_ms=None) -> tuple:
        """1m bars of one closed signal bar [open_ms, close_ms); empty when the manifest has no 1m data for it."""
        self._check(symbol, entered_ms)
        if close_ms > self.t:
            raise PITError('the signal bar is not closed at t')
        return tuple(b for b in self._rows('klines', symbol, '1m', since=open_ms) if b.open_ms < close_ms)


class Access:
    """Opens data for one hypothesis family; every opening is a ledger `data_access` record."""

    def __init__(self, ds: Dataset, plan: S.SplitPlan | None, ledger_path: str, *, candidate_id: str, author: str,
                 cairo_date: str, runs_dir=None):
        self.ds, self.plan, self.path = ds, plan, ledger_path
        self.family = os.path.splitext(os.path.basename(ledger_path))[0]
        self.candidate_id, self.author, self.cairo_date = candidate_id, author, cairo_date
        self.runs_dir = runs_dir if runs_dir is not None else L._runs_of(L._dir_of(ledger_path))

    def _family_exists(self) -> bool:
        return os.path.exists(self.path)

    def _record(self, split, window, run_digest, detail, lineage):
        L.append(self.path, kind='data_access', candidate_id=self.candidate_id, split=split, window=window,
                 author=self.author, cairo_date=self.cairo_date, manifest_digest=self.ds.digest, run_digest=run_digest,
                 detail=detail, lineage=None if self._family_exists() else lineage)

    def _base_detail(self, extra):
        d = {'universe_digest': self.ds.universe_digest, 'labels': list(self.ds.labels)}
        if self.plan is not None:
            d['split_plan_digest'] = self.plan.digest
        d.update(extra or {})
        return d

    def open(self, key: str, *, envelope=None, detail=None, lineage=None) -> Window:
        if self.plan is None:
            raise PITError('a split opens only through a SplitPlan')
        name, window = self.plan.split(key)['name'], self.plan.window(key)
        lo, hi = self.plan.access_range(key)
        d = self._base_detail(detail)
        d['split_key'] = key
        if name != 'holdout':
            if envelope is not None and envelope['run']['split'] == 'holdout':
                raise PITError('a holdout envelope cannot open a non-holdout split')
            self._record(name, window, envelope['run_digest'] if envelope else None, d, lineage)
            return Window(self.ds, lo, hi, key, _TOKEN)
        self._guard_holdout(envelope, window)
        d['eval_digest'] = envelope['run']['eval_digest']
        self._record('holdout', window, envelope['run_digest'], d, lineage)
        return Window(self.ds, lo, hi, key, _TOKEN)

    def open_development(self, start: str, end: str, *, detail=None, lineage=None) -> Window:
        lo, hi = S.parse_utc(start), S.parse_utc(end)
        if not lo < hi:
            raise PITError('development window needs start < end')
        if self.plan is not None and self.plan.overlaps_holdout(lo, hi):
            raise PITError('a development window may not touch the sealed holdout')
        self._record('development', {'start': start, 'end': end}, None, self._base_detail(detail), lineage)
        return Window(self.ds, lo, hi, f'development {start}..{end}', _TOKEN)

    def _guard_holdout(self, env, window):
        def need(ok, msg):
            if not ok:
                raise PITError(f'sealed holdout: {msg}')
        need(env is not None and self.runs_dir is not None, 'needs a frozen run envelope and a runs store')
        try:
            stored = R.load_envelope(self.runs_dir, env['run_digest'])
        except R.ReportError as e:
            raise PITError(f'sealed holdout: {e}')
        need(stored == env, 'envelope is not the stored frozen envelope')
        need(R.run_digest(env['run']) == env['run_digest'], 'envelope digest mismatch')
        need(not R.sealable(env), f'run not sealable: {R.sealable(env)}')
        run = env['run']
        want = {'family': self.family, 'candidate_id': self.candidate_id, 'split': 'holdout', 'window': window,
                'manifest_digest': self.ds.digest, 'universe_digest': self.ds.universe_digest,
                'split_plan_digest': self.plan.digest}
        need(all(run[k] == v for k, v in want.items()), 'envelope identity differs from this access')
        need(self._family_exists(), 'no ledger for this family')
        recs = L._check_state(self.path)[self.family]
        idx = max((i for i, r in enumerate(recs) if r['kind'] in GROUP_KINDS), default=None)
        need(idx is not None, 'no atomic holdout_reveal record in the family ledger')
        head = recs[idx]
        need(head['run_digest'] == env['run_digest'] and head['window'] == window
             and head['detail'].get('eval_digest') == run['eval_digest'],
             'the latest reveal belongs to another run')
        need(all(r['split'] == 'holdout' and r['run_digest'] == env['run_digest'] and r['kind'] in L.COMPONENT_KINDS
                 for r in recs[idx + 1:]), 'the reveal group is closed (another record followed it)')


# ------------------------------------------------------------------ smoke (shape / coverage only, no returns)
def smoke(ds: Dataset, ledger_path: str, start: str, end: str, symbols, *, author, cairo_date,
          candidate_id='res01.r3.smoke') -> dict:
    acc = Access(ds, None, ledger_path, candidate_id=candidate_id, author=author, cairo_date=cairo_date)
    w = acc.open_development(start, end, lineage='root',
                             detail={'purpose': 'R3 PIT-view shape/coverage smoke; no strategy, no returns',
                                     'symbols': list(symbols)})
    v = w.view(w.hi)
    out = {'window': [start, end], 'manifest_digest': ds.digest, 'universe_digest': ds.universe_digest, 'symbols': {}}
    big = 10 ** 7
    for s in symbols:
        member = s in ds.members(w.lo)
        row = {'member_at_start': member}
        if member:
            row['1d'] = len(v.bars(s, '1d', big, entered_ms=w.lo))
            row['4h'] = len(v.bars(s, '4h', big, entered_ms=w.lo))
            row['mark_1h'] = len(v.mark_bars(s, big, entered_ms=w.lo))
            row['funding'] = len(v.funding(s, w.lo, entered_ms=w.lo))
            row['1m'] = len(v.minute_bars(s, w.lo, w.hi, entered_ms=w.lo))
            row['1m_expected'] = (w.hi - w.lo) // 60_000
            row['funding_marks_joined'] = len(v.funding_events(s, w.lo + HOUR, w.hi))
        out['symbols'][s] = row
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='PIT view smoke: shape/coverage counts over a development window')
    ap.add_argument('cmd', choices=['smoke'])
    for k in ('--manifest', '--store', '--universe', '--ledger', '--start', '--end', '--symbols', '--author',
              '--cairo-date'):
        ap.add_argument(k, required=True)
    a = ap.parse_args(argv)
    try:
        with open(a.universe, encoding='utf-8') as f:
            u = json.load(f)
        ds = Dataset(M.load(a.manifest), a.store, u)
        print(json.dumps(smoke(ds, a.ledger, a.start, a.end, a.symbols.split(','), author=a.author,
                               cairo_date=a.cairo_date), indent=1, sort_keys=True))
        return 0
    except (PITError, M.ManifestError, L.LedgerError, U.UniverseError, S.SplitError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
