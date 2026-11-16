"""Acquire verified Binance public mark-price candles; never apply a funding proxy."""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re
import time
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile
from zoneinfo import ZoneInfo

try:
    from tools.research.lane2_funding_history import Cache
except ModuleNotFoundError:
    from lane2_funding_history import Cache

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "research_cache/lane2/mark_archives"
WINDOWS = ROOT / "research_cache/lane2/funding_windows.json"
FUNDING = ROOT / "research_cache/lane2/native_funding/symbols"
BASE = "https://data.binance.vision/data/futures/um/monthly/markPriceKlines"
LABEL = "CANDLE_PROXY_SOURCE_ONLY"


def cairo_now():
    return datetime.now(timezone.utc).astimezone(ZoneInfo("Africa/Cairo")).isoformat()


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_checksum(archive, checksum):
    # Binance publishes sha256 followed by the exact file name.
    fields = checksum.strip().split()
    if len(fields) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
        raise ValueError("invalid checksum format")
    if fields[1].lstrip("*") != archive.name:
        raise ValueError("checksum filename mismatch")
    actual = digest(archive)
    if actual != fields[0].lower():
        raise ValueError("archive checksum mismatch")
    return actual


def request(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "ZackBot-research/1.0"}), timeout=40) as r:
                return r.read()
        except HTTPError as exc:
            if exc.code not in (500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(1 + attempt)


def acquire(symbol, month, cache=CACHE):
    if not re.fullmatch(r"[A-Z0-9]+", symbol) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
        raise ValueError("invalid archive key")
    name = f"{symbol}-1m-{month}.zip"
    url = f"{BASE}/{symbol}/1m/{name}"
    folder = cache / symbol
    folder.mkdir(parents=True, exist_ok=True)
    archive, checksum_path = folder / name, folder / (name + ".CHECKSUM")
    # A pre-existing zip is never trusted without the independently retrieved checksum.
    if not checksum_path.exists():
        payload = request(url + ".CHECKSUM")
        tmp_checksum = checksum_path.with_suffix(".CHECKSUM.part")
        tmp_checksum.write_bytes(payload)
        tmp_checksum.replace(checksum_path)
    if not archive.exists():
        payload = request(url)
        tmp = archive.with_suffix(".zip.part")
        tmp.write_bytes(payload)
        tmp.replace(archive)
    checksum = checksum_path.read_text(encoding="utf-8")
    sha = verify_checksum(archive, checksum)
    metadata = {"source_url": url, "checksum_url": url + ".CHECKSUM", "source_sha256": sha,
                "verified_at_cairo": cairo_now()}
    (folder / (name + ".provenance.json")).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return archive, metadata


def extract(archive, symbol, event_times, metadata):
    targets = {}
    for event in event_times:
        targets.setdefault(event - event % 60000, []).append(event)
    found = []
    with ZipFile(archive) as z:
        files = [n for n in z.namelist() if n.endswith(".csv") and not n.endswith("/")]
        if len(files) != 1:
            raise ValueError("archive must contain exactly one CSV")
        with z.open(files[0]) as raw:
            for row in csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")):
                if not row or not row[0].isdigit():
                    continue
                opened = int(row[0])
                if opened not in targets:
                    continue
                if len(row) < 7:
                    raise ValueError("short candle row")
                close = int(row[6])
                try:
                    value, high, low, closing = map(Decimal, (row[1], row[2], row[3], row[4]))
                    if not all(x.is_finite() and x > 0 for x in (value, high, low, closing)) or not (low <= value <= high and low <= closing <= high):
                        raise ValueError("invalid candle price bounds")
                except InvalidOperation as e:
                    raise ValueError("invalid candle price") from e
                if close != opened + 59999:
                    raise ValueError("invalid candle interval")
                for event in targets.pop(opened):
                    if not opened <= event <= close:
                        raise ValueError("event outside containing candle")
                    found.append({"symbol": symbol, "funding_time_ms": event, "value": row[1],
                                  "low": row[3], "high": row[2], "close": row[4], "candle_open_ms": opened,
                                  "candle_close_ms": close, "timestamp_gap_ms": event - opened,
                                  "source_kind": LABEL, "source_type": "BINANCE_MARK_ARCHIVE",
                                  "containing_candle": True, **metadata})
                if not targets:
                    break
    missing = [event for events in targets.values() for event in events]
    return found, missing


def needed_events(windows, funding_dir):
    grouped = {}
    for path in sorted(funding_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        symbol = data.get("symbol")
        if symbol != path.stem or symbol not in windows or data.get("status") != "PAGINATED_REST_RANGE_COMPLETE":
            continue
        intervals = windows[symbol]
        for event in data.get("events", []):
            mark = event.get("markPrice")
            if mark not in (None, ""):
                continue
            t = int(event["fundingTime"])
            if any(int(start) - 1 <= t <= int(end) + 14400000 for start, end in intervals):
                month = datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m")
                grouped.setdefault((symbol, month), set()).add(t)
    return grouped


def rest_source(cache, symbol, event):
    """Retrieve the exact minute, then nearest mark minute within eight hours."""
    opened = event - event % 60000
    def query(start, end, limit):
        rows, meta = cache.get("markPriceKlines", {"symbol": symbol, "interval": "1m",
                                    "startTime": start, "endTime": end, "limit": limit})
        if not isinstance(rows, list) or len(rows) > limit:
            raise ValueError("invalid REST candle response")
        checked = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                raise ValueError("invalid REST candle row")
            t, close = int(row[0]), int(row[6])
            if not start <= t <= end or t % 60000 or close != t + 59999:
                raise ValueError("invalid REST candle interval")
            try:
                op, high, low, cl = map(Decimal, (row[1], row[2], row[3], row[4]))
                if not all(x.is_finite() and x > 0 for x in (op, high, low, cl)) or not (low <= op <= high and low <= cl <= high):
                    raise ValueError("invalid REST candle price bounds")
            except InvalidOperation as e:
                raise ValueError("invalid REST candle price") from e
            checked.append(row)
        return checked, meta

    rows, meta = query(opened, opened, 1)
    if not rows:
        rows, meta = query(max(0, opened - 8 * 3600000), opened + 8 * 3600000, 1000)
    if not rows:
        return None
    row = min(rows, key=lambda r: (abs(int(r[0]) - event), int(r[0])))
    start, end = int(row[0]), int(row[6])
    return {"symbol": symbol, "funding_time_ms": event, "value": str(row[1]),
            "high": str(row[2]), "low": str(row[3]), "close": str(row[4]),
            "candle_open_ms": start, "candle_close_ms": end,
            "timestamp_gap_ms": event - start, "containing_candle": start <= event <= end,
            "source_kind": LABEL, "source_type": "BINANCE_MARK_REST",
            "source_url": meta["url"], "source_sha256": meta["sha256"],
            "fetched_at_cairo": meta["fetched_cairo"]}


def run(windows=WINDOWS, funding_dir=FUNDING, cache=CACHE, delay=1.0, jobs=4):
    if not 1 <= jobs <= 4:
        raise ValueError("jobs must be between 1 and 4")
    groups = needed_events(json.loads(windows.read_text(encoding="utf-8")), funding_dir)
    rest_cache = Cache(cache.parent / "mark_rest/raw", spacing=2.0)
    records, gaps = [], []
    stopped = False
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / "mark_sources.json"

    def save(incomplete):
        output = {"classification": LABEL, "generated_at_cairo": cairo_now(),
                  "stopped_rate_limit": stopped, "incomplete": incomplete,
                  "records": records, "gaps": gaps}
        tmp = dest.with_suffix(".json.part")
        tmp.write_text(json.dumps(output, indent=2), encoding="utf-8")
        tmp.replace(dest)
        return output

    stop_event = threading.Event()
    local = threading.local()

    def work(key, events):
        symbol, month = key
        previous = getattr(local, "last", None)
        if previous is not None and delay:
            time.sleep(max(0, delay - (time.monotonic() - previous)))
        if stop_event.is_set():
            return None
        local.last = time.monotonic()
        try:
            archive, meta = acquire(symbol, month, cache)
            rows, missing = extract(archive, symbol, events, meta)
            return rows, missing, None
        except HTTPError as exc:
            if exc.code in (418, 429):
                stop_event.set()
            if exc.code == 404:
                return [], sorted(events), "ARCHIVE_OR_CHECKSUM_HTTP_404"
            return [], [], f"HTTP_{exc.code}"
        except Exception as exc:
            return [], [], f"ARCHIVE_ERROR_{type(exc).__name__}: {exc}"

    save(True)
    pending_groups = iter(sorted(groups.items()))
    pending = {}
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        def submit_next():
            if stop_event.is_set():
                return
            try:
                key, events = next(pending_groups)
            except StopIteration:
                return
            pending[pool.submit(work, key, events)] = key

        for _ in range(jobs):
            submit_next()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                symbol, month = pending.pop(future)
                result = future.result()
                if result is None:
                    continue
                rows, missing, error = result
                records.extend(rows)
                if error:
                    gaps.append({"symbol": symbol, "month": month, "reason": error})
                    if error in ("HTTP_418", "HTTP_429"):
                        stopped = True
                        stop_event.set()
                if not stop_event.is_set():
                    try:
                        for t in missing:
                            row = rest_source(rest_cache, symbol, t)
                            if row is None:
                                gaps.append({"symbol": symbol, "funding_time_ms": t, "reason": "CANDLE_ABSENT"})
                            else:
                                records.append(row)
                    except HTTPError as exc:
                        if exc.code in (418, 429):
                            stopped = True
                            stop_event.set()
                        gaps.append({"symbol": symbol, "month": month, "reason": f"REST_HTTP_{exc.code}"})
                    except Exception as exc:
                        gaps.append({"symbol": symbol, "month": month, "reason": f"REST_ERROR_{type(exc).__name__}: {exc}"})
                save(True)
                submit_next()
            if stop_event.is_set():
                for future in pending:
                    future.cancel()
                # Running workers may complete; do not issue any more requests.
                pending = {f: key for f, key in pending.items() if not f.cancelled()}
    unresolved = any(g["reason"] != "ARCHIVE_OR_CHECKSUM_HTTP_404" for g in gaps)
    return save(stopped or unresolved)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", required=True, help="process currently completed funding files once")
    parser.add_argument("--jobs", type=int, default=4, choices=range(1, 5))
    args = parser.parse_args()
    result = run(jobs=args.jobs)
    print(f"{len(result['records'])} source records; {len(result['gaps'])} gaps; rate-limit stop={result['stopped_rate_limit']}")


if __name__ == "__main__":
    main()
