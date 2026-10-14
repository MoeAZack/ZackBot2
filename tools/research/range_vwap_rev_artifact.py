"""The committed `zb-delist-notice/1` notice-register ARTIFACT of `range_vwap_rev` (REX-02; Codex ruling #81 6172019060
point 3). A JSON document that the runner's preflight validates and attests by its exact digest before any book pass.
This module performs NO network fetch: the artifact is built by Integration from official Binance announcement /
archive evidence; tests use synthetic fetch documents only.

Schema (top-level keys exactly `TOP_KEYS`):
  format          'zb-delist-notice/1' (== range_vwap_rev_notice.RULE_ID)
  schema          the pinned announcement-list response schema (range_vwap_rev_notice.SCHEMA)
  parser_sha      SHA-256 of range_vwap_rev_notice.py (the frozen inclusion rule that classified every item)
  frozen          the frozen symbol list the register was built over (the run symbols must be a subset)
  covers_from_ms  the declared start of announcement coverage
  fetches         the raw fetch documents in fetch order: {fetch_ts, ok, schema, raw_digest, items}; replayed through
                  range_vwap_rev_notice.Register (paging-to-overlap, first-seen releaseDate, append-only)
  notices         one row per included (symbol, announcement): symbol, published_at (the announcement releaseDate, the
                  known-at time), effective_delist_ms (the effective delist time stated by the announcement),
                  source_url, source_id (the announcement code), fetch_ts (first fetch that saw it), raw_digest
                  (SHA-256 of that fetch's raw response). Must equal the replayed register entries one-to-one.
  register_digest the replayed Register digest
  digest          SHA-256 of the canonical JSON of everything except `digest` (the artifact digest the run pins)

Coverage (never inferred from the data): proven announcement coverage is [max(covers_from_ms, oldest releaseDate any
successful fetch returned), last successful fetch], for the frozen symbols only. A missing / unreadable / digest-
mismatched artifact is a refusal (`validate`); for a valid one, a run symbol outside `frozen`, anything of the access
window outside the proven coverage, every register R_ext-GAP,
and every data / notice disagreement becomes a NoticeView GAP: the book halts `incomplete.notice_gap` from its start
(INCOMPLETE / NO-RESULT for the affected rows; no flat or forward fill). Disagreements:
  * klines of a noticed symbol at / after its effective delist time (the data contradicts the notice);
  * a symbol whose 4h klines end before the window end with no notice published by then (a delisting can never be
    inferred from the last candle)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import range_vwap_rev as RV                                                                 # noqa: E402
import range_vwap_rev_notice as NT                                                          # noqa: E402

FORMAT = NT.RULE_ID
TOP_KEYS = ('format', 'schema', 'parser_sha', 'frozen', 'covers_from_ms', 'fetches', 'notices', 'register_digest',
            'digest')
FETCH_KEYS = ('fetch_ts', 'ok', 'schema', 'raw_digest', 'items')
NOTICE_KEYS = ('symbol', 'published_at', 'effective_delist_ms', 'source_url', 'source_id', 'fetch_ts', 'raw_digest')
HEX64 = re.compile(r'^[0-9a-f]{64}$')


class ArtifactError(ValueError):
    pass


def _canon(o) -> bytes:
    return json.dumps(o, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest_of(doc: dict) -> str:
    return hashlib.sha256(_canon({k: v for k, v in doc.items() if k != 'digest'})).hexdigest()


def _int(x) -> bool:
    return type(x) is int


def build(frozen, fetches, effective: dict, *, covers_from_ms: int) -> dict:
    """An artifact from fetch documents + {announcement code: effective delist ms} (Integration / tests)."""
    reg = NT.Register(frozen)
    for f in fetches:
        reg.ingest(f)
    notices = [{'symbol': e['symbol'], 'published_at': e['releaseDate'], 'effective_delist_ms': effective[e['code']],
                'source_url': e['url'], 'source_id': e['code'], 'fetch_ts': e['first_fetch_ts'],
                'raw_digest': e['raw_digest']} for e in reg.entries]
    doc = {'format': FORMAT, 'schema': NT.SCHEMA, 'parser_sha': NT.parser_sha(), 'frozen': list(frozen),
           'covers_from_ms': covers_from_ms, 'fetches': [dict(f) for f in fetches], 'notices': notices,
           'register_digest': reg.digest()}
    doc['digest'] = digest_of(doc)
    return doc


class Validated:
    """A validated artifact: the replayed register, its notice rows and the proven coverage [cov_from, cov_to]."""

    def __init__(self, doc, reg, cov_from, cov_to):
        self.doc, self.register, self.digest = doc, reg, doc['digest']
        self.cov_from, self.cov_to = cov_from, cov_to
        self.notices = doc['notices']

    def view(self, tape: dict, symbols, lo: int, hi: int) -> RV.NoticeView:
        """The book's NoticeView over the access window [lo, hi]: the register releases plus every coverage hole,
        register GAP and data / notice disagreement as a GAP (fail closed: INCOMPLETE from its start)."""
        gaps = [tuple(g) for g in self.register.gaps]
        if self.cov_from is None or self.cov_from > lo:                 # coverage starts after the window start
            gaps.append((lo, hi + 1 if self.cov_from is None else min(self.cov_from, hi + 1)))
        if self.cov_to is None or self.cov_to < hi:                     # no successful fetch at / after the end
            gaps.append((lo if self.cov_to is None else max(self.cov_to, lo), hi + 1))
        frozen = set(self.doc['frozen'])
        for s in symbols:
            if s not in frozen:                                         # coverage never proven for this symbol
                gaps.append((lo, hi + 1))
                continue
            gaps += [(g, hi + 1) for g in disagreements(self.notices, s, tape[s], hi)]
        return RV.NoticeView(self.register.view().releases, tuple(sorted(set(gaps))))


def disagreements(notices, s: str, x: RV.Series, hi: int) -> list[int]:
    """The instants from which the data and the notices of `s` disagree (empty = consistent)."""
    out = []
    mine = [n for n in notices if n['symbol'] == s]
    for n in mine:
        late = [o for o in x.bars if o >= n['effective_delist_ms']]
        if late:
            out.append(min(n['published_at'], min(late)))
    end = None if x.last_ms is None else x.last_ms + RV.TF
    if end is not None and end < hi and not any(n['published_at'] <= end for n in mine):
        out.append(end)
    return out


def validate(doc, *, expect_digest) -> Validated:
    """Schema + digest + replay checks; raises ArtifactError (the run refuses). `expect_digest` is the digest the run
    command pinned before access (never taken from the artifact itself)."""
    def need(ok, msg):
        if not ok:
            raise ArtifactError(f'{FORMAT} artifact: {msg}')
    need(isinstance(doc, dict), 'missing')
    need(tuple(sorted(doc)) == tuple(sorted(TOP_KEYS)), f'keys must be exactly {TOP_KEYS}')
    need(doc['format'] == FORMAT and doc['schema'] == NT.SCHEMA, 'format / schema differ')
    need(doc['parser_sha'] == NT.parser_sha(), 'parser_sha differs from the frozen inclusion rule')
    need(doc['digest'] == digest_of(doc), 'digest does not match its content')
    need(isinstance(expect_digest, str) and HEX64.match(expect_digest) and doc['digest'] == expect_digest,
         'not the artifact the run command pinned (digest mismatch)')
    fr = doc['frozen']
    need(isinstance(fr, list) and fr and all(isinstance(s, str) and s for s in fr) and len(set(fr)) == len(fr),
         'frozen must be a non-empty list of distinct symbols')
    need(_int(doc['covers_from_ms']), 'covers_from_ms must be an int ms')
    fs = doc['fetches']
    need(isinstance(fs, list) and fs, 'no fetch documents')
    prev = None
    for f in fs:
        need(isinstance(f, dict) and tuple(sorted(f)) == tuple(sorted(FETCH_KEYS)), f'fetch keys must be {FETCH_KEYS}')
        need(_int(f['fetch_ts']) and (prev is None or f['fetch_ts'] > prev), 'fetch_ts must be increasing ints')
        need(isinstance(f['raw_digest'], str) and HEX64.match(f['raw_digest']), 'raw_digest must be 64-hex')
        need(isinstance(f['items'], list), 'items must be a list')
        prev = f['fetch_ts']
    reg = NT.Register(fr)
    try:
        for f in fs:
            reg.ingest(f)
    except (NT.NoticeError, KeyError, TypeError) as e:
        raise ArtifactError(f'{FORMAT} artifact: the fetch replay refused: {e}')
    need(reg.digest() == doc['register_digest'], 'register_digest differs from the replayed register')
    ns = doc['notices']
    need(isinstance(ns, list), 'notices must be a list')
    for n in ns:
        need(isinstance(n, dict) and tuple(sorted(n)) == tuple(sorted(NOTICE_KEYS)), f'notice keys must be {NOTICE_KEYS}')
        need(n['symbol'] in fr, f'{n["symbol"]!r} is not a frozen symbol')
        need(all(_int(n[k]) for k in ('published_at', 'effective_delist_ms', 'fetch_ts')), 'notice times must be int ms')
        need(n['effective_delist_ms'] >= n['published_at'], 'effective delist before its announcement')
        need(isinstance(n['source_url'], str) and n['source_url'].startswith('https://'), 'source_url must be https')
        need(isinstance(n['source_id'], str) and n['source_id'], 'source_id missing')
        need(isinstance(n['raw_digest'], str) and HEX64.match(n['raw_digest']), 'notice raw_digest must be 64-hex')
    key = lambda n: (n['symbol'], n['source_id'], n['published_at'], n['source_url'], n['fetch_ts'], n['raw_digest'])
    want = sorted((e['symbol'], e['code'], e['releaseDate'], e['url'], e['first_fetch_ts'], e['raw_digest'])
                  for e in reg.entries)
    need(sorted(map(key, ns)) == want, 'notices differ from the replayed register entries')
    proven = [it['releaseDate'] for f in fs if f['ok'] and f['schema'] == NT.SCHEMA for it in f['items']
              if _int(it.get('releaseDate'))]
    cov_from = max(doc['covers_from_ms'], min(proven)) if proven else None
    return Validated(doc, reg, cov_from, reg.last_ok)
