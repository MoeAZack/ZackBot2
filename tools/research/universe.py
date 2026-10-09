"""`zb-pit-universe/1`: the point-in-time weekly universe `pit-top40-qv30d-v1` (RES-01 R2; plan sections 1a and 8).

Input is one `zb-data-manifest/1` built by the `zb-binance-vision-zip/1` loader: its daily last-price klines
(`um/monthly/klines/<SYMBOL>/1d/`) for every historically listed USD-M USDT perpetual, delisted contracts included.
Each archive is re-hashed and must match both the manifest and its published checksum before a byte is parsed.

Rules (all evaluated with information available at the ranking instant only):
  * Ranking instant: every Monday 00:00 UTC. A daily bar counts only when `available_ms = open_ms + 1d <= instant`.
  * Score: trailing 30-day quote volume = the sum of `quote_volume` over the daily bars opening in
    [instant - 30d, instant - 1d]. Rank descending, ties broken by symbol; keep the top 40.
  * Eligibility test (a), listing age: instant - listing >= 30 days. Listing = the contract's first archived daily bar;
    `listing_censored` marks contracts already trading in the store's first month (the true listing is earlier, so the
    test is conservative). Test (b), the strategy's own warm-up, is per strategy and is applied by the R3 harness.
  * Trading-at-instant test: the contract must have the daily bar that closes exactly at the instant. A contract whose
    archive simply stops drops out the first Monday after its last bar - observable then, never earlier.
  * Delist veto: only from a timestamped announcement/status observation (`--delist-observations`), effective from its
    `observed_ms`. The archive end date (`archive_last_close_ms`) and the last traded close (`last_traded_close_ms`;
    settled contracts keep printing zero-volume bars) are hindsight, recorded for audit only; they never veto a
    ranking. A contract with zero trailing quote volume is simply unrankable (`zero-qv30d`), which is observable.
  * Renames (`--renames`, each with an `effective_ms` and a source) join two symbols into one contract identity: one
    volume history, the earliest listing, and the symbol trading at the instant is the member.
  * Exchange rules (tick / step / min qty / min notional) before the first genuinely observed snapshot are labelled
    `RULES-BACKFILLED` per week (`--rules-first-observed-ms`; none exists yet, so every week carries the label).

Output is deterministic (no run clock); its digest is the SHA-256 of the canonical JSON without `digest`, and a written
universe file is never edited. Stdlib only, no network. No price returns are computed here.

Usage (repo root):
  python tools/research/universe.py build --manifest research_evidence/manifests/binance-um-archive-v1.json
      --store C:/Dev/ZackBot2_data/binance_um --out research_evidence/universe/pit-top40-qv30d-v1.json [--workers N]
  python tools/research/universe.py verify FILE --manifest MANIFEST --store DIR [--workers N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manifest as M                                                                        # noqa: E402

FORMAT = 'zb-pit-universe/1'
UNIVERSE_ID = 'pit-top40-qv30d-v1'
DAY = 86_400_000
WEEK = 7 * DAY
MONDAY_EPOCH = 4 * DAY                                      # 1970-01-05 00:00 UTC was a Monday
TOP_N = 40
LOOKBACK_DAYS = 30
MIN_AGE_DAYS = 30
RULES_BACKFILLED = 'RULES-BACKFILLED'
RULES_OBSERVED = 'RULES-OBSERVED'
VETO_AGE = 'listing-age<30d'
VETO_NOT_TRADING = 'no-bar-closing-at-instant'
VETO_DELIST = 'delist-observed'
VETO_NO_VOLUME = 'zero-qv30d'


class UniverseError(ValueError):
    pass


def utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')


def _daily_job(job):
    abs_path, rel, sha = job
    raw, published, data = M.read_archive(abs_path, rel)
    if published != sha:
        raise UniverseError(f'{rel}: bytes no longer match the manifest sha256')
    rows = M.parse_archive_csv(rel, data, M.archive_meta(rel))
    return M.archive_meta(rel)['symbol'], [(int(r[0]), float(r[7])) for r in rows]


def load_daily(manifest: dict, store: str, workers: int = 1) -> dict[str, dict[int, float]]:
    """{symbol: {open_ms: quote_volume}} from the manifest's 1d last-price klines, verified against the manifest."""
    M.validate(manifest)
    if manifest['loader_version'] != M.ARCHIVE_LOADER:
        raise UniverseError(f'the PIT universe needs a {M.ARCHIVE_LOADER} manifest (all-listed, not survivor-only)')
    jobs = [(M.contained(store, f['path']), f['path'], f['sha256']) for f in manifest['files']
            if f['series'] == 'klines' and f['interval'] == '1d']
    if not jobs:
        raise UniverseError('the manifest has no 1d klines')
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            parts = list(ex.map(_daily_job, jobs, chunksize=64))
    else:
        parts = [_daily_job(j) for j in jobs]
    daily: dict[str, dict[int, float]] = {}
    for sym, rows in parts:
        d = daily.setdefault(sym, {})
        for t, qv in rows:
            if t in d:
                raise UniverseError(f'{sym}: duplicate daily bar {utc_date(t)} across archives')
            d[t] = qv
    return daily


def _check_renames(renames, symbols) -> dict[str, str]:
    """old symbol -> contract id (the chain's first symbol). Each rename needs a timestamp and a source."""
    parent = {}
    for r in renames:
        if not (isinstance(r, dict) and set(r) == {'old', 'new', 'effective_ms', 'source'} and type(r['effective_ms']) is int
                and isinstance(r['source'], str) and r['source'] and r['old'] in symbols and r['new'] in symbols
                and r['old'] != r['new'] and r['new'] not in parent):
            raise UniverseError(f'bad rename {r!r}: need {{old, new, effective_ms, source}} over listed symbols')
        parent[r['new']] = r['old']
    cid = {}
    for s in symbols:
        root, seen = s, set()
        while root in parent:
            if root in seen:
                raise UniverseError('rename cycle')
            seen.add(root)
            root = parent[root]
        cid[s] = root
    return cid


def _check_observations(obs, symbols) -> dict[str, int]:
    out = {}
    for o in obs:
        if not (isinstance(o, dict) and set(o) == {'symbol', 'observed_ms', 'source'} and o['symbol'] in symbols
                and type(o['observed_ms']) is int and isinstance(o['source'], str) and o['source']):
            raise UniverseError(f'bad delist observation {o!r}: need {{symbol, observed_ms, source}}')
        out[o['symbol']] = min(out.get(o['symbol'], o['observed_ms']), o['observed_ms'])
    return out


def build(daily: dict[str, dict[int, float]], *, manifest_digest: str, renames=(), delist_observations=(),
          rules_first_observed_ms: int | None = None, top_n: int = TOP_N, universe_id: str = UNIVERSE_ID) -> dict:
    symbols = sorted(daily)
    if not symbols or not M.HEX64.match(manifest_digest or ''):
        raise UniverseError('need daily bars and a 64-hex manifest digest')
    cid = _check_renames(list(renames), set(symbols))
    delist = _check_observations(list(delist_observations), set(symbols))
    store_start = min(min(d) for d in daily.values() if d)
    store_end = max(max(d) for d in daily.values() if d) + DAY
    contracts: dict[str, list[str]] = {}
    for s in symbols:
        contracts.setdefault(cid[s], []).append(s)
    listing = {c: min(min(daily[s]) for s in ss if daily[s]) for c, ss in contracts.items()}

    table = []
    for s in symbols:
        d = daily[s]
        traded = [t for t, q in d.items() if q > 0]
        table.append({'symbol': s, 'contract_id': cid[s], 'first_open_ms': min(d) if d else None,
                      'listing_ms': listing[cid[s]], 'listing_censored': listing[cid[s]] == store_start,
                      'archive_last_close_ms': max(d) + DAY if d else None,
                      'archive_ended_before_store_end': bool(d) and max(d) + DAY < store_end,
                      # settled contracts keep printing zero-volume daily bars, so the last traded close is the
                      # practical delist date; like the archive end it is hindsight, recorded for audit only
                      'last_traded_close_ms': max(traded) + DAY if traded else None})

    first = store_start + LOOKBACK_DAYS * DAY
    monday = first + (MONDAY_EPOCH - first) % WEEK
    weeks = []
    while monday <= store_end:
        lo = monday - LOOKBACK_DAYS * DAY
        scored, vetoes = [], []
        for c, ss in sorted(contracts.items()):
            # open in [lo, monday - 1d] <=> closed (available) at or before the instant
            qv = sum(daily[s].get(t, 0.0) for s in ss for t in range(lo, monday, DAY))
            lives = [s for s in ss if monday - DAY in daily[s]]
            if qv <= 0 and not lives:
                continue                                                       # not trading in the window at all
            # the member symbol: the one trading at the instant (the newest name if a rename overlaps), else the last seen
            live = max(lives, key=lambda s: (min(daily[s]), s)) if lives else None
            sym = live or max(ss, key=lambda s: (max((t for t in daily[s] if t < monday), default=-1), s))
            reason = None
            if monday - listing[c] < MIN_AGE_DAYS * DAY:
                reason = VETO_AGE
            elif live is None:
                reason = VETO_NOT_TRADING
            elif min((delist[s] for s in ss if s in delist), default=monday + 1) <= monday:
                reason = VETO_DELIST
            elif qv <= 0:
                reason = VETO_NO_VOLUME
            if reason:
                vetoes.append([sym, reason])
            else:
                scored.append((-qv, sym, qv))
        scored.sort()
        top = scored[:top_n]
        weeks.append({'monday_ms': monday, 'monday_utc': utc_date(monday), 'eligible': len(scored),
                      'members': [s for _, s, _ in top], 'qv30d_usdt': [int(round(q)) for _, _, q in top],
                      'vetoes': sorted(vetoes),
                      'rules': RULES_OBSERVED if rules_first_observed_ms is not None and monday >= rules_first_observed_ms
                      else RULES_BACKFILLED})
        monday += WEEK

    u = {'format': FORMAT, 'universe_id': universe_id, 'manifest_digest': manifest_digest,
         'rule': {'instant': 'Monday 00:00 UTC', 'score': f'sum quote_volume of 1d bars with open in '
                                                          f'[instant-{LOOKBACK_DAYS}d, instant-1d] (available_ms <= instant)',
                  'top_n': top_n, 'min_listing_age_days': MIN_AGE_DAYS, 'tie_break': 'symbol ascending',
                  'warmup_test': 'per strategy, applied by the R3 harness (independent of the listing-age test)',
                  'delist_veto': 'timestamped observations only; archive end is hindsight, audit only',
                  'rules_label': f'{RULES_BACKFILLED} before rules_first_observed_ms'},
         'store_start_ms': store_start, 'store_end_ms': store_end, 'rules_first_observed_ms': rules_first_observed_ms,
         'renames': sorted(renames, key=lambda r: (r['effective_ms'], r['old'])),
         'delist_observations': sorted(delist_observations, key=lambda o: (o['observed_ms'], o['symbol'])),
         'symbols': table, 'weeks': weeks}
    u['digest'] = hashlib.sha256(M.canonical(u)).hexdigest()
    return u


def digest_of(u: dict) -> str:
    return hashlib.sha256(M.canonical({k: v for k, v in u.items() if k != 'digest'})).hexdigest()


def write(u: dict, path: str) -> None:
    """Write once (identical bytes are a no-op; different bytes are refused)."""
    if u.get('digest') != digest_of(u):
        raise UniverseError('digest does not match the canonical content')
    data = json.dumps(u, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False).encode('ascii') + b'\n'
    if os.path.exists(path):
        with open(path, 'rb') as f:
            if f.read() == data:
                return
        raise UniverseError(f'{path} exists with different content; universe files are immutable, use a new id')
    with open(path, 'wb') as f:
        f.write(data)


def _load_json(path):
    if not path:
        return []
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--out', required=True)
    v = sub.add_parser('verify')
    v.add_argument('file')
    for p in (b, v):
        p.add_argument('--manifest', required=True)
        p.add_argument('--store', required=True, help='the data store the manifest describes (outside the repo)')
        p.add_argument('--renames', help='JSON list of {old, new, effective_ms, source}')
        p.add_argument('--delist-observations', help='JSON list of {symbol, observed_ms, source}')
        p.add_argument('--rules-first-observed-ms', type=int)
        p.add_argument('--workers', type=int, default=1)
    a = ap.parse_args(argv)
    try:
        m = M.load(a.manifest)
        u = build(load_daily(m, a.store, a.workers), manifest_digest=m['digest'], renames=_load_json(a.renames),
                  delist_observations=_load_json(a.delist_observations),
                  rules_first_observed_ms=a.rules_first_observed_ms)
        if a.cmd == 'build':
            write(u, a.out)
            print(u['digest'])
            return 0
        with open(a.file, encoding='utf-8') as f:
            have = json.load(f)
        ok = have.get('digest') == u['digest'] == digest_of(have)
        print('OK' if ok else f'MISMATCH: file {have.get("digest")} rebuilt {u["digest"]}')
        return 0 if ok else 1
    except (M.ManifestError, UniverseError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
