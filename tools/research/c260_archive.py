"""Read-only fixed C260 development streams behind an issued authority.

Synthetic fixtures have a separate, permanently non-authorizing constructor.
The production constructor validates the context-owned durable capability
before inspecting artifacts, source paths or caches. No forward path exists.
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
from tools.research import c260_authority as AUTH
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


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


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


class _Archive:
    """Shared parser only; subclasses fix the separate authority boundaries."""

    def _initialize(self, *, root: Path, store: Path, pins: dict, raws: dict):
        self._ensure_authority()
        self.root = root.resolve()
        C.load_proposal_metadata()
        self.digests = {}
        artifacts = {}
        if set(pins) != {'manifest', 'pit', 'filters', 'coverage'} or set(raws) != set(pins):
            raise SourceIncomplete('EXACT_ARTIFACT_SET_REQUIRED')
        for name, pin in pins.items():
            if set(pin) != {'path', 'byte_sha256'}:
                raise SourceIncomplete('ARTIFACT_PIN_SHAPE')
            M.check_rel_path(pin['path'])
            raw = raws[name]
            if type(raw) is not bytes:
                raise SourceIncomplete('IMMUTABLE_AUTHENTICATED_ARTIFACT_BYTES_REQUIRED')
            digest = hashlib.sha256(raw).hexdigest()
            if digest != pin['byte_sha256']:
                raise SourceIncomplete(f'{name}: ARTIFACT_CHECKSUM')
            self.digests[name] = digest
            artifacts[name] = strict_json(gzip.decompress(raw) if pin['path'].endswith('.gz') else raw)
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
        self.filters = self._development_filters(artifacts['filters'], pins['pit'])
        self.store = store.resolve()
        self._cache = {}
        self._issued_streams = {}
        self.source_reads = []
        if type(self) is DevelopmentArchive:
            self.manifest, self.pit, self.digests = map(_freeze, (self.manifest, self.pit, self.digests))
            self.filters = A.FoldFilterAuthority(
                self.filters.fold_name, self.filters.authority_sha256, self.filters.snapshot_sha256,
                self.filters.captured_at, self.filters.eligible_symbols, _freeze(self.filters.rules))
            self._metadata_binding = (self.manifest, self.pit, self.filters, self.digests, self._cache)

    def _ensure_authority(self):
        if type(self) is DevelopmentArchive:
            AUTH.validate_issued(self._authority)
            cap = self._authority
            if (hasattr(self, 'start_ms') and (self.start_ms, self.end_ms) != (cap.start_ms, cap.end_ms)
                    or hasattr(self, 'root') and self.root != cap.repository_root
                    or hasattr(self, 'store') and self.store != cap.store_root):
                raise C.C260Locked('C260_ISSUED_SOURCE_SCOPE_CHANGED')
            if hasattr(self, '_metadata_binding') and any(
                    actual is not bound for actual, bound in zip(
                        (self.manifest, self.pit, self.filters, self.digests, self._cache), self._metadata_binding)):
                raise C.C260Locked('C260_AUTHENTICATED_METADATA_REPLACED')
        elif type(self) is not SyntheticArchive:
            raise C.C260Locked('C260_FIXED_ARCHIVE_CONSTRUCTOR_REQUIRED')

    def _development_filters(self, authority, pit_pin):
        """Apply unchanged v3 policy to C260's fixed development start.

        v3 contains only its original WF/CORE20 metadata. Do not fabricate a
        development cell or use those CORE20 cells as the trading universe.
        The protected C260 source implementation binds this exact derivation.
        """
        if ((authority.get('authority'), authority.get('version'), authority.get('status'), authority.get('signed_by'))
                != ('STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY', 3, 'FROZEN', 'codex')):
            raise SourceIncomplete('FROZEN_V3_FILTER_SCHEMA_REQUIRED')
        A._validate_filter_authority_rebind_v3(authority, self.root)
        if (authority.get('pit_universe_path') != pit_pin['path']
                or authority.get('pit_universe_sha256') != self.digests['pit']
                or authority.get('pit_universe_digest') != self.pit['digest']):
            raise SourceIncomplete('FILTER_PIT_CROSSBINDING')
        captures, seen = [], set()
        for row in authority.get('snapshots', ()):
            capture, server = milliseconds(row.get('captured_at')), milliseconds(row.get('server_time'))
            if capture in seen:
                raise SourceIncomplete('DUPLICATE_FILTER_CAPTURE')
            seen.add(capture)
            if capture < self.start_ms and server < self.start_ms:
                captures.append((capture, server, row))
        if not captures:
            raise SourceIncomplete('NO_STRICTLY_PRE_DEVELOPMENT_FILTER_SNAPSHOT')
        _, server, chosen = max(captures, key=lambda item: item[0])
        # Every original evidence payload remains checksum-authenticated.
        # Parse rules only from the selected causal snapshot, never a future one.
        payload = None
        for row in authority['snapshots']:
            path = Path(M.contained(str(self.root), row['path']))
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row.get('sha256'):
                raise SourceIncomplete('FILTER_SNAPSHOT_CHECKSUM')
            if row is chosen:
                payload = strict_json(raw)
        if payload is None or type(payload.get('serverTime')) is not int or payload['serverTime'] != server:
            raise SourceIncomplete('FILTER_SERVER_TIME_BINDING')
        try:
            rules = A.F.rules_from_exchange_info(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceIncomplete('INVALID_EXCHANGE_FILTER_SNAPSHOT') from exc
        eligible, symbols = set(), set()
        for row in payload.get('symbols', ()):
            symbol = row.get('symbol')
            if type(symbol) is not str or not symbol or symbol in symbols:
                raise SourceIncomplete('DUPLICATE_OR_INVALID_FILTER_SYMBOL')
            symbols.add(symbol)
            if not (row.get('status') == 'TRADING' and row.get('contractType') == 'PERPETUAL'
                    and row.get('quoteAsset') == 'USDT' and row.get('underlyingType') == 'COIN'):
                continue
            filters = row.get('filters')
            types = [item.get('filterType') for item in filters if isinstance(item, dict)] if isinstance(filters, list) else []
            if any(types.count(kind) != 1 for kind in ('PRICE_FILTER', 'MARKET_LOT_SIZE', 'MIN_NOTIONAL')) or symbol not in rules:
                continue
            if any(not math.isfinite(float(rules[symbol].get(key, math.nan))) or float(rules[symbol][key]) <= 0
                   for key in ('tick', 'step', 'min_qty', 'min_notional', 'max_qty')):
                continue
            eligible.add(symbol)
        return A.FoldFilterAuthority('development', self.digests['filters'], chosen['sha256'],
                                     chosen['captured_at'], frozenset(eligible),
                                     {symbol: dict(rules[symbol]) for symbol in eligible})

    def eligible(self, decision_ms: int) -> tuple[str, ...]:
        self._ensure_authority()
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
        self._ensure_authority()
        if type(self) is DevelopmentArchive and (
                (start, end) != (self._authority.start_ms, self._authority.end_ms)
                or record not in self.manifest['files']):
            raise C.C260Locked('C260_FIXED_DEVELOPMENT_SOURCE_RECORD_REQUIRED')
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
        self._ensure_authority()
        key = symbol, series
        if key in self._cache:
            if type(self) is DevelopmentArchive and self._cache[key] is not self._issued_streams.get(key):
                raise C.C260Locked('C260_AUTHENTICATED_STREAM_REPLACED')
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
        self._issued_streams[key] = self._cache[key]
        return self._cache[key]

    def klines(self, symbol: str):
        return self._stream(symbol, 'klines')

    def marks(self, symbol: str):
        return self._stream(symbol, 'markPriceKlines')

    def funding(self, symbol: str):
        return self._stream(symbol, 'fundingRate')


class SyntheticArchive(_Archive):
    """Explicit fixture receipt; permanently synthetic and non-authorizing."""

    synthetic_only = True

    def __init__(self, root: Path):
        root = root.resolve()
        receipt = strict_json((root / 'c260.synthetic.json').read_bytes())
        if (set(receipt) != {'format', 'candidate_id', 'proposal_sha256', 'artifacts'}
                or receipt['format'] != 'zb-c260-SYNTHETIC-NON-EVIDENCE/1'
                or receipt['candidate_id'] != 260 or receipt['proposal_sha256'] != C.PREREG_SHA256
                or set(receipt['artifacts']) != {'manifest', 'pit', 'filters', 'coverage'}):
            raise SourceIncomplete('SYNTHETIC_FIXTURE_RECEIPT_REQUIRED')
        raws = {name: Path(M.contained(str(root), pin['path'])).read_bytes()
                for name, pin in receipt['artifacts'].items()}
        self._initialize(root=root, store=Path(M.contained(str(root), 'store')),
                         pins=receipt['artifacts'], raws=raws)


class DevelopmentArchive(_Archive):
    """Fixed real development parser; only an actual issued context can enter."""

    synthetic_only = False

    def __init__(self, authority):
        AUTH.validate_issued(authority)  # Before reading ANY object field/path.
        self._authority = authority
        expected = tuple(map(milliseconds, C.load_proposal_metadata()['windows_half_open_utc']['development']))
        if (authority.start_ms, authority.end_ms) != expected or authority.end_ms > SEALED_START:
            raise C.C260Locked('C260_EXACT_DEVELOPMENT_WINDOW_REQUIRED')
        pins, raws = {}, {}
        for name, path, digest, raw in authority.artifacts:
            if name in pins:
                raise SourceIncomplete('DUPLICATE_AUTHENTICATED_ARTIFACT')
            pins[name] = {'path': path, 'byte_sha256': digest}
            raws[name] = raw
        self._initialize(root=authority.repository_root, store=authority.store_root, pins=pins, raws=raws)
