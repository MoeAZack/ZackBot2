"""`zb-exec-obs-manifest/1`: manifested, point-in-time Binance execution observations (BT-EXEC-OBS-01; contract
6224963744, Codex assignment 6224897113, Codex ruling 6224996960, Cowork review 6224991417).

Execution-realism claims (maker fills, latency, queue position, depth impact) need observations a bar store does not
hold. This module pins them exactly the way `manifest.py` pins candles, serves them causally inside an opened pit
Window, and says - fail closed - why a run that claims realism is not promotable. It changes no fill, P&L or strategy
behaviour: nothing here simulates anything (Q4: queue and depth MODELS are deferred; only their identifiers are bound),
so EVERY claim is refused `model_not_implemented` until a later registered simulator implements it (Codex 6224996960).

Source (Q2): `data.binance.vision` USD-M zips only, mirrored under the `binance_um` store as
  um/daily/<bookTicker|bookDepth|aggTrades>/<SYMBOL>/<SYMBOL>-<series>-YYYY-MM-DD.zip
  um/monthly/<bookTicker|aggTrades>/<SYMBOL>/<SYMBOL>-<series>-YYYY-MM.zip      (bookDepth is published daily only)
each beside a `.ok` sidecar holding the SHA-256 published in the archive's `.CHECKSUM` (`manifest.read_archive`). Any
other series directory is refused, never skipped. The zip must hold exactly the one member `<name>.csv` (an extra,
substituted or path-traversing member hard-refuses before any row is read); the manifest binds the member name, the
zip SHA-256 and the SHA-256 of the RAW sidecar bytes, and the path binds source / series / symbol / date (Codex
addendum 6225107087 item 4). Each authentic Binance schema is parsed by its own row schema (item 1); a row the schema
cannot hold (column count, header other than the exact optional Binance header, non-canonical integer / decimal /
timestamp / boolean text, an empty file) refuses with `source_schema_mismatch`:
  book_ticker          (bookTicker) update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,
                       transaction_time,event_time
                       event time = `event_time`; sequence = `update_id`, strictly increasing within AND across files
                       (Binance update ids are non-contiguous by design: a jump is not a gap; a non-increase
                       rejects).
  book_depth_summary   (bookDepth) timestamp,percentage,depth,notional
                       PERCENTAGE-BAND depth summary (cumulative depth / notional within +-k % of the mid), not an L2 or
                       fixed-depth snapshot: it cannot reconstruct a queue and is never presented as if it could
                       (Codex Q2). `timestamp` is a UTC datetime STRING `YYYY-MM-DD HH:MM:SS` (second resolution, not
                       epoch ms): parsed strictly (round-trip exact) to ms; it is the event time. Rows sharing a
                       timestamp form one summary; summary times strictly increase; each summary carries exactly the
                       manifest's frozen `depth_scope` band set, depth and notional non-decreasing outward from the mid
                       on each side. There is no sequence column (first/last_seq null, no sequence check).
  agg_trades           (aggTrades) agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,
                       is_buyer_maker
                       no separate event time: `transact_time` IS the event time. Sequence = `agg_trade_id`, strictly
                       increasing within and across files; `first_trade_id <= last_trade_id`; a trade-id overlap with
                       the previous row rejects the file, a jump (`first_id != prev last_id + 1` inside a file,
                       `agg_id != prev last agg_id + 1` across consecutive files) is a `seq` GAP.
Row checks fail closed (any failure rejects the file): 13-digit ms inside the file's day/month, transaction <= event
time, event time non-decreasing, sequence strictly increasing, bid > 0, ask > 0, bid < ask (crossed AND locked books are
refused), quantities > 0, canonical plain-decimal numbers (`manifest.QV_RE`; a percentage may carry one leading `-`).
Two files of one (symbol, series) whose periods overlap (a daily inside a monthly, a duplicate) are refused, as
`pit._refuse_overlap` does for candles: one source per instant.

Gap report: deterministic and sorted - `head` / `tail` (period edge to first / last event) and `time` (between
consecutive events) gaps longer than the series threshold of the allow-listed `gap_policy_id` (thresholds live in
GAP_POLICIES, never chosen by a caller; item 3), `seq` gaps (aggTrades only - a bookTicker update-id jump is no proof of
a missing row), and `file` gaps (a period with no file between two manifested files of one (symbol, series)). `at_ms`
places each entry in time (a seq gap: the last event before the jump). It is stored in the manifest with
`gap_report_digest` = SHA-256 of its canonical JSON; `validate` recomputes the digest from the stored list and `verify`
recomputes the list from the files - either mismatch fails. Per file, `coverage.covered_ms` = period length minus its
head / tail / time gaps; `gap_count` counts its gap entries.

Point in time: `ExecObs(manifest, store, latency_policy_id, window)` serves rows ONLY inside an opened `pit.Window` (its
lo / hi, created only by `pit.Access`, ledger-recorded; Codex amendment 8): holdout-dated rows are unreachable without
the existing sealed-holdout access path, and nothing past the window's `hi` is ever served. A file is re-hashed (zip ==
sidecar == manifest sha256) before any row of it is parsed, as `pit.Dataset.rows` does. Every row carries `available_ms
= event_ms + feed_ms`, the frozen integer of the latency policy (Q3: `lat-none/1` = 0 ms; `lat-fixed-250/1` = 250 ms,
ASSUMED / UNFITTED, never calibrated latency evidence). `view(T)` is the only way to rows and every accessor filters
`event_ms >= lo` and `available_ms <= T`; no accessor takes another time, so changing, deleting or adding any row with
`available_ms > T` cannot change an output at T. Equal timestamps order deterministically: per series by sequence then
row order, and `View.events` by (available_ms, series, sequence or row index). A latency policy value outside int [0,
MAX_FEED_MS] or an unknown id refuses. NOTE: a row with `available_ms == T` is visible to a decision at T, but that does
NOT mean a decision at T may fill against the book at T - the fill timing rule (strictly after the decision) belongs to
the later fill-simulator ticket and is not assumed here.

Promotion (contract section 4 + Codex Q4): `realism_refusals(run, manifest, store=...)` recomputes the manifest digest
and the gap report from the manifest AND the files (never the envelope's values on trust) and lists every reason a /3
run is not promotable. The realism a run NEEDS is derived from its registered `eval.config.execution` (EXECUTION_KEYS,
allow-listed; unknown keys / values refuse) and must be declared in `claims`, else `exec_claims_incomplete:<claim>`
(item 2: an empty `claims` cannot bypass); every needed or declared claim adds `model_not_implemented:<claim>` (plus
`latency_unfitted:latency`), then the data reasons (coverage from the allow-listed `coverage_policy_id`).
`report.make_report` records them as `exec_realism`; any refusal = non-promotable, labelled, never hidden (the
`exec_calibration.promotable` style). Nothing needed or claimed = today's labelled taker / bar-close behaviour, nothing
to refuse. This module writes no ledger or registry record; a schema or integrity fault raises before any manifest
exists (no artifact).

Usage (repo root):
  python tools/research/exec_obs.py build --store DIR --id ID --root um/daily --depth-scope bd-pct-1-5/1
      --accessed-cairo TEXT --out FILE
  python tools/research/exec_obs.py verify FILE --store DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import namedtuple
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manifest as M                                                                        # noqa: E402
import pit as P                                                                             # noqa: E402
import splits as S                                                                          # noqa: E402

FORMAT = 'zb-exec-obs-manifest/1'
LOADER = 'zb-binance-vision-exec/1'
SOURCE = 'binance-vision-um/1'
DATA_ROOT = M.ARCHIVE_ROOT                                  # always the explicit out-of-repo `binance_um` store
AVAILABLE_RULE = 'event_ms + latency_policy.feed_ms'
SERIES = {'bookTicker': 'book_ticker', 'bookDepth': 'book_depth_summary', 'aggTrades': 'agg_trades'}  # dir -> series
SERIES_NAMES = tuple(sorted(SERIES.values()))
HEADERS = {'book_ticker': b'update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,transaction_time,'
                          b'event_time',
           'book_depth_summary': b'timestamp,percentage,depth,notional',
           'agg_trades': b'agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker'}
NCOL = {s: h.count(b',') + 1 for s, h in HEADERS.items()}
SEQUENCED = ('agg_trades', 'book_ticker')                   # book_depth_summary has no sequence column
SCHEMA = 'source_schema_mismatch'                           # the refusal of a row the authentic schema cannot hold
# Codex addendum 6225107087 item 3: gap and coverage thresholds are versioned, allow-listed policy ids whose values
# live HERE; no caller passes a threshold, and a new threshold is a new id. Gaps are only REPORTED (and lower coverage);
# they never reject a file. Not calibrated.
GAP_POLICIES = {'gap-policy/1': {'book_ticker': 60_000, 'book_depth_summary': 120_000, 'agg_trades': 60_000}}
COVERAGE_POLICIES = {'coverage-policy/1': 0.95}
# Q3: frozen integer feed latencies per policy id (a new policy is a new id, never an edited constant) + their labels.
LATENCY_POLICIES = {'lat-none/1': 0, 'lat-fixed-250/1': 250}
MAX_FEED_MS = 60_000                                        # a policy outside [0, MAX_FEED_MS] int ms is refused
LATENCY_LABELS = {'lat-none/1': 'none', 'lat-fixed-250/1': 'assumed/unfitted'}
LAT_NONE = 'lat-none/1'
# Q4: declared identifiers only; the depth / queue MODELS are deferred. `*-none/1` = no such model claimed.
DEPTH_NONE, QUEUE_NONE = 'depth-none/1', 'queue-none/1'
DEPTH_SCOPES = {'bd-pct-1-5/1': ('-5', '-4', '-3', '-2', '-1', '1', '2', '3', '4', '5')}
DEPTH_SCOPE_IDS = (DEPTH_NONE, *sorted(DEPTH_SCOPES))
QUEUE_POLICIES = (QUEUE_NONE, 'queue-fifo-back/1')
CLAIMS = ('depth_impact', 'latency', 'maker_fill', 'queue_position')
# Codex Q4: no claim is implemented by this ticket, whatever data is present.
IMPLEMENTED_CLAIMS: tuple[str, ...] = ()
# Series a claim cannot be made without, for every symbol of the run (contract section 4).
CLAIM_SERIES = {'maker_fill': ('agg_trades', 'book_ticker'), 'queue_position': ('agg_trades', 'book_ticker'),
                'depth_impact': ('book_depth_summary',), 'latency': ()}
# Codex addendum item 2: the realism a run NEEDS is derived from its registered execution config
# (`eval.config.execution`), never only from what it declares. Every key and value is allow-listed; an unknown key or
# value refuses. Absent = today's taker / bar-close execution (market entries and exits pay taker, costs.py), which
# needs no claim. A resting maker order needs both a maker-fill and a queue-position model.
EXECUTION_KEYS = {
    'entry_liquidity': {'taker': (), 'maker': ('maker_fill', 'queue_position')},
    'exit_liquidity': {'taker': (), 'maker': ('maker_fill', 'queue_position')},
    'target_order': {'market': (), 'limit': ('maker_fill', 'queue_position')},
    'latency': {'none': (), 'modelled': ('latency',)},
    'depth_impact': {'none': (), 'modelled': ('depth_impact',)},
    'queue': {'none': (), 'modelled': ('queue_position',)},
}
# A config key outside `execution` that names execution behaviour is refused rather than silently ignored.
UNSCOPED_EXEC_RE = re.compile(r'maker|taker|queue|latency|depth|impact|liquidity|limit_order|order_type|fill_model')
EXEC_KEYS = {'manifest_digest', 'gap_report_digest', 'latency_policy_id', 'depth_scope_id', 'queue_policy_id',
             'gap_policy_id', 'coverage_policy_id', 'claims'}
TOP_KEYS = {'format', 'manifest_id', 'loader_version', 'available_rule', 'data_root', 'accessed_cairo', 'note',
            'depth_scope', 'gap_policy_id', 'files', 'gap_report', 'gap_report_digest', 'digest'}
FILE_KEYS = {'path', 'series', 'symbol', 'member', 'sha256', 'sidecar_sha256', 'bytes', 'rows', 'first_event_ms',
             'last_event_ms', 'first_seq', 'last_seq', 'source', 'coverage'}
COV_KEYS = {'start_ms', 'end_ms', 'covered_ms', 'gap_count'}
GAP_KEYS = {'symbol', 'series', 'path', 'kind', 'from', 'to', 'at_ms'}
GAP_KINDS = ('file', 'head', 'seq', 'tail', 'time')
TIME_GAPS = ('head', 'tail', 'time')
NAME_RE = re.compile(r'^((?:[A-Z0-9]|[^\x00-\x7f])+)-(bookTicker|bookDepth|aggTrades)-(\d{4})-(\d{2})'
                     r'(?:-(\d{2}))?\.zip$')
MS_RE = re.compile(rb'^[1-9][0-9]{12}$')                    # 13-digit epoch ms
ID_RE = re.compile(rb'^(0|[1-9][0-9]{0,19})$')
PCT_RE = re.compile(rb'^-?(0|[1-9][0-9]{0,19})(\.[0-9]{1,18})?$')
BOOL = {b'true': True, b'false': False}
DEPTH_TS = '%Y-%m-%d %H:%M:%S'

BookTick = namedtuple('BookTick', 'update_id bid bid_qty ask ask_qty transaction_ms event_ms available_ms')
DepthSummary = namedtuple('DepthSummary', 'event_ms bands available_ms')   # bands: ((pct, depth, notional), ...)
AggTrade = namedtuple('AggTrade', 'agg_id price qty first_id last_id event_ms is_buyer_maker available_ms')


class ExecObsError(ValueError):
    pass


def _sha(obj) -> str:
    return hashlib.sha256(M.canonical(obj)).hexdigest()


def gap_report_digest(report) -> str:
    return _sha(report)


def file_meta(rel: str) -> dict:
    """Series / symbol / period of a store-relative archive path; raises unless the layout is exact (an unknown
    series directory is refused, never ignored)."""
    parts = M.check_rel_path(rel).split('/')
    m = NAME_RE.match(parts[-1])
    ok = (bool(m) and len(parts) == 5 and parts[0] == 'um' and parts[1] in ('daily', 'monthly')
          and parts[2] == m.group(2) and parts[3] == m.group(1) and (m.group(5) is None) == (parts[1] == 'monthly')
          and not (parts[2] == 'bookDepth' and parts[1] == 'monthly'))
    try:
        y, mo, d = int(m.group(3)), int(m.group(4)), int(m.group(5) or 1)
        start = datetime(y, mo, d, tzinfo=timezone.utc)
    except (AttributeError, TypeError, ValueError):
        ok = False
    if not ok:
        raise ExecObsError(f'{rel}: not um/<daily|monthly>/<series>/<SYMBOL>/<SYMBOL>-<series>-YYYY-MM[-DD].zip '
                           f'(series bookTicker / bookDepth (daily only) / aggTrades)')
    s = int(start.timestamp() * 1000)
    e = s + M.INTERVALS['1d'] if parts[1] == 'daily' else \
        int(datetime(y + mo // 12, mo % 12 + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return {'series': SERIES[parts[2]], 'symbol': m.group(1), 'start_ms': s, 'end_ms': e}


def _dec(rel, b: bytes, what: str, *, positive: bool = True, signed: bool = False) -> str:
    """A canonical plain decimal, kept as its exact text - never a float, so every output is byte-reproducible."""
    if not (PCT_RE if signed else M.QV_RE).match(b):
        raise ExecObsError(f'{SCHEMA}: {rel}: {what} {b!r} is not a canonical plain decimal')
    if positive and Decimal(b.decode('ascii')) <= 0:
        raise ExecObsError(f'{rel}: {what} {b!r} must be > 0')
    return b.decode('ascii')


def _int(rel, b: bytes, what: str, rx=ID_RE) -> int:
    if not rx.match(b):
        raise ExecObsError(f'{SCHEMA}: {rel}: {what} {b!r} is not a canonical integer'
                           + (' (13-digit ms)' if rx is MS_RE else ''))
    return int(b)


def _lines(rel: str, data: bytes, series: str) -> list[list[bytes]]:
    lines = data.split(b'\n')
    if lines and lines[-1] == b'':
        lines.pop()
    lines = [ln[:-1] if ln.endswith(b'\r') else ln for ln in lines]
    if lines and lines[0] == HEADERS[series]:
        lines = lines[1:]
    elif lines and lines[0][:1] and not lines[0][:1].isdigit():
        raise ExecObsError(f'{SCHEMA}: {rel}: unknown header {lines[0][:80]!r} '
                           f'(only the exact Binance header is accepted)')
    rows = [ln.split(b',') for ln in lines]
    if any(len(r) != NCOL[series] for r in rows):
        raise ExecObsError(f'{SCHEMA}: {rel}: a row does not have {NCOL[series]} columns')
    if not rows:
        raise ExecObsError(f'{SCHEMA}: {rel}: no rows')
    return rows


def _depth_ms(rel, b: bytes) -> int:
    try:
        s = b.decode('ascii')
        d = datetime.strptime(s, DEPTH_TS)
    except (UnicodeDecodeError, ValueError):
        d = None
    if d is None or d.strftime(DEPTH_TS) != s:            # round-trip exact: no single-digit / padded / ms variants
        raise ExecObsError(f'{SCHEMA}: {rel}: bookDepth timestamp {b!r} is not UTC {DEPTH_TS}')
    return int(d.replace(tzinfo=timezone.utc).timestamp() * 1000)


def parse(rel: str, data: bytes, meta: dict, depth_scope: dict, feed_ms: int = 0) -> list:
    """Row-by-row check of one archive CSV (fail closed); typed rows with `available_ms = event_ms + feed_ms`."""
    series, lo, hi = meta['series'], meta['start_ms'], meta['end_ms']
    rows, out = _lines(rel, data, series), []

    def in_period(t, what):
        if not lo <= t < hi:
            raise ExecObsError(f'{rel}: {what} {t} lies outside the file period [{lo}, {hi})')
        return t
    if series == 'book_ticker':
        for r in rows:
            uid = _int(rel, r[0], 'update_id')
            bid, bq = _dec(rel, r[1], 'best_bid_price'), _dec(rel, r[2], 'best_bid_qty')
            ask, aq = _dec(rel, r[3], 'best_ask_price'), _dec(rel, r[4], 'best_ask_qty')
            tx = in_period(_int(rel, r[5], 'transaction_time', MS_RE), 'transaction_time')
            ev = in_period(_int(rel, r[6], 'event_time', MS_RE), 'event_time')
            if not Decimal(bid) < Decimal(ask):
                raise ExecObsError(f'{rel}: crossed or locked book at update {uid} (bid {bid} >= ask {ask})')
            if tx > ev:
                raise ExecObsError(f'{rel}: transaction_time {tx} > event_time {ev} at update {uid}')
            if out and uid <= out[-1].update_id:
                raise ExecObsError(f'{rel}: update_id {uid} not strictly increasing (bad sequence)')
            if out and ev < out[-1].event_ms:
                raise ExecObsError(f'{rel}: event_time {ev} decreases (bad sequence)')
            out.append(BookTick(uid, bid, bq, ask, aq, tx, ev, ev + feed_ms))
    elif series == 'agg_trades':
        for r in rows:
            aid = _int(rel, r[0], 'agg_trade_id')
            price, qty = _dec(rel, r[1], 'price'), _dec(rel, r[2], 'quantity')
            f, l = _int(rel, r[3], 'first_trade_id'), _int(rel, r[4], 'last_trade_id')
            ev = in_period(_int(rel, r[5], 'transact_time', MS_RE), 'transact_time')
            if r[6] not in BOOL:
                raise ExecObsError(f'{SCHEMA}: {rel}: is_buyer_maker {r[6]!r} is not true/false')
            if f > l:
                raise ExecObsError(f'{rel}: first_trade_id {f} > last_trade_id {l} at agg trade {aid}')
            if out and aid <= out[-1].agg_id:
                raise ExecObsError(f'{rel}: agg_trade_id {aid} not strictly increasing (bad sequence)')
            if out and f <= out[-1].last_id:
                raise ExecObsError(f'{rel}: trade ids of agg trade {aid} overlap the previous row (bad sequence)')
            if out and ev < out[-1].event_ms:
                raise ExecObsError(f'{rel}: transact_time {ev} decreases (bad sequence)')
            out.append(AggTrade(aid, price, qty, f, l, ev, BOOL[r[6]], ev + feed_ms))
    else:
        want = sorted(Decimal(p) for p in depth_scope['bands'])
        groups: list[tuple[int, list]] = []
        for r in rows:
            t = in_period(_depth_ms(rel, r[0]), 'timestamp')
            band = (_dec(rel, r[1], 'percentage', positive=False, signed=True),
                    _dec(rel, r[2], 'depth', positive=False), _dec(rel, r[3], 'notional', positive=False))
            if groups and t == groups[-1][0]:
                groups[-1][1].append(band)
            elif groups and t < groups[-1][0]:
                raise ExecObsError(f'{rel}: summary timestamp {t} decreases')
            else:
                groups.append((t, [band]))
        for t, bands in groups:
            got = sorted(Decimal(b[0]) for b in bands)
            if got != want:
                raise ExecObsError(f'{rel}: summary {t} bands {[str(g) for g in got]} != frozen depth scope '
                                   f'{depth_scope["id"]} {list(depth_scope["bands"])}')
            bands = sorted(bands, key=lambda b: Decimal(b[0]))
            for side in ([b for b in bands if Decimal(b[0]) > 0],                        # outward: +1 -> +5
                         [b for b in reversed(bands) if Decimal(b[0]) < 0]):             # outward: -1 -> -5
                for a, b in zip(side, side[1:]):
                    if Decimal(b[1]) < Decimal(a[1]) or Decimal(b[2]) < Decimal(a[2]):
                        raise ExecObsError(f'{rel}: summary {t} depth/notional not monotonic outward from the mid '
                                           f'({a[0]} -> {b[0]})')
            out.append(DepthSummary(t, tuple(bands), t + feed_ms))
    return out


def _seq(series, r):
    return r.update_id if series == 'book_ticker' else r.agg_id if series == 'agg_trades' else None


def gaps_of(rel: str, meta: dict, rows: list, max_gap: int) -> list[dict]:
    """The gap entries of one parsed file: time gaps > max_gap and aggTrades trade-id jumps. A bookTicker update-id
    jump is NOT a gap (ids are non-contiguous by design, so a jump proves no missing row; Codex addendum item 3).
    `at_ms` is the last observed event before the gap."""
    base = {'symbol': meta['symbol'], 'series': meta['series'], 'path': rel}
    t = [r.event_ms for r in rows]
    out = []
    if t[0] - meta['start_ms'] > max_gap:
        out.append(dict(base, kind='head', at_ms=meta['start_ms'], **{'from': meta['start_ms'], 'to': t[0]}))
    out += [dict(base, kind='time', at_ms=a, **{'from': a, 'to': b}) for a, b in zip(t, t[1:]) if b - a > max_gap]
    if meta['end_ms'] - t[-1] > max_gap:
        out.append(dict(base, kind='tail', at_ms=t[-1], **{'from': t[-1], 'to': meta['end_ms']}))
    if meta['series'] == 'agg_trades':
        out += [dict(base, kind='seq', at_ms=a.event_ms, **{'from': a.last_id, 'to': b.first_id})
                for a, b in zip(rows, rows[1:]) if b.first_id != a.last_id + 1]
    return out


def read(abs_path: str, rel: str) -> tuple[bytes, str, bytes, bytes]:
    """(zip bytes, published checksum, raw sidecar bytes, CSV bytes). `manifest.read_archive` refuses a zip whose
    SHA-256 differs from its sidecar and any member list other than exactly `<name>.csv` (an extra, substituted or
    path-traversing member hard-refuses before a row is read); the raw sidecar bytes are kept so the manifest can
    bind them too (Codex addendum item 4)."""
    try:
        raw, published, data = M.read_archive(abs_path, rel)
        with open(abs_path + '.ok', 'rb') as f:
            side = f.read()
    except (M.ManifestError, OSError) as e:
        raise ExecObsError(f'{rel}: {e}')
    return raw, published, side, data


def scan(abs_path: str, rel: str, depth_scope: dict, gap_policy_id: str) -> tuple[dict, list[dict]]:
    """(file entry, its gap entries). The zip is hashed and must equal its published sidecar before any row is read."""
    meta = file_meta(rel)
    raw, published, side, data = read(abs_path, rel)
    if meta['series'] == 'book_depth_summary' and depth_scope['id'] not in DEPTH_SCOPES:
        raise ExecObsError(f'{rel}: a bookDepth summary needs a frozen depth scope, not {depth_scope["id"]}')
    rows = parse(rel, data, meta, depth_scope)
    gaps = gaps_of(rel, meta, rows, GAP_POLICIES[gap_policy_id][meta['series']])
    lost = sum(g['to'] - g['from'] for g in gaps if g['kind'] in TIME_GAPS)
    s = meta['series']
    entry = {'path': rel, 'series': s, 'symbol': meta['symbol'], 'member': member_of(rel), 'sha256': published,
             'sidecar_sha256': hashlib.sha256(side).hexdigest(), 'bytes': len(raw),
             'rows': len(rows), 'first_event_ms': rows[0].event_ms, 'last_event_ms': rows[-1].event_ms,
             'first_seq': _seq(s, rows[0]), 'last_seq': _seq(s, rows[-1]),
             'source': {'kind': SOURCE, 'url': M.archive_url(rel), 'archive_checksum': published},
             'coverage': {'start_ms': meta['start_ms'], 'end_ms': meta['end_ms'],
                          'covered_ms': meta['end_ms'] - meta['start_ms'] - lost, 'gap_count': len(gaps)}}
    return entry, gaps


def member_of(rel: str) -> str:
    """The one archive member a Binance zip holds: its own name with `.csv` (bound in the manifest)."""
    return rel.rsplit('/', 1)[1][:-4] + '.csv'


def _cross_file(files: list) -> list[dict]:
    """Gaps BETWEEN consecutive files of one (symbol, series): a missing period (`file`) and an aggTrades id jump
    (`seq`, at the last event before it). Overlapping periods (duplicates, a daily inside a monthly) and a sequence
    that does not increase across the boundary are refused: two sources for one instant would make the PIT view
    ambiguous (`pit._refuse_overlap`)."""
    out = []
    fs = sorted(files, key=lambda f: (f['symbol'], f['series'], f['coverage']['start_ms']))
    for a, b in zip(fs, fs[1:]):
        if (a['symbol'], a['series']) != (b['symbol'], b['series']):
            continue
        base = {'symbol': b['symbol'], 'series': b['series'], 'path': b['path']}
        if b['coverage']['start_ms'] < a['coverage']['end_ms']:
            raise ExecObsError(f'{a["path"]} and {b["path"]} overlap (one source per instant)')
        if b['coverage']['start_ms'] > a['coverage']['end_ms']:
            out.append(dict(base, kind='file', at_ms=a['coverage']['end_ms'],
                            **{'from': a['coverage']['end_ms'], 'to': b['coverage']['start_ms']}))
        if b['series'] in SEQUENCED and b['first_seq'] <= a['last_seq']:
            raise ExecObsError(f'{b["path"]}: sequence {b["first_seq"]} does not increase across the file boundary '
                               f'(previous file ends at {a["last_seq"]})')
        if b['series'] == 'agg_trades' and b['first_seq'] != a['last_seq'] + 1:
            out.append(dict(base, kind='seq', at_ms=a['last_event_ms'],
                            **{'from': a['last_seq'], 'to': b['first_seq']}))
    return out


def _sort_gaps(gaps):
    return sorted(gaps, key=lambda g: (g['symbol'], g['series'], g['path'], g['at_ms'], g['kind'], g['from'], g['to']))


def depth_scope_of(scope_id: str) -> dict:
    if scope_id != DEPTH_NONE and scope_id not in DEPTH_SCOPES:
        raise ExecObsError(f'depth scope must be one of {list(DEPTH_SCOPE_IDS)}')
    return {'id': scope_id, 'bands': list(DEPTH_SCOPES.get(scope_id, ()))}


def _report(files: list, gaps: list) -> list:
    return _sort_gaps(gaps + _cross_file(files))


def build(roots, *, manifest_id: str, base: str, depth_scope_id: str, gap_policy_id: str = 'gap-policy/1',
          accessed_cairo: str | None = None, note: str = '') -> dict:
    """`base` is the explicit `binance_um` store directory. Deterministic: files sorted by path, no run clock. Any
    schema or integrity fault raises before a manifest exists (no artifact)."""
    scope = depth_scope_of(depth_scope_id)
    if gap_policy_id not in GAP_POLICIES:
        raise ExecObsError(f'gap policy must be one of {sorted(GAP_POLICIES)} (thresholds are never caller-chosen)')
    files, gaps = [], []
    for root in roots:
        top = M.contained(base, root)
        if not os.path.isdir(top):
            raise ExecObsError(f'{root}: not a directory under the data store')
        for d, dirs, names in os.walk(top):
            dirs.sort()
            for name in sorted(names):
                if name.endswith('.zip'):
                    rel = os.path.relpath(os.path.join(d, name), os.path.realpath(base)).replace(os.sep, '/')
                    f, g = scan(M.contained(base, rel), rel, scope, gap_policy_id)
                    files.append(f)
                    gaps += g
    files.sort(key=lambda f: f['path'])
    report = _report(files, gaps)
    m = {'format': FORMAT, 'manifest_id': manifest_id, 'loader_version': LOADER, 'available_rule': AVAILABLE_RULE,
         'data_root': DATA_ROOT, 'accessed_cairo': accessed_cairo, 'note': note, 'depth_scope': scope,
         'gap_policy_id': gap_policy_id, 'files': files, 'gap_report': report,
         'gap_report_digest': gap_report_digest(report)}
    m['digest'] = M.digest_of(m)
    validate(m)
    return m


def validate(m: dict) -> None:
    """Schema + both digests. Raises ExecObsError naming the first problem."""
    def need(ok, msg):
        if not ok:
            raise ExecObsError(msg)
    need(isinstance(m, dict) and set(m) == TOP_KEYS, f'top-level keys must be exactly {sorted(TOP_KEYS)}')
    need(m['format'] == FORMAT and m['loader_version'] == LOADER and m['available_rule'] == AVAILABLE_RULE
         and m['data_root'] == DATA_ROOT, f'format / loader / rule / data_root must be {FORMAT} / {LOADER} / '
                                          f'{AVAILABLE_RULE!r} / {DATA_ROOT}')
    need(isinstance(m['manifest_id'], str) and m['manifest_id'] and isinstance(m['note'], str),
         'manifest_id must be a non-empty string, note a string')
    need(m['accessed_cairo'] is None or (isinstance(m['accessed_cairo'], str)
                                         and M._strict_date(m['accessed_cairo'][:10])),
         'accessed_cairo must be null or start with a valid YYYY-MM-DD Cairo date')
    sc = m['depth_scope']
    need(isinstance(sc, dict) and set(sc) == {'id', 'bands'} and sc['id'] in DEPTH_SCOPE_IDS
         and sc == depth_scope_of(sc['id']), f'depth_scope must be a frozen scope {list(DEPTH_SCOPE_IDS)}')
    need(m['gap_policy_id'] in GAP_POLICIES, f'gap_policy_id must be one of {sorted(GAP_POLICIES)}')
    need(isinstance(m['files'], list) and m['files'], 'files must be a non-empty list')
    paths = [f.get('path') if isinstance(f, dict) else None for f in m['files']]
    need(all(isinstance(p, str) for p in paths) and paths == sorted(set(paths)), 'file paths must be unique, sorted')
    for f in m['files']:
        p = f['path']
        need(set(f) == FILE_KEYS, f'{p}: file keys must be exactly {sorted(FILE_KEYS)}')
        meta = file_meta(p)
        need(f['series'] == meta['series'] and f['symbol'] == meta['symbol'], f'{p}: series/symbol disagree with path')
        need(f['member'] == member_of(p), f'{p}: archive member must be exactly {member_of(p)}')
        need(all(isinstance(f[k], str) and M.HEX64.match(f[k]) for k in ('sha256', 'sidecar_sha256')),
             f'{p}: sha256 / sidecar_sha256 must be 64 lowercase hex')
        need(f['source'] == {'kind': SOURCE, 'url': M.archive_url(p), 'archive_checksum': f['sha256']},
             f'{p}: source must be {SOURCE} at {M.ARCHIVE_URL}<path> with checksum == sha256')
        need(all(M._is_int(f[k]) for k in ('bytes', 'rows', 'first_event_ms', 'last_event_ms')) and f['rows'] > 0
             and f['bytes'] > 0 and meta['start_ms'] <= f['first_event_ms'] <= f['last_event_ms'] < meta['end_ms'],
             f'{p}: rows/bytes > 0 and first <= last event inside the file period required')
        need((M._is_int(f['first_seq']) and M._is_int(f['last_seq']) and f['first_seq'] <= f['last_seq'])
             if f['series'] in SEQUENCED else (f['first_seq'] is None and f['last_seq'] is None),
             f'{p}: first/last_seq int (null for book_depth_summary)')
        c = f['coverage']
        need(isinstance(c, dict) and set(c) == COV_KEYS and all(M._is_int(v) for v in c.values())
             and (c['start_ms'], c['end_ms']) == (meta['start_ms'], meta['end_ms'])
             and 0 <= c['covered_ms'] <= c['end_ms'] - c['start_ms'] and c['gap_count'] >= 0,
             f'{p}: coverage must be {sorted(COV_KEYS)} over the file period')
    gr = m['gap_report']
    need(isinstance(gr, list) and all(isinstance(g, dict) and set(g) == GAP_KEYS and g['kind'] in GAP_KINDS
                                      and all(M._is_int(g[k]) for k in ('from', 'to', 'at_ms')) and g['from'] < g['to']
                                      and g['path'] in paths for g in gr) and gr == _sort_gaps(gr),
         f'gap_report must be a sorted list of {sorted(GAP_KEYS)} entries naming manifested files')
    need(m['gap_report_digest'] == gap_report_digest(gr), 'gap_report_digest does not match the stored gap report')
    need(m['digest'] == M.digest_of(m), 'digest does not match the canonical content')


def verify(m: dict, base: str) -> list[str]:
    """Re-scan every file and recompute the gap report; returns the mismatches (empty = still describes the bytes)."""
    validate(m)
    out, files, gaps = [], [], []
    for f in m['files']:
        try:
            now, g = scan(M.contained(base, f['path']), f['path'], m['depth_scope'], m['gap_policy_id'])
        except (ExecObsError, M.ManifestError, OSError) as e:
            out.append(f'{f["path"]}: {e}')
            continue
        files.append(now)
        gaps += g
        out += [f'{f["path"]}: {k} {f[k]} != {now[k]}' for k in sorted(FILE_KEYS) if now[k] != f[k]]
    if not out:
        try:
            if gap_report_digest(_report(files, gaps)) != m['gap_report_digest']:
                out.append('gap_report_digest recomputed from the files differs from the manifest')
        except ExecObsError as e:
            out.append(str(e))
    return out


def write(m: dict, path: str) -> None:
    """Write once; identical bytes are a no-op, different bytes are refused (a written manifest is never edited)."""
    validate(m)
    data = json.dumps(m, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False).encode('ascii') + b'\n'
    if os.path.exists(path):
        with open(path, 'rb') as fh:
            if fh.read() == data:
                return
        raise ExecObsError(f'{path} exists with different content; manifests are immutable, use a new manifest_id')
    with open(path, 'wb') as fh:
        fh.write(data)


def load(path: str) -> dict:
    try:
        with open(path, 'rb') as fh:
            m = json.loads(fh.read(), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except (OSError, ValueError) as e:
        raise ExecObsError(f'{path}: unreadable or not strict JSON: {e}')
    validate(m)
    return m


def feed_ms_of(latency_policy_id) -> int:
    """The frozen feed latency of a policy id; an unknown id, or a policy value that is not an int in
    [0, MAX_FEED_MS] (negative, overflowing, bool, float), refuses (Codex addendum item 5)."""
    if latency_policy_id not in LATENCY_POLICIES:
        raise ExecObsError(f'latency policy must be one of {sorted(LATENCY_POLICIES)}')
    v = LATENCY_POLICIES[latency_policy_id]
    if not M._is_int(v) or not 0 <= v <= MAX_FEED_MS:
        raise ExecObsError(f'latency policy {latency_policy_id} value {v!r} is not an int in [0, {MAX_FEED_MS}] ms')
    return v


class ExecObs:
    """One verified exec-obs manifest under one frozen latency policy, served inside one opened `pit.Window`.
    Harness-side; `view(T)` is the only way to rows."""

    def __init__(self, manifest: dict, store: str, latency_policy_id: str, window):
        validate(manifest)
        self._feed = feed_ms_of(latency_policy_id)
        # Codex amendment 8: never a free-standing range - only a Window that pit.Access opened (ledger-recorded, the
        # sealed holdout behind its full guard) bounds what is served.
        if not isinstance(window, P.Window) or P._SOURCES.get(window._cap) is not window:
            raise ExecObsError('exec observations are served only inside a pit.Window opened through pit.Access')
        self._m, self._store, self._lo, self._hi = manifest, store, window.lo, window.hi
        self.digest, self.latency_policy_id = manifest['digest'], latency_policy_id
        self.latency_label = LATENCY_LABELS[latency_policy_id]
        self._files: dict[tuple, list] = {}
        for f in manifest['files']:
            self._files.setdefault((f['series'], f['symbol']), []).append(f)
        for v in self._files.values():
            v.sort(key=lambda f: f['coverage']['start_ms'])
        self._cache: dict[str, list] = {}

    def _rows(self, f: dict) -> list:
        """Parsed rows of one manifest file after the zip AND raw-sidecar re-check (fail closed), cached per path."""
        p = f['path']
        if p not in self._cache:
            _, published, side, data = read(M.contained(self._store, p), p)
            if published != f['sha256'] or hashlib.sha256(side).hexdigest() != f['sidecar_sha256']:
                raise ExecObsError(f'{p}: bytes no longer match the manifest sha256 / sidecar')   # BEFORE any parse
            rows = parse(p, data, file_meta(p), self._m['depth_scope'], self._feed)
            if (len(rows), rows[0].event_ms, rows[-1].event_ms) != (f['rows'], f['first_event_ms'], f['last_event_ms']):
                raise ExecObsError(f'{p}: rows / event span differ from the manifest')
            self._cache[p] = rows
        return self._cache[p]

    def view(self, t: int) -> 'View':
        if not M._is_int(t) or not self._lo <= t <= self._hi:
            raise ExecObsError(f'decision time {t!r} outside the opened window [{self._lo}, {self._hi}]')
        return View(self, t)


class View:
    """Frozen at `t`: every accessor returns only rows with `event_ms >= window lo` and `available_ms <= t` (t <= the
    window's hi). Per-series accessors are in file order: event time, then the series' sequence (update_id /
    agg_trade_id) for equal event times, then row order. `events` merges the series with the deterministic tiebreak
    (available_ms, series name, sequence or per-series row index) (Codex addendum item 5)."""
    __slots__ = ('_obs', 't')

    def __init__(self, obs: ExecObs, t: int):
        object.__setattr__(self, '_obs', obs)
        object.__setattr__(self, 't', t)

    def __setattr__(self, k, v):
        raise AttributeError('a view is frozen at its decision time')

    def _series(self, series: str, symbol: str, n=None, since=None) -> tuple:
        o, out = self._obs, []
        lo = o._lo if since is None else max(o._lo, since)
        for f in o._files.get((series, symbol), []):
            # available_ms >= event_ms >= period start: a file starting after t holds nothing available at t
            if f['coverage']['start_ms'] > self.t or f['coverage']['end_ms'] <= lo:
                continue
            out += [r for r in o._rows(f) if r.available_ms <= self.t and r.event_ms >= lo]
        return tuple(out[-n:] if n is not None else out)

    def book_ticker(self, symbol: str, n: int | None = None) -> tuple:
        return self._series('book_ticker', symbol, n)

    def book_depth_summary(self, symbol: str, n: int | None = None) -> tuple:
        return self._series('book_depth_summary', symbol, n)

    def agg_trades(self, symbol: str, since_ms: int | None = None) -> tuple:
        return self._series('agg_trades', symbol, since=since_ms)

    def events(self, symbol: str) -> tuple:
        """Every series of `symbol`, merged as ((series, row), ...) in (available_ms, series, seq | index) order."""
        out = []
        for s in SERIES_NAMES:
            out += [(r.available_ms, s, _seq(s, r) if s in SEQUENCED else i, r)
                    for i, r in enumerate(self._series(s, symbol))]
        out.sort(key=lambda x: x[:3])
        return tuple((s, r) for _, s, _, r in out)


# ---- run identity block (`zb-research-run/3`) and the fail-closed promotion gate ------------------------------------

def validate_block(eo) -> None:
    """Shape of a run's `exec_obs` block (report.validate_run dispatches here for /3 runs). Keys, policy ids and claim
    strings are exact: unknown values are refused, never normalized or ignored."""
    def need(ok, msg):
        if not ok:
            raise ExecObsError(f'exec_obs: {msg}')
    need(isinstance(eo, dict) and set(eo) == EXEC_KEYS, f'keys must be exactly {sorted(EXEC_KEYS)}')
    for k in ('manifest_digest', 'gap_report_digest'):
        need(eo[k] is None or (isinstance(eo[k], str) and bool(M.HEX64.match(eo[k]))), f'{k} must be null or 64 hex')
    need((eo['manifest_digest'] is None) == (eo['gap_report_digest'] is None),
         'manifest_digest and gap_report_digest are both null or both set')
    need(eo['latency_policy_id'] in LATENCY_POLICIES, f'latency_policy_id must be one of {sorted(LATENCY_POLICIES)}')
    need(eo['depth_scope_id'] in DEPTH_SCOPE_IDS, f'depth_scope_id must be one of {list(DEPTH_SCOPE_IDS)}')
    need(eo['queue_policy_id'] in QUEUE_POLICIES, f'queue_policy_id must be one of {list(QUEUE_POLICIES)}')
    need(eo['gap_policy_id'] in GAP_POLICIES, f'gap_policy_id must be one of {sorted(GAP_POLICIES)}')
    need(eo['coverage_policy_id'] in COVERAGE_POLICIES,
         f'coverage_policy_id must be one of {sorted(COVERAGE_POLICIES)}')
    c = eo['claims']
    need(isinstance(c, list) and all(isinstance(x, str) for x in c) and c == sorted(set(c)),
         'claims must be a sorted unique list of strings')
    need(set(c) <= set(CLAIMS), f'unknown claim(s) {sorted(set(c) - set(CLAIMS))}: claims are a subset of '
                                f'{list(CLAIMS)}')


def required_claims(config) -> tuple[set, list[str]]:
    """(claims the registered execution config needs, refusals for keys / values outside the allow-list)."""
    if not isinstance(config, dict):
        return set(), ['execution_config_invalid: eval.config is not an object']
    bad = sorted(f'unscoped_execution_key:{k}' for k in config
                 if k != 'execution' and isinstance(k, str) and UNSCOPED_EXEC_RE.search(k))
    ex = config.get('execution', {})
    if not isinstance(ex, dict):
        return set(), bad + ['execution_config_invalid: eval.config.execution is not an object']
    need = set()
    for k in sorted(ex):
        if k not in EXECUTION_KEYS:
            bad.append(f'unknown_execution_key:{k}')
        elif not isinstance(ex[k], str) or ex[k] not in EXECUTION_KEYS[k]:
            bad.append(f'unknown_execution_value:{k}')
        else:
            need |= set(EXECUTION_KEYS[k][ex[k]])
    return need, bad


def _clip(a, b, lo, hi):
    return max(0, min(b, hi) - max(a, lo))


def _covered(m: dict, symbol: str, series: str, lo: int, hi: int) -> int:
    """ms of the run window [lo, hi) covered by manifested files of (symbol, series), minus their time gaps (window
    level: a missing day file simply contributes nothing)."""
    paths = {f['path'] for f in m['files'] if (f['symbol'], f['series']) == (symbol, series)}
    have = sum(_clip(f['coverage']['start_ms'], f['coverage']['end_ms'], lo, hi)
               for f in m['files'] if f['path'] in paths)
    lost = sum(_clip(g['from'], g['to'], lo, hi) for g in m['gap_report'] if g['path'] in paths
               and g['kind'] in TIME_GAPS)
    return have - lost


def realism_refusals(run: dict, manifest: dict | None, *, store: str | None) -> list[str]:
    """Every reason this /3 run is not promotable on execution realism (empty = nothing to refuse); sorted, atomic -
    one reason makes the whole run non-promotable. Reasons are `<reason>:<claim>[:<symbol>/<series>]` or a bare
    manifest-level reason. The realism NEEDED is derived from `eval.config.execution` (`required_claims`) and must be
    declared (`exec_claims_incomplete`); every needed or declared claim is `model_not_implemented` in this ticket. The
    manifest digest and gap report are recomputed from the manifest and the files under `store` (the envelope's values
    are only compared, never trusted). Window, symbols and thresholds come from the run and the frozen policy ids,
    never from the caller."""
    eo = run.get('exec_obs') if isinstance(run, dict) else None
    claims = eo.get('claims') if isinstance(eo, dict) else None
    if not isinstance(claims, list):
        return ['exec_obs_invalid: no claims list']
    unknown = sorted(f'unknown_claim:{c}' for c in claims if c not in CLAIMS)
    try:
        validate_block(eo)
    except ExecObsError as e:
        return unknown + [f'exec_obs_invalid: {e}']
    config = run['eval']['config']
    need, out = required_claims(config)
    out += [f'exec_claims_incomplete:{c}' for c in sorted(need - set(claims))]
    eff = sorted(need | set(claims))
    if not eff:
        return sorted(out)                                 # taker / bar-close as today: no realism needed or claimed
    out += [f'model_not_implemented:{c}' for c in eff if c not in IMPLEMENTED_CLAIMS]
    if 'latency' in eff:
        out.append('latency_unfitted:latency')             # Q3: no policy id here is a fitted latency
    if eo['manifest_digest'] is None:
        return sorted(out + [f'no_manifest:{c}' for c in eff])
    if manifest is None or store is None:
        return sorted(out + [f'manifest_unavailable:{c}' for c in eff])
    try:
        validate(manifest)
    except (ExecObsError, M.ManifestError, KeyError, TypeError, AttributeError) as e:
        return sorted(out + [f'manifest_invalid: {e}'])
    bad = []
    if M.digest_of(manifest) != eo['manifest_digest']:
        bad.append('manifest_digest_mismatch')
    if gap_report_digest(manifest['gap_report']) != eo['gap_report_digest']:
        bad.append('gap_report_digest_mismatch')
    if manifest['gap_policy_id'] != eo['gap_policy_id']:
        bad.append('gap_policy_mismatch')
    try:
        unverified = verify(manifest, store)               # files re-hashed, gap report recomputed from them
    except (ExecObsError, M.ManifestError) as e:
        unverified = [str(e)]
    bad += [f'manifest_files_unverified: {u}' for u in unverified]
    if bad:
        return sorted(out + bad)                           # nothing below can be trusted on an unproven manifest
    lo, hi = S.parse_utc(run['window']['start']), S.parse_utc(run['window']['end'])
    syms = config.get('symbols')
    floor = COVERAGE_POLICIES[eo['coverage_policy_id']]
    for c in eff:
        if c == 'latency' and eo['latency_policy_id'] == LAT_NONE:
            out.append('latency_policy_none:latency')
        if c == 'queue_position' and eo['queue_policy_id'] == QUEUE_NONE:
            out.append('queue_policy_none:queue_position')
        if c == 'depth_impact' and eo['depth_scope_id'] != manifest['depth_scope']['id']:
            out.append('depth_scope_mismatch:depth_impact')
        if not CLAIM_SERIES[c]:
            continue
        if not (isinstance(syms, list) and syms and all(isinstance(s, str) and s for s in syms)):
            out.append(f'run_symbols_undeclared:{c}')
            continue
        for sym in sorted(set(syms)):
            for ser in CLAIM_SERIES[c]:
                key = f'{c}:{sym}/{ser}'
                if not any((f['symbol'], f['series']) == (sym, ser) for f in manifest['files']):
                    out.append(f'missing_series:{key}')
                    continue
                if _covered(manifest, sym, ser, lo, hi) < floor * (hi - lo):
                    out.append(f'coverage_below_min:{key}')
                gs = [g for g in manifest['gap_report'] if (g['symbol'], g['series']) == (sym, ser)]
                if any(g['kind'] == 'file' and _clip(g['from'], g['to'], lo, hi) for g in gs):
                    out.append(f'missing_period:{key}')
                if any(g['kind'] == 'seq' and lo <= g['at_ms'] < hi for g in gs):
                    out.append(f'sequence_gap:{key}')
    return sorted(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--id', required=True)
    b.add_argument('--root', action='append', required=True)
    b.add_argument('--depth-scope', default=DEPTH_NONE, choices=DEPTH_SCOPE_IDS)
    b.add_argument('--gap-policy', default='gap-policy/1', choices=sorted(GAP_POLICIES))
    b.add_argument('--accessed-cairo')
    b.add_argument('--note', default='')
    b.add_argument('--out', required=True)
    v = sub.add_parser('verify')
    v.add_argument('file')
    for p in (b, v):
        p.add_argument('--store', required=True, help='the binance_um store directory (outside the repository)')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'build':
            m = build(a.root, manifest_id=a.id, base=a.store, depth_scope_id=a.depth_scope,
                      gap_policy_id=a.gap_policy, accessed_cairo=a.accessed_cairo, note=a.note)
            write(m, a.out)
            print(m['digest'])
            return 0
        bad = verify(load(a.file), a.store)
        print('\n'.join(bad) if bad else 'OK')
        return 1 if bad else 0
    except (ExecObsError, M.ManifestError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
