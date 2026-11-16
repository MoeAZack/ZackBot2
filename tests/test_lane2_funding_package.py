import csv
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class EvidencePackageTests(unittest.TestCase):
    def fixture(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        root = Path(folder.name); cache = root/'cache'; out = root/'out'
        for name in ('native_funding/raw', 'native_funding/symbols', 'mark_archives'):
            (cache/name).mkdir(parents=True, exist_ok=True)
        stamp = 1673136000000
        events = [dict(symbol='1000LUNCUSDT', fundingTime=stamp+i*28800000,
                       fundingRate='0.0001', markPrice='' if i < 2 else '100') for i in range(3)]
        url = 'https://fapi.binance.com/fapi/v1/fundingRate?symbol=1000LUNCUSDT&limit=1000'
        raw = json.dumps(events).encode(); key = hashlib.sha256(url.encode()).hexdigest()
        rawpath = cache/'native_funding/raw'/f'{key}.json'; rawpath.write_bytes(raw)
        meta = dict(url=url, sha256=hashlib.sha256(raw).hexdigest())
        data = dict(symbol='1000LUNCUSDT', status='PAGINATED_REST_RANGE_COMPLETE',
                    start_ms=stamp-28800000, end_ms=stamp+86400000, events=events,
                    pages=[dict(count=3, response=meta)])
        (cache/'native_funding/symbols/1000LUNCUSDT.json').write_text(json.dumps(data))
        (cache/'funding_windows.json').write_text(json.dumps({'1000LUNCUSDT': [[stamp,stamp+57600000]]}))
        records = [dict(symbol='1000LUNCUSDT', funding_time_ms=e['fundingTime'], value='100',
                       high='110', low='90', close='105', candle_open_ms=e['fundingTime'],
                       candle_close_ms=e['fundingTime']+59999, timestamp_gap_ms=0,
                       containing_candle=True, source_type='fixture', source_url=url,
                       source_sha256='fixture', verified_at_cairo='2023-01-08T02:00:00+02:00') for e in events[:2]]
        markpath = cache/'mark_archives/mark_sources.json'
        markpath.write_text(json.dumps(dict(incomplete=False, stopped_rate_limit=False, records=records, gaps=[])))
        details = root/'details.jsonl.gz'
        with gzip.open(details,'wt') as stream:
            stream.write(json.dumps(dict(status='COMPLETE_MODELLED_CLOCK', job_id='r6_regime_flip_4h:0:path|cost15',
                proxy_events=[dict(symbol='1000LUNCUSDT',funding_time_ms=stamp,primary_qty=1)]))+'\n')
        return cache,out,details,rawpath,markpath

    def run_package(self, cache, out, details):
        return subprocess.run([sys.executable,'tools/research/lane2_funding_package.py',
            '--cache',str(cache),'--out',str(out),'--details',str(details)],capture_output=True,text=True)

    def test_unused_proxy_does_not_inflate_applied_count(self):
        cache,out,details,_,_ = self.fixture(); result=self.run_package(cache,out,details)
        self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads((out/'coverage_summary.json').read_text())
        self.assertEqual(report['required_proxy_marks'],2)
        self.assertEqual(report['applied_proxy_marks'],1)
        with gzip.open(out/'mark_proxy_ledger.csv.gz','rt') as stream: rows=list(csv.DictReader(stream))
        self.assertEqual([r['applied_to_closed_trade_book'] for r in rows],['True','False'])

    def test_raw_response_corruption_rejected(self):
        cache,out,details,raw,_ = self.fixture(); raw.write_bytes(b'corrupt')
        result=self.run_package(cache,out,details)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('checksum mismatch',result.stderr)
        self.assertFalse((out/'coverage_summary.json').exists())

    def test_missing_required_proxy_rejected(self):
        cache,out,details,_,marks=self.fixture()
        payload=json.loads(marks.read_text());payload['records'].pop();marks.write_text(json.dumps(payload))
        result=self.run_package(cache,out,details)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('required marks lack source candles',result.stderr)


if __name__ == '__main__': unittest.main()
