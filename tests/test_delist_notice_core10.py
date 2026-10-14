"""REX-03: the committed CORE-10 `zb-delist-notice/1` artifact (Codex ruling #81 6172019060 point 3) validates against
the REX-02 validator, is digest-pinned, and its coverage sidecar proves the per-symbol verdicts. Nothing is fetched."""
import hashlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import delist_notice_collect as DC                                                          # noqa: E402
import range_vwap_rev_artifact as NA                                                        # noqa: E402

# Collected 2026-10-14 (18:54-19:50 UTC, 409 sequential requests at 1 / 8 s, no 429) by delist_notice_collect.py.
STEM = os.path.join(ROOT, 'research_evidence', 'notices', 'zb-delist-notice-core-10-20261014')
ARTIFACT, COVERAGE = STEM + '.json', STEM + '.coverage.json'
ARTIFACT_DIGEST = '1554d2f33384426d68b08d150f667ba623a4d5de55f826c2ed0092c96cf0fcd3'   # what --notice-sha256 pins
ARTIFACT_FILE_SHA = 'd79bfddca7d2e502cc07439f8662f618a5e58450afd2d2eca4e0e1708e3d5813'
COVERAGE_FILE_SHA = 'c3eb4ccc510bb911336c9395dfa88743ba34002fd44824c140b8ed1a8a32554c'
PENDING = pytest.mark.skipif(STEM is None, reason='CORE-10 notice artifact not collected')
CORE10 = ['BTCUSDT', 'ETHUSDT', 'ZECUSDT', 'SOLUSDT', 'XRPUSDT', 'NEARUSDT', 'HYPEUSDT', 'DOGEUSDT', 'UNIUSDT',
          'SUIUSDT']


def _load(p):
    with open(p, 'rb') as f:
        raw = f.read()
    return raw, json.loads(raw)


@pytest.fixture(scope='module')
def art():
    return _load(ARTIFACT)


@pytest.fixture(scope='module')
def cov():
    return _load(COVERAGE)


@PENDING
def test_file_bytes_pinned(art, cov):
    assert hashlib.sha256(art[0]).hexdigest() == ARTIFACT_FILE_SHA
    assert hashlib.sha256(cov[0]).hexdigest() == COVERAGE_FILE_SHA
    assert b'\r\n' not in art[0] and b'\r\n' not in cov[0]


@PENDING
def test_artifact_validates_with_pinned_digest(art):
    v = NA.validate(art[1], expect_digest=ARTIFACT_DIGEST)
    assert v.digest == ARTIFACT_DIGEST
    assert v.cov_from is not None and v.cov_from <= DC.WINDOW_FROM_MS      # coverage reaches the window start
    assert v.cov_to is not None and v.cov_to >= DC.WINDOW_TO_MS            # and the window end
    assert not v.register.gaps


@PENDING
def test_artifact_refuses_another_digest(art):
    with pytest.raises(NA.ArtifactError):
        NA.validate(art[1], expect_digest='0' * 64)
    bad = dict(art[1], covers_from_ms=art[1]['covers_from_ms'] + 1)
    with pytest.raises(NA.ArtifactError):
        NA.validate(bad, expect_digest=ARTIFACT_DIGEST)


def test_core10_is_the_frozen_universe_tier():
    syms, digest = DC.tier_symbols(ROOT, 'CORE-10')
    assert syms == CORE10
    assert NA.HEX64.match(digest)


@PENDING
def test_coverage_sidecar_binds_the_artifact(art, cov):
    c = cov[1]
    assert c['format'] == DC.COVERAGE_FORMAT
    assert c['digest'] == hashlib.sha256(DC._canon({k: v for k, v in c.items() if k != 'digest'})).hexdigest()
    assert c['artifact_digest'] == ARTIFACT_DIGEST
    assert c['window'] == {'from_ms': DC.WINDOW_FROM_MS, 'to_ms_exclusive': DC.WINDOW_TO_MS,
                           'from_utc': '2021-01-04T00:00:00Z', 'to_utc': '2026-09-28T00:00:00Z'}
    manifest = [[p['url'], p['sha256']] for p in c['pages']]
    assert hashlib.sha256(DC._canon(manifest)).hexdigest() == c['page_manifest_digest']
    assert [f['raw_digest'] for f in art[1]['fetches']] == [c['page_manifest_digest']]
    for p in c['pages']:
        assert p['url'].startswith(DC.API) and NA.HEX64.match(p['sha256'])
    for s in c['body_scans']:
        assert s['url'].startswith(DC.API) and NA.HEX64.match(s['sha256'])


@PENDING
def test_every_catalog_covers_the_window(cov):
    cats = cov[1]['catalogs']
    assert cats and all(c['complete'] or c['reached_below_window'] for c in cats)
    assert cov[1]['body_scan_failures'] == 0


@PENDING
def test_core10_verdicts_as_collected(cov):
    assert {s: v['status'] for s, v in cov[1]['verdicts'].items()} == dict.fromkeys(CORE10, 'proven_none')


@PENDING
def test_verdicts_match_the_artifact(art, cov):
    v, a = cov[1]['verdicts'], art[1]
    assert sorted(v) == sorted(CORE10)
    assert a['frozen'] == cov[1]['frozen'] == [s for s in CORE10 if v[s]['status'] != 'unproven']
    noticed = {n['symbol'] for n in a['notices']}
    for s in CORE10:
        assert v[s]['status'] in ('notice', 'proven_none', 'unproven')
        assert (v[s]['status'] == 'notice') == (s in noticed)
    for n in a['notices']:
        assert n['source_url'].startswith(DC.PUBLIC_URL.split('{')[0])
        assert n['published_at'] >= DC.WINDOW_FROM_MS


@PENDING
def test_unproven_symbol_is_a_notice_gap(art):
    """A run symbol outside the artifact's frozen list is a whole-window GAP (runtime INCOMPLETE), never a pass."""
    v = NA.validate(art[1], expect_digest=ARTIFACT_DIGEST)
    lo, hi = DC.WINDOW_FROM_MS, DC.WINDOW_TO_MS - 1
    view = v.view({}, ['NOTINLISTUSDT'], lo, hi)
    assert (lo, hi + 1) in view.gaps


# --- collector pure functions (synthetic) -------------------------------------------------------------------------

def test_body_text_and_scan():
    body = json.dumps({'node': 'root', 'child': [{'node': 'text', 'text': 'Binance Futures will delist USDⓈ-M '},
                                                 {'node': 'element', 'child': [{'node': 'text',
                                                                                'text': 'ZECUSDT Perpetual'}]}]})
    t = DC.body_text({'body': body})
    s = DC.scan('Binance Futures Will Delist Multiple USDⓈ-M Perpetual Contracts', t, CORE10)
    assert s == {'pair_hits': ['ZECUSDT'], 'base_hits': [], 'perpetual': True}
    assert DC.scan('x', 'ZEC spot pairs', CORE10)['base_hits'] == ['ZEC']


def test_verdicts_pure():
    cats = [{'complete': True, 'reached_below_window': False}, {'complete': False, 'reached_below_window': True}]
    scans = [{'code': 'g1', 'pair_hits': ['ZECUSDT'], 'perpetual': True},
             {'code': 'g2', 'pair_hits': ['BTCUSDT'], 'perpetual': False}]
    entries = [{'symbol': 'SOLUSDT', 'code': 'n1'}]
    v = DC.verdicts(['BTCUSDT', 'ZECUSDT', 'SOLUSDT'], entries, scans, cats, scan_failures=0)
    assert [v[s]['status'] for s in ('BTCUSDT', 'ZECUSDT', 'SOLUSDT')] == ['proven_none', 'unproven', 'notice']
    v = DC.verdicts(['BTCUSDT'], [], [], cats[1:] + [{'complete': False, 'reached_below_window': False}],
                    scan_failures=0)
    assert v['BTCUSDT']['status'] == 'unproven'
    assert DC.verdicts(['BTCUSDT'], [], [], cats, scan_failures=1)['BTCUSDT']['status'] == 'unproven'
    assert DC.verdicts(['BTCUSDT'], [], [], [], scan_failures=0)['BTCUSDT']['status'] == 'unproven'


def test_effective_time_parse():
    pub = 1700000000000
    assert DC.effective_ms('trading closes at 2023-11-20 09:00 (UTC) ...', pub) == 1700470800000
    assert DC.effective_ms('no stated time', pub) is None


def test_window_constants():
    assert DC.utc(DC.WINDOW_FROM_MS) == '2021-01-04T00:00:00Z' and DC.utc(DC.WINDOW_TO_MS) == '2026-09-28T00:00:00Z'


def test_collector_refuses_non_official_urls():
    with pytest.raises(ValueError):
        DC.Client(delay=0).get('https://example.com/x')
