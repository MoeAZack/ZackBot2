"""Remedy A for `incomplete.split_end` (Codex ruling 6185035993 point 4; Build draft 6183334843 A): the NEW, explicitly
versioned, DEVELOPMENT-ONLY candidate rule `range_vwap_rev.v1-splitcut` (`entry-cutoff/1`). An entry decided at close
t is admitted only if t + (cap + 1) x 4h <= split end (the cap exit fills at the open t + cap x 4h, inside the split).

Proved here, on synthetic tapes only (rex_synth; no store, data folder, holdout or network):
  * the frozen `range_vwap_rev.v1` is unchanged: golden digests of its book / B0 / runner outputs were computed with the
    master code before this change (origin/master 4e9f1df) and are re-derived here with the current code;
  * with the variant no lot is open at any split end (0 split_end rows where v1 had some);
  * the boundary is exact on both sides; the cap is read from the configuration;
  * causal: no price after the decision close can change an entry decision;
  * the baselines that open X1-book lots (B2, B0) get the same cutoff, on both cost rows;
  * the variant record is distinct from v1 and non-promotable (ledger, summary, run config, run id);
  * the variant must be selected explicitly (no default switch, unknown ids refuse)."""
import hashlib
import inspect
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rex_synth as S                                                                       # noqa: E402

RV = S.RV
import ledger as L                                                                          # noqa: E402
import pit_run as PR                                                                        # noqa: E402
import progress as PG                                                                       # noqa: E402
import range_vwap_rev_eval as EV                                                            # noqa: E402
import run_r4 as G                                                                          # noqa: E402
import test_range_vwap_rev_binding as B                                                     # noqa: E402
import test_range_vwap_rev_loader as LD                                                     # noqa: E402
from test_range_vwap_rev_binding import plan                                                # noqa: E402,F401
from test_range_vwap_rev_loader import fx                                                   # noqa: E402,F401

ms, H, TF = S.ms, S.H, S.TF
LE = S.long_entry(5)                                 # X1 long signal decided at the close ms(6), filled at ms(6)
B2P = {-1: 103.0, 4: 103.0, 5: 105.0}                # B2 signal decided at the close ms(6)
CUT = RV.SPLITCUT_RULE
VID = RV.SPLITCUT_CANDIDATE


# --- golden begin (this block is executed verbatim against the pre-change master code to derive the digests below)
def _canon(o) -> str:
    return hashlib.sha256(json.dumps(o, sort_keys=True, default=repr).encode()).hexdigest()


def golden_fixtures(S):
    le = S.long_entry(5)
    return {'open_at_end': (12, dict(vwap=le)), 'mean': (16, dict(vwap=le, close={12: 104.0})),
            'invalidation': (12, dict(vwap={**le, 8: 106.0})),
            'stop': (12, dict(vwap=le, ohlc={8: (100, 101, 97.0, 99)})),
            'cap36': (36, dict(vwap=le)), 'cap37': (37, dict(vwap=le)), 'cap40': (40, dict(vwap=le)),
            'b2': (12, dict(vwap={-1: 103.0, 4: 103.0, 5: 105.0})), 'late': (40, dict(vwap=S.long_entry(25)))}


def golden_book(RV, S, **kw) -> str:
    """Every v1 book output over the fixture matrix: sides x modes (x1, b2, b0) x cost rows, the primary and its
    neighbours on one fixture, and a two-symbol book."""
    out = []
    for name, (n, f) in sorted(golden_fixtures(S).items()):
        for side in RV.SIDES:
            tape = {'AAA': S.build(n=n, side=side, **f)}
            for row in ('base', 'fees_slip_x2'):
                for mode in ('x1', 'b2'):
                    out.append(RV.run_book(tape, RV.PRIMARY, side, first=S.H, end=S.ms(n), mode=mode, row=row, **kw))
                out.append(RV.run_book(tape, RV.PRIMARY, side, first=S.H, end=S.ms(n), mode='b0', row=row,
                                       draws={('AAA', S.ms(6)), ('AAA', S.ms(9)), ('AAA', S.ms(n - 2))}, **kw))
    for side in RV.SIDES:
        tape = {'AAA': S.build(n=40, side=side, vwap=S.long_entry(5)), 'BBB': S.build(n=40, side=side,
                                                                                     vwap=S.long_entry(20))}
        for cfg in [RV.PRIMARY] + RV.neighbours():
            out.append(RV.run_book(tape, cfg, side, first=S.H, end=S.ms(40), **kw))
    return _canon(out)


def golden_b0(RV, EV, S, **kw) -> str:
    out = []
    for side in RV.SIDES:
        tape = {s: S.build(n=40, side=side, vwap=S.long_entry(j)) for s, j in (('AAA', 5), ('BBB', 9))}
        x1 = RV.run_book(tape, RV.PRIMARY, side, first=S.H, end=S.ms(40), **kw)
        out.append(EV.z_table(tape, RV.PRIMARY, side, first=S.H, end=S.ms(40), **kw))
        out.append(EV.b0(tape, RV.PRIMARY, side, x1, first=S.H, end=S.ms(40), R=4, **kw))
    return _canon([{repr(k): v for k, v in o.items()} for o in out])


def golden_runner(B, tmp, **kw) -> str:
    """The sealed summary / detail of synthetic v1 runner passes (one COMPLETE, one split_end INCOMPLETE)."""
    plan = B.PR.rex_plan(B.ROOT)
    out = []
    for i, t in enumerate((B.tape(vwap=B.RS.long_entry(5), close={12: 104.0}), B.tape(vwap=B.RS.long_entry(5)))):
        final = B.run(B.env(plan, t=t), os.path.join(tmp, str(i)), **kw)
        doc, summary, detail = B.docs(final)
        out.append([summary, detail])
    return _canon(out)
# --- golden end


# derived by executing the golden block above against `git archive 4e9f1df` (master before this change)
GOLDEN_BOOK = 'e4be3bc649659bbd6542d83e6ce1f3fc490a34f128f0b0ec6c33d09a4c9001f1'
GOLDEN_B0 = '917c61e476ddb3fea3d229950301bf7d3a1f16bd682e554d3f959a8ae1553309'
GOLDEN_RUNNER = 'faa2a4a431e32e5cb9620056e43883337dbc4150aeaebed4835272cbe9748cca'


def book(n, side='long', mode='x1', row='base', cfg=RV.PRIMARY, rule=None, draws=None, **f):
    tape = {'AAA': S.build(n=n, side=side, **f)}
    return RV.run_book(tape, cfg, side, first=H, end=ms(n), mode=mode, row=row, draws=draws, entry_rule=rule)


def strip(res):
    return {k: v for k, v in res.items() if k not in ('entry_rule', 'candidate_id', 'promotable')}


# ------------------------------------------------------------------ v1 is unchanged
def test_v1_book_outputs_are_byte_identical_to_master():
    assert golden_book(RV, S) == GOLDEN_BOOK
    assert golden_book(RV, S, entry_rule=None) == GOLDEN_BOOK       # the explicit default is the same path


def test_v1_b0_outputs_are_byte_identical_to_master():
    assert golden_b0(RV, EV, S) == GOLDEN_B0


def test_v1_runner_artifacts_are_byte_identical_to_master(tmp_path):
    assert golden_runner(B, str(tmp_path)) == GOLDEN_RUNNER
    assert golden_runner(B, str(tmp_path / 'v'), variant=RV.CANDIDATE) == GOLDEN_RUNNER


def test_v1_result_carries_no_variant_keys():
    r = book(12, vwap=LE)
    assert not {'entry_rule', 'candidate_id', 'promotable'} & set(r)
    assert r['censor']['reason'] == 'incomplete.split_end'                 # the frozen censoring stays as it was


# ------------------------------------------------------------------ the variant removes split_end
@pytest.mark.parametrize('side', RV.SIDES)
@pytest.mark.parametrize('row', PR.REX_COST_ROWS)
def test_variant_has_no_split_end_where_v1_had_some(side, row):
    v1_inc = 0
    for n in range(8, 46):
        for j in (5, 9, 14, 20, 26):
            if j + 2 > n:
                continue
            f = dict(vwap=S.long_entry(j))
            a = book(n, side, row=row, **f)
            b = book(n, side, row=row, rule=CUT, **f)
            v1_inc += sum(r['reason'] == 'incomplete.split_end' for r in a['incomplete'])
            assert b['status'] == RV.COMPLETE and b['incomplete'] == [] and 'censor' not in b
            assert b['open_lots'] == {}
            assert all(t['exit_ms'] < ms(n) for t in b['trades'])
            assert (b['entry_rule'], b['candidate_id'], b['promotable']) == (CUT, VID, False)
    assert v1_inc > 50                                                     # v1 had many on the same tapes


def test_variant_on_a_two_symbol_book_with_neighbours():
    for side in RV.SIDES:
        tape = {'AAA': S.build(n=40, side=side, vwap=S.long_entry(5)),
                'BBB': S.build(n=40, side=side, vwap=S.long_entry(20))}
        for cfg in [RV.PRIMARY] + RV.neighbours():
            a = RV.run_book(tape, cfg, side, first=H, end=ms(40))
            b = RV.run_book(tape, cfg, side, first=H, end=ms(40), entry_rule=CUT)
            assert b['incomplete'] == [] and b['open_lots'] == {}
            if a['status'] == RV.INCOMPLETE:
                assert b['counts'].get('splitcut_blocked', 0) >= 1


# ------------------------------------------------------------------ the exact boundary
def test_boundary_rule_is_t_plus_cap_plus_one_bars_le_end():
    t = ms(6)
    assert RV.splitcut_allows(t, 30, t + 31 * TF) is True                  # cap exit fills at t + 30 bars: inside
    assert RV.splitcut_allows(t, 30, t + 31 * TF - 1) is False
    assert RV.splitcut_allows(t, 30, t + 30 * TF) is False                 # cap exit would be decided AT the end
    assert RV.splitcut_allows(t, 10, t + 11 * TF) is True and RV.splitcut_allows(t, 10, t + 10 * TF) is False


@pytest.mark.parametrize('side', RV.SIDES)
def test_boundary_both_sides_on_a_book(side):
    # decision ms(6), cap 30: the cap exit is decided at ms(36) and fills at the open ms(36)
    ok_v1, ok = book(37, side, vwap=LE), book(37, side, vwap=LE, rule=CUT)  # end ms(37) = t + 31 bars: admitted
    assert ok_v1['status'] == RV.COMPLETE and [t['reason'] for t in ok_v1['trades']] == ['exit.cap']
    assert ok_v1['trades'][0]['exit_ms'] == ms(36)
    assert strip(ok) == ok_v1 and 'splitcut_blocked' not in ok['counts']  # identical to v1 when admitted
    no_v1, no = book(36, side, vwap=LE), book(36, side, vwap=LE, rule=CUT)  # end ms(36) = t + 30 bars: blocked
    assert no_v1['status'] == RV.INCOMPLETE and no_v1['censor']['reason'] == 'incomplete.split_end'
    assert no['status'] == RV.COMPLETE and no['trades'] == [] and no['signals'] == []
    assert no['counts']['splitcut_blocked'] == 1


@pytest.mark.parametrize('side', RV.SIDES)
def test_the_cap_is_read_from_the_configuration(side):
    cfg = RV.Cfg('cap10', cap=10)
    a = book(17, side, cfg=cfg, vwap=LE, rule=CUT)                         # ms(6) + 11 bars = ms(17): admitted
    assert [(t['reason'], t['exit_ms']) for t in a['trades']] == [('exit.cap', ms(16))]
    b = book(16, side, cfg=cfg, vwap=LE, rule=CUT)
    assert b['trades'] == [] and b['counts']['splitcut_blocked'] == 1 and b['status'] == RV.COMPLETE
    assert book(16, side, cfg=cfg, vwap=LE)['censor']['reason'] == 'incomplete.split_end'


# ------------------------------------------------------------------ causal
def test_rule_reads_no_price():
    assert list(inspect.signature(RV.splitcut_allows).parameters) == ['t', 'cap', 'end']


@pytest.mark.parametrize('side', RV.SIDES)
@pytest.mark.parametrize('n', [36, 37])
def test_prices_after_the_decision_cannot_change_an_entry_decision(side, n):
    base = dict(vwap=LE)
    later = [dict(vwap=LE, ohlc={8: (100, 101, 97.0, 99)}),                # stopped out early (v1 would be COMPLETE)
             dict(vwap=LE, close={12: 104.0}),                             # mean close early
             dict(vwap={**LE, 8: 106.0}),                                  # invalidated early
             dict(vwap=LE, ohlc={i: (100, 160, 40, 100) for i in range(7, n)})]
    ref = book(n, side, rule=CUT, **base)
    for f in later:
        r = book(n, side, rule=CUT, **f)
        assert r['signals'] == ref['signals']                              # the same entry decisions
        assert r['counts'].get('splitcut_blocked', 0) == ref['counts'].get('splitcut_blocked', 0)
        assert [t['entry_ms'] for t in r['trades']] == [t['entry_ms'] for t in ref['trades']]
    if n == 36:                                                            # blocked although the trade would have
        v1 = book(n, side, **later[0])                                     # closed in time: the rule never peeks
        assert v1['status'] == RV.COMPLETE and len(v1['trades']) == 1
        assert book(n, side, rule=CUT, **later[0])['trades'] == []


@pytest.mark.parametrize('side', RV.SIDES)
def test_decisions_before_k_ignore_bars_from_k_on(side):
    f = dict(vwap={**S.long_entry(5), **{k: v for k, v in S.long_entry(20).items()}})
    ref = book(40, side, rule=CUT, **f)
    for k in (8, 15, 22, 30):
        g = dict(f, ohlc={i: (100, 150, 50, 100) for i in range(k, 40)})
        r = book(40, side, rule=CUT, **g)
        assert [s for s in r['signals'] if s[1] <= ms(k)] == [s for s in ref['signals'] if s[1] <= ms(k)]


# ------------------------------------------------------------------ baselines get the same cutoff
@pytest.mark.parametrize('side', RV.SIDES)
@pytest.mark.parametrize('row', PR.REX_COST_ROWS)
def test_b2_gets_the_cutoff(side, row):
    a = book(12, side, mode='b2', row=row, vwap=B2P)
    b = book(12, side, mode='b2', row=row, vwap=B2P, rule=CUT)
    assert a['censor']['reason'] == 'incomplete.split_end'
    assert b['status'] == RV.COMPLETE and b['incomplete'] == [] and b['counts']['splitcut_blocked'] >= 1
    c = book(40, side, mode='b2', row=row, vwap=B2P, rule=CUT)              # admitted far from the end: same as v1
    assert strip(c) == book(40, side, mode='b2', row=row, vwap=B2P)


@pytest.mark.parametrize('side', RV.SIDES)
@pytest.mark.parametrize('row', PR.REX_COST_ROWS)
def test_b0_replicate_books_get_the_cutoff(side, row):
    d = {('AAA', ms(5)), ('AAA', ms(38))}               # ms(5): cap exit ms(35); ms(38): open at the end in v1
    a = book(40, side, mode='b0', row=row, draws=d, vwap=LE)
    b = book(40, side, mode='b0', row=row, draws=d, rule=CUT, vwap=LE)
    assert a['status'] == RV.INCOMPLETE and a['censor']['reason'] == 'incomplete.split_end'
    assert b['status'] == RV.COMPLETE and b['counts']['b0_shortfall_splitcut'] == 1
    assert [t['decision_ms'] for t in b['trades']] == [ms(5)]


@pytest.mark.parametrize('side', RV.SIDES)
def test_b0_draw_pool_and_replicates_use_the_x1_rule(side):
    tape = {s: S.build(n=40, side=side, vwap=S.long_entry(j)) for s, j in (('AAA', 5), ('BBB', 9))}
    zt = EV.z_table(tape, RV.PRIMARY, side, first=H, end=ms(40), entry_rule=CUT)
    full = EV.z_table(tape, RV.PRIMARY, side, first=H, end=ms(40))
    assert zt == {k: v for k, v in full.items() if RV.splitcut_allows(k[1], RV.CAP_BARS, ms(40))}
    assert max(t for _, t in zt) == ms(9) and len(zt) < len(full)
    x1 = RV.run_book(tape, RV.PRIMARY, side, first=H, end=ms(40), entry_rule=CUT)
    r = EV.b0(tape, RV.PRIMARY, side, x1, first=H, end=ms(40), R=4, entry_rule=CUT)
    assert r['status'] == RV.COMPLETE
    with pytest.raises(RV.RexError, match='same entry_rule'):
        EV.b0(tape, RV.PRIMARY, side, x1, first=H, end=ms(40), R=4)       # a v1 B0 cannot be matched to the variant
    with pytest.raises(RV.RexError, match='same entry_rule'):
        EV.b0(tape, RV.PRIMARY, side, RV.run_book(tape, RV.PRIMARY, side, first=H, end=ms(40)), first=H,
              end=ms(40), R=4, entry_rule=CUT)


def test_b1_is_not_an_x1_book_and_takes_no_entry_rule():
    assert 'entry_rule' not in inspect.signature(EV.b1_book).parameters    # continuous weekly comparator (valued at
    assert {c['mode'] for c in PR.rex_configs()} == {'x1', 'b2'}           # the boundary, never split_end); B3 = cash


def test_runner_applies_the_cutoff_to_every_config_and_both_rows(plan, tmp_path):
    t = B.tape(vwap=S.long_entry(5))                                       # v1: split_end at the window end
    v1 = B.docs(B.run(B.env(plan, t=t), tmp_path / 'v1'))[1]
    assert v1['status'] == RV.INCOMPLETE
    final = B.run(B.env(plan, t=t), tmp_path / 'cut', variant=VID)
    doc, summary, detail = B.docs(final)
    assert summary['status'] == RV.COMPLETE and 'incomplete' not in summary
    assert doc['evidence'] == [f'summary-{doc["run"]}.json']
    assert detail['splits']
    for sp in detail['splits']:                                            # the raw per-split books
        for side, by_cfg in sp['results'].items():
            assert set(by_cfg) == {c['name'] for c in PR.rex_configs()}
            for name, by_row in by_cfg.items():
                assert set(by_row) == set(PR.REX_COST_ROWS)
                for row, res in by_row.items():
                    assert res['entry_rule'] == CUT and res['incomplete'] == [] and res['promotable'] is False
    blocked = [sp['results']['long'][RV.PRIMARY.name][row]['counts'].get('splitcut_blocked', 0)
               for sp in detail['splits'] for row in PR.REX_COST_ROWS]
    assert all(b >= 1 for b in blocked)


# ------------------------------------------------------------------ distinct, non-promotable variant record
def test_variant_record_is_distinct_and_non_promotable():
    v = RV.SPLITCUT
    assert v['candidate_id'] == VID != RV.CANDIDATE and v['variant_of'] == RV.CANDIDATE
    assert v['development_only'] is True and v['promotable'] is False and v['entry_rule'] == CUT
    assert 'NON-PROMOTABLE' in v['label'] and 'DEVELOPMENT-ONLY' in v['label']
    assert RV.CANDIDATE == 'range_vwap_rev.v1' and PR.REX_RUN.candidate_id == RV.CANDIDATE   # v1 profile unchanged


def test_variant_run_is_a_distinct_non_promotable_run(plan, tmp_path):
    t = B.tape(vwap=S.long_entry(5), close={12: 104.0})
    f1 = B.run(B.env(plan, t=t), tmp_path / 'a')
    f2 = B.run(B.env(plan, t=t), tmp_path / 'b', variant=VID)
    d1, s1, _ = B.docs(f1)
    d2, s2, det2 = B.docs(f2)
    assert d1['run'] != d2['run']
    assert s1['candidate_id'] == RV.CANDIDATE and 'variant' not in s1
    assert s2['candidate_id'] == VID and s2['variant'] == RV.SPLITCUT and s2['promotable'] is False
    assert det2['access']['candidate_id'] == VID and det2['access']['detail']['promotable'] is False
    assert s1['run'] != s2['run']                                          # sha(run_config): the variant block


def test_variant_ledger_lineage_is_its_own_candidate(fx, monkeypatch, capsys):
    monkeypatch.setattr(PR, 'rex_sources', lambda repo: fx['src'])
    rc = G.main(['rex', '--window', LD.WINDOW, '--tier', 'CORE-10', '--out-dir', fx['out'], '--store', fx['store'],
                 '--notice', fx['notice'], '--notice-sha256', fx['art']['digest'], '--author', 't',
                 '--cairo-date', '2026-10-15', '--variant', VID])
    out = capsys.readouterr()
    assert rc in (0, 4), out.err
    final = out.out.strip().splitlines()[-1]
    doc = PG.read(final)
    summary = json.load(open(os.path.join(os.path.dirname(final), f'summary-{doc["run"]}.json')))
    assert summary['candidate_id'] == VID and summary['promotable'] is False
    assert not any(r['reason'] == 'incomplete.split_end' for r in summary.get('incomplete', []))
    recs = LD.records(fx['ledger'])
    assert [r['kind'] for r in recs] == ['data_access'] + (['variant'] * 9 + ['baseline']) * 2
    assert all(r['candidate_id'] == VID and r['detail']['promotable'] is False for r in recs)
    assert all(r['detail']['variant'] == RV.SPLITCUT for r in recs)


# ------------------------------------------------------------------ explicit selection only
def test_default_is_v1_and_unknown_variants_refuse(plan, tmp_path):
    assert inspect.signature(RV.run_book).parameters['entry_rule'].default is None
    assert inspect.signature(PR.run_rex).parameters['variant'].default is None
    assert inspect.signature(PR.rex_load).parameters['variant'].default is None
    assert PR.rex_variant(None) == (RV.CANDIDATE, None)
    with pytest.raises(RV.RexError):
        book(12, vwap=LE, rule='entry-cutoff/2')
    with pytest.raises(PR.PitRunError, match='unknown variant'):
        PR.rex_variant('range_vwap_rev.v2')
    with pytest.raises(PR.PitRunError, match='unknown variant'):
        B.run(B.env(plan), tmp_path / 'x', variant='splitcut')
    assert not os.path.exists(tmp_path / 'x')


def test_a_store_env_loaded_for_one_variant_refuses_the_other(plan, tmp_path):
    e = B.env(plan, ledger=str(tmp_path / 'range_vwap_rev.jsonl'), variant=VID)
    with pytest.raises(PR.PitRunError, match='loaded for'):
        B.run(e, tmp_path / 'o')                                           # no silent fallback to v1
    e = B.env(plan, ledger=str(tmp_path / 'range_vwap_rev.jsonl'))
    with pytest.raises(PR.PitRunError, match='loaded for'):
        B.run(e, tmp_path / 'o', variant=VID)                              # a v1 store env cannot run the variant
    assert not os.path.exists(tmp_path / 'o')


def test_cli_rejects_an_unknown_variant(capsys):
    with pytest.raises(SystemExit):
        G.main(['rex', '--window', B.WINDOW, '--tier', 'CORE-10', '--variant', 'range_vwap_rev.v2'])


def test_cli_without_variant_runs_v1(fx, monkeypatch, capsys):
    monkeypatch.setattr(PR, 'rex_sources', lambda repo: fx['src'])
    rc = G.main(['rex', '--window', LD.WINDOW, '--tier', 'CORE-10', '--out-dir', fx['out'], '--store', fx['store'],
                 '--notice', fx['notice'], '--notice-sha256', fx['art']['digest'], '--author', 't',
                 '--cairo-date', '2026-10-15'])
    assert rc in (0, 4)
    recs = LD.records(fx['ledger'])
    assert all(r['candidate_id'] == RV.CANDIDATE and 'variant' not in r['detail'] for r in recs)
