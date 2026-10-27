"""EDGE-04 exact Binance UM 1m coverage inventory and resumable archive acquisition.

Inventory reads manifest metadata only.  Acquisition is deliberately bounded to the frozen
development interval and the CORE-10-ELIGIBLE-9 symbols; it can never request the sealed holdout.
Downloaded bytes live in an explicitly supplied external ``binance_um`` store.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manifest as M  # noqa: E402

FORMAT = "zb-edge04-1m-attestation/1"
OVERLAY_FORMAT = "zb-edge04-1m-overlay-manifest/1"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "ZECUSDT", "SOLUSDT", "XRPUSDT",
           "NEARUSDT", "DOGEUSDT", "UNIUSDT", "SUIUSDT")
START = "2021-01-04T00:00:00Z"
END = "2026-09-28T00:00:00Z"
HOLDOUT = "2026-11-02T00:00:00Z"
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
HOST_PREFIX = BASE + "/"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MINUTE = 60_000


class AcquireError(ValueError):
    pass


def _ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _month_bounds(period: str) -> tuple[int, int]:
    y, m = map(int, period.split("-"))
    a = datetime(y, m, 1, tzinfo=timezone.utc)
    b = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc)
    return int(a.timestamp() * 1000), int(b.timestamp() * 1000)


def periods(start: str = START, end: str = END) -> tuple[str, ...]:
    a, b = _ms(start), _ms(end)
    if not a < b <= _ms(HOLDOUT):
        raise AcquireError("request must be non-empty and end on/before the sealed development cutoff")
    out, cur = [], datetime.fromtimestamp(a / 1000, tz=timezone.utc).replace(day=1)
    while int(cur.timestamp() * 1000) < b:
        out.append(cur.strftime("%Y-%m"))
        cur = datetime(cur.year + (cur.month == 12), cur.month % 12 + 1, 1, tzinfo=timezone.utc)
    return tuple(out)


def relpath(symbol: str, period: str) -> str:
    if symbol not in SYMBOLS or period not in periods():
        raise AcquireError("coordinate lies outside frozen EDGE-04 development scope")
    return f"um/monthly/klines/{symbol}/1m/{symbol}-1m-{period}.zip"


def url(symbol: str, period: str) -> str:
    return f"{BASE}/{symbol}/1m/{symbol}-1m-{period}.zip"


def inventory(doc: dict) -> dict:
    """Conservative metadata-only audit. The first archive month may start at listing;
    every later month must contain every minute. No zip/candle content is opened here.
    """
    M.validate(doc)
    if doc["loader_version"] != M.ARCHIVE_LOADER or doc["data_root"] != "binance_um":
        raise AcquireError("EDGE-04 requires the attested Binance archive manifest")
    wanted = set(periods())
    rows = [f for f in doc["files"] if f.get("series") == "klines" and f.get("interval") == "1m"
            and f.get("symbol") in SYMBOLS and os.path.basename(f["path"])[-11:-4] in wanted]
    by = {s: {} for s in SYMBOLS}
    for f in rows:
        p = os.path.basename(f["path"])[-11:-4]
        if p in by[f["symbol"]]:
            raise AcquireError(f"duplicate manifest month {f['symbol']} {p}")
        by[f["symbol"]][p] = f
    report = []
    for symbol in SYMBOLS:
        have = by[symbol]
        first = min(have) if have else None
        missing, deficits = [], []
        for p in periods():
            # Pre-listing months are not fabricated into gaps. Listing starts at the first
            # official archive present in the attested manifest.
            if first is None or p < first:
                continue
            f = have.get(p)
            if f is None:
                missing.append(p)
                continue
            lo, hi = _month_bounds(p)
            expected = (hi - (max(lo, f["first_open_ms"]) if p == first else lo)) // MINUTE
            if f["last_open_ms"] >= hi or f["first_open_ms"] < lo:
                deficits.append({"period": p, "reason": "time_outside_month"})
            elif f["rows"] != expected:
                contiguous = f["rows"] == (f["last_open_ms"] - f["first_open_ms"]) // MINUTE + 1
                gaps = []
                if contiguous:
                    a = lo
                    while a < f["first_open_ms"]:
                        gaps.append(datetime.fromtimestamp(a / 1000, tz=timezone.utc).strftime("%Y-%m-%d")); a += 86_400_000
                    a = f["last_open_ms"] + MINUTE
                    while a < hi:
                        gaps.append(datetime.fromtimestamp(a / 1000, tz=timezone.utc).strftime("%Y-%m-%d")); a += 86_400_000
                deficits.append({"period": p, "expected_rows": expected, "actual_rows": f["rows"],
                                 "missing_rows": expected - f["rows"], "contiguous_present_span": contiguous,
                                 "missing_utc_days": gaps})
        report.append({"symbol": symbol, "listing_month": first, "archive_months": len(have),
                       "bytes": sum(f["bytes"] for f in have.values()),
                       "rows": sum(f["rows"] for f in have.values()),
                       "missing_archives": missing, "row_deficits": deficits,
                       "complete": bool(have) and not missing and not deficits})
    result = {"format": FORMAT, "source_manifest_id": doc["manifest_id"],
              "source_manifest_digest": doc["digest"], "window": {"start": START, "end": END},
              "symbols": report, "complete": all(x["complete"] for x in report),
              "method": "manifest metadata only; no archive or candle rows opened"}
    result["digest"] = hashlib.sha256(M.canonical(result)).hexdigest()
    return result


def plan(doc: dict) -> list[dict]:
    """Plan absent monthly archives plus official daily overlays for bounded row deficits."""
    inv = inventory(doc)
    req = []
    for s in inv["symbols"]:
        for p in s["missing_archives"]:
            req.append({"kind": "monthly", "symbol": s["symbol"], "period": p, "url": url(s["symbol"], p),
                        "checksum_url": url(s["symbol"], p) + ".CHECKSUM",
                        "path": relpath(s["symbol"], p)})
        for deficit in s["row_deficits"]:
            days = deficit.get("missing_utc_days", [])
            if not deficit.get("contiguous_present_span"):
                lo, hi = _month_bounds(deficit["period"])
                days = [datetime.fromtimestamp(x / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                        for x in range(lo, hi, 86_400_000)]
            for day in days:
                base = f"https://data.binance.vision/data/futures/um/daily/klines/{s['symbol']}/1m"
                name = f"{s['symbol']}-1m-{day}.zip"
                req.append({"kind": "daily_overlay", "symbol": s["symbol"], "period": day,
                            "url": f"{base}/{name}", "checksum_url": f"{base}/{name}.CHECKSUM",
                            "path": f"edge04_daily/klines/{s['symbol']}/1m/{name}"})
    return sorted(req, key=lambda x: x["path"])


def _open(url_: str, headers: dict | None = None):
    if not url_.startswith("https://data.binance.vision/data/futures/um/") or ".." in url_:
        raise AcquireError("refusing non-official archive URL")
    return urllib.request.urlopen(urllib.request.Request(url_, headers=headers or {}), timeout=90)


def _checksum(body: bytes, filename: str) -> str:
    p = body.decode("ascii", "strict").split()
    if not p or not HEX64.match(p[0].lower()) or (len(p) > 1 and p[1].lstrip("*") != filename):
        raise AcquireError("invalid official checksum")
    return p[0].lower()


def fetch_one(req: dict, store: str, *, opener=_open, chunk_size: int = 1024 * 1024) -> dict:
    """Resume a partial zip, verify the official checksum, validate all 1m rows, then atomically import it."""
    target = M.contained(store, req["path"])
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        with contextlib.closing(opener(req["checksum_url"])) as r:
            published = _checksum(r.read(), os.path.basename(target))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"path": req["path"], "status": "archive_missing"}
        raise
    part = target + ".part"
    offset = os.path.getsize(part) if os.path.exists(part) else 0
    existing = None
    if offset:
        with open(part, "rb") as f:
            existing = hashlib.sha256(f.read()).hexdigest()
    if existing != published:  # a fully downloaded .part survives a crash without an invalid EOF range request
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        with contextlib.closing(opener(req["url"], headers)) as r:
            status = getattr(r, "status", 200)
            mode = "ab" if offset and status == 206 else "wb"
            with open(part, mode) as f:
                while True:
                    chunk = r.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    f.flush()
                    os.fsync(f.fileno())
    with open(part, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    if actual != published:
        # A bad resumed prefix cannot repair itself on another Range append. Remove only
        # this uncommitted partial so the next run restarts the same immutable object.
        os.remove(part)
        raise AcquireError(f"checksum mismatch for {req['path']}")
    # scan_archive checks member identity, schema, closed-bar close time, order and duplicates.
    # Its archive contract requires the verified checksum sidecar, so stage that beside the
    # partial object before validation; neither staged file is visible as an imported zip.
    with open(part + ".ok", "w", encoding="ascii", newline="\n") as f:
        f.write(published + "\n")
    if req.get("kind") == "daily_overlay":
        _scan_daily(part, req)
    else:
        M.scan_archive(part, req["path"])
    os.replace(part, target)
    os.replace(part + ".ok", target + ".ok")
    return {"path": req["path"], "status": "verified", "sha256": published,
            "bytes": os.path.getsize(target)}


def _scan_daily(path: str, req: dict) -> None:
    """Validate an exact official daily overlay, including a complete 1,440-row closed-bar day."""
    member = f"{req['symbol']}-1m-{req['period']}.csv"
    with zipfile.ZipFile(path) as z:
        if z.namelist() != [member]:
            raise AcquireError(f"daily archive must contain exactly {member}")
        raw = z.read(member)
    day = _ms(req["period"] + "T00:00:00Z")
    meta = {"series": "klines", "interval": "1m", "month_start_ms": day, "month_end_ms": day + 86_400_000}
    rows = M.parse_archive_csv(req["path"], raw, meta)
    opens = [int(r[0]) for r in rows]
    if len(opens) != 1440 or opens != list(range(day, day + 86_400_000, MINUTE)):
        raise AcquireError(f"{req['period']} daily overlay is not closed-bar complete")


def acquire(reqs: list[dict], store: str, *, opener=_open, sleep=time.sleep, clock=time.monotonic,
            min_gap_s: float = 1.0) -> list[dict]:
    if min_gap_s < 1.0:
        raise AcquireError("official archive requests must be paced at least one second apart")
    out, last = [], None
    def paced(url_, headers=None):
        nonlocal last
        if last is not None:
            wait = min_gap_s - (clock() - last)
            if wait > 0:
                sleep(wait)
        last = clock()
        return opener(url_, headers)
    for req in reqs:
        try:
            out.append(fetch_one(req, store, opener=paced))
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                out.append({"path": req["path"], "status": "stopped_rate_limit", "http": e.code})
                break
            raise
    return out


def apply_acquisition_attestation(inv: dict, reqs: list[dict], acquisitions: list[dict]) -> dict:
    """Bind verified imported archives to the metadata inventory without hiding source gaps."""
    status = {x["path"]: x for x in acquisitions}
    monthly_ok = all(status.get(r["path"], {}).get("status") == "verified"
                     for r in reqs if r["kind"] == "monthly")
    overlay_ok = all(status.get(r["path"], {}).get("status") == "verified"
                     for r in reqs if r["kind"] == "daily_overlay")
    unresolved_shape = any(d.get("missing_rows") and not d.get("missing_utc_days")
                           for s in inv["symbols"] for d in s["row_deficits"])
    inv["acquisition"] = acquisitions
    inv["effective_coverage"] = {
        "complete": monthly_ok and overlay_ok and not unresolved_shape,
        "monthly_source_gaps_preserved": sum(len(s["row_deficits"]) for s in inv["symbols"]),
        "daily_overlays_verified": sum(status.get(r["path"], {}).get("status") == "verified"
                                       for r in reqs if r["kind"] == "daily_overlay"),
        "rule": "daily overlays supplement named source-gap days; monthly archives are never rewritten",
    }
    return inv


def build_manifest(store: str, out: str, accessed_cairo: str) -> dict:
    roots = [f"um/monthly/klines/{s}/1m" for s in SYMBOLS]
    doc = M.build(roots, manifest_id="edge04-core10-eligible9-1m-v1", source_class="archive-verified",
                  base=store, data_root="binance_um", survivor_only=False, accessed_cairo=accessed_cairo,
                  note="EDGE-04 exact 1m execution tape; official Binance UM monthly archives")
    M.write(doc, out)
    return doc


def write_once(doc: dict, path: str) -> None:
    raw = json.dumps(doc, sort_keys=True, indent=1).encode("ascii") + b"\n"
    if os.path.exists(path):
        with open(path, "rb") as f:
            if f.read() == raw:
                return
        raise AcquireError(f"{path} already exists with different bytes")
    with open(path, "xb") as f:
        f.write(raw)


def overlay_digest(doc: dict) -> str:
    return hashlib.sha256(M.canonical({k: v for k, v in doc.items() if k != "digest"})).hexdigest()


def validate_overlay_manifest(doc: dict) -> None:
    keys = {"format", "attestation_sha256", "attestation_digest", "source_manifest_digest", "window",
            "files", "digest"}
    if not isinstance(doc, dict) or set(doc) != keys or doc.get("format") != OVERLAY_FORMAT:
        raise AcquireError("invalid EDGE-04 overlay manifest shape")
    if any(not isinstance(doc.get(k), str) or not HEX64.match(doc[k])
           for k in ("attestation_sha256", "attestation_digest", "source_manifest_digest", "digest")):
        raise AcquireError("overlay identity hashes must be lowercase SHA-256")
    if doc["window"] != {"start": START, "end": END} or doc["digest"] != overlay_digest(doc):
        raise AcquireError("overlay manifest digest/window mismatch")
    seen, coverage = set(), set()
    for f in doc["files"]:
        need = {"path", "symbol", "day", "sha256", "bytes", "rows", "first_open_ms", "last_open_ms",
                "source_url", "published_checksum"}
        if set(f) != need or f["symbol"] not in SYMBOLS or not HEX64.match(f["sha256"]):
            raise AcquireError("invalid overlay file identity")
        if f["sha256"] != f["published_checksum"] or f["rows"] != 1440 or f["bytes"] <= 0:
            raise AcquireError("overlay file is not hash-bound exact daily coverage")
        day = _ms(f["day"] + "T00:00:00Z")
        if f["first_open_ms"] != day or f["last_open_ms"] != day + 86_340_000:
            raise AcquireError("overlay daily coverage bounds mismatch")
        if f["path"] in seen or (f["symbol"], f["day"]) in coverage:
            raise AcquireError("duplicate overlay path or symbol/day")
        if f["source_url"] != f"https://data.binance.vision/data/futures/um/daily/klines/{f['symbol']}/1m/" \
                f"{f['symbol']}-1m-{f['day']}.zip":
            raise AcquireError("overlay source is not its exact official Binance URL")
        seen.add(f["path"]); coverage.add((f["symbol"], f["day"]))


def build_overlay_manifest(store: str, attestation_path: str, out: str) -> dict:
    with open(attestation_path, "rb") as f:
        att_raw = f.read()
    att = json.loads(att_raw)
    if att.get("format") != FORMAT or att.get("digest") != hashlib.sha256(
            M.canonical({k: v for k, v in att.items() if k != "digest"})).hexdigest():
        raise AcquireError("acquisition attestation identity mismatch")
    if not att.get("effective_coverage", {}).get("complete"):
        raise AcquireError("cannot manifest an incomplete acquisition")
    planned = {r["path"]: r for r in att["acquisition_plan"] if r["kind"] == "daily_overlay"}
    verified = {x["path"]: x for x in att["acquisition"] if x.get("status") == "verified"}
    if set(planned) != set(verified):
        raise AcquireError("verified overlay set differs from the frozen acquisition plan")
    files = []
    for path in sorted(planned):
        req, got = planned[path], verified[path]
        ap = M.contained(store, path)
        with open(ap, "rb") as f:
            raw = f.read()
        actual = hashlib.sha256(raw).hexdigest()
        with open(ap + ".ok", encoding="ascii") as f:
            published = f.read().strip()
        if actual != got["sha256"] or published != actual or len(raw) != got["bytes"]:
            raise AcquireError(f"{path}: bytes/sidecar no longer match the acquisition attestation")
        _scan_daily(ap, req)
        day = _ms(req["period"] + "T00:00:00Z")
        files.append({"path": path, "symbol": req["symbol"], "day": req["period"], "sha256": actual,
                      "bytes": len(raw), "rows": 1440, "first_open_ms": day,
                      "last_open_ms": day + 86_340_000, "source_url": req["url"],
                      "published_checksum": published})
    doc = {"format": OVERLAY_FORMAT, "attestation_sha256": hashlib.sha256(att_raw).hexdigest(),
           "attestation_digest": att["digest"], "source_manifest_digest": att["source_manifest_digest"],
           "window": {"start": START, "end": END}, "files": files}
    doc["digest"] = overlay_digest(doc)
    validate_overlay_manifest(doc)
    write_once(doc, out)
    return doc


def load_overlay_manifest(path: str) -> dict:
    with open(path, encoding="ascii") as f:
        doc = json.load(f)
    validate_overlay_manifest(doc)
    return doc


def overlay_rows(store: str, file: dict):
    """Re-prove one manifest file and return its exact causal Bars."""
    ap = M.contained(store, file["path"])
    with open(ap, "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != file["sha256"]:
        raise AcquireError(f"{file['path']}: overlay bytes differ from manifest")
    req = {"symbol": file["symbol"], "period": file["day"], "path": file["path"]}
    _scan_daily(ap, req)
    member = f"{file['symbol']}-1m-{file['day']}.csv"
    with zipfile.ZipFile(ap) as z:
        rows = M.parse_archive_csv(file["path"], z.read(member),
                                   {"series":"klines", "interval":"1m",
                                    "month_start_ms":file["first_open_ms"],
                                    "month_end_ms":file["last_open_ms"] + MINUTE})
    import pit as P
    return [P.Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[7]),
                  int(r[0]) + MINUTE) for r in rows]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--attestation", required=True)
    ap.add_argument("--store")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--new-manifest")
    ap.add_argument("--accessed-cairo")
    a = ap.parse_args(argv)
    doc = M.load(a.manifest)
    inv = inventory(doc)
    inv["acquisition_plan"] = plan(doc)
    inv["digest"] = hashlib.sha256(M.canonical({k: v for k, v in inv.items() if k != "digest"})).hexdigest()
    if a.fetch:
        if not a.store:
            ap.error("--fetch requires an external --store")
        apply_acquisition_attestation(inv, inv["acquisition_plan"],
                                      acquire(inv["acquisition_plan"], a.store))
        inv["digest"] = hashlib.sha256(M.canonical({k: v for k, v in inv.items() if k != "digest"})).hexdigest()
    write_once(inv, a.attestation)
    if a.new_manifest:
        if not (a.store and a.accessed_cairo):
            ap.error("--new-manifest requires --store and --accessed-cairo")
        build_manifest(a.store, a.new_manifest, a.accessed_cairo)
    print(f"complete={inv['complete']} planned={len(inv['acquisition_plan'])} -> {a.attestation}")
    return 0 if inv["complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
