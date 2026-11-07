"""Read-only C260 archive component. Only a synthetic fixture entry is installed.

The fixture receipt is deliberately not production authority and cannot mint
sealed evidence. The real CLI must refuse before reaching this module. Artifact
schemas are the existing manifest/v8 PIT/v3 filters, not a made-up price tape.
"""
from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
import urllib.parse

from tools.research import c260_low_idio_variance as C
from tools.research import manifest as M
from tools.research import strategy_causal_pack_01_archive as A
from tools.research import universe as U

H = C.HOUR_MS
SEALED_START = 1_790_553_600_000


class SourceIncomplete(RuntimeError):
    pass


def strict_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SourceIncomplete('DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(SourceIncomplete(value)))


def milliseconds(text: str) -> int:
    return A._utc_ms(text)


@dataclass(frozen=True)
class Kline:
    open_ms: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float

    def pack(self):
        return C.PACK.Bar(self.open_ms, self.open, self.high, self.low, self.close)


@dataclass(frozen=True)
class Funding:
    time_ms: int
    interval_hours: int | None
    rate: float


class SyntheticArchive:
    """Checksum-bound typed streams, always synthetic and non-authorizing.

    The root is an explicit test fixture directory. No real data root default,
    caller loader, evaluation seal, protected receipt, or identity override is
    offered. Only the frozen development window can be loaded in this slice.
    """

    synthetic_only = True

    def __init__(self, root: Path):
        self.root = root.resolve()
        receipt = strict_json((self.root / 'c260.synthetic.json').read_bytes())
        if (set(receipt) != {'format', 'candidate_id', 'proposal_sha256', 'artifacts'}
                or receipt['format'] != 'zb-c260-SYNTHETIC-NON-EVIDENCE/1'
                or receipt['candidate_id'] != 260 or receipt['proposal_sha256'] != C.PREREG_SHA256
                or set(receipt['artifacts']) != {'manifest', 'pit', 'filters', 'coverage'}):
            raise SourceIncomplete('SYNTHETIC_FIXTURE_RECEIPT_REQUIRED')
        C.load_proposal_metadata()
        self.digests = {}
        artifacts = {}
        for name, pin in receipt['artifacts'].items():
            if set(pin) != {'path', 'byte_sha256'}:
                raise SourceIncomplete('ARTIFACT_PIN_SHAPE')
            path = Path(M.contained(str(self.root), pin['path']))
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != pin['byte_sha256']:
                raise SourceIncomplete(f'{name}: ARTIFACT_CHECKSUM')
            self.digests[name] = digest
            artifacts[name] = strict_json(gzip.decompress(raw) if path.suffix == '.gz' else raw)
        self.manifest, self.pit = artifacts['manifest'], artifacts['pit']
        M.validate(self.manifest)
        if (self.manifest['loader_version'] != M.ARCHIVE_LOADER
                or self.pit.get('format') != U.FORMAT
                or self.pit.get('digest') != U.digest_of(self.pit)
                or self.pit.get('manifest_digest') != self.manifest['digest']):
            raise SourceIncomplete('MANIFEST_PIT_CONTENT_CROSSBINDING')
        weeks = self.pit.get('weeks')
        if (not isinstance(weeks, list) or not weeks
                or any(type(w.get('monday_ms')) is not int for w in weeks)
                or any(b['monday_ms'] <= a['monday_ms'] for a, b in zip(weeks, weeks[1:]))):
            raise SourceIncomplete('PIT_WEEK_ORDER')
        for week in weeks:
            members = week.get('books', {}).get('crypto', {}).get('members')
            if (not isinstance(members, list) or not members or len(members) != len(set(members))
                    or any(type(symbol) is not str or not symbol for symbol in members)):
                raise SourceIncomplete('PIT_FULL_MEMBER_SHAPE')
        self.start_ms, self.end_ms = map(milliseconds, C.load_proposal_metadata()[
            'windows_half_open_utc']['development'])
        coverage = artifacts['coverage']
        if (coverage.get('format') != 'zb-strategy-causal-pack-01-authority-coverage-preflight/1'
                or coverage.get('manifest_digest') != self.manifest['digest']
                or coverage.get('pit_universe_digest') != self.pit['digest']
                or coverage.get('digest') != M.digest_of(coverage)
                or not {('klines', '1h'), ('markPriceKlines', '1h'), ('fundingRate', None)}.issubset(
                    {(row.get('series'), row.get('interval')) for row in coverage.get('required_streams', [])})):
            raise SourceIncomplete('COVERAGE_CONTENT_CROSSBINDING')
        # Authenticate unchanged CORE20 authority cells as metadata. Its returned
        # eligibility/rules are applied to ALL PIT members; cells never select us.
        authority = artifacts['filters']
        if authority.get('version') != 3:
            raise SourceIncomplete('FROZEN_V3_FILTER_SCHEMA_REQUIRED')
        contract = {'data': {'exchange_filter_authority_sha256': self.digests['filters'],
                            'pit_universe_path': receipt['artifacts']['pit']['path'],
                            'pit_universe_sha256': self.digests['pit'],
                            'pit_universe_digest': self.pit['digest']},
                    'folds': [{'name': 'development', 'start': '2025-01-01T00:00:00Z',
                               'end': '2025-04-01T00:00:00Z'}]}
        self.filters = A.load_fold_filter_authorities(
            self.root / receipt['artifacts']['filters']['path'], self.digests['filters'], self.root, contract
        )['development']
        self.store = Path(M.contained(str(self.root), 'store'))
        self._cache = {}
        self.source_reads = []

    def eligible(self, decision_ms: int) -> tuple[str, ...]:
        if not self.start_ms <= decision_ms < self.end_ms:
            raise SourceIncomplete('DEVELOPMENT_ONLY_PIT_ASOF')
        week = max((w for w in self.pit['weeks'] if w['monday_ms'] <= decision_ms),
                   key=lambda w: w['monday_ms'], default=None)
        if week is None:
            raise SourceIncomplete('MISSING_CAUSAL_PIT')
        return tuple(s for s in week['books']['crypto']['members'] if s in self.filters.eligible_symbols)

    def _selected_rows(self, record: dict, start: int, end: int):
        """Hash raw bytes, validate timestamp envelope, clip before economics.

        A bounded September REST page is not rejected because its funding path
        has an October calendar end. Actual URL/page bounds and manifest times
        are checked instead. Nonselected OHLC/rate fields are never converted.
        """
        rel = record['path']
        meta = M.archive_meta(rel)
        path = M.contained(str(self.store), rel)
        if rel.endswith('.zip'):
            raw, _, data = M.read_archive(path, rel)
            if hashlib.sha256(raw).hexdigest() != record['sha256'] or len(raw) != record['bytes']:
                raise SourceIncomplete('SOURCE_CHECKSUM_BEFORE_PARSE')
            lines = data.splitlines()
            header = M.FUNDING_HEADER if meta['series'] == 'fundingRate' else M.KLINE_HEADER
            if lines and lines[0] == header:
                lines = lines[1:]
            try:
                times = [int(line.split(b',', 1)[0]) for line in lines]
            except ValueError as exc:
                raise SourceIncomplete('SOURCE_TIMESTAMP_NOT_INTEGER') from exc
            selected = b'\n'.join(line for line, t in zip(lines, times) if start <= t < end)
            rows = M.parse_archive_csv(rel, selected, meta)
        else:
            raw = Path(path).read_bytes()
            if hashlib.sha256(raw).hexdigest() != record['sha256'] or len(raw) != record['bytes']:
                raise SourceIncomplete('SOURCE_CHECKSUM_BEFORE_PARSE')
            sidecar = Path(path + '.ok').read_text(encoding='ascii').strip()
            provenance_raw = Path(M.contained(str(self.store), record['source']['provenance_path'])).read_bytes()
            provenance = strict_json(provenance_raw)
            source = record['source']
            if (sidecar != record['sha256'] or hashlib.sha256(provenance_raw).hexdigest() != source['provenance_sha256']
                    or provenance.get('url') != source['url']
                    or provenance.get('response_sha256') != record['sha256']
                    or not isinstance(provenance.get('accessed_cairo'), str)
                    or type(provenance.get('server_time_ms')) is not int):
                raise SourceIncomplete('API_CHECKSUM_PROVENANCE')
            payload = strict_json(raw)
            if not isinstance(payload, list):
                raise SourceIncomplete('API_ARRAY_REQUIRED')
            funding = meta['series'] == 'fundingRate'
            times = [row.get('fundingTime') if funding and isinstance(row, dict)
                     else row[0] if not funding and isinstance(row, list) and row else None for row in payload]
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(source['url']).query)
            query_start, query_end = int(query['startTime'][0]), int(query['endTime'][0])
            valid_url = M._funding_api_url(source['url'], meta) if funding else M._kline_api_url(source['url'], meta)
            if (not valid_url or query_end >= SEALED_START
                    or any(type(t) is not int or not query_start <= t <= query_end for t in times)):
                raise SourceIncomplete('API_ACTUAL_QUERY_PAGE_BOUNDS')
            first_key, last_key = ('first_funding_time_ms', 'last_funding_time_ms') if funding else ('first_open_ms', 'last_open_ms')
            if (provenance.get('rows') != len(payload)
                    or provenance.get(first_key) != (times[0] if times else None)
                    or provenance.get(last_key) != (times[-1] if times else None)
                    or provenance.get('terminal_page') != (len(payload) < (1000 if funding else 1500))):
                raise SourceIncomplete('API_PAGE_COUNT_BOUNDS')
            if not funding:
                expected = {'symbol': query['symbol'][0], 'interval': query['interval'][0],
                            'startTime': query_start, 'endTime': query_end, 'limit': 1500}
                if provenance.get('params') != expected or (times and times[0] != query_start):
                    raise SourceIncomplete('API_PARAMS_BINDING')
            selected = M.canonical([row for row, t in zip(payload, times) if start <= t < end])
            rows = M.parse_funding_api(rel, selected, meta) if funding else M.parse_kline_api(rel, selected, meta)
        if (hashlib.sha256(raw).hexdigest() != record['sha256'] or len(raw) != record['bytes']
                or len(times) != record['rows'] or (times[0] if times else None) != record['first_open_ms']
                or (times[-1] if times else None) != record['last_open_ms']
                or any(b <= a for a, b in zip(times, times[1:]))
                or any(not meta['month_start_ms'] <= t < meta['month_end_ms'] for t in times)):
            raise SourceIncomplete(f'{rel}: SOURCE_CHECKSUM_OR_TIMESTAMP_METADATA')
        self.source_reads.append({'path': rel, 'sha256': record['sha256'], 'start_ms': start, 'end_ms': end})
        return rows, rel.endswith('.zip')

    def _stream(self, symbol: str, series: str):
        key = symbol, series
        if key in self._cache:
            return self._cache[key]
        start, end = self.start_ms, self.end_ms
        if end > SEALED_START:
            raise SourceIncomplete('SEALED_BOUNDARY_BEFORE_SOURCE_READ')
        converted = {}
        for record in self.manifest['files']:
            if (record['symbol'] != symbol or record['series'] != series
                    or record['interval'] != (None if series == 'fundingRate' else '1h')
                    or not record['rows'] or record['last_open_ms'] < start or record['first_open_ms'] >= end):
                continue
            rows, zipped = self._selected_rows(record, start, end)
            for row in rows:
                if series == 'fundingRate':
                    t = int(row[0]) if zipped else row['fundingTime']
                    rate = float(row[2]) if zipped else float(row['fundingRate'])
                    interval = int(row[1]) if zipped else None
                    if (not math.isfinite(rate) or (interval is not None and (interval <= 0 or interval > 24))):
                        raise SourceIncomplete('FUNDING_RATE_CADENCE')
                    point = Funding(t, interval, rate)
                else:
                    t = int(row[0])
                    point = Kline(t, *(float(row[i]) for i in (1, 2, 3, 4, 7)))
                    point.pack().validate()
                    if not math.isfinite(point.quote_volume) or point.quote_volume < 0:
                        raise SourceIncomplete('NONFINITE_QUOTE_VOLUME')
                if t in converted:
                    raise SourceIncomplete('DUPLICATE_SOURCE_COORDINATE')
                converted[t] = point
        if not converted:
            raise SourceIncomplete(f'MISSING_REQUIRED_STREAM:{symbol}:{series}')
        self._cache[key] = MappingProxyType(dict(sorted(converted.items())))
        return self._cache[key]

    def klines(self, symbol: str):
        return self._stream(symbol, 'klines')

    def marks(self, symbol: str):
        return self._stream(symbol, 'markPriceKlines')

    def funding(self, symbol: str):
        return self._stream(symbol, 'fundingRate')
