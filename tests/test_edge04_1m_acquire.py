import hashlib
import io
import json
import os
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "research"))
import edge04_1m_acquire as A
import manifest as M


def entry(symbol, period, rows=None, first=None):
    lo, hi = A._month_bounds(period)
    first = lo if first is None else first
    rows = (hi - first) // A.MINUTE if rows is None else rows
    path = f"um/monthly/klines/{symbol}/1m/{symbol}-1m-{period}.zip"
    return {"path": path, "symbol": symbol, "interval": "1m", "series": "klines", "sha256": "a" * 64,
            "bytes": 10, "rows": rows, "first_open_ms": first,
            "last_open_ms": first + (rows - 1) * A.MINUTE,
            "source": {"kind": M.ARCHIVE_KIND, "url": M.archive_url(path), "archive_checksum": "a" * 64}}


def manifest(files):
    d = {"format": M.FORMAT, "manifest_id": "x", "source_class": "archive-verified",
         "survivor_only": False, "loader_version": M.ARCHIVE_LOADER, "available_rule": M.ARCHIVE_RULE,
         "accessed_cairo": None, "note": "", "data_root": "binance_um", "files": sorted(files, key=lambda x:x["path"])}
    d["digest"] = M.digest_of(d)
    return d


def test_periods_are_development_only_and_holdout_refused():
    assert A.periods()[0] == "2021-01" and A.periods()[-1] == "2026-09"
    with pytest.raises(A.AcquireError):
        A.periods(A.START, "2026-11-03T00:00:00Z")


def test_inventory_detects_missing_month_and_row_gap_without_opening_store(monkeypatch):
    ps = A.periods()
    fs = []
    for s in A.SYMBOLS:
        fs += [entry(s, p) for p in ps]
    fs = [f for f in fs if not (f["symbol"] == "BTCUSDT" and "2022-02" in f["path"])]
    z = next(f for f in fs if f["symbol"] == "ZECUSDT" and "2023-03" in f["path"])
    z["rows"] -= 1
    z["last_open_ms"] -= A.MINUTE
    d = manifest(fs)
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("data read")))
    got = A.inventory(d)
    assert not got["complete"]
    by = {x["symbol"]: x for x in got["symbols"]}
    assert by["BTCUSDT"]["missing_archives"] == ["2022-02"]
    assert by["ZECUSDT"]["row_deficits"][0]["missing_rows"] == 1


def test_first_listing_month_can_start_late():
    fs = [entry(s, p) for s in A.SYMBOLS for p in A.periods()]
    sui = [f for f in fs if f["symbol"] != "SUIUSDT"]
    for p in A.periods()[28:]:
        lo, _ = A._month_bounds(p)
        sui.append(entry("SUIUSDT", p, first=lo + (120 * A.MINUTE) if p == A.periods()[28] else None))
    assert A.inventory(manifest(sui))["symbols"][-1]["complete"]


def _zip(symbol="BTCUSDT", period="2021-01"):
    lo, _ = A._month_bounds(period)
    lines = [f"{lo},1,2,0,1,1,{lo + A.MINUTE - 1},1,1,1,1,0"]
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as z:
        z.writestr(f"{symbol}-1m-{period}.csv", "\n".join(lines) + "\n")
    return bio.getvalue()


class Resp(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body); self.status = status


def test_fetch_resumes_verifies_and_imports_atomically(tmp_path):
    raw = _zip(); sha = hashlib.sha256(raw).hexdigest(); seen = []
    req = {"symbol":"BTCUSDT", "period":"2021-01", "url":A.url("BTCUSDT","2021-01"),
           "checksum_url":A.url("BTCUSDT","2021-01")+".CHECKSUM", "path":A.relpath("BTCUSDT","2021-01")}
    target = M.contained(str(tmp_path), req["path"]); os.makedirs(os.path.dirname(target));
    with open(target + ".part", "wb") as f: f.write(raw[:17])
    def op(u, headers=None):
        seen.append((u, headers or {}))
        return Resp((sha + "  BTCUSDT-1m-2021-01.zip\n").encode()) if u.endswith("CHECKSUM") else Resp(raw[17:], 206)
    got = A.fetch_one(req, str(tmp_path), opener=op)
    assert got["status"] == "verified" and os.path.exists(target + ".ok") and not os.path.exists(target + ".part")
    assert seen[-1][1] == {"Range": "bytes=17-"}


def test_bad_checksum_never_imports(tmp_path):
    raw = _zip(); req = {"symbol":"BTCUSDT", "period":"2021-01", "url":A.url("BTCUSDT","2021-01"),
           "checksum_url":A.url("BTCUSDT","2021-01")+".CHECKSUM", "path":A.relpath("BTCUSDT","2021-01")}
    def op(u, headers=None): return Resp(("0"*64 + "  BTCUSDT-1m-2021-01.zip\n").encode()) if u.endswith("CHECKSUM") else Resp(raw)
    with pytest.raises(A.AcquireError, match="checksum mismatch"):
        A.fetch_one(req, str(tmp_path), opener=op)
    assert not os.path.exists(M.contained(str(tmp_path), req["path"]))


def test_complete_partial_resumes_without_redownloading_zip(tmp_path):
    raw = _zip(); sha = hashlib.sha256(raw).hexdigest()
    req = {"symbol":"BTCUSDT", "period":"2021-01", "kind":"monthly", "url":A.url("BTCUSDT","2021-01"),
           "checksum_url":A.url("BTCUSDT","2021-01")+".CHECKSUM", "path":A.relpath("BTCUSDT","2021-01")}
    target=M.contained(str(tmp_path),req["path"]); os.makedirs(os.path.dirname(target)); open(target+".part","wb").write(raw)
    def op(u, headers=None):
        assert u.endswith("CHECKSUM")
        return Resp((sha + "  BTCUSDT-1m-2021-01.zip\n").encode())
    assert A.fetch_one(req,str(tmp_path),opener=op)["status"] == "verified"


def test_acquire_pacing_and_write_once(tmp_path):
    sleeps=[]
    with pytest.raises(A.AcquireError): A.acquire([], str(tmp_path), min_gap_s=.5)
    p=tmp_path/"a.json"; A.write_once({"x":1}, str(p)); A.write_once({"x":1}, str(p))
    with pytest.raises(A.AcquireError): A.write_once({"x":2}, str(p))


def test_acquisition_attestation_requires_every_overlay():
    inv = {"symbols": [{"row_deficits": [{"missing_rows": 1, "missing_utc_days": ["2022-01-01"]}]}]}
    req = [{"kind": "daily_overlay", "path": "x"}]
    assert not A.apply_acquisition_attestation(inv, req, [])["effective_coverage"]["complete"]
    inv = {"symbols": [{"row_deficits": [{"missing_rows": 1, "missing_utc_days": ["2022-01-01"]}]}]}
    assert A.apply_acquisition_attestation(inv, req, [{"path":"x", "status":"verified"}])["effective_coverage"]["complete"]


def test_coordinate_traversal_and_unfrozen_symbol_refused():
    with pytest.raises(A.AcquireError): A.relpath("HYPEUSDT", "2021-01")
    with pytest.raises(A.AcquireError): A._open("https://example.com/x")


def test_plan_uses_daily_overlay_for_boundary_hole():
    fs = [entry(s, p) for s in A.SYMBOLS for p in A.periods()]
    f = next(x for x in fs if x["symbol"] == "BTCUSDT" and "2022-02" in x["path"])
    f["rows"] -= 1440; f["last_open_ms"] -= 86_400_000
    got = A.plan(manifest(fs))
    assert len(got) == 1 and got[0]["kind"] == "daily_overlay" and got[0]["period"] == "2022-02-28"


def test_overlay_manifest_digest_and_duplicate_coverage_fail_closed():
    day = A._ms("2022-02-26T00:00:00Z")
    f = {"path":"edge04_daily/klines/ZECUSDT/1m/ZECUSDT-1m-2022-02-26.zip",
         "symbol":"ZECUSDT", "day":"2022-02-26", "sha256":"a"*64, "bytes":10, "rows":1440,
         "first_open_ms":day, "last_open_ms":day+86_340_000,
         "source_url":"https://data.binance.vision/data/futures/um/daily/klines/ZECUSDT/1m/"
                      "ZECUSDT-1m-2022-02-26.zip", "published_checksum":"a"*64}
    d={"format":A.OVERLAY_FORMAT,"attestation_sha256":"b"*64,"attestation_digest":"c"*64,
       "source_manifest_digest":"d"*64,"window":{"start":A.START,"end":A.END},"files":[f]}
    d["digest"]=A.overlay_digest(d); A.validate_overlay_manifest(d)
    bad={**d,"files":[f,dict(f)]}; bad["digest"]=A.overlay_digest(bad)
    with pytest.raises(A.AcquireError,match="duplicate overlay"):
        A.validate_overlay_manifest(bad)
    with pytest.raises(A.AcquireError,match="digest/window"):
        A.validate_overlay_manifest({**d,"digest":"0"*64})
