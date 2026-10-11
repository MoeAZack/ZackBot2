"""STRAT-00 R4-0 item 4: the committed long + short accounting input fixture (Codex 6106625697).

Always: the fixture bytes match the pinned sha256, carry no Build answers, stay before the holdout and match the trade
specs / conventions of the exporter. With the archive store present (the owner's Windows PC; ZB_STORE overrides the
default path): the exporter regenerates the fixture byte-identically into a scratch ledger copy."""
import hashlib
import json
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import accounting_sample as A                                                               # noqa: E402
import splits as S                                                                          # noqa: E402

STORE = os.environ.get('ZB_STORE', 'C:/Dev/ZackBot2_data/binance_um')
ANSWERS = {'entry_fill', 'exit_fill', 'entry_bps', 'exit_bps', 'qty', 'stop', 'exit_reason', 'exit_gap',
           'exit_time_ms', 'fees', 'funding', 'gross', 'net', 'R'}


def raw():
    with open(os.path.join(ROOT, A.FIXTURE), 'rb') as f:
        return f.read()


def test_fixture_is_pinned_answer_free_and_before_the_holdout():
    b = raw()
    assert hashlib.sha256(b).hexdigest() == A.FIXTURE_SHA256 and b'\r\n' not in b
    d = json.loads(b)
    assert d['format'] == A.FORMAT and d['conventions'] == A.CONVENTIONS and d['window'] == list(A.WINDOW)
    assert [{k: t[k] for k in A.TRADES[0]} for t in d['trades']] == list(A.TRADES)
    assert {t['side'] for t in d['trades']} == {'long', 'short'}
    keys = set(d) | {k for t in d['trades'] for k in t}
    assert not keys & ANSWERS                                          # inputs only: the answers stay Build-side
    holdout = S.parse_utc(A.HOLDOUT_START)
    for t in d['trades']:
        assert t['bars_4h'] and t['funding_events']
        assert max(x['open_ms'] for x in t['bars_4h']) + A.TF_MS <= S.parse_utc(A.WINDOW[1]) <= holdout
        assert max(f['time_ms'] for f in t['funding_events']) < holdout


@pytest.mark.skipif(not os.path.isdir(STORE), reason='archive store not present (owner Windows PC only)')
def test_fixture_regenerates_byte_identically_from_the_store(tmp_path):
    led = tmp_path / 'ledger'
    shutil.copytree(os.path.join(ROOT, os.path.dirname(A.LEDGER)), led)
    out = A.run(ROOT, STORE, str(tmp_path / 'out'), author='test', cairo_date='2026-10-11',
                ledger=str(led / os.path.basename(A.LEDGER)))
    assert out['accounting_inputs.json'] == A.FIXTURE_SHA256
    with open(tmp_path / 'out' / 'accounting_inputs.json', 'rb') as f:
        assert f.read() == raw()
