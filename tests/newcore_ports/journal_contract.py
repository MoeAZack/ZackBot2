"""The JournalPort CONTRACT SUITE (STEP0_INTERFACE.md section 3). Every implementation runs it unchanged:

    from journal_contract import JournalContract          # module lives in tests/newcore_ports/

    class TestMyJournal(JournalContract):
        @pytest.fixture
        def make_journal(self, tmp_path):                  # -> () -> a fresh, empty JournalPort for (ACCT, PF)
            ...
        @pytest.fixture
        def reopen(self):                                  # -> (journal) -> the same journal after a process restart
            ...

Every event is a real NC-01 DomainEvent (nc_events.Scenario); nothing builds a header by hand."""
import pytest

from nc_events import ACCT, ENTRY, KEY, LOT, PF, Scenario, replace
from newcore.domain import EventCursor, IntentState, Purpose, admit, check_event_chain, make_id
from newcore.ports import keys as K
from newcore.ports.journal import Admission, JournalConflict, JournalPort, claim_signal

S, D, W, U, C = (IntentState.SUBMITTED, IntentState.DURABLE, IntentState.WORKING, IntentState.UNKNOWN,
                 IntentState.CANCELLING)


# ---------------------------------------------------------------------------------------------- good journals
def flow_fill_and_protect(s):
    s.entry_filled()
    sid = s.protect_attempt(0)
    s.result(sid, 'known')
    s.step(sid, S, W)
    s.hold()


def flow_fallback(s):
    s.entry_filled()
    s.refused(s.protect_attempt(0, 'classic'))             # classic refused (e.g. -4120) -> closed rejected
    aid = s.protect_attempt(1, 'algo')                     # the journaled second route, new lineage intent
    s.result(aid, 'known')
    s.step(aid, S, W)


def flow_unknown_then_found(s):
    s.entry_filled()
    sid = s.protect_attempt(0)
    s.result(sid, 'unknown')                               # lost answer: never fall back, query the same id
    s.step(sid, S, U)
    s.result(sid, 'not_found')
    s.result(sid, 'known')
    s.step(sid, U, W)


def flow_never_sent(s):
    i = K.derive_intent_id(ACCT, KEY)
    s.decision(key=KEY, intent_ids=(i,))
    s.record(i)
    s.step(i, D, C)
    s.result(i, 'not_sent')
    s.step(i, C, IntentState.NOT_SENT)


def flow_skip(s):
    s.decision(key=KEY)                                    # a SKIP / WAIT-like keyed decision authorizing nothing


GOOD = {'fill_and_protect': flow_fill_and_protect, 'fallback': flow_fallback,
        'unknown_then_found': flow_unknown_then_found, 'never_sent': flow_never_sent}


# ---------------------------------------------------------------------------------------------- refused events
def _flow(*fns):
    s = Scenario()
    for fn in fns:
        fn(s)
    return s


def bad_gap():
    s = _flow(flow_fill_and_protect)
    return s.events[:3], s.events[4]


def bad_reorder():
    s = _flow(flow_fill_and_protect)
    return s.events[:1], replace(s.events[2], sequence=2)        # sent renumbered ahead of its intent_recorded


def bad_reused_sequence():
    s = _flow(flow_fill_and_protect)
    return s.events[:3], replace(s.events[2], event_id=make_id('evt', 999))


def bad_same_id_other_bytes():
    s = _flow(flow_fill_and_protect)
    return s.events[:3], replace(s.events[2], at_ms=s.events[2].at_ms + 1)


def bad_result_before_intent():
    s = Scenario()
    s.decision(key=KEY, intent_ids=(ENTRY,))
    s.n += 1                                               # the intent_recorded that never happened
    ev = s.result(ENTRY, 'filled')
    return s.events[:1], replace(ev, sequence=2)


def bad_result_before_sent():
    s = Scenario()
    s.decision(key=KEY, intent_ids=(ENTRY,))
    s.record(ENTRY)
    return s.events[:], s.result(ENTRY, 'unknown')


def bad_close_before_final():
    s = Scenario()
    s.decision(key=KEY, intent_ids=(ENTRY,))
    s.record(ENTRY)
    s.step(ENTRY, D, S)
    return s.events[:], s.step(ENTRY, S, IntentState.FILLED)


def bad_after_close():
    s = _flow(lambda s: s.entry_filled())
    return s.events[:], s.result(ENTRY, 'filled')                 # anything after the close


def bad_signal_decided_twice():
    s = _flow(lambda s: s.entry_filled())
    return s.events[:], s.decision(key=KEY, dec_id=K.derive_decision_id(ACCT, KEY))


def bad_decision_authorizes_leg1():
    s = Scenario()
    return [], s.decision(key=KEY, intent_ids=(K._derive_leg(ACCT, KEY, 1),))


def bad_close_decision_authorizes_two_legs():
    s = _flow(lambda s: s.entry_filled())
    kc = K.decision_key('trend_ema_mom', 'v1', '4h', 'SOLUSDT', 'LONG', KEY.candle_close_ms + 14_400_000, Purpose.CLOSE)
    legs = (K.derive_intent_id(ACCT, kc), K._derive_leg(ACCT, kc, 1))
    return s.events[:], s.decision(key=kc, intent_ids=legs, owner=LOT)


def bad_leg1_after_leg0_completed():
    s = _flow(lambda s: s.entry_filled())
    leg1 = K._derive_leg(ACCT, KEY, 1)
    s.intents[leg1] = replace(s.intents[ENTRY], intent_id=leg1, client_order_id=K.client_id_for(leg1))
    return s.events[:], s.record(leg1)


def _lineage_bad(intent_id, owner=LOT):
    s = _flow(lambda s: s.entry_filled())
    s.decision(purpose=Purpose.PROTECT, intent_ids=(intent_id,), owner=owner)
    return s.events[:], s.record(intent_id)


def bad_wrong_parent():
    return _lineage_bad(K.derive_child_intent_id(ACCT, ENTRY, Purpose.PROTECT, 0))  # parent: entry, owner: lot


def bad_wrong_ordinal():
    return _lineage_bad(K.derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 1))       # first stop must be ordinal 0


def bad_wrong_id():
    return _lineage_bad(make_id('int', 12345))


def bad_unknown_owner():
    other = K.derive_lot_id(ACCT, make_id('int', 777))
    return _lineage_bad(K.derive_child_intent_id(ACCT, other, Purpose.PROTECT, 0), owner=other)


def bad_stop_for_an_unfilled_entry():
    s = Scenario()
    s.decision(key=KEY, intent_ids=(ENTRY,))
    s.record(ENTRY)
    s.step(ENTRY, D, S)
    s.refused(ENTRY)                                       # nothing executed: no lot exists
    sid = K.derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 0)
    s.decision(purpose=Purpose.PROTECT, intent_ids=(sid,), owner=LOT)
    return s.events[:], s.record(sid)


def bad_unkeyed_intent_without_owner():
    s = Scenario()
    manual = make_id('int', 4321)
    s.decision(purpose=Purpose.ENTRY, intent_ids=(manual,))
    return s.events[:], s.record(manual)


def bad_keyed_decision_with_another_id():
    return [], Scenario().decision(key=KEY, intent_ids=(ENTRY,), dec_id=make_id('dec', 99))


def bad_unauthorized_intent():
    s = _flow(lambda s: s.entry_filled())
    sid = K.derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 0)       # lineage-correct, but its decision (the
    dec = K.derive_decision_id(ACCT, KEY)                                # entry's) authorized only the entry intent
    s.intents[sid] = s._intent(sid, Purpose.PROTECT, dec, s.events[0].at_ms, owner=LOT)
    return s.events[:], s.record(sid)


def bad_client_id_not_derived():
    s = _flow(lambda s: s.entry_filled())
    sid = K.derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 0)
    s.decision(purpose=Purpose.PROTECT, intent_ids=(sid,), owner=LOT)
    s.intents[sid] = replace(s.intents[sid], client_order_id='zbn1o-' + 'a' * 26)
    return s.events[:], s.record(sid)


def bad_fallback_after_unknown():
    s = _flow(lambda s: s.entry_filled())
    s.result(s.protect_attempt(0), 'unknown')              # the classic order may exist: no second route
    aid = K.derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 1)
    s.decision(purpose=Purpose.PROTECT, intent_ids=(aid,), owner=LOT, route='algo')
    return s.events[:], s.record(aid)


def bad_algo_without_classic():
    s = _flow(lambda s: s.entry_filled())
    aid = K.derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 0)
    s.decision(purpose=Purpose.PROTECT, intent_ids=(aid,), owner=LOT, route='algo')
    return s.events[:], s.record(aid)


def bad_duplicate_route_send():
    s = _flow(flow_fallback)
    aid = K.derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 1)
    return s.events[:], s.step(aid, D, S)                  # a second send of the algo attempt


def bad_second_algo_after_algo_rejected():
    s = _flow(lambda s: s.entry_filled())
    s.refused(s.protect_attempt(0, 'classic'))
    s.refused(s.protect_attempt(1, 'algo'))
    a2 = K.derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 2)
    s.decision(purpose=Purpose.PROTECT, intent_ids=(a2,), owner=LOT, route='algo')
    return s.events[:], s.record(a2)


def bad_wrong_from_state():
    s = _flow(lambda s: s.entry_filled())
    sid = s.protect_attempt(0)
    return s.events[:], s.step(sid, W, C)                  # the intent is SUBMITTED, not WORKING


def bad_not_an_event():
    return [], 'not an event'




# name -> (case, the refusal reason every implementation must report: one gate, one mapping)
BAD = {f.__name__[4:]: (f, why) for f, why in (
    (bad_gap, 'G2: 5 is a gap'), (bad_reorder, 'sent before the intent was recorded'),
    (bad_reused_sequence, 'G2: 3 is an already-used sequence'), (bad_same_id_other_bytes, 'G3'),
    (bad_result_before_intent, 'result_recorded before the intent was recorded'),
    (bad_result_before_sent, 'G9: a never-sent intent only takes a final not_sent'),
    (bad_close_before_final, 'G10: closed before its final result'), (bad_after_close, 'G10: result_recorded after'),
    (bad_signal_decided_twice, 'G4: decision recorded twice'),
    (bad_decision_authorizes_leg1, 'G4: a keyed decision authorizes exactly'),
    (bad_close_decision_authorizes_two_legs, 'G4: a keyed decision authorizes exactly'),
    (bad_leg1_after_leg0_completed, "not in its decision's authorized intent set"),
    (bad_wrong_parent, 'G5: lineage id must be'), (bad_wrong_ordinal, 'G5: lineage id must be'),
    (bad_wrong_id, 'G5: lineage id must be'), (bad_unknown_owner, 'G5: owner is no lot'),
    (bad_stop_for_an_unfilled_entry, 'G5: owner is no lot'),
    (bad_unkeyed_intent_without_owner, 'G5: an unkeyed intent needs its lineage owner'),
    (bad_keyed_decision_with_another_id, 'G4: a keyed decision id must be'),
    (bad_unauthorized_intent, "not in its decision's authorized intent set"),
    (bad_client_id_not_derived, 'G5: the client id must be client_id_for'),
    (bad_fallback_after_unknown, 'G6: algo only as the fallback'), (bad_algo_without_classic, 'G6: algo only'),
    (bad_duplicate_route_send, 'G7: sent twice'), (bad_second_algo_after_algo_rejected, 'G6: algo only'),
    (bad_wrong_from_state, 'the intent is submitted'), (bad_not_an_event, 'is not an NC-01 DomainEvent'))}


# ---------------------------------------------------------------------------------------------- the suite
class JournalContract:
    def test_is_a_journal_port(self, make_journal):
        assert isinstance(make_journal(), JournalPort)

    @pytest.mark.parametrize('flow', sorted(GOOD))
    def test_good_journal_applies_and_agrees_with_nc01(self, make_journal, flow):
        j, s = make_journal(), _flow(GOOD[flow])
        assert [j.append(ev) for ev in s.events] == [Admission.APPLY] * len(s.events)
        assert j.last_sequence() == len(s.events)
        assert list(j.read()) == s.events and list(j.read(2)) == s.events[2:]
        check_event_chain(list(j.read()))                  # the NC-01 chain authority accepts the same journal
        cur = EventCursor(account_id=ACCT, aggregate_id=PF, last_sequence=0, applied=())
        for ev in j.read():
            cur, adm = admit(cur, ev)
            assert adm.value == 'apply'
        assert j.find_decision(K.derive_decision_id(ACCT, KEY)).decision.key == KEY
        assert j.find_decision(make_id('dec', 4242)) is None

    def test_identical_reappend_is_a_no_op(self, make_journal):
        j, s = make_journal(), _flow(flow_fallback)
        for ev in s.events:
            j.append(ev)
        assert all(j.append(ev) is Admission.ALREADY_APPLIED for ev in s.events)
        assert list(j.read()) == s.events

    @pytest.mark.parametrize('case', sorted(BAD))
    def test_refused_event_changes_nothing(self, make_journal, case):
        case, why = BAD[case]
        prefix, bad = case()
        j = make_journal()
        for ev in prefix:
            assert j.append(ev) is Admission.APPLY
        with pytest.raises(JournalConflict) as refused:
            j.append(bad)
        assert why in str(refused.value), str(refused.value)
        assert list(j.read()) == list(prefix) and j.last_sequence() == len(prefix)

    def test_restart_keeps_identity_between_routes(self, make_journal, reopen):
        s = Scenario()
        s.entry_filled()
        classic = s.protect_attempt(0, 'classic')
        s.refused(classic)
        j = make_journal()
        for ev in s.events:
            j.append(ev)
        before = j.gate().grammar.next_child_intent_id(LOT, Purpose.PROTECT)
        j = reopen(j)                                      # crash between the two routes
        g = j.gate().grammar
        assert g.next_child_intent_id(LOT, Purpose.PROTECT) == before == K.derive_child_intent_id(
            ACCT, LOT, Purpose.PROTECT, 1)
        claim = claim_signal(g, KEY)                       # the re-delivered entry signal is spent
        assert not claim.fresh and claim.intent_ids == (ENTRY,)
        mark = len(s.events)
        aid = s.protect_attempt(1, 'algo')
        assert aid == before
        assert [j.append(ev) for ev in s.events[mark:]] == [Admission.APPLY] * 3
        j = reopen(j)
        with pytest.raises(JournalConflict):               # after a restart the algo route still cannot be re-sent
            j.append(s.step(aid, D, S))
        assert j.last_sequence() == mark + 3

    def test_consumed_signal_across_restart(self, make_journal, reopen):
        s = _flow(flow_skip)
        j = make_journal()
        j.append(s.events[0])
        for jj in (j, reopen(j)):
            claim = claim_signal(jj.gate().grammar, KEY)
            assert not claim.fresh and claim.intent_ids == () and claim.decision_id == K.derive_decision_id(ACCT, KEY)
