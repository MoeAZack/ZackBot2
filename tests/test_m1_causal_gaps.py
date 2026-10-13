"""M1: causal missing-data accounting in the R4b runner (missing-data policy zb-missing-data/1,
docs/newcore/research/MISSING_DATA_POLICY_v1.md; Codex M1 release #13 6153717648). Synthetic tapes only - no market
data, no registration, no ledger history, no holdout.

Cowork fixture contract v1 (#13 6153637054) + v2 delta (6153781897) + v1.1 (6153804359), Codex rulings controlling:
every case id M1-<ID> has a test named test_m1_<id> (lower case, '-' -> '_'). Same clock as the contract:
T0 = 2026-01-05T00:00Z, 4h bars, bar key K(k) = T0 + k x 4h covers [K(k-1), K(k)), the entry signal at K(300) fills
at the open K(300) (bar key K(301)), funding at K(2j) is played in bar key K(2j+1). The book halts at the FIRST
unaccountable event; everything through the last valid cycle is repr-identical to the same tape without the event
(`assert_prefix` compares against a run of the clean tape truncated there). Superseded v1 expectations (AG-01 / AG-02
continuation, MB-02 as a refusal) follow the Codex rulings and the v2 delta.
"""
import hashlib
import json
import math
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import costs as C                                                                           # noqa: E402
import pit_run as PR                                                                        # noqa: E402
import splits as S                                                                          # noqa: E402
import trend_ema_mom as T                                                                   # noqa: E402

TF = T.TF_MS
T0 = S.parse_utc('2026-01-05T00:00:00Z')
RULE = {'tick': 0.01, 'step': 0.001, 'min_qty': 0.001, 'min_notional': 5.0}
LONG = {c['name']: c for c in PR.configs()}
SHORT = {c['name']: c for c in PR.configs_short()} if hasattr(PR, 'configs_short') else {}
A, B, CC = 'AAAUSDT', 'BBBUSDT', 'CCCUSDT'
FIRST, LAST = 261, 320


def K(k):
    return T0 + k * TF


def tape(n=400, *, sym=A, side='long', le=(300,), lx=(340,), d='30.0', drop=(), end=None, fund_drop=(),
         mark_none=(), n_last=True):
    """Bars keyed 1..n (close K(k), open K(k-1) = 100, flat with a 0.5 range); funding at K(2j) (rate 1e-4, 8h, mark =
    the bar open); `drop` = bar keys missing (their funding rows stay); `end` = no record for keys > end (end of series);
    `fund_drop` / `mark_none` = funding TIMES without a row / with no mark."""
    out = []
    for k in range(1, n + 1):
        t = K(k)
        cyc = {'t': t, 'r': [], 'g': 'btc_up'}
        if side == 'short':
            cyc['side'] = 'short'
        if end is None or k <= end:
            f = [[K(k - 1), 0.0001, 8, None if K(k - 1) in mark_none else 100.0]] if (
                (k - 1) % 2 == 0 and K(k - 1) not in fund_drop) else []
            if k in drop:
                if f:
                    cyc['r'].append({'s': sym, 'f': f})
            else:
                cyc['r'].append({'s': sym, 'b': [100.0, 100.25, 99.75, 100.0], 'f': f, 'w': '1' * 13,
                                 'x': ('1' if k in lx else '0') * 13,
                                 'e': {str(j): d for j in range(13)} if k in le else {}, 'd': d,
                                 'n': 100.0 if (k < n or n_last) else None, 'a': '0.8'})
        out.append(cyc)
    return out


def merge(*tapes):
    return [dict(c[0], r=[r for x in c for r in x['r']]) for c in zip(*tapes)]


def cfg_of(side, name=None):
    return (SHORT if side == 'short' else LONG)[name or (PR.SHORT_PRIMARY_NAME if side == 'short' else PR.PRIMARY_NAME)]


def kw(tp, hi=None):
    syms = sorted({r['s'] for o in tp for r in o['r']})
    return dict(symbols=syms, first=K(FIRST), last=K(LAST), hi=hi or tp[-1]['t'], slip_c=0.05,
                rules={s: RULE for s in syms})


def book(tp, side='long', hi=None, cfg=None, **extra):
    return PR.run_book(PR.read_tape(tp), cfg_of(side, cfg), side=side, **kw(tp, hi), **extra)


def acct(tp, side='long', hi=None, cfg=None, **extra):
    return PR.account(tp, cfg_of(side, cfg), side=side, rows=('base',), **kw(tp, hi), **extra)


def lots_of(res):
    return sorted((r['symbol'], r['entry_ms'], r['qty'], r['fill'], r['stop'], r['fees'], r['funding'])
                  for r in res['incomplete'] if r.get('entry_ms') is not None)


def assert_prefix(res, clean, lv, side='long', hi=None, cfg=None, **extra):
    """repr-identical decisions / fills / trades / wallet / exposure / series through K(lv) versus the clean tape
    truncated at K(lv) (same split end): the event changed nothing before it, and nothing after it was produced."""
    ref = book([c for c in clean if c['t'] <= K(lv)], side, hi=hi or clean[-1]['t'], cfg=cfg, **extra)
    assert repr(res['series']) == repr(ref['series']) and res['series'][-1][0] == K(lv)
    assert repr(res['trades']) == repr(ref['trades'])
    assert res['refusals'] == ref['refusals'] and repr(res['wallet_end']) == repr(ref['wallet_end'])
    assert repr(lots_of(res)) == repr(lots_of(ref))                   # the open lots = the exposure at the refusal
    full = book(clean, side, hi=hi or clean[-1]['t'], cfg=cfg, **extra)
    assert repr(full['series'][:len(res['series'])]) == repr(res['series'])
    c = res['censor']                                                 # cash / exposure consistent at the refusal
    assert c['halted'] and c['last_valid_ms'] == K(lv) and c['wallet'] == res['wallet_end']
    assert c['mtm'] == res['series'][-1][1] and c['wallet'] + c['unrealised'] == c['mtm']
    assert c['open_lots'] == len(lots_of(res))


def primary(res):
    rows = [r for r in res['incomplete'] if not r.get('secondary')]
    assert len(rows) == 1, rows
    return rows[0]


def one(res, reason, first_k, last_k, sym=A, phase=None):
    assert res['status'] == 'INCOMPLETE' and res['policy'] == PR.POLICY
    r = primary(res)
    assert (r['symbol'], r['reason'], r['first_unaccountable_ms'], r['last_valid_ms']) == (sym, reason, K(first_k),
                                                                                          K(last_k))
    assert r['class'] == PR.GAP_CLASS[reason]
    if phase is not None:
        assert r['phase'] == phase
    return r


# ------------------------------------------------------------------ control
def test_m1_id_01():
    for side in ('long', 'short'):
        res = book(tape(side=side), side)
        assert res['status'] == 'COMPLETE' and res['incomplete'] == [] and 'censor' not in res
        (tr,) = res['trades']
        assert tr['entry_ms'] == K(300) and tr['exit_ms'] == K(340) and tr['exit_reason'] == 'exit.signal'
        a = acct(tape(side=side), side)
        assert a['status'] == 'COMPLETE' and a['rows']['base']['status'] == 'COMPLETE' and a['excluded_symbols'] == []
        PR.require_complete(a)


# ------------------------------------------------------------------ missing candle
@pytest.mark.parametrize('side', ['long', 'short'])
def test_m1_mb_01l_and_m1_mb_01s(side):
    res = book(tape(side=side, drop={310}), side)
    one(res, 'incomplete.gap', 310, 309, phase='bar')
    assert res['trades'] == []
    assert_prefix(res, tape(side=side), 309, side)


def test_m1_mb_01l():
    test_m1_mb_01l_and_m1_mb_01s('long')


def test_m1_mb_01s():
    test_m1_mb_01l_and_m1_mb_01s('short')


def test_m1_mb_02():
    """Codex ruling 4: a valid signal whose next-open candle is missing is INCOMPLETE (candle gap, phase next_open),
    no fill, no trade, and NOT a refusal."""
    res = book(tape(drop={301}), 'long')
    r = one(res, 'incomplete.gap', 301, 300, phase='next_open')
    assert r['entry_ms'] is None and res['trades'] == [] and res['refusals'] == {}
    assert_prefix(res, tape(), 300)
    assert acct(tape(drop={301}))['status'] == 'INCOMPLETE'


def test_m1_mb_03():
    """A flat symbol's candle gap that nothing depends on stays COMPLETE and identical to the control."""
    res = book(tape(drop={280}), 'long')
    assert res['status'] == 'COMPLETE' and repr(res) == repr(book(tape(drop={280}), 'long'))
    ctrl = book(tape(), 'long')
    assert repr(res['trades']) == repr(ctrl['trades']) and repr(res['wallet_end']) == repr(ctrl['wallet_end'])


def test_m1_mb_04():
    res = book(tape(drop={310, 320}), 'long')
    one(res, 'incomplete.gap', 310, 309)
    assert_prefix(res, tape(), 309)


# ------------------------------------------------------------------ funding gaps
@pytest.mark.parametrize('side', ['long', 'short'])
def test_m1_fg_01(side):
    res = book(tape(side=side, fund_drop={K(310)}), side)
    one(res, 'incomplete.funding', 310, 310, phase='funding')
    assert_prefix(res, tape(side=side), 310, side)


def test_m1_fg_01l():
    test_m1_fg_01('long')


def test_m1_fg_01s():
    test_m1_fg_01('short')


@pytest.mark.parametrize('side', ['long', 'short'])
def test_m1_fg_02(side):
    res = book(tape(side=side, mark_none={K(310)}), side)
    one(res, 'incomplete.funding_mark', 310, 310, phase='funding')
    assert_prefix(res, tape(side=side), 310, side)


def test_m1_fg_02l():
    test_m1_fg_02('long')


def test_m1_fg_02s():
    test_m1_fg_02('short')


def test_m1_fg_03():
    """A funding-rate gap outside every held lot's window (flat at K(280), lot from K(300)) is irrelevant: COMPLETE and
    digest-identical to the control."""
    for side in ('long', 'short'):
        res, ctrl = book(tape(side=side, fund_drop={K(280)}), side), book(tape(side=side), side)
        assert res['status'] == 'COMPLETE' and repr(res) == repr(ctrl)
        flat = book(tape(side=side, le=(), fund_drop={K(280)}), side)
        assert flat['status'] == 'COMPLETE' and repr(flat) == repr(book(tape(side=side, le=()), side))


# ------------------------------------------------------------------ delisting / end of series
@pytest.mark.parametrize('side', ['long', 'short'])
def test_m1_dl_01(side):
    res = book(tape(side=side, end=309), side)
    one(res, 'incomplete.delisted', 310, 309, phase='bar')
    assert res['trades'] == []                                         # no forced mark fill, no exit trade
    assert_prefix(res, tape(side=side), 309, side)


def test_m1_dl_01l():
    test_m1_dl_01('long')


def test_m1_dl_01s():
    test_m1_dl_01('short')


def test_m1_dl_04():
    res = book(tape(end=309), 'long')
    one(res, 'incomplete.delisted', 310, 309)
    assert res['trades'] == [] and not any('exit_price' in r or 'mark' in r for r in res['incomplete'])


def _delist(side):
    tp = tape(side=side, end=312, lx=())
    res = book(tp, side, delist={A: K(305)})
    assert res['status'] == 'COMPLETE' and res['incomplete'] == []
    (tr,) = res['trades']
    assert tr['exit_reason'] == 'exit.delist' and tr['exit_ms'] == K(306) > K(305)   # first open STRICTLY after
    x = PR.read_tape(tp)[1][A]
    bps = x.bps(K(306), C.TR_SMA14, 0.05)
    if side == 'short':
        want = PR.delist_cover(notice_ms=K(305), opens=[(K(306), 100.0)], last_mark=0.0, bps=bps,
                               scen=C.STRESS['base'], tick=0.01)
        assert want[1:] == (K(306), 'exit.delist') and tr['exit_price'] == want[0]
    else:
        assert tr['exit_price'] == C.fill_price(100.0, 'sell', bps, C.STRESS['base'], tick=0.01)
    a = acct(tp, side, delist={A: K(305)})
    assert a['status'] == 'COMPLETE'
    return res


def test_m1_dl_02():
    _delist('short')


def test_m1_dl_05():
    _delist('long')


def test_m1_dl_02_notice_exactly_at_an_open_is_not_executable_there():
    tp = tape(side='short', end=312, lx=())
    (tr,) = book(tp, 'short', delist={A: K(306) - 1})['trades']
    assert tr['exit_ms'] == K(306)                                     # notice 1 ms before the open: that open
    (tr,) = book(tp, 'short', delist={A: K(306)})['trades']
    assert tr['exit_ms'] == K(307)                                     # notice AT the open: the next one


def test_m1_dl_06():
    res = book(tape(side='short', end=305, lx=()), 'short', delist={A: K(305)})
    one(res, 'incomplete.delisted', 306, 305)
    assert res['trades'] == []


def test_m1_dl_03():
    res = book(tape(le=(), end=280), 'long')
    assert res['status'] == 'COMPLETE' and res['incomplete'] == [] and res['trades'] == []


def test_m1_dl_no_entry_after_a_known_notice():
    res = book(tape(lx=()), 'long', delist={A: K(299)})
    assert res['trades'] == [] and res['refusals'] == {'filter.delist_notice': 1} and res['status'] == 'COMPLETE'


# ------------------------------------------------------------------ split-end censoring
@pytest.mark.parametrize('side', ['long', 'short'])
def test_m1_sp_01(side):
    tp = tape(n=330, side=side, lx=(), n_last=False)
    res = book(tp, side)
    r = one(res, 'incomplete.split_end', 330, 330, phase='split_end')
    assert r['reason'] != 'incomplete.gap' and not res['censor']['halted']
    assert len(res['series']) == 330 and res['trades'] == []
    assert res['censor']['wallet'] + res['censor']['unrealised'] == res['censor']['mtm'] == res['series'][-1][1]


def test_m1_sp_03():
    test_m1_sp_01('short')


def test_m1_sp_02():
    tp = tape(n=330, lx=(330,))
    res = book(tp, 'long')
    assert res['status'] == 'COMPLETE'
    (tr,) = res['trades']
    assert tr['exit_ms'] == K(330) and tr['exit_reason'] == 'exit.signal'


# ------------------------------------------------------------------ aggregates: the first event wins, the row stops
def test_m1_ag_01(monkeypatch):
    clean = merge(tape(sym=A), tape(sym=B, le=(312,), lx=()))
    gapped = merge(tape(sym=A, drop={310}), tape(sym=B, le=(312,), lx=()))
    calls = []
    real = PR.run_book
    monkeypatch.setattr(PR, 'run_book', lambda *a, **k: calls.append(1) or real(*a, **k))
    a = acct(gapped)
    assert len(calls) == 1 and a['status'] == 'INCOMPLETE' and a['excluded_symbols'] == []
    monkeypatch.setattr(PR, 'run_book', real)
    res = book(gapped)
    one(res, 'incomplete.gap', 310, 309)
    assert not any(t['symbol'] == B for t in res['trades'])            # B's K(312) signal is never sized
    assert res['series'][-1][0] == K(309)
    assert_prefix(res, clean, 309)


def test_m1_ag_02():
    tp = merge(tape(sym=A, fund_drop={K(310)}), tape(sym=B, end=316), tape(sym=CC, n_last=False, lx=()))
    clean = merge(tape(sym=A), tape(sym=B), tape(sym=CC, n_last=False, lx=()))
    res = book(tp)
    one(res, 'incomplete.funding', 310, 310)
    sec = [r for r in res['incomplete'] if r.get('secondary')]
    assert {r['symbol'] for r in sec} == {B, CC} and all(r['reason'] == PR.HALTED for r in sec)
    assert not any(r['reason'] in ('incomplete.delisted', 'incomplete.split_end') for r in res['incomplete'])
    assert_prefix(res, clean, 310)


def test_m1_ag_03():
    tp = merge(tape(sym=A, drop={310}), tape(sym=B), tape(sym=CC))
    res = book(tp)
    one(res, 'incomplete.gap', 310, 309)
    sec = sorted((r for r in res['incomplete'] if r.get('secondary')), key=lambda r: r['symbol'])
    assert [(r['symbol'], r['reason'], r['primary_ref'], r['class']) for r in sec] == [
        (s, 'incomplete.book_halt', f'{A}@{K(310)}', 'halted') for s in (B, CC)]
    assert not any('exit_price' in r or 'mark' in r for r in res['incomplete'])
    assert res['trades'] == []                                         # halted lots' PnL is never a completed trade
    assert_prefix(res, merge(tape(sym=A), tape(sym=B), tape(sym=CC)), 309)


def test_m1_ag_04():
    tp = merge(tape(sym=A, side='short', mark_none={K(310)}), tape(sym=B, side='short', le=(311,), lx=()))
    res = book(tp, 'short')
    one(res, 'incomplete.funding_mark', 310, 310)
    assert not any(t['symbol'] == B for t in res['trades']) and res['series'][-1][0] == K(310)
    assert_prefix(res, merge(tape(sym=A, side='short'), tape(sym=B, side='short', le=(311,), lx=())), 310, 'short')


def test_m1_hb_01():
    test_m1_ag_03()


def test_m1_hb_02():
    """After the halt nothing is sized or filled for any symbol: no trade, refusal or series row past the last
    valid cycle (B's later signals included)."""
    tp = merge(tape(sym=A, drop={310}), tape(sym=B, le=(300, 312, 330), lx=(305, 340)))
    res = book(tp)
    assert res['series'][-1][0] == K(309) and all(t['exit_ms'] <= K(309) for t in res['trades'])
    assert [t['symbol'] for t in res['trades']] == [B]                 # B's trade closed before the gap is kept


# ------------------------------------------------------------------ metamorphic prefix property
def test_m1_mm_01():
    ks = (306, 310, 320)
    runs = {k: book(tape(drop={k})) for k in ks}
    for k in ks:
        one(runs[k], 'incomplete.gap', k, k - 1)
        assert_prefix(runs[k], tape(), k - 1)
    for k1 in ks:
        for k2 in ks:
            if k1 < k2:
                n = len(runs[k1]['series'])
                assert repr(runs[k2]['series'][:n]) == repr(runs[k1]['series'])


# ------------------------------------------------------------------ the unit (B1 long) and B1 short books
def test_m1_unit_b1_keeps_earlier_trades_and_halts_at_the_first_event():
    tp = merge(tape(sym=A, drop={140}), tape(sym=B))           # A and B both hold K(126) -> K(168)
    u = PR.account_unit(tp, LONG['B1'], first=K(20), last=K(350), hi=K(400), slip_c=0.05, rows=('base',))
    clean = PR.account_unit(merge(tape(sym=A), tape(sym=B)), LONG['B1'], first=K(20), last=K(350), hi=K(400),
                            slip_c=0.05, rows=('base',))
    assert clean['status'] == 'COMPLETE' and u['status'] == 'INCOMPLETE' and u['excluded_symbols'] == []
    r = primary(u)
    assert (r['symbol'], r['reason'], r['last_valid_ms']) == (A, 'incomplete.gap', K(139))
    keep = [t for t in clean['trades'] if t['exit_ms'] < K(139)]
    assert keep and all(t in u['trades'] for t in keep)                # earlier trades of A are NOT excluded
    assert all(t['exit_ms'] <= K(139) for t in u['trades']) and {t['symbol'] for t in keep} == {A, B}
    assert any(r.get('secondary') and r['symbol'] == B for r in u['incomplete'])


def test_m1_b1_short_book_halts_with_rollback():
    tp = merge(tape(sym=A, side='short', le=(), lx=(), drop={210}), tape(sym=B, side='short', le=(), lx=()))
    r = PR.b1_short_book(tp, symbols=[A, B], first=K(20), last=K(380), hi=K(400), slip_c=0.05,
                         rules={A: RULE, B: RULE})
    clean = PR.b1_short_book(merge(tape(sym=A, side='short', le=(), lx=()), tape(sym=B, side='short', le=(), lx=())),
                             symbols=[A, B], first=K(20), last=K(380), hi=K(400), slip_c=0.05,
                             rules={A: RULE, B: RULE})
    assert clean['status'] == 'COMPLETE' and r['status'] == 'INCOMPLETE' and r['month_r'] is None
    p = primary(r)
    assert (p['symbol'], p['reason'], p['last_valid_ms']) == (A, 'incomplete.gap', K(209))
    n = len(r['series'])
    assert r['series'][-1][0] == K(209) and repr(clean['series'][:n]) == repr(r['series'])
    assert repr(clean['legs'][:len(r['legs'])]) == repr(r['legs']) and all(lg['time_ms'] < K(209) for lg in r['legs'])
    assert r['row'] == clean['row'] == 'base'                         # Codex P2: the stress-row id survives a halt


# ------------------------------------------------------------------ Codex review 6154396021 corrections
@pytest.mark.parametrize('side', ['long', 'short'])
def test_m1_codex_p1_same_cycle_faults_have_exactly_one_primary_in_the_shared_book(side):
    """Two held symbols both missing at K(310): ONE primary (the first in the frozen order), the rest HALTED."""
    tp = merge(tape(sym=A, side=side, drop={310}), tape(sym=B, side=side, drop={310}), tape(sym=CC, side=side))
    res = book(tp, side)
    one(res, 'incomplete.gap', 310, 309, phase='bar')                 # primary() asserts exactly one
    sec = sorted((r for r in res['incomplete'] if r.get('secondary')), key=lambda r: r['symbol'])
    assert [(r['symbol'], r['reason'], r['primary_ref']) for r in sec] == [
        (s, 'incomplete.book_halt', f'{A}@{K(310)}') for s in (B, CC)]
    assert_prefix(res, merge(tape(sym=A, side=side), tape(sym=B, side=side), tape(sym=CC, side=side)), 309, side)


def test_m1_codex_p1_same_cycle_faults_have_exactly_one_primary_in_unit_b1():
    tp = merge(tape(sym=A, drop={140}), tape(sym=B, drop={140}))
    u = PR.account_unit(tp, LONG['B1'], first=K(20), last=K(350), hi=K(400), slip_c=0.05, rows=('base',))
    r = primary(u)                                                     # exactly one primary
    assert (r['symbol'], r['reason'], r['last_valid_ms'], r['secondary']) == (A, 'incomplete.gap', K(139), False)
    sec = [x for x in u['incomplete'] if x.get('secondary')]
    assert [(x['symbol'], x['reason'], x['primary_ref']) for x in sec] == [(B, PR.HALTED, f'{A}@{K(140)}')]
    with pytest.raises(PR.PitRunError):
        PR.require_complete(u)


def test_m1_codex_p1_b1_short_missing_execution_open_is_incomplete_next_open():
    """One eligible member decided at first=K(20); its execution candle K(21) is missing -> rolled back, INCOMPLETE
    incomplete.gap phase next_open, no leg or fill (was: silently COMPLETE)."""
    kw_ = dict(symbols=[A], first=K(20), last=K(380), hi=K(400), slip_c=0.05, rules={A: RULE}, row='base')
    clean = PR.b1_short_book(tape(sym=A, side='short', le=(), lx=()), **kw_)
    assert clean['status'] == 'COMPLETE' and clean['legs'][0]['time_ms'] == K(20)   # the control trades at K(20)
    r = PR.b1_short_book(tape(sym=A, side='short', le=(), lx=(), drop={21}), **kw_)
    assert r['status'] == 'INCOMPLETE' and r['legs'] == [] and r['month_r'] is None and r['row'] == 'base'
    p = primary(r)
    assert (p['symbol'], p['reason'], p['phase'], p['first_unaccountable_ms'], p['last_valid_ms']) == (
        A, 'incomplete.gap', 'next_open', K(21), K(20))
    n = len(r['series'])
    assert r['series'][-1][0] == K(20) and r['series'] == clean['series'][:n] and r['wallet_end'] == PR.BOOK['equity0']


# ------------------------------------------------------------------ status contract
def test_m1_st_01():
    for side in ('long', 'short'):
        res, a = book(tape(side=side), side), acct(tape(side=side), side)
        assert res['status'] == a['status'] == a['rows']['base']['status'] == 'COMPLETE'
        assert res['policy'] == a['policy'] == PR.POLICY
    d = PR._detail(acct(tape()))
    assert d['status'] == 'COMPLETE' and d['policy'] == PR.POLICY and d['rows']['base']['status'] == 'COMPLETE'
    m = PR.metrics(PR._acc(acct(tape())), rows=('base',))
    assert m['status'] == 'COMPLETE' and m['missing_data_policy'] == PR.POLICY and 'incomplete_classes' not in m
    u = PR.account_unit(tape(), LONG['B1'], first=K(20), last=K(350), hi=K(400), slip_c=0.05, rows=('base',))
    assert u['status'] in ('COMPLETE', 'INCOMPLETE') and u['policy'] == PR.POLICY
    bs = PR.b1_short_book(tape(side='short', le=(), lx=()), symbols=[A], first=K(20), last=K(380), hi=K(400),
                          slip_c=0.05, rules={A: RULE})
    assert bs['status'] == 'COMPLETE' and bs['policy'] == PR.POLICY
    bad = PR.metrics(PR._acc(acct(tape(drop={310}))), rows=('base',))
    assert bad['status'] == 'INCOMPLETE' and bad['incomplete_classes'] == {'missing_bar': 1}


# ------------------------------------------------------------------ complete-data equivalence vs master 4f7e3b4
def _rand_tape(rng, sym, side, n=520):
    out, c = [], 100.0
    les = {k for k in range(1, n) if rng.random() < 0.06}
    lxs = {k for k in range(1, n) if rng.random() < 0.05}
    for k in range(1, n + 1):
        o = c
        c = max(5.0, round(c * (1 + rng.gauss(0, 0.012)), 2))
        if rng.random() < 0.03:
            o = round(o * (1 + rng.choice((-1, 1)) * rng.uniform(0.01, 0.04)), 2)    # gapping opens
        h, lo = max(o, c) + round(rng.uniform(0.05, 1.2), 2), min(o, c) - round(rng.uniform(0.05, 1.2), 2)
        f = [[K(k - 1) + 3, round(rng.uniform(-3e-4, 3e-4), 6), 8, o]] if (k - 1) % 2 == 0 else []
        d = f'{rng.uniform(0.8, 4.0):.2f}'
        rec = {'s': sym, 'b': [o, h, lo, c], 'f': f, 'w': ''.join(rng.choice('1111110') for _ in range(13)),
               'x': ''.join('1' if (k in lxs and rng.random() < 0.8) else '0' for _ in range(13)),
               'e': {str(j): d for j in range(13) if rng.random() < 0.8} if k in les else {}, 'd': d,
               'n': c, 'a': f'{rng.uniform(0.3, 2.0):.3f}'}
        cyc = {'t': K(k), 'r': [rec], 'g': rng.choice(('btc_up', 'btc_down'))}
        if side == 'short':
            cyc['side'] = 'short'
        out.append(cyc)
    return out


def _strip(o):
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if k not in ('status', 'policy')}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


def complete_data_digest(pr=PR) -> str:
    """sha256 over every long and short configuration's account (all stress rows for primary / baselines) on seeded
    complete multi-symbol tapes; only the new explicit status / policy fields are stripped. Every row must be complete
    on both sides of the comparison (the uncapped development-only variant is split-end censored by design and is
    omitted)."""
    out = []
    for seed in (1, 2, 3):
        for side in ('long', 'short'):
            rng = random.Random(seed * 7919 + (side == 'short'))
            tp = merge(*(_rand_tape(rng, s, side) for s in (A, B, CC)))
            n = len(tp)
            cfgs = pr.configs() if side == 'long' else pr.configs_short()
            for c in cfgs:
                if c['name'] == pr.DEV_ONLY:
                    continue
                rows = tuple(C.STRESS) if c['role'] in ('primary', 'baseline') else ('base',)
                res = pr.account(tp, c, symbols=[A, B, CC], first=K(40), last=K(n - 182), hi=K(n), slip_c=0.05,
                                 rules={s: RULE for s in (A, B, CC)}, rows=rows, side=side)
                assert not res.get('incomplete') and not any(r.get('incomplete') for r in res['rows'].values()
                                                             if isinstance(r, dict)) if 'rows' in res else True, c
                out.append([seed, side, c['name'], _strip(res)])
    return hashlib.sha256(json.dumps(out, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


# complete_data_digest() run against the tools/research tree of master 4f7e3b4 (status / policy fields stripped)
GOLDEN_4F7E3B4 = '4e5727e1c20638a679ce90a96610f85b1a1607e6116e29d23e297e068531a6b3'


def test_m1_complete_data_digest_equals_master_4f7e3b4():
    assert GOLDEN_4F7E3B4 is not None and complete_data_digest() == GOLDEN_4F7E3B4


# ------------------------------------------------------------------ consumer refusal (M1-CN-01 .. 07)
import ledger as L                                                                          # noqa: E402
import progress as PG                                                                       # noqa: E402
import r4_readiness as RD                                                                   # noqa: E402
import report as RP                                                                         # noqa: E402
import run_r4 as G                                                                          # noqa: E402


def gapped():
    return acct(tape(drop={310}))                                      # an INCOMPLETE account


def clean():
    return acct(tape())


def _stub_env():
    rc = {'configs': ['x'], 'stress_rows': ['base'], 'seed': 1, 'resamples': 10}
    ident = {'commit': '1' * 40, 'evaluator': PR.sha(rc), 'manifest': '2' * 64, 'universe': '3' * 64,
             'classification': None, 'prereg': None}
    return {'tiers': {'CORE-10': ['A'], 'CORE-20': ['A', 'B']}, 'run_config': rc, 'identity': ident}


def _seal(path, monkeypatch, summary):
    monkeypatch.setattr(PR, 'evaluate_tier', lambda env, tier, prog: (dict(summary, tier=tier), {'tier': tier}))
    return PR.run_tiers(_stub_env(), str(path), clock=None)


class _Plan:
    def split(self, key):
        return {'name': 'train', 'fold': None}


def _summary(tp):
    env = {'run_config': {'stress_rows': ['base'], 'seed': 1, 'resamples': 10, 'perms': 10}, 'plan': _Plan(),
           'n_trials': 14, 'cost_labels': [], 'identity': {}}
    det = {'train': {'configs': {c['name']: PR._detail(PR.account(tp, c, rows=('base',), **kw(tp)))
                                 for c in PR.configs()}}}
    return PR.summarize(env, 'CORE-10', [A], {'train': {}}, det, [], [])[0]


def test_m1_cn_01(tmp_path):
    """Ledger: no ledger kind carries a result; the only promotable record (holdout_reveal) needs a sealed report,
    which refuses an INCOMPLETE payload before anything happens: the ledger file bytes are unchanged."""
    assert not {'result', 'promotion', 'evidence'} & set(L.KINDS)
    led = tmp_path / 'trend_ema_mom.jsonl'
    led.write_bytes(b'')
    for bad in ({'long': {'status': 'INCOMPLETE'}}, {'long': {'incomplete': [{'reason': 'incomplete.gap'}]}}):
        with pytest.raises(RP.ReportError):
            RP.make_report({}, bad, {}, str(led))
    assert led.read_bytes() == b''


def test_m1_cn_02(tmp_path, monkeypatch):
    """Report / readiness / promotion refuse an INCOMPLETE result; readiness never turns it into a PASS."""
    with pytest.raises(RP.ReportError, match='INCOMPLETE'):
        RP.refuse_incomplete({'long': {'base': gapped()}})
    fin = _seal(tmp_path, monkeypatch, {'status': 'INCOMPLETE'})[0]
    assert PG.read(fin)['state'] == 'sealed' and PG.read(fin)['evidence'] == []
    with pytest.raises(RD.ReadinessError, match='INCOMPLETE'):
        RD.sealed_artifacts(fin)
    with pytest.raises(RD.ReadinessError):
        RD.build({'pilot': {'status': 'INCOMPLETE'}})
    assert G.pit_incomplete([fin]) == [fin]


def test_m1_cn_03(tmp_path, monkeypatch):
    """A legacy result with no status key fails closed everywhere."""
    with pytest.raises(PR.PitRunError, match='no explicit'):
        PR.require_complete({'trades': [], 'incomplete': []})
    fin = _seal(tmp_path, monkeypatch, {'format': PR.SUMMARY_FORMAT})[0]
    assert PG.read(fin)['evidence'] == [] and G.pit_incomplete([fin]) == [fin]
    with pytest.raises(RD.ReadinessError):
        RD.sealed_artifacts(fin)
    assert PR.metrics({'counted': [], 'incomplete': [], 'refusals': {}, 'excluded_symbols': [],
                       'excluded_trades': 0}, rows=('base',))['status'] == 'INCOMPLETE'
    with pytest.raises(RP.ReportError):
        RP.refuse_incomplete({'long': {'status': None}})


def test_m1_cn_04():
    """COMPLETE that still carries incomplete rows / excluded symbols / a censor is a contradiction."""
    for bad in (dict(clean(), incomplete=[{'reason': 'incomplete.gap', 'secondary': False}]),
                dict(clean(), excluded_symbols=[A]), dict(clean(), censor={'halted': True})):
        with pytest.raises(PR.PitRunError, match='contradict'):
            PR.require_complete(bad)
    with pytest.raises(RP.ReportError):
        RP.refuse_incomplete({'long': {'status': 'COMPLETE', 'excluded_symbols': [A]}})


def test_m1_cn_05(tmp_path, monkeypatch):
    """The control is accepted unchanged by every consumer."""
    PR.require_complete(clean())
    RP.refuse_incomplete({'long': {'base': clean()}})
    finals = _seal(tmp_path, monkeypatch, {'status': 'COMPLETE'})
    assert all(PG.read(f)['evidence'] for f in finals) and G.pit_incomplete(finals) == []
    RD.sealed_artifacts(finals[0])
    s = _summary(tape())
    assert s['status'] == 'COMPLETE' and s['verdict'] == 'PARK' and 'incomplete_rows' not in s
    PR.require_complete(s)


def test_m1_cn_06(tmp_path, monkeypatch):
    """One refusal per consumer: detail, aggregate, report, readiness, gate (summary / verdict / EDGE rows),
    promotion (the run_r4 CLI exits 4, never success)."""
    d = PR._detail(gapped())                                           # detail
    assert d['status'] == 'INCOMPLETE' and d['rows']['base']['censor']['halted']
    with pytest.raises(PR.PitRunError):
        PR.require_complete(d)
    m, _ = PR._agg({'train': {'configs': {PR.PRIMARY_NAME: d}}}, PR.PRIMARY_NAME, ['train'], ('base',), None)
    assert m['status'] == 'INCOMPLETE' and m['book']['base']['net_pct_equity'] is None          # aggregate
    with pytest.raises(RP.ReportError):                                # report
        RP.refuse_incomplete({'long': {'base': d}})
    fin = _seal(tmp_path, monkeypatch, {'status': 'INCOMPLETE'})[0]
    with pytest.raises(RD.ReadinessError):                             # readiness
        RD.sealed_artifacts(fin)
    s = _summary(tape(drop={310}))                                     # gate / verdict / EDGE rows
    assert s['status'] == 'INCOMPLETE' and s['verdict'] == 'INCOMPLETE' and 'INCOMPLETE' in s['flags']
    assert s['incomplete_rows']['primary'] == 1 and s['incomplete_classes']['missing_bar'] >= 1
    assert not any(v['status'] in ('PASS', 'FAIL') for v in s['edge00'].values())
    assert A.encode() not in json.dumps(s).encode()                    # disclosure: no symbol in the summary
    with pytest.raises(PR.PitRunError):
        PR.require_complete(s)
    monkeypatch.setattr(G, 'run_pit', lambda *a, **k: [fin])           # promotion / CLI
    assert G.main(['pit', '--author', 'x', '--cairo-date', '2026-10-13', '--store', 's']) == 4


def test_m1_cn_07():
    rows = [{'symbol': B, 'reason': PR.HALTED, 'secondary': True, 'primary_ref': f'{A}@{K(310)}'}]
    for status in ('INCOMPLETE', 'COMPLETE'):
        with pytest.raises(PR.PitRunError, match='without a primary'):
            PR.require_complete({'status': status, 'incomplete': rows})
