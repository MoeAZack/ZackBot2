"""Opaque synthetic cache bytes only; no network or canonical store accesses."""
import io
import json
from pathlib import Path
import urllib.error
import zipfile

import pytest

from tools.research import authority_v6 as V


@pytest.fixture
def vision_request(monkeypatch):
    monkeypatch.setattr(V, 'frozen_seed_symbols', lambda root: ('BTCUSDT',))
    return V._vision_request('klines', '1h', 'daily', 'BTCUSDT', '2026-11-04')


def payload(vision_request, raw=b'opaque synthetic archive'):
    return V.sha256(raw).encode() + b'  ' + Path(vision_request.path).name.encode() + b'\n'


def acquire(vision_request, tmp_path, fetch, cap=10000):
    return V.acquire_vision((vision_request,), tmp_path/'store', tmp_path/'ledger', fetch,
                            max_total_bytes=cap, phase='seed')


def seed_cache(vision_request, tmp_path, raw=b'opaque synthetic archive'):
    checksum = payload(vision_request, raw)
    acquire(vision_request, tmp_path, lambda url: (200, checksum if url.endswith('.CHECKSUM') else raw, url))
    return tmp_path/'store'/vision_request.path, checksum, raw


def test_linked_positive_rechecks_bytes_without_network_or_store_or_ledger_writes(vision_request, tmp_path, monkeypatch):
    target, checksum, raw = seed_cache(vision_request, tmp_path)
    before = (tmp_path/'ledger').read_bytes()
    monkeypatch.setattr(V, '_write_atomic', lambda *args: pytest.fail('cache rewrite'))
    result = acquire(vision_request, tmp_path, lambda url: pytest.fail('cache network'))
    assert result['verified'] == result['cache_reused'] == [vision_request.path]
    assert result['bytes'] == len(raw)
    assert target.read_bytes() == raw and Path(str(target)+'.CHECKSUM').read_bytes() == checksum
    assert (tmp_path/'ledger').read_bytes() == before
    V._validate_ledger_raw(before)


@pytest.mark.parametrize('attack', ['raw', 'ok', 'checksum', 'missing', 'admission_size',
                                   'admission_url', 'checksum_link'])
def test_inconsistent_cache_never_skips_fresh_official_checksum(vision_request, tmp_path, attack):
    target, checksum, raw = seed_cache(vision_request, tmp_path)
    ledger = tmp_path/'ledger'
    rows = [json.loads(line) for line in ledger.read_bytes().splitlines()]
    if attack == 'raw':
        target.write_bytes(b'corrupted')
    elif attack == 'ok':
        Path(str(target)+'.ok').write_bytes(b'0'*64+b'\n')
    elif attack == 'checksum':
        Path(str(target)+'.CHECKSUM').write_bytes(b'invalid')
    elif attack == 'missing':
        target.unlink()
    elif attack == 'admission_size':
        rows[-1]['raw_bytes'] += 1
    elif attack == 'admission_url':
        rows[-1]['url'] = 'https://attacker.invalid/'
    else:
        rows[0]['checksum_raw_sha256'] = '0'*64
    if attack in ('admission_size', 'admission_url', 'checksum_link'):
        ledger.write_bytes(b''.join(V.canonical(row)+b'\n' for row in rows))
    before = ledger.read_bytes()
    calls = []
    def fetch(url):
        calls.append(url)
        return 200, checksum if url.endswith('.CHECKSUM') else raw, url
    result = acquire(vision_request, tmp_path, fetch)
    assert calls[0] == vision_request.checksum_url
    assert result['verified'] == [vision_request.path]
    assert target.read_bytes() == raw
    assert ledger.read_bytes().startswith(before)
    records = V._validate_ledger_raw(ledger.read_bytes())[1]
    assert records[-1]['decision'] == 'PRESENT_CHECKSUM_VERIFIED' and records[-1]['attempt'] == 1
    if attack in ('raw', 'ok', 'missing'):
        assert calls == [vision_request.checksum_url, vision_request.url]


def test_prefetched_positive_needs_fresh_official_checksum_then_new_admission(vision_request, tmp_path):
    target, checksum, raw = seed_cache(vision_request, tmp_path)
    (tmp_path/'ledger').unlink()
    calls = []
    def fetch(url):
        calls.append(url)
        assert url == vision_request.checksum_url
        return 200, checksum, url
    result = acquire(vision_request, tmp_path, fetch)
    assert calls == [vision_request.checksum_url] and result['cache_reused'] == [vision_request.path]
    assert target.read_bytes() == raw
    rows = V._validate_ledger_raw((tmp_path/'ledger').read_bytes())[1]
    assert [r['decision'] for r in rows] == ['CHECKSUM_RAW_PRESERVED', 'PRESENT_CHECKSUM_VERIFIED']


def test_failed_archive_attempt_remains_and_next_attempt_is_contiguous(vision_request, tmp_path):
    raw = b'opaque recovered archive'
    checksum = payload(vision_request, raw)
    def failure(url):
        if url == vision_request.checksum_url:
            return 200, checksum, url
        raise urllib.error.URLError('synthetic transport failure')
    with pytest.raises(V.AuthorityV6Error, match='archive fetch failed'):
        acquire(vision_request, tmp_path, failure)
    before = (tmp_path/'ledger').read_bytes()
    assert json.loads(before.splitlines()[-1])['decision'] == 'ARCHIVE_FETCH_FAILED'
    acquire(vision_request, tmp_path, lambda url: (200, checksum if url.endswith('.CHECKSUM') else raw, url))
    after = (tmp_path/'ledger').read_bytes()
    assert after.startswith(before)
    assert V._validate_ledger_raw(after)[1][-1]['attempt'] == 1
    acquire(vision_request, tmp_path, lambda url: pytest.fail('recovered positive refetch'))
    assert (tmp_path/'ledger').read_bytes() == after


def test_official_404_is_revalidated_even_with_unlinked_local_positive(vision_request, tmp_path):
    seed_cache(vision_request, tmp_path)
    (tmp_path/'ledger').unlink()
    calls = []
    def absent(url):
        calls.append(url)
        return 404, b'', url
    for _ in range(2):
        result = acquire(vision_request, tmp_path, absent)
        assert result['official_404'] == [vision_request.path] and not result['verified']
    assert calls == [vision_request.checksum_url]*2
    rows = V._validate_ledger_raw((tmp_path/'ledger').read_bytes())[1]
    assert [r['attempt'] for r in rows] == [0, 1]


def test_cache_bytes_count_against_unchanged_total_cap(vision_request, tmp_path):
    _, _, raw = seed_cache(vision_request, tmp_path)
    before = (tmp_path/'ledger').read_bytes()
    with pytest.raises(V.AuthorityV6Error, match='byte cap'):
        acquire(vision_request, tmp_path, lambda url: pytest.fail('cap refetch'), cap=len(raw)-1)
    assert (tmp_path/'ledger').read_bytes() == before


def test_invalid_ledger_is_refused_before_cache_or_network(vision_request, tmp_path):
    seed_cache(vision_request, tmp_path)
    (tmp_path/'ledger').write_bytes(b'{"decision":"bad"}')
    with pytest.raises(V.AuthorityV6Error, match='newline'):
        acquire(vision_request, tmp_path, lambda url: pytest.fail('invalid history network'))


def test_reuse_does_not_manufacture_cutoff_or_schema_acceptance(vision_request, tmp_path):
    t = V.CUTOFF_MS
    csv = f'{t},1,2,0.5,1.5,3,{t+V.HOUR_MS-1},4,5,1,2,0\n'.encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(Path(vision_request.path).name[:-4]+'.csv', csv)
    seed_cache(vision_request, tmp_path, buffer.getvalue())
    acquire(vision_request, tmp_path, lambda url: pytest.fail('positive refetch'))
    with pytest.raises((V.AuthorityV6Error, ValueError)):
        V.scan_v6_record(tmp_path/'store', vision_request.path, tmp_path/'ledger')
    rows = V._validate_ledger_raw((tmp_path/'ledger').read_bytes())[1]
    assert rows[-1]['decision'] == 'VISION_PARSER_SCHEMA_CUTOFF_REFUSED'
    assert not any(r['decision']=='VISION_PARSER_SCHEMA_CUTOFF_ACCEPTED' for r in rows)
