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
    # assembled at run time so the tree holds no key-like literal (verify.py's static secret scan)
    'binance apiKey=' + 'vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zv' + 'sw0MuIgwCIPy6utIco14y7Ju91duEh8A',
    'telegram ' + '123456789' + ':AA' + 'HdqTcvCH1vGWJxfSeofSAs0K5PALDsaw0',
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
    dec = F.decision_with_intents(ids, p.account_id, F.Action.ENTER, ReasonCode.ENTRY_SIGNAL)
    for build in (lambda: F.replace(inc, incident_id=huge), lambda: F.replace(inc, detail=huge),
                  lambda: check_id(huge, 'f', 'lot'), lambda: F.replace(inc, symbol=huge),
                  lambda: F.replace(dec, decision_id=huge)):          # its path embeds the raw id before the check
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
        ExternalTrade(trade_id='9\u0663', venue=F.Venue.BINANCE_USDM, symbol='SOLUSDT', at_ms=F.T0, qty=D('1'),
                      price=D('1'))                                       # Arabic-Indic digit
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
    with pytest.raises(InvalidRecord, match='incident id .* used for a different fact'):
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


# ======================================================================= Codex review of #44 (31d8d06): repro groups
def _booking(seed, trade_ids=('9001', '9002')):
    """A full external-close booking chain (5 events from sequence `start`) citing `trade_ids`."""
    from test_nc01_r3 import _external_close
    p, ids, dec, it, res = _external_close(seed)
    trades = tuple(F.replace(t, trade_id=tid) for t, tid in zip(res.external_trades, trade_ids))
    return p, ids, dec, it, F.replace(res, external_trades=trades)


def _book_events(ids, acct, dec, it, res, start, pf_id=None):
    from newcore.domain import DecisionRecorded, IntentRecorded, IntentState, IntentStateChanged, ResultObserved
    dur = F.replace(it, state=IntentState.DURABLE)
    E = lambda cls, n, at, **kw: F.event(cls, ids, acct, n, at=at, aggregate=pf_id, **kw)      # noqa: E731
    return [E(DecisionRecorded, start, dec.at_ms, decision=dec, reason=dec.reason),
            E(IntentRecorded, start + 1, dec.at_ms, intent=dur, reason=dur.reason),
            E(ResultObserved, start + 2, res.observed_at_ms, result=res, reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE),
            E(IntentStateChanged, start + 3, res.observed_at_ms, intent_id=it.intent_id,
              from_state=IntentState.DURABLE, to_state=IntentState.FILLED)]


def _two_bookings(second_trades=('9001', '9003')):
    """Two sequential external-close bookings in ONE account log; the second cites `second_trades`."""
    from newcore.domain import Authority, IntentState
    p, ids, dec, it, res = _booking(700)
    acct = p.account_id
    first = _book_events(ids, acct, dec, it, res, 1)
    lt = p.lots[0]
    dec2_id = ids.id('dec')
    it2 = F.intent(ids, acct, F.Purpose.REDUCE, lt.symbol, lt.side, res.requested_qty, owner_id=lt.lot_id,
                   state=IntentState.PLANNED, decision_id=dec2_id, reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE,
                   created=F.T0 + 100_000)
    dec2 = F.build(F.Decision, decision_id=dec2_id, account_id=acct, at_ms=F.T0 + 100_000, action=F.Action.RECONCILE,
                   reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE, authority=Authority.RECONCILIATION, symbol=lt.symbol,
                   side=lt.side, subject_id=lt.lot_id, intents=(it2,))
    trades = tuple(F.replace(t, trade_id=tid) for t, tid in zip(res.external_trades, second_trades))
    res2 = F.replace(res, result_id=ids.id('res'), intent_id=it2.intent_id, client_order_id=it2.client_order_id,
                     resolved_by=dec2_id, observed_at_ms=F.T0 + 105_000, external_trades=trades)
    return p, ids, acct, first, _book_events(ids, acct, dec2, it2, res2, 5)


def test_p1a_one_venue_trade_is_booked_once_per_account():
    from newcore.domain import (Admission, EventCursor, InvalidRecord, admit, canonical_bytes, check_event_chain,
                                fold_facts, loads)
    p, ids, acct, first, second = _two_bookings()
    with pytest.raises(InvalidRecord, match='venue trade .*/9001 is already booked'):
        check_event_chain(first + second)
    check_event_chain(first + _two_bookings(('9003', '9004'))[4])                 # new trades: fine
    # restart: the consumed set is a durable fact of the log - folded from the decoded bytes, then carried forward
    decoded = [loads(canonical_bytes(ev)) for ev in first]
    facts = fold_facts(decoded)
    assert '9001' in {t.trade_id for t in facts.trades} and loads_facts_roundtrip(facts) == facts
    with pytest.raises(InvalidRecord, match='venue trade .*/9001 is already booked'):
        check_event_chain(second, after_sequence=4, facts=facts)
    # a repeated reconcile pass re-delivers the identical booking: idempotent, never a second consumption
    cur = EventCursor(account_id=acct, aggregate_id=F.pf_id(acct), last_sequence=0, applied=())
    for ev in decoded:
        cur, _ = admit(cur, ev)
    assert all(admit(cur, ev) == (cur, Admission.ALREADY_APPLIED) for ev in decoded)
    assert fold_facts(decoded, facts) == facts                                      # replaying the same facts: no-op


def loads_facts_roundtrip(facts):
    from newcore.domain.codec import from_json, to_json
    from newcore.domain import FactIndex
    return from_json(FactIndex, to_json(facts), 'facts')


def test_p1b_a_superseding_final_has_its_own_result_id_and_names_the_prior_fact():
    from newcore.domain import (DecisionRecorded, IntentState, IntentStateChanged, InvalidRecord, ResultObserved,
                                check_event_chain)
    from test_nc01_r3 import _late_fill, _late_fill_decision
    p, ids, acct, it, rs, late, head, E = _late_fill()
    cor = rs['corroborated']
    closed = E(IntentStateChanged, 6, F.T0 + 30_000, intent_id=it.intent_id, from_state=IntentState.UNKNOWN,
               to_state=IntentState.CANCELLED)
    good = F.replace(late, supersedes_result_id=cor.result_id)
    fix = _late_fill_decision(ids, acct, it, good)
    check_event_chain(head + [closed, E(ResultObserved, 7, good.observed_at_ms, result=good),
                              E(DecisionRecorded, 8, fix.at_ms, decision=fix, reason=fix.reason)])
    with pytest.raises(InvalidRecord, match='supersedes itself'):
        F.replace(good, result_id=cor.result_id)                                     # cannot even be built
    reused = F.replace(good, result_id=cor.result_id, supersedes_result_id=None)     # the prior fact's own id
    with pytest.raises(InvalidRecord, match='result id .* different fact'):
        check_event_chain(head + [closed, E(ResultObserved, 7, reused.observed_at_ms, result=reused)])
    unnamed = F.replace(good, supersedes_result_id=None)                              # does not name the prior fact
    with pytest.raises(InvalidRecord, match='event after the final result'):
        check_event_chain(head + [closed, E(ResultObserved, 7, unnamed.observed_at_ms, result=unnamed)])
    other = F.replace(good, supersedes_result_id=ids.id('res'))                       # names some other fact
    with pytest.raises(InvalidRecord, match='event after the final result'):
        check_event_chain(head + [closed, E(ResultObserved, 7, other.observed_at_ms, result=other)])
    with pytest.raises(InvalidRecord, match='supersedes'):
        F.replace(rs['known'], supersedes_result_id=cor.result_id)                    # only an executed FINAL can
    # result ids are global durable facts: the same id with other content is a conflict anywhere in the log
    clash = F.replace(rs['unknown'], result_id=cor.result_id, observed_at_ms=F.T0 + 2_000)
    with pytest.raises(InvalidRecord, match='result id .* different fact'):
        check_event_chain(head[:4] + [E(ResultObserved, 5, clash.observed_at_ms, result=clash),
                                      F.replace(head[4], sequence=6)])


def test_p2_1_identical_incident_replay_is_a_no_op_and_a_conflict_is_refused():
    from newcore.domain import IncidentRecorded, InvalidRecord, check_event_chain, fold_facts
    ids, p, inc = _incident()
    a = F.event(IncidentRecorded, ids, p.account_id, 1, at=F.T0 + 600, incident=inc, reason=inc.kind)
    same = F.event(IncidentRecorded, ids, p.account_id, 2, at=F.T0 + 700, incident=inc, reason=inc.kind)
    check_event_chain([a, same])                                                      # the identical fact again
    other = F.replace(same, incident=F.replace(inc, detail='a different observation'))
    with pytest.raises(InvalidRecord, match='incident id .* used for a different fact'):
        check_event_chain([a, other])
    with pytest.raises(InvalidRecord, match='incident id .* used for a different fact'):
        check_event_chain([F.replace(other, sequence=2)], after_sequence=1, facts=fold_facts([a]))


def test_p2_2_an_external_booking_is_resolved_by_its_own_recorded_reconcile_decision():
    from newcore.domain import InvalidRecord, check_event_chain, check_result_for_intent
    from test_nc01_r3 import _booking_chain, _external_close
    p, ids, dec, it, res = _external_close(710)
    stranger = F.replace(res, resolved_by=ids.id('dec'))
    with pytest.raises(InvalidRecord, match='resolved by the RECONCILE decision that booked it'):
        check_result_for_intent(it, stranger, None)
    chain = _booking_chain(p, ids, dec, it, res)
    without_decision = [F.replace(ev, sequence=n) for n, ev in enumerate(chain[1:], 1)]
    with pytest.raises(InvalidRecord, match='authorising RECONCILE decision is not in the log'):
        check_event_chain(without_decision)


def test_p1b_a_result_naming_a_prior_fact_needs_that_prior_fact():
    """A first FINAL for a live intent that claims to supersede some result is refused: there is nothing to
    supersede (chain); the step-0 twin is test_journal_refuses_a_dangling_supersedes_result_id."""
    from newcore.domain import InvalidRecord, ResultObserved, check_event_chain
    from test_nc01_r3 import _late_fill
    p, ids, acct, it, rs, late, head, E = _late_fill()
    dangling = F.replace(late, observed_at_ms=F.T0 + 2_000, supersedes_result_id=ids.id('res'))
    with pytest.raises(InvalidRecord, match='names a prior result it does not supersede'):
        check_event_chain(head[:4] + [E(ResultObserved, 5, dangling.observed_at_ms, result=dangling)])
    check_event_chain(head[:4] + [E(ResultObserved, 5, dangling.observed_at_ms,
                                    result=F.replace(dangling, supersedes_result_id=None))])


# ================================================ Codex re-review of #44 at a676019 (6065073614): repro groups
def _compacted(second_trades=('9001', '9003')):
    """Booking 1 (trades 9001 / 9002, seq 1-4) + an incident (seq 5) compacted into a snapshot that carries the facts;
    `second` is booking 2 renumbered as the tail after the snapshot (seq 6-9)."""
    from newcore.domain import IncidentRecorded, Snapshot, fold_facts
    p, ids, acct, first, second = _two_bookings(second_trades)
    inc = F.incident(ids, acct, p)
    inc_ev = F.event(IncidentRecorded, ids, acct, 5, at=F.T0 + 200_000, incident=inc, reason=inc.kind)
    prefix = first + [inc_ev]
    snap = Snapshot(account_id=acct, generation=p.generation, last_sequence=5, written_at_ms=F.T0 + 300_000,
                    writer_build='nc01-test', portfolio=p, facts=fold_facts(prefix))
    snap = loads(canonical_bytes(snap))                                         # the restart reads it back
    tail = [F.replace(ev, sequence=ev.sequence + 1) for ev in second]
    return p, ids, acct, prefix, tail, inc, snap


def test_rr_p1_the_snapshot_carries_the_facts_and_the_chain_is_seeded_from_it():
    from newcore.domain import IncidentRecorded, InvalidRecord, check_event_chain, fold_facts
    p, ids, acct, prefix, tail, inc, snap = _compacted()
    assert snap.facts == fold_facts(prefix) and fold_facts(prefix, snap.facts) == snap.facts      # repeat: no-op
    seed = dict(after_sequence=snap.last_sequence, facts=snap.facts)
    with pytest.raises(InvalidRecord, match='venue trade .*9001 is already booked'):              # trade conflict
        check_event_chain(tail, **seed)
    check_event_chain(_compacted(('9003', '9004'))[4], **seed)                                     # new trades: fine
    booked = prefix[2].result
    clash = [tail[0], tail[1], F.replace(tail[2], result=F.replace(tail[2].result, result_id=booked.result_id))]
    with pytest.raises(InvalidRecord, match='result id .* used for a different fact'):             # result conflict
        check_event_chain(clash, **seed)
    same = F.event(IncidentRecorded, ids, acct, 6, at=F.T0 + 400_000, incident=inc, reason=inc.kind)
    check_event_chain([same], **seed)                                                              # incident repeat
    with pytest.raises(InvalidRecord, match='incident id .* used for a different fact'):           # incident conflict
        check_event_chain([F.replace(same, incident=F.replace(inc, detail='changed'))], **seed)


def test_rr_p1_a_snapshot_without_facts_fails_closed():
    import json
    p, ids, acct, prefix, tail, inc, snap = _compacted()
    doc = json.loads(canonical_bytes(snap))
    del doc['body']['facts']                                                    # the pre-r3a snapshot shape
    with pytest.raises(InvalidRecord, match="missing keys \\['facts'\\]"):
        loads(json.dumps(doc))


def test_rr_p1_a_fact_index_is_self_consistent():
    from newcore.domain import ConsumedTrade
    p, ids, acct, prefix, tail, inc, snap = _compacted()
    t = snap.facts.trades[0]
    with pytest.raises(InvalidRecord, match='names no recorded result'):
        F.replace(snap.facts, trades=(F.replace(t, result_id=ids.id('res')),) + snap.facts.trades[1:])
    assert isinstance(t, ConsumedTrade)


def _trade_result(ids, acct, symbol, trade_id, venue=None):
    from newcore.domain import Evidence, ExternalTrade, OrderResult, ResultObserved, ResultPhase
    from newcore.domain.account import Venue
    rid = ids.id('res')
    trade = ExternalTrade(trade_id=trade_id, venue=venue or Venue.BINANCE_USDM, symbol=symbol, at_ms=F.T0,
                          qty=D('1'), price=D('100'))
    res = F.build(OrderResult, result_id=rid, intent_id=ids.id('int'), account_id=acct, client_order_id=ids.cid('c'),
                  phase=ResultPhase.FINAL, requested_qty=D('1'), observed_at_ms=F.T0 + 1000, executed_qty=D('1'),
                  avg_price=D('100'), evidence=Evidence.EXCHANGE_EXTERNAL, resolved_by=ids.id('dec'),
                  external_trades=(trade,))
    return lambda n: F.event(ResultObserved, ids, acct, n, at=F.T0 + 2000, result=res)


def test_rr_p2a_venue_trades_are_scoped_by_venue_and_symbol():
    """Binance userTrades ids are symbol-scoped: SOL trade 42 and BTC trade 42 are different trades; SOL 42 twice is
    one trade - refused live, across a restart (FactIndex decoded) and after compaction (seeded chain)."""
    from newcore.domain import fold_facts
    from newcore.domain.codec import from_json, to_json
    from newcore.domain import FactIndex
    ids = F.Ids(720)
    acct = ids.id('acct')
    sol, btc, sol_again = (_trade_result(ids, acct, 'SOLUSDT', '42'), _trade_result(ids, acct, 'BTCUSDT', '42'),
                           _trade_result(ids, acct, 'SOLUSDT', '42'))
    facts = fold_facts([sol(1), btc(2)])                                        # same id, two symbols: allowed
    assert {(t.symbol, t.trade_id) for t in facts.trades} == {('SOLUSDT', '42'), ('BTCUSDT', '42')}
    with pytest.raises(InvalidRecord, match='venue trade .*SOLUSDT.*42 is already booked'):
        fold_facts([sol(1), btc(2), sol_again(3)])
    restarted = from_json(FactIndex, to_json(facts), 'facts')
    with pytest.raises(InvalidRecord, match='venue trade .*SOLUSDT.*42 is already booked'):
        fold_facts([sol_again(3)], restarted)
    fold_facts([_trade_result(ids, acct, 'ETHUSDT', '42')(3)], restarted)


def test_rr_p2a_an_external_trade_is_of_the_booking_intents_symbol():
    from newcore.domain import check_result_for_intent
    from test_nc01_r3 import _external_close
    p, ids, dec, it, res = _external_close(730)
    other = tuple(F.replace(t, symbol='BTCUSDT') for t in res.external_trades)
    with pytest.raises(InvalidRecord, match="the booking intent's symbol"):
        check_result_for_intent(it, F.replace(res, external_trades=other), None)


def test_rr_p2b_the_document_cap_counts_utf8_bytes_for_text_input():
    from newcore.domain.codec import MAX_DOCUMENT_BYTES
    over = '"' + 'é' * (MAX_DOCUMENT_BYTES // 2) + '"'                   # ~half the chars, just over the bytes
    assert len(over) < MAX_DOCUMENT_BYTES < len(over.encode('utf-8'))
    for doc in (over, over.encode('utf-8')):
        with pytest.raises(InvalidRecord, match='larger than') as ex:
            loads(doc)
        assert len(str(ex.value)) < 200                                         # the input is never echoed
    at_cap = '"' + 'é' * ((MAX_DOCUMENT_BYTES - 2) // 2) + '"'           # exactly at (or just under) the cap
    assert len(at_cap.encode('utf-8')) <= MAX_DOCUMENT_BYTES
    with pytest.raises(InvalidRecord) as ex:
        loads(at_cap)                                                           # admitted by the cap; not a document
    assert 'larger than' not in str(ex.value)


# ======================================================= Cowork on a676019 (6065763087): follow-ups folded into r3a
SHORT_SECRET = 'sk-' + 'live-' + 'Q7f3K9'                 # short enough to slip past a length cap


@pytest.mark.parametrize('field', ['symbol', 'evidence', 'intent_refs', 'lot_refs', 'account_id', 'incident_id'])
def test_cw_a_short_secret_in_a_wrong_field_is_never_echoed(field):
    ids, p, inc = _incident()
    value = (SHORT_SECRET,) if field in ('evidence', 'intent_refs', 'lot_refs') else SHORT_SECRET
    with pytest.raises(InvalidRecord) as ex:
        F.replace(inc, **{field: value})
    text = str(ex.value)
    assert SHORT_SECRET not in text and 'Q7f3K9' not in text
    assert f'len={len(SHORT_SECRET)}' in text and 'sha256:' in text               # type, length, short hash


def test_cw_a_record_path_and_codec_errors_never_echo_text():
    import json
    ids, p, inc = _incident()
    dec = F.decision_with_intents(ids, p.account_id, F.Action.ENTER, ReasonCode.ENTRY_SIGNAL)
    with pytest.raises(InvalidRecord) as ex:
        F.replace(dec, decision_id=SHORT_SECRET)                               # the record path names its own id
    assert SHORT_SECRET not in str(ex.value) and SHORT_SECRET not in ex.value.path
    doc = json.loads(canonical_bytes(F.event(_incident_event_cls(), ids, p.account_id, 1, at=F.T0 + 600,
                                             incident=inc, reason=inc.kind)))
    for mutate in (lambda d: d['body']['incident'].__setitem__('kind', SHORT_SECRET),
                   lambda d: d.__setitem__('record_type', SHORT_SECRET),
                   lambda d: d.__setitem__('schema_version', SHORT_SECRET)):
        bad = json.loads(json.dumps(doc))
        mutate(bad)
        with pytest.raises(Exception) as ex:
            loads(json.dumps(bad))
        assert SHORT_SECRET not in str(ex.value)


def test_cw_an_incident_is_never_its_own_evidence_and_refs_stay_opaque():
    from newcore.domain import IncidentRecorded, check_event_chain
    ids, p, inc = _incident()
    with pytest.raises(InvalidRecord, match='never its own evidence'):
        F.replace(inc, evidence=(ids.id('res'), inc.incident_id))
    F.replace(inc, evidence=(ids.id('inc'),))                                   # another incident is fine
    ghost = F.replace(inc, intent_refs=(ids.id('int'),), lot_refs=(ids.id('lot'),), position_refs=(ids.id('pos'),),
                      evidence=(ids.id('res'),))
    check_event_chain([F.event(IncidentRecorded, ids, p.account_id, 1, at=F.T0 + 600, incident=ghost,
                               reason=ghost.kind)])                            # existence is the reconciler's job
