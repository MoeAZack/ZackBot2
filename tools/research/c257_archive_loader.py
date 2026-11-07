"""Checksum-bound, local-only authority-v6 to C257 raw evidence bridge.

Construction opens metadata only. Fold data is opened lazily, after the protected
adapter reserves the single-use identity. No holdout loader exists.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from tools.research import authority_v6 as V
from tools.research import c257_successor_guard as G
from tools.research import funding_shock_reversal_xsec_c257_evaluator as E
from tools.research import funding_shock_reversal_xsec_c257_stagea as S
from tools.research import manifest as M


class ArchiveLoaderError(S.IncompleteAuthority):
    pass


def _path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ArchiveLoaderError("authority path must be relative")
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        raise ArchiveLoaderError("authority path escapes its bound store")
    return path


def _number(value: object) -> Decimal:
    result = Decimal(value.decode("ascii") if isinstance(value, bytes) else str(value))
    if not result.is_finite():
        raise ArchiveLoaderError("non-finite archive number")
    return result


class ProtectedArchiveLoader:
    def __init__(self, *, root: Path, store: Path, pins: Mapping):
        self.root, self.store = root, store
        documents, hashes = {}, {}
        for name in G.OUTPUT_NAMES:
            pin = pins[name]
            if pin["path"] != G.OUTPUT_PATHS[name]:
                raise ArchiveLoaderError("authority output path changed")
            raw = _path(root, pin["path"]).read_bytes()
            hashes[name] = hashlib.sha256(raw).hexdigest()
            if hashes[name] != pin["byte_sha256"]:
                raise ArchiveLoaderError("authority output byte checksum changed")
            documents[name] = G._parse_output(name, raw)
            if documents[name]["digest"] != pin["content_digest"]:
                raise ArchiveLoaderError("authority output content checksum changed")
        G._verify_output_structure(documents, hashes)
        self.manifest = documents["manifest_v6"]
        self.pit = documents["pit_v9"]
        self.filters = documents["filter_v4"]
        self._development_passed = False

    def permit_forward_after_development(self) -> None:
        self._development_passed = True

    def _source_rows(self, record: Mapping, start: int, end: int, *, new: bool):
        """Hash whole source bytes; interpret economic columns only inside window.

        Monthly files can straddle a sealed boundary. Rows beyond it remain
        opaque; even schema checking is restricted to admitted timestamp rows.
        """
        rel = record["path"]
        path = _path(self.store, rel)
        meta = M.archive_meta(rel)
        if new and (meta["series"], meta["interval"], meta["symbol"]) != (
                record["series"], record["interval"], record["symbol"]):
            raise ArchiveLoaderError("record coordinates differ from archive path")
        if not new and any(key in record and record[key] != meta[key]
                           for key in ("series","interval","symbol")):
            raise ArchiveLoaderError("base record coordinates differ from archive path")
        raw = path.read_bytes()
        expected = record["raw_sha256"] if new else record["sha256"]
        expected_size = record["raw_bytes"] if new else record["bytes"]
        if hashlib.sha256(raw).hexdigest() != expected or len(raw) != expected_size:
            raise ArchiveLoaderError("archive byte checksum/size differs from pinned manifest")
        source = record["source"]
        if source.get("archive_checksum") != expected:
            raise ArchiveLoaderError("source checksum differs from manifest")
        if not rel.endswith(".zip"):
            # The only non-ZIP source inside these four folds is base REST.
            # Exact pinned page observations and query bounds decide access,
            # rather than the calendar month encoded by a funding path.
            last = record.get("last_open_ms")
            query = V.parse_qs_strict(source.get("url", ""))
            query_end = int(query.get("endTime", -1))
            if (type(last) is not int or last >= end or query_end < last
                    or record.get("first_open_ms", end) > last):
                raise ArchiveLoaderError("REST page observations cross the sealed fold boundary")
            if new:
                raise ArchiveLoaderError("v6 residual API cannot enter pre-holdout folds")
            if source["kind"] == M.KLINE_API_KIND:
                verified, rows = M.read_kline_api(str(path), rel, source)
            elif source["kind"] == M.FUNDING_API_KIND:
                verified, rows = M.read_funding_api(str(path), rel, source)
            else:
                raise ArchiveLoaderError("unsupported pinned source")
            if verified != raw:
                raise ArchiveLoaderError("archive changed during read")
            times = [int(row["fundingTime"] if isinstance(row, Mapping) else row[0]) for row in rows]
            if (len(times) != record["rows"] or not times or min(times) != record["first_open_ms"]
                    or max(times) != last or any(t >= end for t in times)):
                raise ArchiveLoaderError("REST page rows differ from exact manifest bounds")
            return [row for row in rows if start <= int(
                row["fundingTime"] if isinstance(row, Mapping) else row[0]) < end]
        if path.with_name(path.name+".ok").read_text(encoding="ascii").strip() != expected:
            raise ArchiveLoaderError("archive published checksum mismatch")
        if new:
            checksum = path.with_name(path.name+".CHECKSUM").read_bytes()
            if (hashlib.sha256(checksum).hexdigest() != source["checksum_raw_sha256"]
                    or V.parse_official_checksum(checksum, path.name) != expected):
                raise ArchiveLoaderError("official archive checksum bytes changed")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            name = path.name[:-4]+".csv"
            if archive.namelist() != [name]:
                raise ArchiveLoaderError("unexpected archive members")
            selected = []
            previous = -1
            with archive.open(name) as stream:
                for line in stream:
                    line = line.rstrip(b"\r\n")
                    if line in (M.KLINE_HEADER, M.FUNDING_HEADER):
                        continue
                    timestamp = int(line.split(b",", 1)[0])
                    if timestamp <= previous:
                        raise ArchiveLoaderError("archive timestamps are duplicate/reversed")
                    previous = timestamp
                    if timestamp >= end:
                        break  # never interpret economic values past the boundary
                    if timestamp >= start:
                        selected.append(line)
            rows = M.parse_archive_csv(rel, b"\n".join(selected), meta) if selected else []
        if new:
            normalized = V._semantic_rows(meta, rows)
            for row, semantic in zip(rows, normalized):
                if record["row_digests"].get(str(int(row[0]))) != V.sha256(V.canonical(semantic)):
                    raise ArchiveLoaderError("archive row digest differs from pinned authority")
        return rows

    def _fold_filters(self, fold: str, start: int):
        key = "development_eligible_segment" if fold == "development" else fold
        capture = self.filters["fold_bindings"][key]
        snapshots = self.filters["snapshots"]
        descriptors = [S.FilterSnapshot(S._iso_ms(row["captured_at"]),
                         S._iso_ms(row["server_time"]), row["sha256"]) for row in snapshots]
        selected = S.freeze_filter_snapshot(descriptors, start)
        matching = [row for row in snapshots if row["captured_at"] == capture
                    and row["sha256"] == selected.digest]
        if len(matching) != 1:
            raise ArchiveLoaderError("fold binding is not the latest strictly prior snapshot")
        descriptor = matching[0]
        raw = _path(self.root, descriptor["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != selected.digest:
            raise ArchiveLoaderError("frozen filter payload checksum changed")
        payload = G._strict_json(raw, "filter snapshot")
        if payload.get("serverTime") != selected.server_time_ms:
            raise ArchiveLoaderError("snapshot server time differs from descriptor")
        eligible = V._snapshot_eligible(payload)
        out = {}
        for row in payload["symbols"]:
            symbol = row["symbol"]
            if symbol not in eligible:
                continue
            if symbol in out:
                raise ArchiveLoaderError("duplicate filter symbol")
            raw_filters = row["filters"]
            indexed = {item["filterType"]: item for item in raw_filters}
            if len(indexed) != len(raw_filters):
                raise ArchiveLoaderError("duplicate exchange filter")
            lot, notional = indexed["MARKET_LOT_SIZE"], indexed["MIN_NOTIONAL"]
            result = S.ExactFilters(_number(indexed["PRICE_FILTER"]["tickSize"]),
                _number(lot["stepSize"]), _number(lot["minQty"]), _number(lot["maxQty"]),
                _number(notional.get("notional", notional.get("minNotional"))))
            result.validate()
            out[symbol] = result
        return out

    def load_fold(self, fold: str, initial_equity: Decimal) -> E.SealedEvidence:
        if fold not in E.FOLD_NAMES or fold != "development" and not self._development_passed:
            raise ArchiveLoaderError("forward or holdout data is sealed")
        # Several fixed capital books share the same raw fold, but never outcomes.
        start, end, _ = S.frozen_splits()[fold]
        if fold == "development":
            start = S._iso_ms(S.load_frozen_contract()["walk_forward"][
                "development_filter_eligible_segment"].split("/")[0])
        filters = self._fold_filters(fold, start)
        cells = V._weekly_membership_cells(self.pit, frozenset(filters), start, end)
        key = "development_eligible_segment" if fold == "development" else fold
        if (cells != self.filters["weekly_membership"][key]
                or V.sha256(V.canonical(cells)) != self.filters["weekly_membership_sha256"][key]):
            raise ArchiveLoaderError("derived CORE-20 differs from bound membership")
        symbols = {symbol for cell in cells for symbol in cell["members"]}
        read_symbols = symbols | {"BTCUSDT"}
        warm = start-32*8*S.HOUR_MS
        tables: dict[tuple[str, str], dict[int, object]] = {}
        for new, records in ((False, self.manifest["active_base_files_v5"]),
                             (True, self.manifest["files_v6"])):
            for record in records:
                meta = M.archive_meta(record["path"])
                stream, symbol = meta["series"], meta["symbol"]
                if symbol not in read_symbols or (stream, meta["interval"]) not in (
                        ("klines", "1h"), ("markPriceKlines", "1h"), ("fundingRate", None)):
                    continue
                if symbol not in symbols and stream != "klines":
                    continue
                if meta["month_start_ms"] >= end or meta["month_end_ms"] <= warm:
                    continue
                target = tables.setdefault((symbol, stream), {})
                for row in self._source_rows(record, warm, end, new=new):
                    timestamp = int(row["fundingTime"] if isinstance(row, Mapping) else row[0])
                    if timestamp in target:
                        raise ArchiveLoaderError("duplicate source timestamp in active authority")
                    target[timestamp] = row
        bars, marks, funding, funding_rates = {}, {}, {}, {}
        for symbol in read_symbols:
            bars[symbol] = tuple(S.HourBar(t, *(_number(row[i]) for i in (1,2,3,4,7)))
                for t, row in sorted(tables.get((symbol, "klines"), {}).items()))
            marks[symbol] = {t: _number(row[4]) for t, row in
                             tables.get((symbol, "markPriceKlines"), {}).items()}
            chain = []
            funding_rates[symbol] = {}
            for t, row in sorted(tables.get((symbol, "fundingRate"), {}).items()):
                # The first warm row establishes cadence, never a signal.
                if isinstance(row, Mapping):
                    rate = _number(row["fundingRate"])
                    interval = (t-chain[-1].nominal_ms)//S.HOUR_MS if chain else 0
                else:
                    interval, rate = int(row[1]), _number(row[2])
                chain.append(S.FundingRow(symbol,t,t+S.FUNDING_DELAY_MS,float(rate),interval))
                funding_rates[symbol][t] = rate
            funding[symbol] = chain
        cohorts = []
        funding_indices = {symbol:{row.nominal_ms:index for index,row in enumerate(chain)}
                           for symbol,chain in funding.items()}
        times = sorted({row.nominal_ms for chain in funding.values() for row in chain
                        if start <= row.nominal_ms < end and row.interval_hours in S.ALLOWED_INTERVAL_HOURS})
        for timestamp in times:
            active = max((cell for cell in cells if S._iso_ms(cell["monday_utc"]) <= timestamp),
                         key=lambda cell: cell["monday_utc"])
            if not any(timestamp in funding_indices[symbol] for symbol in active["members"]):
                continue
            if timestamp+10*S.HOUR_MS >= end:
                cohorts.append(E.RawCohortEvidence(f"{fold}:{timestamp}",timestamp,{},"FOLD_FLAT_BOUNDARY"))
                continue
            evidence = {}
            vetoes = {}
            for symbol in active["members"]:
                chain = funding[symbol]
                index = funding_indices[symbol].get(timestamp)
                current = chain[index] if index is not None else None
                prior = chain[max(0,index-31):index] if index is not None else []
                if current is None or len(prior) != 31:
                    vetoes[symbol] = "NO_CURRENT_SETTLEMENT" if current is None else "INSUFFICIENT_PRIOR_FUNDING"
                    continue
                try:
                    S.robust_funding_surprise(current, prior, decision_ms=timestamp+S.FUNDING_DELAY_MS)
                except S.IncompleteAuthority as exc:
                    vetoes[symbol] = str(exc)
                    continue  # the immutable symbol veto, before tail pairing
                causal_bars = tuple(bar for bar in bars[symbol]
                    if timestamp-24*S.HOUR_MS <= bar.open_ms <= timestamp+10*S.HOUR_MS)
                evidence[symbol] = E.RawSymbolEvidence(current,tuple(prior),causal_bars,bars[symbol],
                    filters[symbol],tuple(E.FundingPoint(symbol,row.nominal_ms,row.available_ms,
                        funding_rates[symbol][row.nominal_ms]) for row in chain
                        if timestamp <= row.nominal_ms < timestamp+10*S.HOUR_MS),
                    tuple(E.MarkClose(symbol,t,price) for t,price in marks[symbol].items()
                          if timestamp <= t < timestamp+10*S.HOUR_MS),
                    timestamp,timestamp+10*S.HOUR_MS)
            cohorts.append(E.RawCohortEvidence(f"{fold}:{timestamp}",timestamp,evidence,symbol_vetoes=vetoes))
        # Portfolio marks use each verified last-completed mark's causal boundary.
        # Evaluator trims these to actual exposure windows after deriving entries.
        portfolio = tuple(E.MarkClose(symbol,t+S.HOUR_MS,price) for symbol in sorted(symbols)
                          for t,price in sorted(marks[symbol].items()) if start <= t+S.HOUR_MS < end)
        coverage = {"effective_start_ms":start,"end_exclusive_ms":end,
            "scheduled_event_hours":tuple(cohort.nominal_ms for cohort in cohorts),
            "pre_authority_ineligible_calendar_hours":(start-S.frozen_splits()[fold][0])//S.HOUR_MS,
            "pre_authority_event_count":None,
            "pre_authority_policy":"FILTER_AUTHORITY_UNAVAILABLE; no pre-authority market rows read"}
        return E.SealedEvidence(fold,initial_equity,tuple(cohorts),portfolio,
                                tuple(bar for bar in bars["BTCUSDT"] if bar.open_ms>=start-S.HOUR_MS),coverage)
