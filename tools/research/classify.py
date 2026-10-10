"""`zb-instrument-classes/2`: deterministic, sourced, point-in-time instrument classification (RES-01 R4-0 item 2).

Replaces the manual `zb-instrument-classes/1` review with a builder over COMMITTED source snapshots only. The source
contract is docs/newcore/research/CLASSIFICATION_SOURCES.md; this module implements it. No network: snapshots are
captured separately (see `extract`) and every input file is bound by its SHA-256.

Inputs
  * one or more `zb-exchangeinfo-snapshot/1` files: the symbol entries of an unauthenticated
    `GET https://fapi.binance.com/fapi/v1/exchangeInfo` response, with retrieval URL, retrieval time and the SHA-256
    of the raw response body (`extract` produces one from a saved body);
  * one `zb-announcement-records/1` file: Binance announcements transcribed as records (URL, publication time,
    retrieval time, page SHA-256, kind, symbol, effective time, asserted class);
  * the `zb-data-manifest/1` archive manifest (first/last archived daily bar per symbol, relisting gaps);
  * optionally the record id of the `tradfi-launch` announcement that bounds the pre-TradFi crypto rule.

Output: per manifest symbol, contiguous dated rows `[effective_from_ms, effective_to_ms)` (last row open-ended), each
citing its source ids, plus a digest over the canonical JSON. Same inputs -> byte-identical output (no run clock).
Missing, unmapped or contradictory evidence -> an explicit `UNKNOWN` row, never a guess; `UNKNOWN` is not addressable.
Scope (owner scope cut, #53 comment 6095065913): only the native-crypto research universe and the gold pilot (XAUUSDT
direct, PAXGUSDT tokenized) are classified; every other symbol gets one audited `OUT_OF_SCOPE` (DEFERRED) row, never
active and never researched. Each row carries its `scope`.

Usage (repo root):
  python tools/research/classify.py extract RAW_BODY --retrieved-ms MS --snapshot-id ID [--manifest MANIFEST]
      --out SNAPSHOT.json
  python tools/research/classify.py build --exchange-info SNAP.json [--exchange-info ...] --announcements ANN.json
      --manifest research_evidence/manifests/binance-um-archive-v1.json.gz [--tradfi-cutoff RECORD_ID]
      --classes-id instrument-classes-v2 --out research_evidence/universe/instrument-classes-v2.json
  python tools/research/classify.py verify FILE (same inputs as build)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manifest as M                                                                        # noqa: E402

FORMAT = 'zb-instrument-classes/2'
SNAPSHOT_FORMAT = 'zb-exchangeinfo-snapshot/1'
ANNOUNCEMENTS_FORMAT = 'zb-announcement-records/1'
BUILDER_VERSION = 'zb-classify/1'
EXCHANGEINFO_URL = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
DAY = 86_400_000
LISTING_TOLERANCE_MS = DAY          # documented listing times further apart than this are a contradiction
RELIST_GAP_MS = 30 * DAY            # an archive gap this long ends the contiguous contract the cutoff rule covers

UNKNOWN = 'UNKNOWN'
OUT_OF_SCOPE = 'OUT_OF_SCOPE'       # DEFERRED (owner scope cut #53 6095065913): audited, never active, never researched
CLASSES = ('crypto', 'crypto-index', 'tokenized-gold', 'commodity')
CRYPTO_CLASSES = ('crypto', 'crypto-index')
# Scope (owner scope cut): the native-crypto research universe plus a two-instrument gold pilot. The pilot only selects
# scope; each pilot symbol's class is still derived from its sources and must equal the expected identity, else UNKNOWN.
GOLD_PILOT = {'XAUUSDT': ('commodity', 'gold-spot'), 'PAXGUSDT': ('tokenized-gold', 'paxg')}
SCOPE_CRYPTO, SCOPE_GOLD, SCOPE_DEFERRED = 'crypto-research', 'gold-pilot', 'deferred'
# Committed, reviewable mapping tables (contract section 3). A base asset missing here fails closed to UNKNOWN.
COMMODITY_SUBCLASS = {'XAU': 'gold-spot'}
TOKENIZED_GOLD_BASES = {'PAXG': 'paxg', 'XAUT': 'xaut'}    # XAUT is recognised only to route it out of the crypto book

SNAP_KEYS = {'format', 'snapshot_id', 'retrieval_url', 'retrieved_ms', 'response_sha256', 'response_server_time_ms',
             'scope', 'symbols'}
SNAP_SYMBOL_KEYS = {'symbol', 'pair', 'baseAsset', 'quoteAsset', 'contractType', 'underlyingType', 'underlyingSubType',
                    'onboardDate'}                          # only the fields the builder reads (ruling 3: minimal)
ANN_KEYS = {'format', 'records'}
RECORD_KEYS = {'record_id', 'kind', 'symbol', 'url', 'published_ms', 'retrieved_ms', 'page_sha256', 'effective_ms',
               'class', 'subclass'}
KINDS = ('listing', 'class-change', 'tradfi-launch')


class ClassifyError(ValueError):
    pass


def _need(ok, msg):
    if not ok:
        raise ClassifyError(msg)


def _int(x):
    return type(x) is int


def _sha(x):
    return isinstance(x, str) and len(x) == 64 and all(c in '0123456789abcdef' for c in x)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- input validation

def validate_snapshot(s: dict) -> None:
    _need(isinstance(s, dict) and set(s) == SNAP_KEYS, f'snapshot: keys must be exactly {sorted(SNAP_KEYS)}')
    _need(s['format'] == SNAPSHOT_FORMAT, f'snapshot: format must be {SNAPSHOT_FORMAT}')
    _need(isinstance(s['snapshot_id'], str) and s['snapshot_id'], 'snapshot: snapshot_id must be a non-empty string')
    _need(s['retrieval_url'] == EXCHANGEINFO_URL, f'snapshot: retrieval_url must be {EXCHANGEINFO_URL}')
    _need(_int(s['retrieved_ms']) and s['retrieved_ms'] > 0, 'snapshot: retrieved_ms must be a positive int')
    _need(_sha(s['response_sha256']), 'snapshot: response_sha256 must be a lowercase sha256 hex')
    _need(isinstance(s['scope'], str) and s['scope'], 'snapshot: scope must be a non-empty string')
    _need(s['response_server_time_ms'] is None or _int(s['response_server_time_ms']),
          'snapshot: response_server_time_ms must be null or an int (the body serverTime, kept for audit)')
    _need(isinstance(s['symbols'], list), 'snapshot: symbols must be a list')
    seen = set()
    for e in s['symbols']:
        _need(isinstance(e, dict) and set(e) == SNAP_SYMBOL_KEYS,
              f'snapshot {s["snapshot_id"]}: symbol entry keys must be exactly {sorted(SNAP_SYMBOL_KEYS)}')
        _need(isinstance(e['symbol'], str) and e['symbol'] and e['symbol'] not in seen,
              f'snapshot {s["snapshot_id"]}: symbol {e.get("symbol")!r} missing or duplicated')
        seen.add(e['symbol'])
        for k in ('pair', 'baseAsset', 'quoteAsset', 'contractType', 'underlyingType'):
            _need(isinstance(e[k], str), f'{e["symbol"]}: {k} must be a string')
        _need(isinstance(e['underlyingSubType'], list) and all(isinstance(x, str) for x in e['underlyingSubType']),
              f'{e["symbol"]}: underlyingSubType must be a list of strings')
        _need(_int(e['onboardDate']), f'{e["symbol"]}: onboardDate must be an int')


def validate_announcements(a: dict) -> None:
    _need(isinstance(a, dict) and set(a) == ANN_KEYS, f'announcements: keys must be exactly {sorted(ANN_KEYS)}')
    _need(a['format'] == ANNOUNCEMENTS_FORMAT, f'announcements: format must be {ANNOUNCEMENTS_FORMAT}')
    _need(isinstance(a['records'], list), 'announcements: records must be a list')
    ids = set()
    for r in a['records']:
        _need(isinstance(r, dict) and set(r) == RECORD_KEYS, f'record: keys must be exactly {sorted(RECORD_KEYS)}')
        rid = r['record_id']
        _need(isinstance(rid, str) and rid and rid not in ids, f'record id {rid!r} missing or duplicated')
        ids.add(rid)
        _need(r['kind'] in KINDS, f'{rid}: kind must be one of {KINDS}')
        _need(isinstance(r['url'], str) and r['url'].startswith('https://www.binance.com/'),
              f'{rid}: url must be a https://www.binance.com/ announcement URL')
        _need(_int(r['published_ms']) and _int(r['retrieved_ms']) and r['published_ms'] <= r['retrieved_ms'],
              f'{rid}: published_ms <= retrieved_ms (ints) required')
        _need(_sha(r['page_sha256']), f'{rid}: page_sha256 must be a lowercase sha256 hex')
        _need(_int(r['effective_ms']), f'{rid}: effective_ms must be an int')
        if r['kind'] == 'tradfi-launch':
            _need(r['symbol'] is None and r['class'] is None and r['subclass'] is None,
                  f'{rid}: a tradfi-launch record carries no symbol/class/subclass')
            continue
        _need(isinstance(r['symbol'], str) and r['symbol'], f'{rid}: symbol must be a non-empty string')
        if r['kind'] == 'class-change':
            _need(r['class'] in CLASSES, f'{rid}: a class-change must assert a class in {CLASSES}')
        else:
            _need(r['class'] is None or r['class'] in CLASSES, f'{rid}: class must be null or one of {CLASSES}')
        _need(r['subclass'] is None or (isinstance(r['subclass'], str) and r['subclass']),
              f'{rid}: subclass must be null or a non-empty string')
        _need(r['class'] is not None or r['subclass'] is None, f'{rid}: a subclass needs a class')


# ---------------------------------------------------------------- exchangeInfo -> class

def derive(e: dict) -> tuple[str, str]:
    """(class, subclass) from one exchangeInfo entry, or (UNKNOWN, reason). ('abstain', reason) = no class claim."""
    ut, ct, base, subs = e['underlyingType'], e['contractType'], e['baseAsset'], set(e['underlyingSubType'])
    if e['quoteAsset'] != 'USDT':
        return UNKNOWN, f'quote-asset:{e["quoteAsset"]}'
    if e['pair'] != e['symbol'] or base + e['quoteAsset'] != e['symbol']:
        return UNKNOWN, 'identity-mismatch'                 # symbol is not base+quote: ambiguous identity (ruling 2)
    if ut in ('COIN', 'INDEX'):
        if ct != 'PERPETUAL':
            return UNKNOWN, f'contract-type:{ut}/{ct}'
        if ut == 'INDEX':
            return ('crypto-index', 'index') if 'Crypto' in subs else ('abstain', 'index-not-tagged-crypto')
        if 'RWA' in subs:
            if base in TOKENIZED_GOLD_BASES:
                return 'tokenized-gold', TOKENIZED_GOLD_BASES[base]
            return 'abstain', f'rwa-base-unmapped:{base}'
        return 'crypto', 'coin'
    if ct != 'TRADIFI_PERPETUAL':
        return UNKNOWN, f'contract-type:{ut}/{ct}'
    if ut == 'COMMODITY':
        return ('commodity', COMMODITY_SUBCLASS[base]) if base in COMMODITY_SUBCLASS else \
            ('abstain', f'commodity-base-unmapped:{base}')
    return UNKNOWN, f'underlying-type-unmapped:{ut}'


# ---------------------------------------------------------------- archive evidence

def archive_spans(manifest: dict) -> dict[str, list[tuple[int, int]]]:
    """symbol -> contiguous [first_open, last_open] runs of its 1d klines (split where the gap >= RELIST_GAP_MS)."""
    files: dict[str, list[tuple[int, int]]] = {}
    for f in manifest['files']:
        if f.get('series', 'klines') == 'klines' and f['interval'] == '1d':
            files.setdefault(f['symbol'], []).append((f['first_open_ms'], f['last_open_ms']))
    out = {}
    for s, fs in files.items():
        fs.sort()
        runs = [list(fs[0])]
        for a, b in fs[1:]:
            if a - runs[-1][1] >= RELIST_GAP_MS:
                runs.append([a, b])
            else:
                runs[-1][1] = max(runs[-1][1], b)
        out[s] = [tuple(r) for r in runs]
    return out


# ---------------------------------------------------------------- builder

def _row(sym, cls, sub, a, b, observed, sources, basis):
    prov = None if cls in (UNKNOWN, OUT_OF_SCOPE) else ('contemporaneous' if observed <= a else 'retrospective')
    return {'symbol': sym, 'class': cls, 'subclass': sub, 'effective_from_ms': a, 'effective_to_ms': b,
            'class_first_observed_ms': observed, 'provenance': prov, 'sources': sorted(set(sources)), 'basis': basis}


def _unknown(sym, a, b, sources, reason):
    return _row(sym, UNKNOWN, UNKNOWN, a, b, None, sources, f'UNKNOWN:{reason}')


def _combine(doc_claims, x=None):
    """Announcement claims [(class, subclass|None)] + one exchangeInfo result -> (class, subclass), None = disagree.

    """
    classes = {c for c, _ in doc_claims} | ({x[0]} if x else set())
    if len(classes) != 1:
        return None
    cls = classes.pop()
    subs = {s for _, s in doc_claims if s is not None} | ({x[1]} if x else set())
    if len(subs) > 1:
        return None
    return cls, (subs.pop() if subs else 'unspecified')


def _segment(sym, a, b, docs, snaps, kind):
    """Rows for documented segment [a, b) (kind 'listing' or 'change').

    docs: the segment's announcement records; snaps: [(retrieved_ms, sid, derived)] sorted by retrieval."""
    rows = []
    doc_claims = [(r['class'], r['subclass']) for r in docs if r['class'] is not None]
    doc_ids = [f'ann:{r["record_id"]}' for r in docs]
    doc_seen = min((r['published_ms'] for r in docs if r['class'] is not None), default=None)
    abstains = [f'{sid}={d[1]}' for _, sid, d in snaps if d[0] == 'abstain']
    groups = []                                             # consecutive runs of identical derived results
    for t, sid, d in snaps:
        if d[0] == 'abstain':
            continue
        if groups and groups[-1]['d'] == d:
            groups[-1]['ids'].append(sid)
            groups[-1]['last'] = t
        else:
            groups.append({'d': d, 'ids': [sid], 'first': t, 'last': t})
    all_ids = doc_ids + [sid for _, sid, _ in snaps]
    head = 'identity-fact-at-listing' if kind == 'listing' else 'class-change'
    if doc_claims and _combine(doc_claims) is None:
        return [_unknown(sym, a, b, all_ids, 'announcements-disagree')]
    if not groups:
        if not doc_claims:
            return [_unknown(sym, a, b, all_ids, 'no-class-source' + (f'({";".join(abstains)})' if abstains else ''))]
        cls, sub = _combine(doc_claims)
        return [_row(sym, cls, sub, a, b, doc_seen, doc_ids, f'{head}:announcement')]
    g0 = groups[0]
    if g0['d'][0] == UNKNOWN:
        return [_unknown(sym, a, b, all_ids, g0['d'][1])]
    if doc_claims:
        merged = _combine(doc_claims, g0['d'])
        if merged is None:
            return [_unknown(sym, a, b, all_ids, 'announcement-vs-exchangeinfo')]
        rows.append(_row(sym, *merged, a, None, min(doc_seen, g0['first']), doc_ids + g0['ids'],
                         f'{head}:exchangeinfo+announcement'))
    else:
        if kind == 'change':                                # unreachable: change segments always carry a claim
            raise ClassifyError(f'{sym}: class-change segment without an asserted class')
        rows.append(_row(sym, *_combine([], g0['d']), a, None, g0['first'], g0['ids'], f'{head}:exchangeinfo'))
    prev = g0
    for g in groups[1:]:
        # undocumented change between two snapshots (mutable contract): the gap is UNKNOWN and the new class dates only
        # from the first snapshot that shows it; a contemporaneous class-change record is what resolves it
        gap_from = max(prev['last'] + 1, a)
        rows[-1]['effective_to_ms'] = gap_from
        gf = max(g['first'], gap_from)
        if gf > gap_from:
            rows.append(_unknown(sym, gap_from, gf, prev['ids'] + g['ids'], 'undocumented-class-change'))
        if g['d'][0] == UNKNOWN:
            rows.append(_unknown(sym, gf, None, g['ids'], g['d'][1]))
        else:
            rows.append(_row(sym, *_combine([], g['d']), gf, None, g['first'], g['ids'],
                             'observed:exchangeinfo-snapshot'))
        prev = g
    rows[-1]['effective_to_ms'] = b
    return rows


def _pre_rows(sym, start, end, runs, cutoff, mid):
    """Rows for an UNDOCUMENTED contract (no listing source at all): the pre-TradFi crypto rule, else UNKNOWN.

    The rule covers only the contiguous archived run that STARTS before the TradFi launch (a later relisting after a
    gap >= RELIST_GAP_MS is a new contract and is UNKNOWN), and never past `end` (a documented class change)."""
    rows = []
    if cutoff is not None and start < cutoff['ms']:
        run_end = next(r[1] + DAY for r in runs if r[0] <= start <= r[1])
        rule_end = run_end if end is None else min(run_end, end)
        rows.append(_row(sym, 'crypto', 'pre-tradfi-cutoff', start, rule_end, cutoff['published_ms'],
                         [f'rule:tradfi-cutoff:{cutoff["record_id"]}', mid], 'rule:pre-tradfi-cutoff'))
        if end is None or rule_end < end:
            rows.append(_unknown(sym, rule_end, end, [mid], 'archive-beyond-pre-tradfi-run'))
        return rows
    return [_unknown(sym, start, end, [mid], 'no-documented-listing')]


def scope_of(sym, entries) -> tuple[str, str | None]:
    """(scope, deferral reason). Routing reads only contractType / RWA-gold identity; it researches no class."""
    if sym in GOLD_PILOT:
        return SCOPE_GOLD, None
    if entries and all(e['contractType'] == 'TRADIFI_PERPETUAL' for e in entries):
        return SCOPE_DEFERRED, 'tradfi-deferred'
    if entries and all(derive(e)[0] == 'tokenized-gold' for e in entries):
        return SCOPE_DEFERRED, 'tokenized-gold-not-in-pilot'
    return SCOPE_CRYPTO, None


def classify_symbol(sym, runs, snaps, records, cutoff, mid):
    snap_hits = sorted((s['retrieved_ms'], f'xinfo:{s["snapshot_id"]}', s['by'][sym]) for s in snaps
                       if sym in s['by'])
    scope, why = scope_of(sym, [e for _, _, e in snap_hits])
    if scope == SCOPE_DEFERRED:
        start = min([runs[0][0]] + [e['onboardDate'] for _, _, e in snap_hits])
        rows = [_row(sym, OUT_OF_SCOPE, 'DEFERRED', start, None, None, [sid for _, sid, _ in snap_hits] + [mid],
                     f'OUT_OF_SCOPE:{why}')]
    else:
        rows = _classify(sym, runs, snap_hits, records, cutoff, mid)
    for r in rows:
        if scope == SCOPE_GOLD and r['class'] not in (UNKNOWN, OUT_OF_SCOPE) and \
                (r['class'], r['subclass']) != GOLD_PILOT[sym]:
            r.update(_unknown(sym, r['effective_from_ms'], r['effective_to_ms'], r['sources'],
                              f'gold-pilot-identity-mismatch:{r["class"]}/{r["subclass"]}'))
        elif scope == SCOPE_CRYPTO and r['class'] not in CRYPTO_CLASSES + (UNKNOWN,):
            r.update(_row(sym, OUT_OF_SCOPE, 'DEFERRED', r['effective_from_ms'], r['effective_to_ms'], None,
                          r['sources'], f'OUT_OF_SCOPE:non-crypto-class:{r["class"]}/{r["subclass"]}'))
        r['scope'] = scope
    return rows


def _classify(sym, runs, snap_hits, records, cutoff, mid):
    first_open = runs[0][0]
    listings = [r for r in records if r['kind'] == 'listing']
    changes = sorted((r for r in records if r['kind'] == 'class-change'),
                     key=lambda r: (max(r['effective_ms'], r['published_ms']), r['record_id']))
    all_ids = [sid for _, sid, _ in snap_hits] + [f'ann:{r["record_id"]}' for r in records] + [mid]
    onboard = {e['onboardDate'] for _, _, e in snap_hits}
    listing_times = sorted(onboard | {r['effective_ms'] for r in listings})
    if listing_times and listing_times[-1] - listing_times[0] > LISTING_TOLERANCE_MS:
        return [_unknown(sym, min(first_open, listing_times[0]), None, all_ids, 'listing-time-disagreement')]
    listing = listing_times[0] if listing_times else None
    derived = [(t, sid, derive(e)) for t, sid, e in snap_hits]
    change_at = [max(r['effective_ms'], r['published_ms']) for r in changes]   # never before publication
    if listing is not None and any(t <= listing for t in change_at):
        return [_unknown(sym, min(first_open, listing), None, all_ids, 'class-change-before-listing')]
    rows = []
    if listing is None:
        end = change_at[0] if change_at else None
        rows += _pre_rows(sym, first_open, end, runs, cutoff, mid)
        bounds = change_at
        seg_docs = [[r] for r in changes]
    else:
        if first_open < listing - listing % DAY:
            # archived trading before the documented listing day: a relisted / migrated / rebranded contract whose
            # earlier identity no source documents (ruling 2: mutable contract -> UNKNOWN, no rule applied)
            rows.append(_unknown(sym, first_open, listing, all_ids, 'archive-precedes-documented-listing'))
        bounds = [listing] + change_at
        seg_docs = [listings] + [[r] for r in changes]
    for i, a in enumerate(bounds):
        b = bounds[i + 1] if i + 1 < len(bounds) else None
        if i == 0 and listing is not None:
            seg_snaps = [x for x in derived if b is None or x[0] < b]
        else:
            seg_snaps = [x for x in derived if x[0] >= a and (b is None or x[0] < b)]
        rows += _segment(sym, a, b, seg_docs[i], seg_snaps, 'listing' if i == 0 and listing is not None else 'change')
    merged = []
    for r in rows:                                          # fold adjacent identical known classes
        p = merged[-1] if merged else None
        if p and r['class'] != UNKNOWN and (p['class'], p['subclass']) == (r['class'], r['subclass']) \
                and p['effective_to_ms'] == r['effective_from_ms']:
            p['effective_to_ms'] = r['effective_to_ms']
            p['sources'] = sorted(set(p['sources']) | set(r['sources']))
            if r['basis'] not in p['basis'].split('|'):
                p['basis'] += '|' + r['basis']
        else:
            merged.append(r)
    return merged


def build(snapshots, announcements, manifest, *, classes_id, tradfi_cutoff=None, snapshot_file_sha=None,
          announcements_file_sha=None) -> dict:
    for s in snapshots:
        validate_snapshot(s)
    validate_announcements(announcements)
    M.validate(manifest)
    _need(manifest['loader_version'] == M.ARCHIVE_LOADER, f'the manifest must be a {M.ARCHIVE_LOADER} manifest')
    _need(isinstance(classes_id, str) and classes_id, 'classes_id must be a non-empty string')
    sids = [s['snapshot_id'] for s in snapshots]
    _need(len(sids) == len(set(sids)), 'snapshot ids must be unique')
    recs = announcements['records']
    by_id = {r['record_id']: r for r in recs}
    cutoff = None
    if tradfi_cutoff is not None:
        r = by_id.get(tradfi_cutoff)
        _need(r is not None and r['kind'] == 'tradfi-launch', f'--tradfi-cutoff {tradfi_cutoff!r}: no such tradfi-launch record')
        cutoff = {'record_id': r['record_id'], 'ms': r['effective_ms'], 'published_ms': r['published_ms']}
    snaps = [dict(s, by={e['symbol']: e for e in s['symbols']}) for s in snapshots]
    spans = archive_spans(manifest)
    _need(spans, 'the manifest has no 1d klines')
    mid = f'manifest:{manifest["manifest_id"]}'
    recs_by_sym: dict[str, list] = {}
    for r in recs:
        if r['symbol'] is not None:
            recs_by_sym.setdefault(r['symbol'], []).append(r)
    rows = []
    for sym in sorted(spans):
        rows += classify_symbol(sym, spans[sym], snaps, sorted(recs_by_sym.get(sym, []), key=lambda r: r['record_id']),
                                cutoff, mid)
    sources = {mid: {'kind': 'archive-manifest', 'manifest_id': manifest['manifest_id'], 'digest': manifest['digest']}}
    for i, s in enumerate(snapshots):
        sources[f'xinfo:{s["snapshot_id"]}'] = {
            'kind': 'exchangeinfo-snapshot', 'retrieval_url': s['retrieval_url'], 'retrieved_ms': s['retrieved_ms'],
            'response_sha256': s['response_sha256'],
            'file_sha256': snapshot_file_sha[i] if snapshot_file_sha else None}
    for r in recs:
        sources[f'ann:{r["record_id"]}'] = {'kind': f'announcement:{r["kind"]}', 'url': r['url'],
                                            'published_ms': r['published_ms'], 'retrieved_ms': r['retrieved_ms'],
                                            'page_sha256': r['page_sha256']}
    if cutoff:
        sources[f'rule:tradfi-cutoff:{cutoff["record_id"]}'] = {
            'kind': 'rule', 'rule': 'an undocumented contract whose contiguous archived run starts before the first '
                                    'TradFi perpetual launch is crypto for that run', 'cutoff_ms': cutoff['ms']}
    used = {x for r in rows for x in r['sources']}
    counts: dict[str, int] = {}
    for r in rows:
        k = f'{r["scope"]}:{r["class"]}'
        counts[k] = counts.get(k, 0) + 1
    out = {'format': FORMAT, 'classes_id': classes_id, 'builder': BUILDER_VERSION,
           'contract': 'docs/newcore/research/CLASSIFICATION_SOURCES.md',
           'classes': list(CLASSES) + [UNKNOWN, OUT_OF_SCOPE], 'mapping': {
               'commodity_subclass': COMMODITY_SUBCLASS, 'tokenized_gold_bases': TOKENIZED_GOLD_BASES,
               'gold_pilot': {k: list(v) for k, v in GOLD_PILOT.items()}, 'listing_tolerance_ms': LISTING_TOLERANCE_MS,
               'relist_gap_ms': RELIST_GAP_MS},
           'inputs': {'announcements_file_sha256': announcements_file_sha, 'records': len(recs),
                      'snapshots': sids, 'tradfi_cutoff_record': tradfi_cutoff},
           'sources': {k: v for k, v in sorted(sources.items()) if k in used},
           'symbols': len(spans), 'row_counts': dict(sorted(counts.items())), 'rows': rows}
    out['digest'] = digest_of(out)
    return out


def digest_of(c: dict) -> str:
    return hashlib.sha256(M.canonical({k: v for k, v in c.items() if k != 'digest'})).hexdigest()


def dumps(c: dict) -> bytes:
    return json.dumps(c, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False).encode('ascii') + b'\n'


def dumps_compact(doc: dict, list_key: str) -> bytes:
    """Deterministic small input file: header keys sorted, then one canonical JSON line per element of `list_key`."""
    head = {k: v for k, v in doc.items() if k != list_key}
    items = [M.canonical(x).decode('ascii') for x in doc[list_key]]
    body = json.dumps(head, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)[:-1]
    return (body + f',"{list_key}":[\n' + ',\n'.join(items) + '\n]}\n').encode('ascii')


# ---------------------------------------------------------------- downstream helpers

def resolve_at(c: dict, symbol: str, t_ms: int) -> dict | None:
    """The row in force for `symbol` at `t_ms`, or None (no row = not classified = not addressable). Membership of a
    book also needs the row's `scope`: gold-pilot rows never join the crypto books."""
    for r in c['rows']:
        if r['symbol'] == symbol and r['effective_from_ms'] <= t_ms and \
                (r['effective_to_ms'] is None or t_ms < r['effective_to_ms']):
            return r
    return None


def addressable(row: dict | None) -> bool:
    return row is not None and row['class'] not in (UNKNOWN, OUT_OF_SCOPE)


ACTIVE_SCOPES = (SCOPE_CRYPTO,)


def active(row: dict | None) -> bool:
    """May join the active R4 book (Codex #53 6095682220: top-40 Binance crypto only). Gold-pilot identities stay
    recorded but inactive and non-blocking until their own future prereg; deferred rows are never active."""
    return addressable(row) and row['scope'] in ACTIVE_SCOPES


# ---------------------------------------------------------------- snapshot extraction (no network)

def extract(raw: bytes, *, retrieved_ms: int, snapshot_id: str, manifest: dict | None = None) -> dict:
    """A minimal `zb-exchangeinfo-snapshot/1` from a saved raw exchangeInfo response body: only SNAP_SYMBOL_KEYS, and
    with `manifest` only the symbols that manifest archives (the raw body itself is bound by hash, not committed)."""
    body = json.loads(raw.decode('utf-8'), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    keep = None
    scope = 'every symbol in the response'
    if manifest is not None:
        M.validate(manifest)
        keep = {f['symbol'] for f in manifest['files']}
        scope = f'symbols of manifest {manifest["manifest_id"]} (digest {manifest["digest"]}) present in the response'
    syms = [{k: (e.get(k) if k != 'underlyingSubType' else list(e.get(k) or [])) for k in SNAP_SYMBOL_KEYS}
            for e in body['symbols'] if keep is None or e.get('symbol') in keep]
    s = {'format': SNAPSHOT_FORMAT, 'snapshot_id': snapshot_id, 'retrieval_url': EXCHANGEINFO_URL,
         'retrieved_ms': retrieved_ms, 'response_sha256': sha256_bytes(raw), 'scope': scope,
         'response_server_time_ms': body.get('serverTime'),
         'symbols': sorted(syms, key=lambda e: e['symbol'])}
    validate_snapshot(s)
    return s


def _read_json(path):
    with open(path, 'rb') as f:
        raw = f.read()
    return json.loads(raw.decode('utf-8'), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c))), \
        sha256_bytes(raw)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    x = sub.add_parser('extract')
    x.add_argument('raw')
    x.add_argument('--retrieved-ms', type=int, required=True)
    x.add_argument('--snapshot-id', required=True)
    x.add_argument('--manifest', help='keep only the symbols this manifest archives')
    x.add_argument('--out', required=True)
    b = sub.add_parser('build')
    b.add_argument('--out', required=True)
    v = sub.add_parser('verify')
    v.add_argument('file')
    for p in (b, v):
        p.add_argument('--exchange-info', action='append', required=True)
        p.add_argument('--announcements', required=True)
        p.add_argument('--manifest', required=True)
        p.add_argument('--tradfi-cutoff')
        p.add_argument('--classes-id', required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'extract':
            with open(a.raw, 'rb') as f:
                s = extract(f.read(), retrieved_ms=a.retrieved_ms, snapshot_id=a.snapshot_id,
                            manifest=M.load(a.manifest) if a.manifest else None)
            with open(a.out, 'wb') as f:
                f.write(dumps_compact(s, 'symbols'))
            print(s['response_sha256'])
            return 0
        snaps = [_read_json(p) for p in a.exchange_info]
        ann, ann_sha = _read_json(a.announcements)
        c = build([s for s, _ in snaps], ann, M.load(a.manifest), classes_id=a.classes_id,
                  tradfi_cutoff=a.tradfi_cutoff, snapshot_file_sha=[h for _, h in snaps], announcements_file_sha=ann_sha)
        data = dumps(c)
        if a.cmd == 'build':
            if os.path.exists(a.out):
                with open(a.out, 'rb') as f:
                    if f.read() != data:
                        raise ClassifyError(f'{a.out} exists with different content; classes are immutable, use a new id')
            else:
                with open(a.out, 'wb') as f:
                    f.write(data)
            print(c['digest'])
            return 0
        with open(a.file, 'rb') as f:
            ok = f.read() == data
        print('OK' if ok else f'MISMATCH: rebuilt digest {c["digest"]}')
        return 0 if ok else 1
    except (M.ManifestError, ClassifyError, ValueError, KeyError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
