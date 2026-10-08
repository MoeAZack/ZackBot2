"""Step-0 JournalPort grammar (G1-G9) and the consumed-signal rule (STEP0_INTERFACE.md sections 2-3)."""
import dataclasses
import hashlib

import pytest

from newcore.ports import journal as J
from newcore.ports import keys as K
from newcore.ports.journal import Admission, EventHeader, EventKind as E, GrammarError, ResultOutcome as R
from newcore.ports.values import PortValueError

ACCT = 'acct_' + '1' * 32
PF = 'pf_' + '2' * 32
KEY = K.DecisionKey(strategy='trend_ema_mom@4h', strategy_version='v1', symbol='SOLUSDT', side='LONG',
                    candle_close_ms=1759924800000, purpose='entry')
DEC = K.derive_decision_id(ACCT, KEY)
ENTRY = K.derive_intent_id(ACCT, KEY)
STOP = K.derive_child_intent_id(ACCT, ENTRY, 'protect', 0)
MGMT_DEC = 'dec_' + '3' * 32          # an unkeyed (management) decision: caller-supplied id


class Log:
    """Builds a header stream with consecutive sequences and distinct event ids / digests."""

    def __init__(self):
        self.headers = []

    def add(self, kind, **kw):
        n = len(self.headers) + 1
        h = EventHeader(kind=kind, event_id=f'evt_{n:032x}', account_id=ACCT, aggregate_id=PF, sequence=n,
                        at_ms=1759924800000 + n, digest=hashlib.sha256(f'{n}{kind}{kw}'.encode()).hexdigest(), **kw)
        self.headers.append(h)
        return self

    def entry_round_trip(self):
        return (self.add(E.DECISION_RECORDED, decision_id=DEC, decision_key=KEY)
                .add(E.INTENT_RECORDED, decision_id=DEC, intent_id=ENTRY, purpose='entry',
                     client_ids=(K.client_id_for(ENTRY),))
                .add(E.SENT, intent_id=ENTRY)
                .add(E.RESULT_RECORDED, intent_id=ENTRY, outcome=R.FINAL, evidence='exchange_final')
                .add(E.INTENT_CLOSED, intent_id=ENTRY, to_state='filled')
                .add(E.DECISION_RECORDED, decision_id=MGMT_DEC)
                .add(E.INTENT_RECORDED, decision_id=MGMT_DEC, intent_id=STOP, purpose='protect',
                     client_ids=(K.client_id_for(STOP), K.client_id_for(STOP, 'algo')))
                .add(E.SENT, intent_id=STOP)
                .add(E.STATE_CHANGED, intent_id=STOP, to_state='working'))


def replay(headers):
    return J.replay(ACCT, PF, headers)


def with_(h, **kw):
    return dataclasses.replace(h, **kw)


# ------------------------------------------------------------------------------------------------ good sequences
def test_entry_fill_then_protect_is_accepted():
    g = replay(Log().entry_round_trip().headers)
    assert g.last_sequence == 9 and g.is_consumed(KEY)


def test_lost_answer_unknown_not_found_then_final_is_accepted():
    log = Log().add(E.DECISION_RECORDED, decision_id=DEC, decision_key=KEY).add(
        E.INTENT_RECORDED, decision_id=DEC, intent_id=ENTRY, purpose='entry', client_ids=(K.client_id_for(ENTRY),))
    log.add(E.SENT, intent_id=ENTRY).add(E.RESULT_RECORDED, intent_id=ENTRY, outcome=R.UNKNOWN)
    log.add(E.STATE_CHANGED, intent_id=ENTRY, to_state='unknown')
    log.add(E.RESULT_RECORDED, intent_id=ENTRY, outcome=R.NOT_FOUND)
    log.add(E.MODE_CHANGED).add(E.INCIDENT_RECORDED)
    log.add(E.RESULT_RECORDED, intent_id=ENTRY, outcome=R.FINAL, evidence='not_found_corroborated')
    log.add(E.INTENT_CLOSED, intent_id=ENTRY, to_state='cancelled').add(E.BINDING_CHANGED)
    assert replay(log.headers).last_sequence == 11


def test_never_sent_intent_ends_not_sent():
    log = Log().add(E.DECISION_RECORDED, decision_id=DEC, decision_key=KEY).add(
        E.INTENT_RECORDED, decision_id=DEC, intent_id=ENTRY, purpose='entry', client_ids=(K.client_id_for(ENTRY),))
    log.add(E.STATE_CHANGED, intent_id=ENTRY, to_state='cancelling')
    log.add(E.RESULT_RECORDED, intent_id=ENTRY, outcome=R.FINAL, evidence='not_sent')
    log.add(E.INTENT_CLOSED, intent_id=ENTRY, to_state='not_sent')
    replay(log.headers)


def test_idempotent_reapply_is_a_no_op():
    hs = Log().entry_round_trip().headers
    g = replay(hs)
    for h in hs:
        assert g.admit(h) is Admission.ALREADY_APPLIED
    assert g.last_sequence == len(hs)


# ------------------------------------------------------------------------------------------------ bad sequences
def bad(headers, match):
    with pytest.raises(GrammarError, match=match):
        replay(headers)


def test_out_of_order_and_gap_are_rejected():
    hs = Log().entry_round_trip().headers
    bad([hs[1], hs[0]] + hs[2:], 'G2')                 # reorder
    bad(hs[:2] + hs[3:], 'G2')                          # gap
    bad(hs[:3] + [with_(hs[3], event_id='evt_' + 'f' * 32, sequence=3)], 'G2')   # sequence reused by another event


def test_duplicate_event_id_with_other_bytes_is_a_conflict():
    hs = Log().entry_round_trip().headers
    g = replay(hs)
    with pytest.raises(GrammarError, match='G3'):
        g.admit(with_(hs[2], digest='0' * 64))
    with pytest.raises(GrammarError, match='G3'):
        g.admit(with_(hs[2], sequence=len(hs) + 1))


def test_other_aggregate_is_rejected():
    hs = Log().entry_round_trip().headers
    bad([with_(hs[0], aggregate_id='pf_' + '9' * 32)], 'G1')


def test_result_before_intent_and_before_sent():
    hs = Log().entry_round_trip().headers
    bad([hs[0], with_(hs[3], sequence=2)], 'before the intent was recorded')
    bad(hs[:2] + [with_(hs[3], sequence=3)], 'never-sent')
    bad(hs[:2] + [with_(hs[2], kind=E.STATE_CHANGED, to_state='working')], 'G7')


def test_close_before_final_and_events_after_close():
    hs = Log().entry_round_trip().headers
    bad(hs[:3] + [with_(hs[4], sequence=4)], 'G9: closed before')
    bad(hs[:5] + [with_(hs[2], event_id='evt_' + 'e' * 32, sequence=6)], 'after the intent closed')
    bad(hs[:4] + [with_(hs[3], event_id='evt_' + 'e' * 32, sequence=5)], 'G8')   # a second final result
    bad(hs[:4] + [with_(hs[4], to_state='not_sent')], 'not_sent')


def test_intent_records_are_never_duplicated_or_orphaned():
    hs = Log().entry_round_trip().headers
    bad(hs[:2] + [with_(hs[1], event_id='evt_' + 'e' * 32, sequence=3)], 'G5: intent recorded twice')
    bad([with_(hs[1], sequence=1)], 'before its decision')


def test_keyed_ids_and_client_ids_must_be_the_derived_ones():
    hs = Log().entry_round_trip().headers
    other = 'int_' + '4' * 32
    bad([with_(hs[0], decision_id='dec_' + '5' * 32)], 'derive_decision_id')
    bad(hs[:1] + [with_(hs[1], intent_id=other, client_ids=(K.client_id_for(other),))], 'derive_intent_id')
    bad(hs[:1] + [with_(hs[1], client_ids=('zbn1o-' + 'a' * 26,))], 'client_id_for')
    bad(hs[:1] + [with_(hs[1], client_ids=(K.client_id_for(ENTRY), K.client_id_for(ENTRY, 'algo')))], 'only protect')
    bad(hs[:1] + [with_(hs[1], purpose='close')], 'purpose differs')


def test_refused_event_leaves_the_grammar_unchanged():
    hs = Log().entry_round_trip().headers
    g = replay(hs[:2])
    with pytest.raises(GrammarError):
        g.admit(with_(hs[3], sequence=3))            # result before sent
    assert g.last_sequence == 2
    assert g.admit(hs[2]) is Admission.APPLY         # the correct next event still applies


@pytest.mark.parametrize('kw', [
    dict(kind=E.SENT),                                                       # intent kind without intent_id
    dict(kind=E.MODE_CHANGED, intent_id=ENTRY),                              # non-intent kind with one
    dict(kind=E.RESULT_RECORDED, intent_id=ENTRY, outcome=R.NOT_FOUND, evidence='exchange_final'),
    dict(kind=E.RESULT_RECORDED, intent_id=ENTRY, outcome=R.FINAL),          # final without evidence
    dict(kind=E.STATE_CHANGED, intent_id=ENTRY, to_state='filled'),          # terminal is intent_closed
    dict(kind=E.INTENT_CLOSED, intent_id=ENTRY, to_state='working'),
    dict(kind=E.INTENT_RECORDED, intent_id=ENTRY, decision_id=DEC, purpose='entry', client_ids=()),
    dict(kind=E.DECISION_RECORDED, decision_id=DEC, decision_key='not a key'),
    dict(kind=E.SENT, intent_id=ENTRY, digest='XYZ'),
])
def test_header_shape_is_strict(kw):
    base = dict(event_id='evt_' + '0' * 32, account_id=ACCT, aggregate_id=PF, sequence=1, at_ms=1759924800000,
                digest='a' * 64)
    with pytest.raises(PortValueError):
        EventHeader(**{**base, **kw})


# ------------------------------------------------------------------------------------------------ consumed signal
def test_consumed_signal_is_idempotent_across_restart():
    log = Log()
    g = replay(log.headers)
    first = J.claim_signal(g, KEY)
    assert first.fresh and first.decision_id == DEC and first.intent_ids == (ENTRY,)
    log.add(E.DECISION_RECORDED, decision_id=DEC, decision_key=KEY).add(
        E.INTENT_RECORDED, decision_id=DEC, intent_id=ENTRY, purpose='entry', client_ids=(K.client_id_for(ENTRY),))
    for g2 in (replay(log.headers), replay(log.headers)):             # restart = replay the durable journal
        again = J.claim_signal(g2, K.DecisionKey.from_canonical(KEY.canonical_bytes()))
        assert not again.fresh and again.decision_id == DEC and again.intent_ids == (ENTRY,)
    bad(log.headers + [with_(log.headers[0], event_id='evt_' + 'd' * 32, sequence=3)], 'G4')


def test_crash_between_decision_and_intent_keeps_the_same_ids():
    log = Log().add(E.DECISION_RECORDED, decision_id=DEC, decision_key=KEY)
    claim = J.claim_signal(replay(log.headers), KEY)
    assert not claim.fresh and claim.intent_ids == ()                   # consumed; no new decision, no new id
    log.add(E.INTENT_RECORDED, decision_id=DEC, intent_id=ENTRY, purpose='entry', client_ids=(K.client_id_for(ENTRY),))
    replay(log.headers)                                                  # only the derived id can still be recorded


def test_other_candle_or_account_is_a_fresh_signal():
    g = replay(Log().entry_round_trip().headers)
    assert J.claim_signal(g, dataclasses.replace(KEY, candle_close_ms=KEY.candle_close_ms + 14_400_000)).fresh
    g2 = J.Grammar('acct_' + '8' * 32, PF)
    assert J.claim_signal(g2, KEY).fresh and J.claim_signal(g2, KEY).decision_id != DEC
