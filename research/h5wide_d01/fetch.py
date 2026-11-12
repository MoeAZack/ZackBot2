"""H5-WIDE-D01 data fetch: Binance USD-M futures 1h klines from data.binance.vision.

Range: 2022-08-01 00:00 UTC .. 2026-10-04 04:00 UTC (open time, inclusive).
Monthly archives 2022-08..2026-09, daily archives 2026-10-01..2026-10-04.
Resumable: existing zips + CHECKSUM files are reused (and re-verified).
Usage: python -I fetch.py   (run from any directory; outputs go next to this script)
"""
import concurrent.futures as cf
import csv
import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "raw")
CLEAN = os.path.join(ROOT, "clean")
SYMBOLS = ("ADAUSDT AVAXUSDT BNBUSDT BTCUSDT DOGEUSDT ETHUSDT LINKUSDT SOLUSDT XRPUSDT 1000PEPEUSDT "
           "AAVEUSDT APTUSDT ARBUSDT ARKUSDT BCHUSDT DOTUSDT ENAUSDT ENJUSDT ETCUSDT FETUSDT FILUSDT "
           "GALAUSDT INJUSDT LTCUSDT MANAUSDT MOVRUSDT NEARUSDT ONDOUSDT ONEUSDT QNTUSDT SANDUSDT "
           "STRKUSDT SUIUSDT SUPERUSDT TAOUSDT UNIUSDT WLDUSDT XLMUSDT ZECUSDT ZROUSDT").split()
START_MS = int(datetime(2022, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2026, 10, 4, 4, tzinfo=timezone.utc).timestamp() * 1000)
H = 3_600_000
BASE = "https://data.binance.vision/data/futures/um"
UA = {"User-Agent": "h5wide-d01-fetch/1.0"}


def periods():
    out = []
    y, m = 2022, 8
    while (y, m) <= (2026, 9):
        out.append(("monthly", f"{y:04d}-{m:02d}"))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    for d in range(1, 5):
        out.append(("daily", f"2026-10-{d:02d}"))
    return out


def http_get(url, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, None
            if i == tries - 1:
                return e.code, None
        except Exception:
            if i == tries - 1:
                return -1, None
        time.sleep(2 ** i)
    return -1, None


def fetch_one(sym, kind, period):
    name = f"{sym}-1h-{period}.zip"
    url = f"{BASE}/{kind}/klines/{sym}/1h/{name}"
    d = os.path.join(RAW, sym)
    zp, cp = os.path.join(d, name), os.path.join(d, name + ".CHECKSUM")
    rec = {"period": period, "kind": kind, "file": name}
    if not (os.path.exists(zp) and os.path.exists(cp)):
        st, data = http_get(url)
        if st == 404:
            rec["status"] = "404"
            return sym, rec
        if data is None:
            rec["status"] = f"error_{st}"
            return sym, rec
        st2, cdata = http_get(url + ".CHECKSUM")
        os.makedirs(d, exist_ok=True)
        with open(zp, "wb") as f:
            f.write(data)
        if cdata is not None:
            with open(cp, "wb") as f:
                f.write(cdata)
    actual = hashlib.sha256(open(zp, "rb").read()).hexdigest()
    expected = None
    if os.path.exists(cp):
        expected = open(cp, "r", encoding="utf-8", errors="replace").read().split()[0].strip().lower()
    rec["sha256"] = actual
    rec["expected"] = expected
    rec["status"] = "ok"
    rec["checksum"] = "missing" if expected is None else ("pass" if expected == actual else "FAIL")
    return sym, rec


def read_zip_rows(zp):
    rows = []
    with zipfile.ZipFile(zp) as z:
        for n in z.namelist():
            text = z.read(n).decode("utf-8")
            for r in csv.reader(io.StringIO(text)):
                if not r or not r[0].strip().lstrip("-").isdigit():
                    continue  # header row or blank
                t = int(r[0])
                if t > 10 ** 14:  # microseconds safeguard
                    t //= 1000
                rows.append([t, r[1], r[2], r[3], r[4], r[5], r[7]])
    return rows


def rest_bar(sym, t):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&startTime={t}&limit=1"
    st, data = http_get(url)
    if data is None:
        return None, f"http_{st}"
    arr = json.loads(data)
    if not arr or int(arr[0][0]) != t:
        return None, "no_bar_at_t"
    k = arr[0]
    return [t, k[1], k[2], k[3], k[4], k[5], k[7]], "ok"


def sha_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def main():
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(CLEAN, exist_ok=True)
    jobs = [(s, k, p) for s in SYMBOLS for k, p in periods()]
    results = {s: [] for s in SYMBOLS}
    with cf.ThreadPoolExecutor(16) as ex:
        for i, (sym, rec) in enumerate(ex.map(lambda j: fetch_one(*j), jobs), 1):
            results[sym].append(rec)
            if i % 400 == 0:
                print(f"downloaded {i}/{len(jobs)}", flush=True)

    manifest = {"contract": "H5-WIDE-D01", "source": BASE, "interval": "1h",
                "range_open_time_ms": [START_MS, END_MS],
                "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "fetch_py_sha256": sha_file(os.path.abspath(__file__)), "symbols": {}}
    summary = []
    for sym in SYMBOLS:
        recs = sorted(results[sym], key=lambda r: r["period"])
        bars = {}
        for r in recs:
            if r["status"] == "ok":
                for row in read_zip_rows(os.path.join(RAW, sym, r["file"])):
                    if START_MS <= row[0] <= END_MS:
                        bars[row[0]] = row
        ts = sorted(bars)
        repairs = []
        for t in ts:
            o, h, l, c, v = bars[t][1:6]
            if float(v) == 0 and float(o) == float(h) == float(l) == float(c):
                new, why = rest_bar(sym, t)
                entry = {"t": t, "before": bars[t][1:]}
                if new is not None and float(new[5]) > 0:
                    bars[t] = new
                    entry.update(after=new[1:], action="replaced")
                else:
                    entry.update(after=None if new is None else new[1:],
                                 action="kept", reason=why if new is None else "rest_volume_zero")
                repairs.append(entry)
        missing = []
        if ts:
            have = set(ts)
            missing = [t for t in range(ts[0], ts[-1] + H, H) if t not in have]
        out = os.path.join(CLEAN, f"{sym}_1h.csv")
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["t", "o", "h", "l", "c", "v", "qv"])
            for t in ts:
                w.writerow(bars[t])
        cks = {r["file"]: r.get("checksum", r["status"]) for r in recs}
        nfail = sum(1 for r in recs if r.get("checksum") in ("FAIL", "missing"))
        nerr = [r["file"] for r in recs if r["status"].startswith("error")]
        n404 = [r["period"] for r in recs if r["status"] == "404"]
        fmt = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
        manifest["symbols"][sym] = {
            "first_open_ms": ts[0] if ts else None, "last_open_ms": ts[-1] if ts else None,
            "first_open_utc": fmt(ts[0]) if ts else None, "last_open_utc": fmt(ts[-1]) if ts else None,
            "bar_count": len(ts), "missing_hours": len(missing),
            "missing_hours_ms": missing,
            "placeholders_found": len(repairs),
            "placeholders_replaced": sum(1 for e in repairs if e["action"] == "replaced"),
            "placeholder_log": repairs,
            "archives_404": n404, "archives_error": nerr,
            "checksum_fail_or_missing": nfail, "checksums": cks,
            "clean_csv": f"clean/{sym}_1h.csv", "clean_csv_sha256": sha_file(out)}
        s = manifest["symbols"][sym]
        summary.append(f"{sym:14s} bars={len(ts):6d} first={s['first_open_utc']} last={s['last_open_utc']} "
                       f"missing={len(missing)} placeholders={len(repairs)} replaced={s['placeholders_replaced']} "
                       f"404={len(n404)} err={len(nerr)} cksum_bad={nfail}")
        print(summary[-1], flush=True)
    with open(os.path.join(ROOT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    with open(os.path.join(ROOT, "SUMMARY.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary) + "\n")
    print("manifest sha256:", sha_file(os.path.join(ROOT, "manifest.json")))


if __name__ == "__main__":
    main()
