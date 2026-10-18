"""TRUE-BOOK-02 (TB02a) - shadow instance identity, append-only evidence log, mark usability, ranking gate."""
import json
import os

import pytest

import engine as E
import shadow_books as SB
import trade_audit as TA


def _installed():
    r = E.with_scalp_slots(E.breadth_sleeves())
    return r[0] if isinstance(r, tuple) else r


def test_installed_profile_is_22_instances_one_per_strategy():
    inst = SB.instances(_installed())
    assert len(inst) == 22
    assert len({x['key'] for x in inst.values()}) == 22


def test_cost_policy_is_the_live_engine_policy():
    assert SB.COST_POLICY['fee_per_fill'] == E.FEE_EST == TA.FEE_EST
    assert SB.COST_POLICY['slip_bps_per_fill'] == TA.EXEC_SLIP_BPS
    assert len(SB.COST_POLICY_HASH) == 16


def test_duplicate_sleeves_dedupe_and_keep_provenance():
    a = E.sleeve('X1', 'ema_mom', .5, .02, 4)
    b = E.sleeve('X2', 'ema_mom', .25, .02, 9)              # share / max_pos are not causal for a normalized shadow book
    inst = SB.instances([a, b])
    assert len(inst) == 1
    assert next(iter(inst.values()))['sleeves'] == ['X1', 'X2']


@pytest.mark.parametrize('change', [dict(risk=.03), dict(tf='1h'), dict(sides=['LONG']), dict(symbols='core8'),
                                    dict(mgmt={'tp_r': 9.0})])
def test_material_config_change_starts_a_new_lineage(change):
    a = E.sleeve('X1', 'ema_mom', .5, .02, 4)
    b = dict(a, id='X2', **change)
    assert SB.identity(a)[0] != SB.identity(b)[0]


def test_mgmt_override_equal_to_default_is_the_same_instance():
    a = E.sleeve('X1', 'ema_mom', .5, .02, 4)
    dflt = SB.config_of(a)['mgmt']
    k = next(iter(dflt))
    b = dict(a, id='X2', mgmt={k: dflt[k]})
    assert SB.identity(a)[0] == SB.identity(b)[0]


def test_unregistered_strategy_is_ignored():
    assert SB.instances([dict(id='Z', key='no_such_strategy', risk=.01)]) == {}


def test_evidence_log_appends_dedupes_and_survives_restart(tmp_path):
    lg = SB.EvidenceLog(tmp_path, 'c1')
    assert lg.mark('i1', 'BTCUSDT', 1_000, 900, 100.0, 950, 'LONG') == 'added'
    assert lg.mark('i1', 'BTCUSDT', 1_000, 900, 100.0, 950, 'LONG') == 'replay'
    assert lg.mark('i1', 'BTCUSDT', 1_001, 900, 101.0, 960, 'LONG') == 'conflict'
    assert lg.watermark(900, ['i1']) == 'added'
    before = open(lg.path, encoding='utf-8').read()
    lg2 = SB.EvidenceLog(tmp_path, 'c1')                       # restart
    assert lg2.mark('i1', 'BTCUSDT', 1_000, 900, 100.0, 950, 'LONG') == 'replay'
    assert open(lg2.path, encoding='utf-8').read() == before   # nothing rewritten, nothing duplicated
    assert lg2.marks('i1')[('BTCUSDT', 900, 'LONG')]['px'] == 100.0
    assert len(before.splitlines()) == 2


def test_evidence_log_is_per_cohort(tmp_path):
    SB.EvidenceLog(tmp_path, 'c1').watermark(900, ['i1'])
    assert SB.EvidenceLog(tmp_path, 'c2').records() == []


def test_torn_line_is_counted_not_repaired(tmp_path):
    lg = SB.EvidenceLog(tmp_path, 'c1')
    lg.watermark(900, ['i1'])
    with open(lg.path, 'a', encoding='utf-8') as fh:
        fh.write('{"kind":"mark","id":"m|i')                  # crash mid-write
    lg2 = SB.EvidenceLog(tmp_path, 'c1')
    assert lg2.torn == 1 and len(lg2.records()) == 1


def test_bad_record_and_cohort_refused(tmp_path):
    with pytest.raises(ValueError):
        SB.EvidenceLog(tmp_path, '../x')
    with pytest.raises(ValueError):
        SB.EvidenceLog(tmp_path, 'c1').append(dict(kind='fill', id='x'))


def test_coverage_gap_is_visible(tmp_path):
    lg = SB.EvidenceLog(tmp_path, 'c1')
    lg.watermark(100, ['i1', 'i2'])
    lg.watermark(300, ['i2'])
    assert lg.coverage([100, 200, 300], 'i1') == [200, 300]
    assert lg.coverage([100, 200, 300], 'i2') == [200]


@pytest.mark.parametrize('rec,ok,why', [
    (None, False, 'missing_decision_mark'),
    (dict(mark_ts=899, decision_ts=1000, px=1.0), False, 'mark_before_close'),
    (dict(mark_ts=900, decision_ts=899, px=1.0), False, 'mark_before_close'),
    (dict(mark_ts=900, decision_ts=1000 + 60_000, px=1.0), False, 'stale_decision_mark'),
    (dict(mark_ts=950, decision_ts=1000, px=0.0), False, 'stale_decision_mark'),
    (dict(mark_ts=900, decision_ts=900 + 30_000, px=1.0), True, None),
])
def test_mark_usable(rec, ok, why):
    assert SB.mark_usable(rec, 900, 30_000) == (ok, why)


def test_rank_gate_needs_20_closes_and_complete_evidence():
    rows = [dict(instance='a', closed=20, net_r=1.0), dict(instance='b', closed=25, net_r=3.0),
            dict(instance='c', closed=19, net_r=9.0), dict(instance='d', closed=40, net_r=9.0, incomplete=['x'])]
    out = {r['instance']: r for r in SB.rank(rows)}
    assert out['b']['rank'] == 1 and out['a']['rank'] == 2
    assert out['c']['rank'] is None and out['c']['unranked'] == 'n<20'
    assert out['d']['rank'] is None and out['d']['unranked'] == 'incomplete'


def test_module_has_no_order_path():
    src = open(SB.__file__, encoding='utf-8').read()
    for bad in ('binance_client', 'new_order', 'place_order', 'import engine', 'requests'):
        assert bad not in src
