"""NC-01 r3 DRAFT amendments (pre-staged for Codex; each item is a separate commit and can be accepted on its own).

Item 2: risk halts and the REC-02 reconcile vocabulary in the reason registry."""
import pytest

import nc01_factories as F
from newcore.domain import Action, ReasonCode

R3_CODES = {
    'risk_gateway.drawdown_kill': (Action.HALT, Action.SKIP),
    'risk_gateway.daily_halt': (Action.HALT, Action.SKIP),
    'reconcile.foreign_quarantine': (Action.RECONCILE, Action.PAUSE, Action.SKIP),
    'reconcile.manual_close': (Action.RECONCILE,),
    'reconcile.manual_add': (Action.RECONCILE, Action.PAUSE),
    'reconcile.stale_read': (Action.RECONCILE, Action.WAIT),
    'reconcile.late_fill_after_not_found': (Action.RECONCILE,),
}


@pytest.mark.parametrize('value', sorted(R3_CODES))
def test_item2_r3_reason_codes_exist_and_are_usable(value):
    from newcore.domain import MEANING
    code = ReasonCode(value)
    assert len(MEANING[code]) > 10 and not code.deprecated
    ids = F.Ids(300)
    for action in R3_CODES[value]:
        d = F.decision_with_intents(ids, ids.id('acct'), action, code)
        assert d.reason is code


def test_item2_r3_codes_are_appended_after_v1():
    order = [r.value for r in ReasonCode]
    assert order.index('risk_gateway.cost_to_stop') < min(order.index(v) for v in R3_CODES)
    assert order[-len(R3_CODES):] == ['risk_gateway.drawdown_kill', 'risk_gateway.daily_halt',
                                      'reconcile.foreign_quarantine', 'reconcile.manual_close', 'reconcile.manual_add',
                                      'reconcile.stale_read', 'reconcile.late_fill_after_not_found']
