"""`zb-delist-notice/1`: the delist-notice register of `range_vwap_rev` (v5 section 5 + addendum P1, P2, P4; frozen by
Codex ruling 1, #13 6161875941). Pure functions over announcement-list fetch documents supplied by the caller: this
module performs NO network fetch (the R_hist build and the R_ext forward collector are REX-02, owned by Integration).

Inclusion rule (pre-registered; titles are NFKC + casefolded first, so `USDⓈ-M` == `USDS-M`):
  * the title must contain `delist` (any form); `close` / `settle` / `cease` / `terminate` alone never qualify (P4);
  * the title must name a USDⓈ-M perpetual (`usds-m perpetual`, or `futures` + `perpetual` + a `...USDT` symbol);
  * it must name a frozen-list symbol (token match); a relaunch / rename notice still counts for the old symbol;
  * excluded: delivery / quarterly, COIN-M, spot-only, margin, monitoring-tag and leverage / tier notices;
  * an included item without an integer API `releaseDate` refuses the build (binding refused); excluded matches (a
    frozen symbol named by a non-qualifying notice) are listed and counted for audit.
Register semantics:
  * the FIRST-SEEN `releaseDate` per announcement code is the known-at time, immutable; later date / title edits and
    deletions are counted, never applied (P2), and entries are only ever appended;
  * paging-to-overlap (P1): each fetch must overlap a code already recorded; a successful fetch without overlap flags an
    `R_ext-GAP` [previous successful fetch, this fetch); an endpoint failure or schema change is covered only when a
    later successful fetch overlaps, else it is a GAP to the final fetch. Any GAP makes eligibility UNDETERMINED from
    its start: the book row halts `incomplete.notice_gap` (INCOMPLETE / NO-RESULT; fail closed);
  * R_hist is bound by digest at registration; the R_ext digest must be bound in the revision binding before the runner
    computes any holdout outcome and before the reveal record, whose detail cites it."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import range_vwap_rev as RV                                                                 # noqa: E402

RULE_ID = 'zb-delist-notice/1'
SCHEMA = 'binance-cms-announcement-list/v1'          # the pinned response schema of the announcement-list endpoint
LAG_MS = 24 * RV.HOUR
EXCLUDE = ('delivery', 'quarterly', 'coin-m', 'margin', 'monitoring tag', 'leverage', 'tier')


class NoticeError(ValueError):
    pass


def parser_sha() -> str:
    with open(os.path.abspath(__file__), 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def norm(title: str) -> str:
    return unicodedata.normalize('NFKC', title).casefold()


def symbols_named(t: str, frozen) -> list[str]:
    return [s for s in frozen if re.search(r'(?<![a-z0-9])' + re.escape(s.casefold()) + r'(?![a-z0-9])', t)]


def classify(title: str, frozen) -> tuple[str, list[str]]:
    """('include' | 'excluded_match' | 'no_match', named frozen symbols)."""
    t = norm(title)
    syms = symbols_named(t, frozen)
    if not syms:
        return 'no_match', []
    perp = 'usds-m perpetual' in t or ('futures' in t and 'perpetual' in t and any(s.endswith('USDT') for s in syms))
    if 'delist' not in t or not perp or any(w in t for w in EXCLUDE if not (w == 'margin' and 'usds-m' in t)):
        return 'excluded_match', syms
    return 'include', syms


def _canon(o) -> bytes:
    return json.dumps(o, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


class Register:
    """The append-only register. `ingest(fetch)` takes one fetch document:
    {'fetch_ts': ms, 'ok': bool, 'schema': str, 'raw_digest': hex, 'items': [{code, title, releaseDate, url}]}."""

    def __init__(self, frozen, *, part='R_hist'):
        self.frozen = tuple(frozen)
        self.part = part
        self.entries = []                                  # included entries, append-only
        self.seen = {}                                     # code -> first-seen item (every item, included or not)
        self.excluded = []
        self.edits = 0
        self.deletions = 0
        self.gaps = []
        self.last_ok = None
        self.pending_fail = None
        self.bound_at = None                               # ms of the registration binding (post-binding counts)
        self.final = None

    def ingest(self, f: dict) -> None:
        if self.final is not None:
            raise NoticeError('register finalised: append-only, no ingest after the final fetch')
        ts = f['fetch_ts']
        if not f.get('ok') or f.get('schema') != SCHEMA:
            if self.pending_fail is None:
                self.pending_fail = self.last_ok if self.last_ok is not None else ts
            return
        items = f.get('items') or []
        codes = {it['code'] for it in items}
        if self.seen and not (codes & set(self.seen)):
            self.gaps.append((self.pending_fail if self.pending_fail is not None else self.last_ok, ts))
        prev_codes = set(self.seen)
        for it in items:
            c = it['code']
            if c in self.seen:
                old = self.seen[c]
                if old['releaseDate'] != it.get('releaseDate') or old['title'] != it['title']:
                    self.edits += 1
                continue
            verdict, syms = classify(it['title'], self.frozen)
            rd = it.get('releaseDate')
            if verdict == 'include' and type(rd) is not int:
                raise NoticeError(f'{c}: included notice without an integer releaseDate (binding refused)')
            self.seen[c] = {'title': it['title'], 'releaseDate': rd}
            if verdict == 'include':
                for s in syms:
                    self.entries.append({'symbol': s, 'code': c, 'url': it.get('url'), 'releaseDate': rd,
                                         'first_fetch_ts': ts, 'raw_digest': f['raw_digest'], 'part': self.part})
            elif verdict == 'excluded_match':
                self.excluded.append({'code': c, 'symbols': syms, 'title': it['title']})
        self.deletions += len(prev_codes - codes - {c for c in prev_codes if self._older_than_page(c, items)})
        self.last_ok, self.pending_fail = ts, None

    def _older_than_page(self, code, items) -> bool:
        """A code older than the page's oldest releaseDate simply fell off the page (not a deletion)."""
        rds = [it.get('releaseDate') for it in items if type(it.get('releaseDate')) is int]
        rd = self.seen[code]['releaseDate']
        return not rds or type(rd) is not int or rd < min(rds)

    def bind_registration(self, at_ms: int) -> str:
        self.bound_at = at_ms
        self.part = 'R_ext'
        return self.digest()

    def finalize(self, end_ms: int) -> str:
        if self.pending_fail is not None:
            self.gaps.append((self.pending_fail, end_ms))
            self.pending_fail = None
        self.final = end_ms
        return self.digest()

    def doc(self) -> dict:
        return {'rule_id': RULE_ID, 'schema': SCHEMA, 'entries': self.entries, 'gaps': self.gaps,
                'excluded_count': len(self.excluded), 'edits': self.edits, 'deletions': self.deletions,
                'final': self.final}

    def digest(self) -> str:
        return hashlib.sha256(_canon(self.doc())).hexdigest()

    def report(self) -> dict:
        post = [e for e in self.entries if self.bound_at is not None and e['first_fetch_ts'] > self.bound_at]
        return {'entries': len(self.entries), 'post_binding': len(post),
                'lag_gt_24h': sum(e['first_fetch_ts'] - e['releaseDate'] > LAG_MS for e in self.entries),
                'excluded_matches': len(self.excluded), 'edits': self.edits, 'deletions': self.deletions,
                'gaps': len(self.gaps)}

    def view(self) -> RV.NoticeView:
        rel = {}
        for e in self.entries:
            rel[e['symbol']] = min(rel.get(e['symbol'], e['releaseDate']), e['releaseDate'])
        return RV.NoticeView(rel, tuple(self.gaps))


def require_binding(binding: dict, register: Register, *, stage: str) -> None:
    """The runner (stage 'runner') and the reveal record (stage 'reveal') refuse unless the immutable revision binding
    carries the R_hist digest and the FINAL R_ext digest of exactly this register."""
    if stage not in ('runner', 'reveal'):
        raise NoticeError("stage must be 'runner' or 'reveal'")
    if register.final is None:
        raise NoticeError(f'{stage}: R_ext has no final fetch yet (bind it before any holdout outcome)')
    if not binding.get('r_hist_digest'):
        raise NoticeError(f'{stage}: R_hist digest not bound at registration')
    if binding.get('r_ext_digest') != register.digest():
        raise NoticeError(f'{stage}: R_ext digest not bound (or not this register) before the {stage}')
    if binding.get('parser_sha') != parser_sha() or binding.get('rule_id') != RULE_ID:
        raise NoticeError(f'{stage}: parser_sha / rule_id not bound')
