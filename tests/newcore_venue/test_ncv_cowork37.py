"""Cowork attacks routed late from PR #37 (venue harness / CLIs). Each test is the repro first, then the fixed
behaviour. Fake HTTP, DUMMY keys, no network."""
from decimal import Decimal as D

import pytest

from fake_binance import FakeBinance
from test_ncv_tnet_harness import env, run, sol_rules, venue_over  # noqa: F401  (env is a fixture)

from newcore.venue.tnet_probes import ProbeAborted, probe_p1


def foreign_long(fb, qty='3'):
    fb.pos[('SOLUSDT', 'LONG')] = D(qty)
    fb.visible_pos[('SOLUSDT', 'LONG')] = D(qty)
    return fb


# ---------------------------------------------------------------------------------------------- T1 (HIGH)
@pytest.mark.parametrize('reuse', [False, True])
def test_t1_probe_p1_never_closes_an_adopted_foreign_position(reuse):
    fb = foreign_long(FakeBinance(reuse_ids=reuse))
    v, t = venue_over(fb)
    res = probe_p1(v, symbol='SOLUSDT', rules=sol_rules(t), price=D('220'), run_id='cw1')
    assert fb.pos[('SOLUSDT', 'LONG')] == D('3')                      # the foreign LONG 3 is untouched
    assert res.filled_id_reused == ('accepted' if reuse else 'refused:duplicate_client_id')
    assert not fb.open_cids()


def test_t1_probe_p1_judges_an_unknown_reuse_against_the_baseline_not_zero():
    from newcore.venue.tnet_seams import FaultHttp
    fb = foreign_long(FakeBinance(reuse_ids=True))
    seam = FaultHttp(fb)
    v, t = venue_over(seam)

    class ArmOnReuse:                     # lose the answer of the reuse close (it executes): UNKNOWN, judge by position
        def __getattr__(self, n):
            return getattr(v, n)

        def submit_market(self, order):
            if order.reduce and not getattr(self, 'armed', False):      # the first reduce order = the reuse close
                self.armed = True
                seam.arm('lost_response')
            return v.submit_market(order)
    res = probe_p1(ArmOnReuse(), symbol='SOLUSDT', rules=sol_rules(t), price=D('220'), run_id='cw2')
    assert res.filled_id_reused == 'accepted'                          # 3 == baseline 3, not 0
    assert fb.pos[('SOLUSDT', 'LONG')] == D('3')


def test_t1_an_unreadable_position_before_the_probe_sends_nothing():
    fb = FakeBinance()
    fb._get_fapi_v2_positionRisk = lambda q: __import__('fake_binance').err(-1001, 'busy', status=503)
    v, t = venue_over(fb)
    with pytest.raises(ProbeAborted, match='nothing sent'):
        probe_p1(v, symbol='SOLUSDT', rules=sol_rules(t), price=D('220'), run_id='cw3')
    assert not [q for q in fb.requests if q.method == 'POST']


def test_t1_cli_with_an_adopted_foreign_long_keeps_it_and_is_clean(env):  # noqa: F811
    fb = foreign_long(FakeBinance())
    rc, out = run(env, ['--probe', 'P1', '--adopt-foreign', 'SOLUSDT:LONG'], http=fb)
    assert rc == 0, out
    assert fb.pos[('SOLUSDT', 'LONG')] == D('3') and 'CLEANUP CLEAN' in out
