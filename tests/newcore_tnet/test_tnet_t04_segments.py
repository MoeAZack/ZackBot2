"""T04 evidence: segmented cassettes (Codex T04 triage 6087020052).

T04-algo PASSED on testnet at d591d88 but its scenario cassette was refused at save (CassetteLeak 'too many encoded
runs'): 31 cycles of the account-wide positionRisk read (~96,000 decode attempts each, measured on the real 5a
cassettes) pass the unchanged per-audit MAX_AUDIT_RUNS = 4,000,000 as ONE document. The fix is evidence-only: the
recorder marks a checkpoint at every cycle / teardown boundary, to_segments() closes a segment there once it holds
SEGMENT_CHARS, and EVERY segment is a complete cassette audited on its own (unchanged budget) before anything is
written; a manifest written last names the segments with their SHA-256; replay joins them fail-closed. The per-audit
caps are unchanged; the whole cassette still stays under MAX_AUDIT_CHARS.

Every test here fails on d591d88 (no checkpoints / to_segments / segment manifest there)."""
import base64
import copy
import json
import os

import pytest

from ncv_support import DUMMY_KEY, DUMMY_SECRET, FakeHttp
from test_ncv_cassette_leaks import ok, req
from test_tnet_5a_cassette_sizes import RealShapedBinance
from tnet_support import World

from newcore.tnet import recording as RC
from newcore.tnet.recording import SEGMENT_SET_FORMAT, run_recorded_suite, save_bundle
from newcore.tnet.replay import ReplayError, load_bundle, replay
from newcore.tnet.rspec import bundled
from newcore.venue import cassette as C
from newcore.venue.cassette import CassetteLeak, CassetteRecorder
from newcore.venue.cassette_replay import leak_audit

REDACT = (DUMMY_KEY, DUMMY_SECRET)


def _spec(sid, **kw):
    s = copy.deepcopy(next(x for x in bundled() if x['id'] == sid))
    s.update(kw)
    return s


def _suite(tmp_path, specs, fb=None):
    w = World()
    if fb is not None:
        w.fb = fb(now_ms=w.fb.now)
    return run_recorded_suite(specs, lambda rec: w.target(recorder=rec), run_nonce='seg', cassette_dir=str(tmp_path),
                              redact=REDACT, monotonic=w.monotonic)


def _files(d):
    return sorted(os.listdir(d))


def _manifest(path):
    return json.load(open(path, encoding='utf-8'))


# ------------------------------------------------------------------------------------------- T04 at its real size

@pytest.fixture(scope='module')
def t04(tmp_path_factory):
    """T04-algo through the testnet seam on real-shaped bodies: the fake never reaches the stop, so the await_exit
    runs its full 30 cycles (31 with the entry), as the bound allows on testnet. One attempt."""
    d = tmp_path_factory.mktemp('t04')
    res, pre, errs = _suite(d, [_spec('T04-algo', attempts=1)], fb=RealShapedBinance)
    return d, res, pre, errs


def test_t04_algo_at_31_cycles_writes_its_cassette(t04):
    """Repro (fails on d591d88: errors [('T04-algo', 'CassetteLeak')], cassette None)."""
    d, res, pre, errs = t04
    (r,) = res.scenarios
    assert len(r.cycle_times) == 31 and r.verdict == 'INCONCLUSIVE'
    assert errs == [] and pre and r.cassette and r.cassette.endswith('tnet-seg-t04-algo.json')
    m = _manifest(r.cassette)
    assert m['format'] == SEGMENT_SET_FORMAT and len(m['segments']) >= 2
    # the per-audit caps are unchanged: segmentation, not a wider budget
    assert (C.MAX_AUDIT_RUNS, C.MAX_AUDIT_CHARS, C.MAX_DECODED_TOKENS) == (4_000_000, 128 * 1024 * 1024, 50_000)


def test_t04_segments_are_complete_ordered_and_each_passes_the_leak_audit_alone(t04):
    d, res, _, _ = t04
    (r,) = res.scenarios
    m = _manifest(r.cassette)
    n, first = len(m['segments']), 0
    for i, ent in enumerate(m['segments'], 1):
        text = open(os.path.join(str(d), ent['file']), encoding='utf-8').read()
        assert DUMMY_KEY not in text and DUMMY_SECRET not in text
        doc = json.loads(text)
        assert doc['segment'] == {'index': i, 'count': n, 'first': first} and ent['first'] == first
        assert len(doc['interactions']) == ent['interactions'] > 0
        assert leak_audit(doc) is None
        first += ent['interactions']
    assert first == m['interactions']
    assert _files(d) == sorted(['tnet-seg-preflight.json', 'tnet-seg-t04-algo.json', 'tnet-seg-t04-algo.meta.json']
                               + [e['file'] for e in m['segments']])


def test_t04_segmented_cassette_replays_to_the_recorded_verdict(t04):
    _, res, _, _ = t04
    (r,) = res.scenarios
    rr = replay(r.cassette)
    assert rr.same and rr.remaining == 0 and rr.replayed.verdict == r.verdict
    assert len(rr.replayed.cycle_times) == 31


# ------------------------------------------------------------------------------------------- recorder segmentation

def _junk_recorder(n, checkpoint_every=1, runs=40):
    """n interactions, each a body of `runs` base64-like runs; a checkpoint after every `checkpoint_every`."""
    body = json.dumps({'msg': ' '.join('QUJDREVGR0hJSktMTU5PUA' for _ in range(runs))})
    rec = CassetteRecorder(FakeHttp(*[ok(body) for _ in range(n)]), redact=REDACT)
    for i in range(n):
        rec(req())
        if (i + 1) % checkpoint_every == 0:
            rec.checkpoint()
    return rec


def test_one_segment_is_exactly_the_to_json_document():
    rec = _junk_recorder(4)
    parts = rec.to_segments()
    assert parts == [(rec.to_json(), 0, 4)] and 'segment' not in json.loads(parts[0][0])


def test_segments_close_only_at_checkpoints_and_cover_every_interaction(monkeypatch):
    monkeypatch.setattr(C, 'SEGMENT_CHARS', 1)               # every checkpoint range is its own segment
    rec = _junk_recorder(9, checkpoint_every=3)
    parts = rec.to_segments()
    assert [(f, n) for _, f, n in parts] == [(0, 3), (3, 3), (6, 3)]
    joined = [it for t, _, _ in parts for it in json.loads(t)['interactions']]
    assert joined == rec.interactions


def test_each_segment_has_its_own_unchanged_budget(monkeypatch):
    """The whole cassette passes one audit's run budget, every segment fits it: produced only when segmented; one
    segment that alone passes the budget is still refused (the cap per audit is never widened)."""
    monkeypatch.setattr(C, 'MAX_AUDIT_RUNS', 1000)
    rec = _junk_recorder(6, runs=40)                           # 40 runs x 8 attempts = 320 per interaction
    with pytest.raises(CassetteLeak, match='too many encoded runs'):
        rec.to_json()
    monkeypatch.setattr(C, 'SEGMENT_CHARS', 1)
    assert len(rec.to_segments()) == 6
    rec = _junk_recorder(6, checkpoint_every=6, runs=40)      # no checkpoint inside: one segment, refused
    with pytest.raises(CassetteLeak, match='too many encoded runs'):
        rec.to_segments()


def test_a_secret_in_any_one_segment_refuses_every_segment(monkeypatch, tmp_path):
    monkeypatch.setattr(C, 'SEGMENT_CHARS', 1)
    hidden = base64.b64encode(DUMMY_SECRET.encode()).decode()
    clean = ok(json.dumps({'msg': 'fine'}))
    rec = CassetteRecorder(FakeHttp(clean, clean, ok(json.dumps({'msg': 'x ' + hidden})), clean), redact=REDACT)
    for _ in range(4):
        rec(req())
        rec.checkpoint()
    with pytest.raises(CassetteLeak, match='encoded form'):
        rec.to_segments()
    with pytest.raises(CassetteLeak):
        save_bundle(rec, str(tmp_path / 'b'), {'format': 'x'}, REDACT)
    assert _files(tmp_path) == []


def test_a_value_learned_later_is_scrubbed_from_an_earlier_segment(monkeypatch):
    monkeypatch.setattr(C, 'SEGMENT_CHARS', 1)
    lk = 'LateListenKeyValue0123456789abcdef'
    rec = CassetteRecorder(FakeHttp(ok(json.dumps({'msg': 'echo ' + lk})), ok(json.dumps({'listenKey': lk}))),
                           redact=REDACT)
    rec(req())
    rec.checkpoint()
    rec(req())
    parts = rec.to_segments()
    assert len(parts) == 2 and not any(lk in t for t, _, _ in parts)


def test_more_segments_than_the_maximum_are_refused(monkeypatch):
    monkeypatch.setattr(C, 'SEGMENT_CHARS', 1)
    monkeypatch.setattr(C, 'MAX_SEGMENTS', 3)
    with pytest.raises(CassetteLeak, match='too many cassette segments'):
        _junk_recorder(4, runs=1).to_segments()


def test_the_whole_cassette_stays_under_the_document_cap(monkeypatch):
    monkeypatch.setattr(C, 'SEGMENT_CHARS', 1)
    rec = _junk_recorder(4, runs=1)
    total = sum(_doc_chars(it) for it in rec.interactions)
    monkeypatch.setattr(C, 'MAX_AUDIT_CHARS', total - 1)
    with pytest.raises(CassetteLeak, match='too much text'):
        rec.to_segments()


def test_a_segment_over_another_audit_cap_is_split_again_at_its_checkpoints(monkeypatch):
    """Codex P1 on beaf82d: six checkpointed dense interactions fit one SEGMENT_CHARS segment but pass a 1,000-run
    budget; packing by bytes alone refused them. Now that segment is split at its own checkpoints until each piece
    passes its unchanged budget; no checkpoint inside, or more than MAX_SEGMENTS pieces, is still a refusal."""
    monkeypatch.setattr(C, 'MAX_AUDIT_RUNS', 1000)
    rec = _junk_recorder(6, runs=40)                           # 320 runs per interaction; default SEGMENT_CHARS
    assert len(rec._segment_ranges()) == 1
    parts = rec.to_segments()
    assert 2 <= len(parts) <= C.MAX_SEGMENTS
    assert [it for t, _, _ in parts for it in json.loads(t)['interactions']] == rec.interactions
    for i, (t, f, n) in enumerate(parts, 1):
        assert json.loads(t)['segment'] == {'index': i, 'count': len(parts), 'first': f}
        assert n <= 3                                          # 3 x 320 = 960 runs fit, 4 x 320 do not
        leak_audit(json.loads(t))
    with pytest.raises(CassetteLeak, match='too many encoded runs'):
        _junk_recorder(6, checkpoint_every=6, runs=40).to_segments()
    monkeypatch.setattr(C, 'MAX_AUDIT_RUNS', 300)              # one interaction alone passes the budget
    with pytest.raises(CassetteLeak, match='too many encoded runs'):
        rec.to_segments()
    monkeypatch.setattr(C, 'MAX_AUDIT_RUNS', 1000)
    monkeypatch.setattr(C, 'MAX_SEGMENTS', 2)
    with pytest.raises(CassetteLeak, match='too many cassette segments'):
        rec.to_segments()


@pytest.mark.parametrize('cap', ['MAX_AUDIT_NODES', 'MAX_AUDIT_STRINGS'])
def test_segments_are_packed_against_the_structural_caps_too(monkeypatch, cap):
    rec = _junk_recorder(6, runs=1)
    b = C._AuditBudget()
    b.document(rec.interactions[:1])
    per = b.nodes if cap == 'MAX_AUDIT_NODES' else b.strings
    monkeypatch.setattr(C, cap, C._ENVELOPE_NODES + 2 * per)   # two interactions plus the envelope headroom
    assert rec._segment_ranges() == [(0, 2), (2, 4), (4, 6)]
    parts = rec.to_segments()
    assert [(f, n) for _, f, n in parts] == [(0, 2), (2, 2), (4, 2)]


def test_a_leak_is_never_answered_by_splitting(monkeypatch):
    calls = []
    real = CassetteRecorder._split
    monkeypatch.setattr(CassetteRecorder, '_split', lambda self, *a: calls.append(a) or real(self, *a))
    hidden = base64.b64encode(DUMMY_SECRET.encode()).decode()
    clean = ok(json.dumps({'msg': 'fine'}))
    rec = CassetteRecorder(FakeHttp(clean, ok(json.dumps({'msg': 'x ' + hidden})), clean), redact=REDACT)
    for _ in range(3):
        rec(req())
        rec.checkpoint()
    with pytest.raises(CassetteLeak, match='encoded form'):
        rec.to_segments()
    assert calls == []


def test_the_global_cap_counts_the_complete_segment_documents(monkeypatch):
    """Codex P2 on beaf82d: the global MAX_AUDIT_CHARS summed interaction slices only, so segment envelopes (format,
    provenance, header) were admitted above it. Now charged on the complete documents written: as the audit charges
    them and as their exact UTF-8 bytes."""
    monkeypatch.setattr(C, 'SEGMENT_CHARS', 1)
    rec = _junk_recorder(4, runs=1)
    parts = rec.to_segments()
    docs = [json.loads(t) for t, _, _ in parts]
    slices = sum(_doc_chars(it) for it in rec.interactions)
    charged = sum(_charged(d) for d in docs)
    written = sum(len(t.encode('utf-8')) for t, _, _ in parts)
    full = max(charged, written)
    assert full > slices
    monkeypatch.setattr(C, 'MAX_AUDIT_CHARS', full - 1)        # slices fit; the written documents do not
    with pytest.raises(CassetteLeak, match='too much text'):
        rec.to_segments()
    monkeypatch.setattr(C, 'MAX_AUDIT_CHARS', full)
    assert rec.to_segments() == parts
    for cap in (charged, written):                              # each total is enforced on its own
        if cap < full:
            monkeypatch.setattr(C, 'MAX_AUDIT_CHARS', cap)
            with pytest.raises(CassetteLeak, match='too much text'):
                rec.to_segments()


def test_the_global_cap_also_holds_the_conservative_charge_of_the_documents(monkeypatch):
    """Tabs are charged 6 bytes each but written as 2: the conservative charge of the complete documents is above
    their written bytes, and that total alone is refused."""
    monkeypatch.setattr(C, 'SEGMENT_CHARS', 1)
    rec = CassetteRecorder(FakeHttp(*[ok('a\tb' * 2000) for _ in range(3)]), redact=REDACT)
    for _ in range(3):
        rec(req())
        rec.checkpoint()
    parts = rec.to_segments()
    charged = sum(_charged(json.loads(t)) for t, _, _ in parts)
    assert len(parts) == 3 and charged > sum(len(t.encode('utf-8')) for t, _, _ in parts)
    monkeypatch.setattr(C, 'MAX_AUDIT_CHARS', charged - 1)
    with pytest.raises(CassetteLeak, match='too much text'):
        rec.to_segments()
    monkeypatch.setattr(C, 'MAX_AUDIT_CHARS', charged)
    assert rec.to_segments() == parts


def _charged(doc):
    b = C._AuditBudget()
    b.document(doc)
    return b.chars


def _doc_chars(obj):
    b = C._AuditBudget()
    b.document([obj])
    return b.chars - 4                                          # the one-element list wrapper


# ------------------------------------------------------------------------------------------- bundle write / replay

@pytest.fixture
def seg_bundle(tmp_path, monkeypatch):
    """T01-long on the plain fake, every checkpoint its own segment (cheap); returns its scenario result."""
    monkeypatch.setattr(C, 'SEGMENT_CHARS', 1)
    res, pre, errs = _suite(tmp_path, [_spec('T01-long')])
    (r,) = res.scenarios
    assert errs == [] and r.cassette
    return tmp_path, r


def test_segments_follow_the_cycle_and_teardown_checkpoints(seg_bundle):
    d, r = seg_bundle
    m = _manifest(r.cassette)
    # boot + one range per cycle + the teardown
    assert m['format'] == SEGMENT_SET_FORMAT and len(m['segments']) == len(r.cycle_times) + 2
    assert replay(r.cassette).same


def test_a_failed_segment_write_leaves_no_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(C, 'SEGMENT_CHARS', 1)
    rec = _junk_recorder(3, runs=1)
    real = RC._write

    def flaky(path, text):
        if path.endswith('.seg02.json'):
            raise OSError('disk full')
        return real(path, text)
    monkeypatch.setattr(RC, '_write', flaky)
    with pytest.raises(OSError):
        save_bundle(rec, str(tmp_path / 'b'), {'format': 'x'}, REDACT)
    assert _files(tmp_path) == []


def _tamper(r, fn):
    fn(os.path.dirname(r.cassette), _manifest(r.cassette))


def _rewrite_manifest(r, m):
    json.dump(m, open(r.cassette, 'w', encoding='utf-8'))


@pytest.mark.parametrize('case', ['missing', 'sha', 'swapped', 'extra', 'total', 'renamed', 'one_segment'])
def test_a_tampered_or_incomplete_segment_set_is_refused(seg_bundle, case):
    d, r = seg_bundle
    m = _manifest(r.cassette)
    seg = [os.path.join(str(d), e['file']) for e in m['segments']]
    if case == 'missing':
        os.remove(seg[1])
    elif case == 'sha':
        with open(seg[1], 'ab') as fh:
            fh.write(b' ')
    elif case == 'swapped':
        a, b = open(seg[0], 'rb').read(), open(seg[1], 'rb').read()
        open(seg[0], 'wb').write(b)
        open(seg[1], 'wb').write(a)
        m['segments'][0]['sha256'], m['segments'][1]['sha256'] = m['segments'][1]['sha256'], m['segments'][0]['sha256']
        _rewrite_manifest(r, m)
    elif case == 'extra':
        open(r.cassette[:-5] + f'.seg{len(seg) + 1:02d}.json', 'w').write('{}')
    elif case == 'total':
        m['interactions'] += 1
        _rewrite_manifest(r, m)
    elif case == 'renamed':
        m['segments'][0]['file'] = '../' + m['segments'][0]['file']
        _rewrite_manifest(r, m)
    elif case == 'one_segment':
        m['segments'] = m['segments'][:1]
        _rewrite_manifest(r, m)
    with pytest.raises(ReplayError):
        load_bundle(r.cassette)


def test_a_segment_with_a_sensitive_value_fails_the_leak_audit_on_replay(seg_bundle):
    import hashlib
    d, r = seg_bundle
    m = _manifest(r.cassette)
    p = os.path.join(str(d), m['segments'][1]['file'])
    doc = json.load(open(p, encoding='utf-8'))
    doc['interactions'][0]['request']['query'].append(['signature', 'abcdef0123456789abcdef'])
    text = json.dumps(doc)
    open(p, 'w', encoding='utf-8').write(text)
    m['segments'][1]['sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    _rewrite_manifest(r, m)
    with pytest.raises(ReplayError, match='segment 2 fails the leak audit'):
        load_bundle(r.cassette)
