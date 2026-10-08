"""Cowork defects on PR #44 (NC-01 r3a): bounded incidents, safe display text, bounded error text, canonical text,
unique incident ids. Each test reproduces the reported defect first (it failed on 31d8d06)."""
from decimal import Decimal as D

import pytest

import nc01_factories as F
from newcore.domain import InvalidRecord, ReasonCode, canonical_bytes, loads

NEWCORE_FRAME_LIMIT = 16 * 1024 * 1024         # NC-02a frame.MAX_RECORD (newcore/store/frame.py on nc-02a-journal)


def _incident(seed=600, **kw):
    ids = F.Ids(seed)
    p, _ = F.single_lot_portfolio(seed, stop_state='confirmed', in_flight='close')
    return ids, p, F.incident(ids, p.account_id, p, **kw)


# ----------------------------------------------------------------------------------------------------------- 1 (MED)
def test_1_incident_reference_tuples_are_capped():
    from newcore.domain.incident import MAX_REFS
    ids, p, inc = _incident()
    many = tuple(ids.id('int') for _ in range(MAX_REFS + 1))
    for field in ('intent_refs', 'lot_refs', 'position_refs', 'evidence'):
        prefix = {'intent_refs': 'int', 'lot_refs': 'lot', 'position_refs': 'pos', 'evidence': 'res'}[field]
        refs = tuple(ids.id(prefix) for _ in range(MAX_REFS + 1))
        with pytest.raises(InvalidRecord, match=f'at most {MAX_REFS} references'):
            F.replace(inc, **{field: refs})
        F.replace(inc, **{field: refs[:MAX_REFS]})                                 # the cap itself is valid
    assert len(many) == MAX_REFS + 1


def test_1_an_oversized_document_is_a_typed_rejection_not_a_bare_value_error():
    from newcore.domain.codec import MAX_DOCUMENT_BYTES
    ids, p, inc = _incident()
    ev = F.event(_incident_event_cls(), ids, p.account_id, 1, at=F.T0 + 600, incident=inc, reason=inc.kind)
    raw = canonical_bytes(ev)
    ref = b'"res_' + b'0' * 32 + b'"'
    n = (17 * 1024 * 1024) // (len(ref) + 1)
    huge = raw.replace(b'"evidence":[', b'"evidence":[' + b','.join([ref] * n) + b',', 1)   # 17 MiB of refs
    assert len(huge) > 17 * 1024 * 1024
    for doc in (huge, huge.decode('ascii')):
        with pytest.raises(InvalidRecord, match='larger than'):
            loads(doc)
    assert MAX_DOCUMENT_BYTES < NEWCORE_FRAME_LIMIT // 2


def _incident_event_cls():
    from newcore.domain import IncidentRecorded
    return IncidentRecorded


def test_1_the_largest_valid_incident_frames_well_under_the_journal_limit():
    from newcore.domain import IncidentRecorded
    from newcore.domain.codec import MAX_DOCUMENT_BYTES
    from newcore.domain.incident import MAX_DETAIL, MAX_REFS
    ids, p, inc = _incident()
    big = F.replace(inc, intent_refs=tuple(ids.id('int') for _ in range(MAX_REFS)),
                    lot_refs=tuple(ids.id('lot') for _ in range(MAX_REFS)),
                    position_refs=tuple(ids.id('pos') for _ in range(MAX_REFS)),
                    evidence=tuple(ids.id('res') for _ in range(MAX_REFS)), detail=('ab ' * MAX_DETAIL)[:MAX_DETAIL])
    ev = F.event(IncidentRecorded, ids, p.account_id, 1, at=F.T0 + 600, incident=big, reason=big.kind)
    raw = canonical_bytes(ev)
    assert len(raw) < 16 * 1024 < MAX_DOCUMENT_BYTES < NEWCORE_FRAME_LIMIT
    assert loads(raw) == ev


# ----------------------------------------------------------------------------------------------------------- 2
@pytest.mark.parametrize('detail', [
    'binance apiKey=vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A',
    'telegram 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw0',
    'secret: c2VjcmV0LXZhbHVlLXRoYXQtaXMtbG9uZw==',
])
def test_2_a_secret_shaped_token_in_detail_is_refused(detail):
    ids, p, inc = _incident()
    with pytest.raises(InvalidRecord, match='key-shaped token'):
        F.replace(inc, detail=detail)


def test_2_ordinary_detail_text_is_kept_verbatim():
    ids, p, inc = _incident()
    text = 'venue flat, journal 1.5 open (SOLUSDT LONG); 2 trades, last 1791400081000'
    assert F.replace(inc, detail=text).detail == text


# ----------------------------------------------------------------------------------------------------------- 3
def test_3_error_text_never_echoes_a_huge_value():
    from newcore.domain import Decision
    from newcore.domain.base import check_id
    huge = 'x' * (10 * 1024 * 1024)
    ids, p, inc = _incident()
    for build in (lambda: F.replace(inc, incident_id=huge), lambda: F.replace(inc, detail=huge),
                  lambda: check_id(huge, 'f', 'lot'), lambda: F.replace(inc, symbol=huge)):
        with pytest.raises(InvalidRecord) as ex:
            build()
        assert len(str(ex.value)) < 1000 and len(ex.value.path) < 400
    doc = canonical_bytes(F.event(_incident_event_cls(), ids, p.account_id, 1, at=F.T0 + 600, incident=inc,
                                  reason=inc.kind))
    for bad in (doc.replace(b'"kind":"reconcile.manual_close"', b'"kind":"' + b'y' * 1_000_000 + b'"'),
                doc.replace(b'"detail":', b'"' + b'k' * 1_000_000 + b'":1,"detail":')):
        with pytest.raises(InvalidRecord) as ex:
            loads(bad)
        assert len(str(ex.value)) < 1000
    assert Decision is not None


# ----------------------------------------------------------------------------------------------------------- 4
@pytest.mark.parametrize('blank', ['', ' ', '   \t'])
def test_4_blank_detail_is_refused_and_absent_is_none(blank):
    ids, p, inc = _incident()
    with pytest.raises(InvalidRecord):
        F.replace(inc, detail=blank)
    assert F.replace(inc, detail=None).detail is None


# ----------------------------------------------------------------------------------------------------------- 5
def test_5_one_canonical_spelling_per_text():
    """NFD (decomposed) text is refused where text is free; identifier-like and incident text is ASCII, so homoglyphs
    (Cyrillic a for Latin a) cannot make a second spelling of the same visible value."""
    from newcore.domain import ExternalTrade
    ids, p, inc = _incident()
    nfc, nfd = 'Café', 'Café'
    homoglyph = 'pаuse'                                   # Cyrillic small a
    for text in (nfd, nfc, homoglyph):
        with pytest.raises(InvalidRecord, match='printable ASCII'):
            F.replace(inc, detail=f'note {text}')
    with pytest.raises(InvalidRecord, match='printable ASCII'):
        ExternalTrade(trade_id='9٣', at_ms=F.T0, qty=D('1'), price=D('1'))       # Arabic-Indic digit
    lt = p.lots[0]
    with pytest.raises(InvalidRecord, match='NFC'):
        F.replace(F.intent(ids, p.account_id, F.Purpose.ENTRY), slot_id=nfd)
    F.replace(F.intent(ids, p.account_id, F.Purpose.ENTRY), slot_id=nfc)                  # NFC free text is fine
    dec = F.decision_with_intents(ids, p.account_id, F.Action.ENTER, ReasonCode.ENTRY_SIGNAL)
    with pytest.raises(InvalidRecord, match='NFC'):
        F.replace(dec, detail=nfd)
    assert F.replace(dec, detail=nfc).detail == nfc and lt is not None


# ----------------------------------------------------------------------------------------------------------- 6
def test_6_an_incident_id_is_used_once_in_the_log():
    from newcore.domain import IncidentRecorded, check_event_chain
    ids, p, inc = _incident()
    a = F.event(IncidentRecorded, ids, p.account_id, 1, at=F.T0 + 600, incident=inc, reason=inc.kind)
    b = F.event(IncidentRecorded, ids, p.account_id, 2, at=F.T0 + 700,
                incident=F.replace(inc, detail='a second observation'), reason=inc.kind)
    with pytest.raises(InvalidRecord, match='incident id used twice'):
        check_event_chain([a, b])
    check_event_chain([a, F.replace(b, incident=F.replace(b.incident, incident_id=ids.id('inc')))])


def test_6_refs_are_well_formed_ids_of_their_kind():
    ids, p, inc = _incident()
    for field, wrong in (('intent_refs', 'lot'), ('lot_refs', 'int'), ('position_refs', 'lot'),
                         ('evidence', 'acct'), ('evidence', 'pos')):
        with pytest.raises(InvalidRecord, match='id'):
            F.replace(inc, **{field: (ids.id(wrong),)})
            raise AssertionError((field, wrong))
    with pytest.raises(InvalidRecord):
        F.replace(inc, intent_refs=('int_' + 'G' * 32,))
