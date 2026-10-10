"""Pre-freeze journals: an explicit pre-release FORMAT BREAK, fail-closed, no migration (Codex review #13).

NC-01 froze OrderIntent with `replaces_intent_id` (and owner_kind). A journal written before the freeze carries intent
records without it. No migration and no shim that guesses `replaces_intent_id` exists or will be added: the strict
schema-1 codec rejects such a record, the store's verdict is DAMAGED, the run never trades on it - it becomes the
tail-loss guard (protect proven own exposure, touch nothing else), the journal bytes are never rewritten, and the
operator archives the old journal and starts fresh (docs/newcore/slice/JOURNAL_FORMAT_BREAK.md)."""
import json

from newcore.domain.codec import Outcome, decode_result
from newcore.runner import app as A
from newcore.runner import config as C
from newcore.store.frame import KIND_SEGMENT, RT_EVENT, file_header, frame, scan
from test_run_cli import cfg_file, run


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k != 'replaces_intent_id'}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def _as_pre_freeze(d):
    """Rewrite every segment as a pre-freeze build wrote it: intent records without replaces_intent_id, valid frames
    (CRC-correct), so the only defect is the schema. Returns (segment bytes after the rewrite, records stripped)."""
    stripped = 0
    for seg in sorted(p for p in (d / 'journal').iterdir() if p.name.endswith('.seg')):
        s = scan(seg.read_bytes(), KIND_SEGMENT)
        out = bytearray(file_header(KIND_SEGMENT))
        for r in s.records:
            payload = r.payload
            if r.rtype == RT_EVENT and b'replaces_intent_id' in payload:
                payload = json.dumps(_strip(json.loads(payload)), sort_keys=True, separators=(',', ':')).encode()
                stripped += 1
            out += frame(r.rtype, payload)
        seg.write_bytes(bytes(out))
    return {p.name: p.read_bytes() for p in (d / 'journal').iterdir()}, stripped


def test_a_pre_freeze_intent_record_is_invalid_to_the_strict_codec(tmp_path):
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '27', '--enable-candidate'])[0] == 0
    d = tmp_path / 'nc' / C.load(cfg).account_id
    seg = sorted(p for p in (d / 'journal').iterdir() if p.name.endswith('.seg'))[-1]
    recs = [r for r in scan(seg.read_bytes(), KIND_SEGMENT).records
            if r.rtype == RT_EVENT and b'replaces_intent_id' in r.payload]
    assert recs
    for r in recs:
        old = json.dumps(_strip(json.loads(r.payload)), sort_keys=True, separators=(',', ':')).encode()
        res = decode_result(old)
        assert res.outcome is not Outcome.OK and 'replaces_intent_id' in str(res.error)   # never guessed


def test_a_pre_freeze_journal_fails_closed_to_the_guard_and_is_never_migrated(tmp_path):
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '27', '--enable-candidate'])[0] == 0      # an open BTC lot
    d = tmp_path / 'nc' / C.load(cfg).account_id
    before, stripped = _as_pre_freeze(d)
    assert stripped
    code, out = run(['run', '--config', cfg, '--cycles', '5', '--enable-candidate'])
    assert code == A.EXIT_STORE_HOLD and 'GUARD' in out and 'damaged' in out                  # HOLD, nothing traded
    after = {p.name: p.read_bytes() for p in (d / 'journal').iterdir()}
    assert {k: v for k, v in after.items() if k.endswith('.seg')} == \
        {k: v for k, v in before.items() if k.endswith('.seg')}                                # no migration, no write
    st = json.loads((d / A.STATE_FILE).read_text())
    assert not [o for o in st['orders'] if o['status'] == 'NEW' and not o['reduce']]           # no entry ever placed
    covered = sum(float(o['qty']) for o in st['orders'] if o['reduce'] and o['status'] == 'NEW'
                  and o['type'] == 'STOP_MARKET')
    assert covered >= sum(float(p[2]) for p in st['positions'])                                # own exposure protected
